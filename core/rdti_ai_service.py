"""
R&D Tax Incentive (RDTI) Drafter — AI Service Layer
Spec: R&DTI Drafter MVP v0.3

Prompt architecture (per spec):
  Intake JSON
      ↓
  Field-specific drafting prompts (one per narrative field)  [Sonnet]
      ↓
  Per-field validator passes                                  [Haiku]
      ↓
  Cross-field consistency check                              [Sonnet]
      ↓
  Character-limit enforcer
      ↓
  Draft with compliance flags

Model assignment:
  - claude-sonnet-4-6 (or gpt-4.1-mini fallback): Drafting, cross-field consistency
  - claude-haiku-4-5 (or gpt-4.1-nano fallback): Individual field validators
"""
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

CHAR_LIMIT = 4000
PROMPT_VERSION = "rdti-v1.0"

# ---------------------------------------------------------------------------
# Style guide injected into every drafting prompt
# ---------------------------------------------------------------------------

STYLE_GUIDE = """
## Writing Style Guide (AusIndustry Register)

**Voice:** Third person. "The Company" or "the activity," never "we" or "you."
**Tense:** Past tense for completed work, future tense for planned work, present for ongoing.
**Tone:** Formal but readable. Avoid bureaucratic padding. One idea per sentence.

**Preferred phrases (use exactly):**
- "Systematic progression of work"
- "Outcomes could not be known or determined in advance"
- "Could only be determined by applying..."
- "Iteratively refined based on results"
- "Evaluated against baseline metrics"
- "Contemporaneous records"

**Avoid:**
- "We developed X" (too general)
- "Innovative," "cutting-edge," "world-first" (marketing language)
- "Solved the problem" (implies outcome was determinable)
- "Successfully" (premature)
- "Breakthrough" (unsupportable)

**Hypothesis structure:**
Opening: "Building on the [foundational work / baseline system] validated during the [period], the hypothesis for this activity is that [specific testable claim]."
Sub-hypotheses: "(a) That [doing X] will [achieve measurable outcome] compared to [baseline]"

**Competent professional framing:**
"A competent professional in the fields of [X, Y] could not have determined the outcomes of this activity in advance for the following reasons. [Specific technical argument]. A competent professional could form reasonable expectations about the general direction of outcomes but could not have determined the specific [results / accuracy / efficiency] achievable within this [context] without undertaking the systematic experimental work described."

**New knowledge framing (avoid "new to us" trap):**
"This finding could not have been derived from existing literature, as it reflects the specific interaction between [the platform's / process's] [architecture / configuration] and the [domain-specific context]."

**Conclusion framing (include what didn't work):**
"The experiment demonstrated that it is possible to [outcome]. However, [specific limitation or residual uncertainty]. This is a rapidly evolving area requiring continued development and iteration in future income periods."
"""

# ---------------------------------------------------------------------------
# AusIndustry Guide to Interpretation — key excerpts for system prompts
# ---------------------------------------------------------------------------

AUSIND_GUIDE_EXCERPT = """
## AusIndustry Guide to Interpretation — Key Principles

**Core R&D Activity requirements (s.355-25 ITAA 1997):**
1. The activity must be conducted for the purpose of generating new knowledge (including new knowledge in the form of new or improved materials, products, devices, processes or services).
2. The activity must involve systematic progression of work based on principles of established science.
3. The outcome of the activity must not be known or determined in advance.
4. The activity must be conducted for the purpose of generating new knowledge.

**Excluded activities (s.355-25(2)):**
- Market research, market testing or market development
- Management studies or efficiency surveys
- Research in social sciences, arts or humanities
- Prospecting, exploring or drilling for minerals, petroleum or natural gas
- The commercial, legal and financial steps necessary to commercialise new or improved materials, products, devices, processes or services
- Activities involved in complying with statutory requirements or standards
- Any activity to the extent that it is carried on outside Australia
- Activities that are directly related to the production of a commercial product or process

**The "competent professional" test:**
A competent professional is a person with relevant knowledge and experience in the field. The question is whether such a person could have determined the outcome of the activity in advance without conducting the experimental work. This is an objective test.

**New knowledge:**
New knowledge must be domain-level knowledge — knowledge that advances the field, not merely knowledge that is new to the applicant. The activity must generate knowledge that could not have been derived from existing literature or expert consultation.
"""

# ---------------------------------------------------------------------------
# LLM call helper (reuses existing ai_service pattern)
# ---------------------------------------------------------------------------

def _call_llm(system_prompt: str, user_prompt: str, tier: str = "sonnet",
              temperature: float = 0.4, max_tokens: int = 1500) -> str:
    """
    Call the LLM using the existing ai_service infrastructure.
    Falls back gracefully if API is unavailable.
    """
    try:
        from core.ai_service import _call_llm as base_call
        return base_call(system_prompt, user_prompt, tier=tier,
                         temperature=temperature, max_tokens=max_tokens)
    except Exception as e:
        logger.error(f"RDTI AI service call failed: {e}")
        raise


# ---------------------------------------------------------------------------
# Field-specific drafting prompts
# ---------------------------------------------------------------------------

FIELD_PROMPTS = {
    "description": {
        "label": "Description of Core R&D Activity",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application for an Australian company.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "Description of Core R&D Activity" field.

This field should:
- Summarise the technical nature of the activity in 3–5 paragraphs
- Establish the technical domain and context
- Identify the specific technical problem being investigated
- NOT read as a project plan or business objective
- Use third person ("The Company") throughout
- Be between 800 and 4,000 characters

Do NOT include the hypothesis, experiment, or conclusions — those are separate fields.
Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Description of Core R&D Activity field based on this intake information:

Project: {project_title}
Business problem: {business_problem}
Existing knowledge at start: {existing_knowledge}
Technical uncertainty: {uncertainty}
Activity title: {activity_title}
Technical question: {technical_question}
Prior search conducted: {prior_search}
Why outcome was unpredictable: {why_unpredictable}

Write the Description field (800–4,000 characters):"""
    },

    "outcome_not_known_in_advance": {
        "label": "How Outcome Could Not Be Known in Advance",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "How the company determined the outcome could not be known in advance" field (sources investigated narrative).

This field should:
- Describe the systematic search conducted BEFORE commencing the activity
- List specific sources investigated (literature, patents, experts, prior work)
- Explain what was found and why it was insufficient to determine the outcome
- Demonstrate that the company genuinely investigated existing knowledge
- Be between 600 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the "outcome could not be known in advance" field based on:

Activity title: {activity_title}
Technical question: {technical_question}
Prior search conducted: {prior_search}
Sources investigated: {sources_investigated}
Why outcome was unpredictable: {why_unpredictable}
Who could have known: {who_could_have_known}

Write the Sources Investigated narrative (600–4,000 characters):"""
    },

    "competent_professional": {
        "label": "Why a Competent Professional Could Not Have Known",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "Why a competent professional could not have known or determined the outcome" field.

This field should:
- Identify the specific technical conditions that created genuine uncertainty
- Explain why even an expert in the field could not have predicted the outcome
- Reference the specific context, constraints, or interdependencies unique to this activity
- NOT be generic (must be specific to this activity)
- Use the competent professional framing from the style guide
- Be between 600 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the "competent professional" field based on:

Activity title: {activity_title}
Technical question: {technical_question}
Why outcome was unpredictable: {why_unpredictable}
Who in the industry could have known: {who_could_have_known}
Prior search findings: {prior_search}

Write the Competent Professional narrative (600–4,000 characters):"""
    },

    "hypothesis": {
        "label": "Hypothesis",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "Hypothesis" field.

This field should:
- State an overarching hypothesis using the template: "Building on [foundational work], the hypothesis for this activity is that [specific testable claim]."
- Include 2–4 specific sub-hypotheses (a), (b), (c)... each with a measurable outcome
- Each sub-hypothesis must be testable and falsifiable
- NOT read as a project objective or goal statement
- NOT use language like "we will develop" or "we aim to"
- Be between 600 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Hypothesis field based on:

Activity title: {activity_title}
Technical question: {technical_question}
Client's raw hypothesis: {hypothesis_raw}
Experiments run: {experiments_run}
Measurement approach: {measurement}

Write the Hypothesis (600–4,000 characters):"""
    },

    "experiment": {
        "label": "Experiment",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "Experiment (and how it tested the hypothesis)" field.

This field should:
- Describe the systematic experimental work conducted
- Show how each experiment directly tested the stated hypothesis and sub-hypotheses
- Demonstrate observation-evaluation-conclusion cycle structure
- Include iteration cycles and how results informed subsequent experiments
- NOT read as "we built it and it worked"
- Be between 600 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Experiment field based on:

Activity title: {activity_title}
Hypothesis (already drafted): {hypothesis}
Experiments run: {experiments_run}
Measurement approach: {measurement}
Learnings: {learnings}

Write the Experiment narrative (600–4,000 characters):"""
    },

    "evaluation_method": {
        "label": "Evaluation Method",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "Evaluation Method" field.

This field should:
- Describe specific metrics and criteria used to evaluate experimental outcomes
- Include quantitative measures where possible (accuracy rates, performance benchmarks, etc.)
- Show how evaluation criteria map to the sub-hypotheses
- Reference baseline comparisons
- Be between 400 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Evaluation Method field based on:

Activity title: {activity_title}
Hypothesis (already drafted): {hypothesis}
Measurement approach: {measurement}
Experiments run: {experiments_run}

Write the Evaluation Method narrative (400–4,000 characters):"""
    },

    "conclusions": {
        "label": "Conclusions",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "Conclusions" field.

This field should:
- State what was learned from the experimental work
- MUST include what did NOT work (failed experiments, rejected hypotheses)
- Connect conclusions back to the hypothesis
- Use the conclusion framing: "The experiment demonstrated that it is possible to [outcome]. However, [limitation]..."
- Indicate ongoing or future work where appropriate
- Be between 400 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Conclusions field based on:

Activity title: {activity_title}
Hypothesis (already drafted): {hypothesis}
Experiments run: {experiments_run}
Learnings (including what didn't work): {learnings}
Measurement approach: {measurement}

Write the Conclusions narrative (400–4,000 characters):"""
    },

    "new_knowledge": {
        "label": "New Knowledge Produced",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "New Knowledge Produced" field.

This field should:
- Describe knowledge generated at the DOMAIN level, not the project level
- NOT describe the product or system built ("we now have a platform")
- NOT describe knowledge that was merely new to the company
- Use the framing: "This finding could not have been derived from existing literature, as it reflects..."
- Be between 400 and 4,000 characters

CRITICAL: This is the most commonly failed field. The knowledge must advance the field, not merely be new to the applicant.

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the New Knowledge Produced field based on:

Activity title: {activity_title}
Technical question: {technical_question}
Conclusions (already drafted): {conclusions}
Learnings: {learnings}
Project domain: {project_title}

Write the New Knowledge narrative (400–4,000 characters):"""
    },

    # Project-level fields
    "objectives": {
        "label": "Project Objectives",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{AUSIND_GUIDE_EXCERPT}

{STYLE_GUIDE}

Your task: Draft the "Project Objectives" field (project level, not activity level).

This field should:
- State the overarching research objectives of the project
- Be framed as research objectives, not business goals
- Cover the full scope of the project across all activities
- Be between 400 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Project Objectives field based on:

Project title: {project_title}
Business problem: {business_problem}
Uncertainty: {uncertainty}
Activities in this project: {activity_titles}

Write the Project Objectives (400–4,000 characters):"""
    },

    "documents_kept": {
        "label": "Documents Kept",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{STYLE_GUIDE}

Your task: Draft the "Documents Kept" field (project level).

This field should:
- List and describe the contemporaneous records kept to demonstrate R&D activities
- Cover all evidence types relevant to the project
- Be specific about the types of records and how they evidence the R&D
- Be between 300 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Documents Kept field based on:

Project title: {project_title}
Evidence types kept: {evidence_kept}
Records description: {records_kept}

Write the Documents Kept narrative (300–4,000 characters):"""
    },

    "beneficiary_description": {
        "label": "Beneficiary Description",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{STYLE_GUIDE}

Your task: Draft the "Beneficiary Description" field (project level).

This field must address three elements:
1. IP ownership — the R&D entity owns the IP arising from the activities
2. Control — the R&D entity controls the R&D activities
3. Financial burden — the R&D entity bears the financial burden of the activities

Be specific and factual. Be between 300 and 4,000 characters.

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Beneficiary Description field based on:

Company name: {company_name}
IP owned by entity: {ip_owned}
Entity controls activities: {entity_controls}
Entity bears financial burden: {financial_burden}
Project title: {project_title}

Write the Beneficiary Description (300–4,000 characters):"""
    },

    # Supporting activity fields
    "supporting_description": {
        "label": "Description of Supporting Activity",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{STYLE_GUIDE}

Your task: Draft the "Description of Supporting Activity" field.

This field should:
- Describe the nature of the supporting activity
- Be clearly distinct from the core activity it supports
- Be between 200 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Supporting Activity Description based on:

Supporting activity title: {activity_title}
Core activity it supports: {core_activity_title}
Description: {intake_description}

Write the Supporting Activity Description (200–4,000 characters):"""
    },

    "direct_relation": {
        "label": "Direct Relation to Core Activity",
        "system": f"""You are an expert R&D Tax Incentive (RDTI) consultant drafting an AusIndustry registration application.

{STYLE_GUIDE}

Your task: Draft the "Direct Relation to Core Activity" field for a supporting activity.

This field should:
- Explain specifically how this supporting activity directly enables or supports the core activity
- Use language like "directly enables," "is an integral component of," "provides the necessary..."
- Be between 200 and 4,000 characters

Output ONLY the narrative text. No headings, no preamble.""",
        "user_template": """Draft the Direct Relation to Core Activity field based on:

Supporting activity: {activity_title}
Core activity: {core_activity_title}
Relation description: {intake_relation}

Write the Direct Relation narrative (200–4,000 characters):"""
    },
}


# ---------------------------------------------------------------------------
# Main drafting function
# ---------------------------------------------------------------------------

def draft_field(field_name: str, context: dict) -> dict:
    """
    Draft a single narrative field using the appropriate prompt.

    Args:
        field_name: One of the keys in FIELD_PROMPTS
        context: Dict of intake data to populate the user template

    Returns:
        dict with keys: content, char_count, over_limit, prompt_version
    """
    if field_name not in FIELD_PROMPTS:
        raise ValueError(f"Unknown field: {field_name}")

    prompt_config = FIELD_PROMPTS[field_name]
    system_prompt = prompt_config["system"]

    # Build user prompt from template, filling in available context
    user_template = prompt_config["user_template"]
    # Replace template placeholders with context values (default to empty string)
    user_prompt = user_template
    for key, value in context.items():
        placeholder = "{" + key + "}"
        user_prompt = user_prompt.replace(placeholder, str(value) if value else "Not provided")

    # Replace any remaining unfilled placeholders
    user_prompt = re.sub(r'\{[a-z_]+\}', 'Not provided', user_prompt)

    try:
        content = _call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tier="sonnet",
            temperature=0.4,
            max_tokens=1200,
        )
        content = content.strip()

        # Enforce character limit
        if len(content) > CHAR_LIMIT:
            content = _compress_to_limit(content, field_name)

        return {
            "content": content,
            "char_count": len(content),
            "over_limit": len(content) > CHAR_LIMIT,
            "prompt_version": PROMPT_VERSION,
            "error": None,
        }
    except Exception as e:
        logger.error(f"Draft field '{field_name}' failed: {e}")
        return {
            "content": "",
            "char_count": 0,
            "over_limit": False,
            "prompt_version": PROMPT_VERSION,
            "error": str(e),
        }


def _compress_to_limit(content: str, field_name: str) -> str:
    """
    Second-pass compression to bring content within 4,000 characters.
    Preserves hypothesis-experiment-evaluation-conclusion structure.
    """
    compress_prompt = f"""The following R&D Tax Incentive narrative for the "{field_name}" field is too long.
Compress it to under 4,000 characters while preserving all key technical arguments, 
the hypothesis-experiment-evaluation-conclusion structure, and the formal register.
Do NOT add new content. Do NOT truncate abruptly.

Original ({len(content)} characters):
{content}

Compressed version (must be under 4,000 characters):"""

    try:
        compressed = _call_llm(
            system_prompt="You are compressing R&D Tax Incentive application text. Preserve all key arguments.",
            user_prompt=compress_prompt,
            tier="sonnet",
            temperature=0.2,
            max_tokens=1000,
        )
        return compressed.strip()
    except Exception as e:
        logger.error(f"Compression failed for {field_name}: {e}")
        # Hard truncate as last resort (not ideal but prevents data loss)
        return content[:3950] + "... [truncated — please review]"


# ---------------------------------------------------------------------------
# Validator layer
# ---------------------------------------------------------------------------

def validate_field(field_name: str, content: str, context: dict = None) -> list:
    """
    Run validators on a single field.
    Returns list of flag dicts: {severity, flag_type, message, suggestion}
    """
    flags = []

    if not content or not content.strip():
        flags.append({
            "severity": "red",
            "flag_type": "missing_mandatory",
            "message": f"The '{field_name}' field is empty.",
            "suggestion": "Use the AI drafter to generate content for this field.",
        })
        return flags

    # Character limit check
    if len(content) > CHAR_LIMIT:
        flags.append({
            "severity": "red",
            "flag_type": "over_char_limit",
            "message": f"Field exceeds 4,000 character limit ({len(content)} characters).",
            "suggestion": "Use the compress function to reduce the length while preserving key arguments.",
        })

    # Field-specific AI validation
    validator_flags = _run_ai_validator(field_name, content, context or {})
    flags.extend(validator_flags)

    return flags


def _run_ai_validator(field_name: str, content: str, context: dict) -> list:
    """
    Run the AI validator for a specific field using Haiku (cheap, fast).
    Returns list of flag dicts.
    """
    validator_prompts = {
        "hypothesis": """Analyse this R&D Tax Incentive hypothesis field and identify compliance issues.

Check for these RED issues (return as "RED: <issue>"):
1. Reads as an objective rather than a testable hypothesis (e.g. "We will develop X" or "The aim is to...")
2. Has no measurable outcome (no specific metrics, thresholds, or falsifiable claims)
3. Experiment doesn't reference the hypothesis (if experiment text is provided)

Check for these AMBER issues (return as "AMBER: <issue>"):
4. Single sentence with no sub-hypotheses (complex activities need sub-hypotheses)
5. Sub-hypotheses lack specific measurable outcomes

If no issues found, return "GREEN: Hypothesis meets quality threshold"

Hypothesis text:
{content}

Return ONLY the flag lines (e.g. "RED: Reads as objective — 'The Company will develop...'")""",

        "new_knowledge": """Analyse this R&D Tax Incentive "New Knowledge Produced" field and identify compliance issues.

Check for these RED issues (return as "RED: <issue>"):
1. Describes "new to us" rather than "new to the field" (e.g. "The Company now has..." or "We learned that...")
2. Reads as project completion rather than domain knowledge (e.g. "The Company now has a working platform")
3. Doesn't identify domain-level knowledge generated

Check for these AMBER issues (return as "AMBER: <issue>"):
4. New knowledge claim is vague and not specific to a domain

If no issues found, return "GREEN: New knowledge field meets quality threshold"

New Knowledge text:
{content}

Return ONLY the flag lines.""",

        "competent_professional": """Analyse this R&D Tax Incentive "Competent Professional" field and identify compliance issues.

Check for these AMBER issues (return as "AMBER: <issue>"):
1. Generic — could apply to any project (lacks specific technical conditions)
2. Doesn't specify the technical conditions creating uncertainty
3. Doesn't reference the specific clinical/technical/environmental context

If no issues found, return "GREEN: Competent professional field meets quality threshold"

Competent Professional text:
{content}

Return ONLY the flag lines.""",

        "conclusions": """Analyse this R&D Tax Incentive "Conclusions" field and identify compliance issues.

Check for these AMBER issues (return as "AMBER: <issue>"):
1. Doesn't mention what didn't work (failed experiments, rejected approaches)
2. Reads as pure success with no limitations or residual uncertainty

If no issues found, return "GREEN: Conclusions field meets quality threshold"

Conclusions text:
{content}

Return ONLY the flag lines.""",

        "evaluation_method": """Analyse this R&D Tax Incentive "Evaluation Method" field and identify compliance issues.

Check for these AMBER issues (return as "AMBER: <issue>"):
1. Lacks quantitative criteria (no specific metrics, thresholds, or benchmarks)
2. Evaluation criteria don't connect to the stated hypothesis

If no issues found, return "GREEN: Evaluation method meets quality threshold"

Evaluation Method text:
{content}

Return ONLY the flag lines.""",

        "description": """Analyse this R&D Tax Incentive "Description of Core R&D Activity" field and identify compliance issues.

Check for these RED issues (return as "RED: <issue>"):
1. Contains excluded-activity language (market research, management studies, commercial production, compliance activities)

Check for these AMBER issues (return as "AMBER: <issue>"):
2. Reads as a project plan or business objective rather than a technical description
3. Uses marketing language ("innovative", "cutting-edge", "world-first")

If no issues found, return "GREEN: Description meets quality threshold"

Description text:
{content}

Return ONLY the flag lines.""",
    }

    if field_name not in validator_prompts:
        # No specific validator for this field — return green
        return [{
            "severity": "green",
            "flag_type": "green",
            "message": f"Field '{field_name}' meets quality threshold.",
            "suggestion": "",
        }]

    prompt = validator_prompts[field_name].replace("{content}", content[:3000])

    try:
        response = _call_llm(
            system_prompt="You are a compliance validator for Australian R&D Tax Incentive applications. Be precise and specific.",
            user_prompt=prompt,
            tier="haiku",
            temperature=0.1,
            max_tokens=500,
        )

        return _parse_validator_response(response, field_name)
    except Exception as e:
        logger.error(f"Validator failed for {field_name}: {e}")
        return []


def _parse_validator_response(response: str, field_name: str) -> list:
    """Parse the validator response into structured flag dicts."""
    flags = []
    lines = response.strip().split('\n')

    severity_map = {
        "RED": "red",
        "AMBER": "amber",
        "GREEN": "green",
    }

    flag_type_map = {
        "hypothesis": {
            "objective": "hypothesis_objective",
            "measurable": "hypothesis_no_measurable",
            "sub-hypothes": "hypothesis_no_substructure",
        },
        "new_knowledge": {
            "new to us": "new_knowledge_project",
            "project completion": "new_knowledge_project",
            "domain": "new_knowledge_project",
        },
        "competent_professional": {
            "generic": "competent_prof_generic",
            "specific": "competent_prof_generic",
        },
        "conclusions": {
            "didn't work": "conclusions_no_failures",
            "failure": "conclusions_no_failures",
        },
        "evaluation_method": {
            "quantitative": "evaluation_no_quantitative",
        },
        "description": {
            "excluded": "excluded_category",
            "marketing": "excluded_category",
        },
    }

    for line in lines:
        line = line.strip()
        if not line:
            continue

        for prefix, severity in severity_map.items():
            if line.upper().startswith(prefix + ":"):
                message = line[len(prefix) + 1:].strip()

                # Determine flag type
                flag_type = "cross_field_inconsistency"
                if field_name in flag_type_map:
                    for keyword, ft in flag_type_map[field_name].items():
                        if keyword.lower() in message.lower():
                            flag_type = ft
                            break

                if severity == "green":
                    flag_type = "green"

                flags.append({
                    "severity": severity,
                    "flag_type": flag_type,
                    "message": message,
                    "suggestion": "",
                })
                break

    if not flags:
        # Default to green if no flags parsed
        flags.append({
            "severity": "green",
            "flag_type": "green",
            "message": f"Field '{field_name}' meets quality threshold.",
            "suggestion": "",
        })

    return flags


# ---------------------------------------------------------------------------
# Cross-field consistency checker
# ---------------------------------------------------------------------------

def check_cross_field_consistency(core_activity) -> list:
    """
    Check consistency across all 8 narrative fields of a Core Activity.
    Uses Sonnet for thorough analysis.
    Returns list of flag dicts.
    """
    if not all([
        core_activity.hypothesis,
        core_activity.experiment,
        core_activity.evaluation_method,
        core_activity.conclusions,
    ]):
        return []  # Not enough fields drafted to check consistency

    prompt = f"""You are reviewing an R&D Tax Incentive application for cross-field consistency.

Check these four fields for logical consistency:

HYPOTHESIS:
{core_activity.hypothesis[:1000]}

EXPERIMENT:
{core_activity.experiment[:1000]}

EVALUATION METHOD:
{core_activity.evaluation_method[:800]}

CONCLUSIONS:
{core_activity.conclusions[:800]}

Identify any AMBER inconsistencies (return as "AMBER: <specific inconsistency>"):
1. Experiment doesn't test the stated hypothesis
2. Evaluation criteria don't match the experiment described
3. Conclusions don't connect back to the hypothesis
4. New knowledge doesn't align with conclusions

If all fields are consistent, return "GREEN: Cross-field consistency check passed"

Return ONLY the flag lines."""

    try:
        response = _call_llm(
            system_prompt="You are a compliance reviewer for Australian R&D Tax Incentive applications.",
            user_prompt=prompt,
            tier="sonnet",
            temperature=0.1,
            max_tokens=400,
        )
        flags = _parse_validator_response(response, "cross_field")
        # Override flag_type for cross-field flags
        for flag in flags:
            if flag["severity"] != "green":
                flag["flag_type"] = "cross_field_inconsistency"
        return flags
    except Exception as e:
        logger.error(f"Cross-field consistency check failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Bulk draft all fields for a Core Activity
# ---------------------------------------------------------------------------

def draft_all_core_activity_fields(core_activity, project, application) -> dict:
    """
    Draft all 8 narrative fields for a Core Activity in sequence.
    Returns dict of field_name -> draft result.
    """
    results = {}

    # Base context from intake
    base_context = {
        "project_title": project.project_title,
        "business_problem": project.intake_business_problem,
        "existing_knowledge": project.intake_existing_knowledge,
        "uncertainty": project.intake_uncertainty,
        "who_could_have_known": project.intake_who_could_have_known,
        "activity_title": core_activity.activity_title,
        "technical_question": core_activity.intake_technical_question,
        "prior_search": core_activity.intake_prior_search,
        "why_unpredictable": core_activity.intake_why_unpredictable,
        "hypothesis_raw": core_activity.intake_hypothesis_raw,
        "experiments_run": core_activity.intake_experiments_run,
        "measurement": core_activity.intake_measurement,
        "learnings": core_activity.intake_learnings,
        "records_kept": core_activity.intake_records_kept,
        "sources_investigated": ", ".join(core_activity.sources_investigated) if core_activity.sources_investigated else "",
        "evidence_kept": ", ".join(core_activity.evidence_kept) if core_activity.evidence_kept else "",
    }

    # Draft in order (later fields can reference earlier ones)
    field_order = [
        "description",
        "outcome_not_known_in_advance",
        "competent_professional",
        "hypothesis",
        "experiment",
        "evaluation_method",
        "conclusions",
        "new_knowledge",
    ]

    for field_name in field_order:
        context = base_context.copy()

        # Add previously drafted fields as context for later fields
        if field_name == "experiment" and results.get("hypothesis"):
            context["hypothesis"] = results["hypothesis"].get("content", "")
        if field_name == "evaluation_method" and results.get("hypothesis"):
            context["hypothesis"] = results["hypothesis"].get("content", "")
        if field_name == "conclusions":
            context["hypothesis"] = results.get("hypothesis", {}).get("content", "")
        if field_name == "new_knowledge":
            context["conclusions"] = results.get("conclusions", {}).get("content", "")

        result = draft_field(field_name, context)
        results[field_name] = result

        # Save to model if successful
        if result["content"] and not result["error"]:
            setattr(core_activity, field_name, result["content"])

    core_activity.save()
    return results


def draft_project_fields(project, application) -> dict:
    """Draft all project-level narrative fields."""
    results = {}

    activity_titles = ", ".join([
        ca.activity_title for ca in project.core_activities.all()
    ])

    base_context = {
        "project_title": project.project_title,
        "business_problem": project.intake_business_problem,
        "uncertainty": project.intake_uncertainty,
        "activity_titles": activity_titles,
        "company_name": application.company_name or application.financial_year.entity.entity_name,
        "ip_owned": "Yes" if application.ip_owned_by_entity else "Not confirmed",
        "entity_controls": "Yes" if application.entity_controls_activities else "Not confirmed",
        "financial_burden": "Yes" if application.entity_bears_financial_burden else "Not confirmed",
        "evidence_kept": "",
        "records_kept": "",
    }

    for field_name in ["objectives", "documents_kept", "beneficiary_description"]:
        result = draft_field(field_name, base_context)
        results[field_name] = result
        if result["content"] and not result["error"]:
            setattr(project, field_name, result["content"])

    project.save()
    return results
