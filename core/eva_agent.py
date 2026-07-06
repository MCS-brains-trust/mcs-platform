"""
Eva Agentic Orchestrator
=========================
Implements a ReAct-style (Reason + Act) agent loop that replaces the
single-shot LLM calls in eva_service.py for complex queries.

The agent can call 8 tools iteratively before producing a final answer:

  1. query_trial_balance       — fetch specific TB lines for an entity/year
  2. search_knowledge_brain    — hybrid RAG search (vector + FTS + lessons)
  3. query_client_graph        — traverse EntityRelationship graph
  4. check_prior_suppressions  — check if a finding was previously suppressed
  5. get_prior_year_findings   — retrieve unresolved findings from prior years
  6. get_learned_lessons       — search EvaLearnedLesson table directly
  7. get_user_style_profile    — retrieve the accountant's style prompt fragment
  8. final_answer              — signal that the agent is done

Every run is logged to EvaAgentTrace for full auditability.

Usage:
    from core.eva_agent import EvaAgentLoop

    agent = EvaAgentLoop(
        financial_year=fy,
        user=request.user,
        trigger_type="chat",
        trigger_input="Is there a Div7A issue with the director loan?",
    )
    result = agent.run()
    # result = {"answer": "...", "trace_id": "...", "iterations": 3}
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 8
AGENT_SYSTEM_PROMPT = """You are Eva, an expert Australian tax and accounting AI assistant
embedded within the StatementHub platform used by MC & S accountants.

You have access to a set of tools to help you answer questions accurately.
You MUST use tools to gather evidence before answering. Do not guess.

## Tool Use Protocol
When you need to use a tool, respond with ONLY a JSON object in this exact format:
{
  "tool": "<tool_name>",
  "args": {<tool arguments>},
  "reasoning": "<one sentence explaining why you are calling this tool>"
}

When you have enough information to answer, respond with:
{
  "tool": "final_answer",
  "args": {"answer": "<your complete answer here>"},
  "reasoning": "I have gathered sufficient evidence to answer."
}

## Rules
- Always check prior suppressions before raising a compliance issue.
- Always check prior year findings for recurring issues.
- Always check learned lessons for entity-specific preferences.
- If the user has a style profile, apply it to your final answer tone.
- Cite specific dollar amounts, account codes, and year labels when available.
- Maximum {max_iterations} tool calls allowed. Be efficient.
- If you cannot find the information needed, say so clearly in your final answer.

## Available Tools
{tool_descriptions}
"""


class EvaAgentLoop:
    """
    The core agent loop. Instantiate with context, then call .run().
    """

    def __init__(
        self,
        financial_year=None,
        user=None,
        trigger_type: str = "chat",
        trigger_input: str = "",
        entity=None,
    ):
        self.financial_year = financial_year
        self.entity = entity or (financial_year.entity if financial_year else None)
        self.user = user
        self.trigger_type = trigger_type
        self.trigger_input = trigger_input
        self.steps = []
        self.iteration = 0
        self._trace = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def run(self) -> dict:
        """
        Execute the agent loop. Returns:
          {
            "answer": str,
            "trace_id": str,
            "iterations": int,
            "steps": list,
          }
        """
        self._create_trace()

        messages = [
            {"role": "user", "content": self.trigger_input}
        ]

        final_answer = None

        while self.iteration < MAX_ITERATIONS:
            self.iteration += 1

            # Call the LLM
            try:
                response_text = self._call_agent_llm(messages)
            except Exception as e:
                logger.error(f"Agent LLM call failed at iteration {self.iteration}: {e}")
                final_answer = f"Eva encountered an error during analysis: {e}"
                break

            # Parse the tool call
            tool_call = self._parse_tool_call(response_text)
            if not tool_call:
                # LLM returned something unparseable — treat as final answer
                final_answer = response_text
                break

            tool_name = tool_call.get("tool", "")
            tool_args = tool_call.get("args", {})
            reasoning = tool_call.get("reasoning", "")

            # Check for final answer
            if tool_name == "final_answer":
                final_answer = tool_args.get("answer", response_text)
                self._log_step(tool_name, tool_args, reasoning, "Final answer produced.")
                break

            # Execute the tool
            try:
                tool_result = self._execute_tool(tool_name, tool_args)
            except Exception as e:
                logger.warning(f"Tool '{tool_name}' failed: {e}")
                tool_result = {"error": str(e)}

            # Log the step
            self._log_step(tool_name, tool_args, reasoning, tool_result)

            # Append tool result to conversation
            result_text = json.dumps(tool_result, default=str)[:3000]  # cap context size
            messages.append({"role": "assistant", "content": response_text})
            messages.append({
                "role": "user",
                "content": f"Tool result for '{tool_name}':\n{result_text}\n\nContinue your analysis."
            })

        if not final_answer:
            final_answer = "Eva reached the maximum number of analysis steps without a conclusive answer. Please review the agent trace for details."

        self._complete_trace(final_answer)

        return {
            "answer": final_answer,
            "trace_id": str(self._trace.id) if self._trace else None,
            "iterations": self.iteration,
            "steps": self.steps,
        }

    # -----------------------------------------------------------------------
    # Tool Execution
    # -----------------------------------------------------------------------
    def _execute_tool(self, tool_name: str, args: dict) -> dict:
        """Dispatch a tool call to the appropriate handler."""
        handlers = {
            "query_trial_balance": self._tool_query_trial_balance,
            "search_knowledge_brain": self._tool_search_knowledge_brain,
            "query_client_graph": self._tool_query_client_graph,
            "check_prior_suppressions": self._tool_check_prior_suppressions,
            "get_prior_year_findings": self._tool_get_prior_year_findings,
            "get_learned_lessons": self._tool_get_learned_lessons,
            "get_user_style_profile": self._tool_get_user_style_profile,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}
        return handler(**args)

    def _tool_query_trial_balance(self, account_code: str = None, account_name: str = None,
                                   section: str = None, **kwargs) -> dict:
        """Fetch trial balance lines for the current financial year."""
        if not self.financial_year:
            return {"error": "No financial year in context"}

        from core.models import TrialBalanceLine
        qs = TrialBalanceLine.objects.filter(financial_year=self.financial_year)

        if account_code:
            qs = qs.filter(account_code__icontains=account_code)
        if account_name:
            qs = qs.filter(account_name__icontains=account_name)
        if section:
            qs = qs.filter(section__icontains=section)

        lines = []
        for line in qs.order_by("account_code")[:50]:
            lines.append({
                "account_code": line.account_code,
                "account_name": line.account_name,
                "section": line.section or "",
                "debit": float(line.debit or 0),
                "credit": float(line.credit or 0),
                "balance": float((line.debit or 0) - (line.credit or 0)),
                "is_adjustment": line.is_adjustment,
            })

        return {
            "financial_year": str(self.financial_year),
            "entity": self.entity.entity_name if self.entity else "",
            "lines": lines,
            "count": len(lines),
        }

    def _tool_search_knowledge_brain(self, query: str, category: str = None, **kwargs) -> dict:
        """Search the knowledge brain using hybrid retrieval."""
        from core.eva_retrieval import hybrid_search
        results = hybrid_search(
            query=query,
            user=self.user,
            entity=self.entity,
            category_filter=category,
            top_k=6,
        )
        return {
            "query": query,
            "results": results,
            "count": len(results),
        }

    def _tool_query_client_graph(self, depth: int = 2, include_div7a: bool = True,
                                  include_officers: bool = True, **kwargs) -> dict:
        """Traverse the entity relationship graph."""
        if not self.entity:
            return {"error": "No entity in context"}
        from core.eva_graph import query_client_graph
        return query_client_graph(
            entity_id=str(self.entity.id),
            depth=depth,
            include_div7a=include_div7a,
            include_officers=include_officers,
            include_prior_findings=False,  # handled by separate tool
        )

    def _tool_check_prior_suppressions(self, check_name: str = None, finding_key: str = None, **kwargs) -> dict:
        """Check if a specific finding was previously suppressed for this entity."""
        if not self.entity:
            return {"error": "No entity in context"}

        from core.models import EvaFindingSuppression

        # EvaFindingSuppression has no direct entity FK and no check_name/finding_key/
        # reason/created_at fields.  It is keyed to a financial_year and stores a
        # v2 fingerprint (entity_id + fy_id + finding_key), rule_category,
        # suppressed_by, suppressed_at and accountant_note.  Scope to this entity's
        # financial years and honour only applicable (non-review) suppressions.
        qs = EvaFindingSuppression.objects.filter(
            financial_year__entity=self.entity,
            requires_review=False,
        )

        if finding_key and self.financial_year:
            # Compute the exact v2 fingerprint and match precisely.
            fingerprint = EvaFindingSuppression.generate_fingerprint(
                str(self.entity.id),
                str(self.financial_year.pk),
                finding_key,
            )
            qs = qs.filter(fingerprint=fingerprint)
        elif check_name:
            # Fall back to the coarse rule_category discriminator.
            qs = qs.filter(rule_category__icontains=check_name)

        suppressions = []
        for s in qs.select_related("suppressed_by", "financial_year").order_by("-suppressed_at")[:20]:
            suppressions.append({
                "rule_category": s.rule_category,
                "financial_year": str(s.financial_year),
                "note": s.accountant_note or "",
                "suppressed_by": s.suppressed_by.get_full_name() if s.suppressed_by else "Unknown",
                "suppressed_at": str(s.suppressed_at),
            })

        return {
            "entity": self.entity.entity_name,
            "suppressions": suppressions,
            "count": len(suppressions),
            "is_suppressed": len(suppressions) > 0,
        }

    def _tool_get_prior_year_findings(self, years_back: int = 3, **kwargs) -> dict:
        """Get prior year Eva findings for this entity."""
        if not self.entity:
            return {"error": "No entity in context"}
        from core.eva_graph import GraphQueryService
        svc = GraphQueryService(entity_id=str(self.entity.id))
        return svc.get_prior_year_findings(years_back=years_back)

    def _tool_get_learned_lessons(self, query: str = "", category: str = None, **kwargs) -> dict:
        """Search EvaLearnedLesson records for relevant firm knowledge."""
        from core.models import EvaLearnedLesson
        from django.db.models import Q
        qs = EvaLearnedLesson.objects.filter(is_active=True)

        if category:
            qs = qs.filter(category=category)

        # Apply the text filter to the queryset BEFORE slicing — filtering the
        # already-sliced first 20 rows silently drops relevant lessons past 20.
        if query:
            qs = qs.filter(lesson_text__icontains=query)

        # Scope to this user/entity OR firm-wide (null) lessons.  Compose with Q
        # on the already-filtered qs so the category/text filters above are
        # preserved on BOTH operands (the previous `| Model.objects.filter(...)`
        # dropped them for the firm-wide side).
        if self.user:
            qs = qs.filter(Q(source_user=self.user) | Q(source_user__isnull=True))
        if self.entity:
            qs = qs.filter(Q(source_entity=self.entity) | Q(source_entity__isnull=True))

        lessons = []
        for lesson in qs.distinct().order_by("-priority_weight")[:20]:
            lessons.append({
                "lesson": lesson.lesson_text,
                "category": lesson.get_category_display(),
                "priority_weight": lesson.priority_weight,
                "source": lesson.source_signal_type or "reflection",
            })

        return {
            "lessons": lessons,
            "count": len(lessons),
        }

    def _tool_get_user_style_profile(self, **kwargs) -> dict:
        """Retrieve the current user's writing style profile."""
        if not self.user:
            return {"has_profile": False, "prompt_fragment": ""}

        try:
            profile = self.user.eva_style_profile
            if profile.is_usable:
                return {
                    "has_profile": True,
                    "prompt_fragment": profile.prompt_fragment,
                    "confidence": profile.confidence_score,
                    "edits_analysed": profile.bas_commentary_edits_analysed + profile.yearend_commentary_edits_analysed,
                }
        except Exception:
            pass

        return {"has_profile": False, "prompt_fragment": ""}

    # -----------------------------------------------------------------------
    # LLM Communication
    # -----------------------------------------------------------------------
    def _build_system_prompt(self) -> str:
        """Build the agent system prompt with tool descriptions."""
        tool_descriptions = """
- query_trial_balance(account_code, account_name, section): Fetch trial balance lines. Use to check specific account balances.
- search_knowledge_brain(query, category): Search ATO guidelines, tax rulings, and firm knowledge. Use for compliance questions.
- query_client_graph(depth, include_div7a, include_officers): Get related entities, directors, and Div7A exposure chains.
- check_prior_suppressions(check_name, finding_key): Check if this issue was previously suppressed by an accountant.
- get_prior_year_findings(years_back): Get unresolved findings from prior Eva reviews.
- get_learned_lessons(query, category): Search lessons learned from accountant corrections.
- get_user_style_profile(): Get the current accountant's writing style preferences.
- final_answer(answer): Provide your final answer. Always call this when done.
"""
        return AGENT_SYSTEM_PROMPT.format(
            max_iterations=MAX_ITERATIONS,
            tool_descriptions=tool_descriptions,
        )

    def _call_agent_llm(self, messages: list) -> str:
        """Call the LLM with the current conversation history."""
        from core.ai_service import _call_llm

        # Build conversation text for single-turn APIs
        conversation_parts = []
        for msg in messages:
            role = msg["role"].upper()
            content = msg["content"]
            conversation_parts.append(f"[{role}]: {content}")

        conversation_text = "\n\n".join(conversation_parts)

        return _call_llm(
            system_prompt=self._build_system_prompt(),
            user_prompt=conversation_text,
            tier="sonnet",
            temperature=0.1,
            max_tokens=1500,
        )

    # -----------------------------------------------------------------------
    # Parsing & Logging
    # -----------------------------------------------------------------------
    def _parse_tool_call(self, response_text: str) -> Optional[dict]:
        """
        Parse a JSON tool call from the LLM response.
        Returns None if the response is not a valid tool call.
        """
        if not response_text:
            return None

        # Try to extract JSON from the response
        text = response_text.strip()

        # Strip a markdown code fence if present (```json … ``` or ``` … ```).
        if text.startswith("```"):
            import re
            text = re.sub(r"^```[a-zA-Z0-9_]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()

        # Try parsing the whole (fence-stripped) response as JSON first.
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "tool" in parsed:
                return parsed
        except json.JSONDecodeError:
            pass

        # Scan for the first balanced JSON object.  A regex like
        # \{[^{}]*"tool"[^{}]*\} cannot cross the nested args:{}, so use
        # raw_decode from each candidate '{' to find a well-formed object.
        decoder = json.JSONDecoder()
        idx = 0
        while True:
            start = text.find("{", idx)
            if start == -1:
                break
            try:
                obj, _end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                idx = start + 1
                continue
            if isinstance(obj, dict) and "tool" in obj:
                return obj
            idx = start + 1

        # If the response contains a final answer in plain text, wrap it
        if len(text) > 50 and not text.startswith("{"):
            return {
                "tool": "final_answer",
                "args": {"answer": text},
                "reasoning": "LLM provided a direct answer.",
            }

        return None

    def _log_step(self, tool_name: str, tool_args: dict, reasoning: str, result) -> None:
        """Log a single agent step."""
        step = {
            "step": self.iteration,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "reasoning": reasoning,
            "tool_result": result if isinstance(result, (dict, list)) else str(result),
        }
        self.steps.append(step)

        # Update the trace record
        if self._trace:
            try:
                self._trace.steps = self.steps
                self._trace.total_iterations = self.iteration
                self._trace.save(update_fields=["steps", "total_iterations"])
            except Exception as e:
                logger.warning(f"Failed to update agent trace: {e}")

    # -----------------------------------------------------------------------
    # Trace Management
    # -----------------------------------------------------------------------
    def _create_trace(self) -> None:
        """Create an EvaAgentTrace record for this run."""
        try:
            from core.models import EvaAgentTrace
            self._trace = EvaAgentTrace.objects.create(
                financial_year=self.financial_year,
                user=self.user,
                trigger_type=self.trigger_type,
                trigger_input=self.trigger_input[:2000],
                steps=[],
            )
        except Exception as e:
            logger.warning(f"Failed to create agent trace: {e}")
            self._trace = None

    def _complete_trace(self, final_answer: str) -> None:
        """Mark the trace as complete with the final answer."""
        if not self._trace:
            return
        try:
            self._trace.final_answer = final_answer[:5000]
            self._trace.completed_at = datetime.now(timezone.utc)
            self._trace.total_iterations = self.iteration
            self._trace.steps = self.steps
            self._trace.save(update_fields=[
                "final_answer", "completed_at", "total_iterations", "steps"
            ])
        except Exception as e:
            logger.warning(f"Failed to complete agent trace: {e}")


# ---------------------------------------------------------------------------
# Convenience function for use in views and tasks
# ---------------------------------------------------------------------------
def run_eva_agent(
    trigger_input: str,
    financial_year=None,
    user=None,
    entity=None,
    trigger_type: str = "chat",
) -> dict:
    """
    Convenience wrapper to run the Eva agent loop.

    Returns:
        {
            "answer": str,
            "trace_id": str or None,
            "iterations": int,
        }
    """
    agent = EvaAgentLoop(
        financial_year=financial_year,
        user=user,
        trigger_type=trigger_type,
        trigger_input=trigger_input,
        entity=entity,
    )
    return agent.run()
