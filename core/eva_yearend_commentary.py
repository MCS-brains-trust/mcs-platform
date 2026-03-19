"""
eva_yearend_commentary.py
=========================
AI-powered year-end client commentary generator for the Package Assemble stage.

Analyses the full-year trial balance (current year + prior-year comparatives)
and produces a five-section client-ready narrative:

  1. Year Snapshot          — headline figures, revenue, profit/loss
  2. Revenue & Income       — income stream breakdown, significant movements
  3. Expense & Margin       — cost structure, gross/net margin trends
  4. Key Observations       — items to watch (max 4)
  5. Recommended Actions    — 1-3 specific actions for the client

The commentary is stored in YearEndCommentary (OneToOne on FinancialYear).
Calling generate_yearend_commentary() is idempotent — it upserts the record.
"""

import json
import logging
import os
from datetime import datetime, timezone as dt_timezone

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_yearend_commentary(financial_year_id, generated_by_id=None, tone="professional"):
    """
    Generate (or regenerate) the year-end commentary for a financial year.

    Returns the YearEndCommentary instance.
    Raises ValueError if the financial year does not exist.
    """
    from core.models import FinancialYear, YearEndCommentary

    try:
        fy = FinancialYear.objects.select_related("entity").get(pk=financial_year_id)
    except FinancialYear.DoesNotExist:
        raise ValueError(f"FinancialYear {financial_year_id} not found")

    # Upsert the commentary record
    commentary, created = YearEndCommentary.objects.get_or_create(
        financial_year=fy,
        defaults={
            "status": YearEndCommentary.Status.GENERATING,
            "tone": tone,
        },
    )
    if not created:
        commentary.status = YearEndCommentary.Status.GENERATING
        commentary.tone = tone
        commentary.version += 1
        commentary.error_message = ""
        commentary.section_snapshot = ""
        commentary.section_revenue = ""
        commentary.section_costs = ""
        commentary.section_watch_items = ""
        commentary.section_actions = ""
        commentary.full_content = ""

    if generated_by_id:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            commentary.generated_by = User.objects.get(pk=generated_by_id)
        except User.DoesNotExist:
            pass

    commentary.generation_started_at = datetime.now(dt_timezone.utc)
    commentary.generation_step = "Building financial context..."
    commentary.save()

    try:
        # Build context from trial balance
        context = _build_financial_context(fy)
        commentary.context_snapshot = context
        commentary.generation_step = "Calling AI model..."
        commentary.save(update_fields=["context_snapshot", "generation_step"])

        # Call LLM
        raw_text = _call_llm(context, tone, fy.entity.entity_name, fy)
        if not raw_text:
            raise RuntimeError("LLM returned empty response")

        # Parse sections
        sections = _parse_sections(raw_text)
        commentary.section_snapshot = sections.get("snapshot", "")
        commentary.section_revenue = sections.get("revenue", "")
        commentary.section_costs = sections.get("costs", "")
        commentary.section_watch_items = sections.get("watch_items", "")
        commentary.section_actions = sections.get("actions", "")
        commentary.full_content = raw_text
        commentary.status = YearEndCommentary.Status.DRAFT
        commentary.model_used = "gpt-4.1-mini"
        commentary.generation_completed_at = datetime.now(dt_timezone.utc)
        commentary.generation_step = ""
        commentary.save()

    except Exception as exc:
        logger.exception("Year-end commentary generation failed for FY %s: %s", financial_year_id, exc)
        commentary.status = YearEndCommentary.Status.ERROR
        commentary.error_message = str(exc)
        commentary.generation_completed_at = datetime.now(dt_timezone.utc)
        commentary.generation_step = ""
        commentary.save()

    return commentary


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_financial_context(fy):
    """
    Build a JSON-serialisable dict of financial data from the trial balance.
    Includes current year and prior-year comparatives.
    """
    from core.models import TrialBalanceLine

    lines = TrialBalanceLine.objects.filter(
        financial_year=fy,
    ).select_related("mapped_line_item").order_by("account_code")

    # Categorise lines
    income_lines = []
    expense_lines = []
    asset_lines = []
    liability_lines = []
    equity_lines = []

    for line in lines:
        cy = float(line.closing_balance or 0)
        py = float((line.prior_debit or 0) - (line.prior_credit or 0))
        entry = {
            "account": line.account_name,
            "code": line.account_code,
            "cy": round(cy, 2),
            "py": round(py, 2),
        }
        try:
            code_num = int(str(line.account_code).split(".")[0])
        except (ValueError, TypeError):
            code_num = 9999

        if code_num < 1200:
            income_lines.append(entry)
        elif code_num < 2000:
            expense_lines.append(entry)
        elif code_num < 3500:
            asset_lines.append(entry)
        elif code_num < 4000:
            liability_lines.append(entry)
        elif code_num < 5000:
            equity_lines.append(entry)

    # Aggregate totals
    total_income_cy = sum(e["cy"] for e in income_lines)
    total_income_py = sum(e["py"] for e in income_lines)
    total_expenses_cy = sum(e["cy"] for e in expense_lines)
    total_expenses_py = sum(e["py"] for e in expense_lines)
    net_profit_cy = total_income_cy - total_expenses_cy
    net_profit_py = total_income_py - total_expenses_py
    total_assets_cy = sum(e["cy"] for e in asset_lines)
    total_liabilities_cy = sum(e["cy"] for e in liability_lines)

    # Top income and expense lines for detail
    top_income = sorted(income_lines, key=lambda x: abs(x["cy"]), reverse=True)[:8]
    top_expenses = sorted(expense_lines, key=lambda x: abs(x["cy"]), reverse=True)[:10]

    # Gross margin (if trading income vs COGS discernible)
    gross_margin_pct = None
    if total_income_cy != 0:
        gross_margin_pct = round((net_profit_cy / total_income_cy) * 100, 1)

    # Year-on-year variances
    income_var = round(total_income_cy - total_income_py, 2)
    income_var_pct = round((income_var / total_income_py * 100), 1) if total_income_py else None
    profit_var = round(net_profit_cy - net_profit_py, 2)
    profit_var_pct = round((profit_var / abs(net_profit_py) * 100), 1) if net_profit_py else None

    return {
        "entity_name": fy.entity.entity_name,
        "entity_type": fy.entity.entity_type,
        "abn": fy.entity.abn or "",
        "financial_year": {
            "label": str(fy),
            "start": str(fy.start_date),
            "end": str(fy.end_date),
        },
        "summary": {
            "total_income_cy": round(total_income_cy, 2),
            "total_income_py": round(total_income_py, 2),
            "total_expenses_cy": round(total_expenses_cy, 2),
            "total_expenses_py": round(total_expenses_py, 2),
            "net_profit_cy": round(net_profit_cy, 2),
            "net_profit_py": round(net_profit_py, 2),
            "total_assets_cy": round(total_assets_cy, 2),
            "total_liabilities_cy": round(total_liabilities_cy, 2),
            "net_assets_cy": round(total_assets_cy - total_liabilities_cy, 2),
            "gross_margin_pct": gross_margin_pct,
            "income_variance": income_var,
            "income_variance_pct": income_var_pct,
            "profit_variance": profit_var,
            "profit_variance_pct": profit_var_pct,
        },
        "top_income_lines": top_income,
        "top_expense_lines": top_expenses,
        "has_prior_year": total_income_py != 0 or total_expenses_py != 0,
    }


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_llm(context, tone, entity_name, fy):
    """Call the LLM to generate the year-end commentary."""
    try:
        from openai import OpenAI
        client = OpenAI()  # uses OPENAI_API_KEY + base_url from environment

        tone_instruction = {
            "professional": (
                "Write in a professional, formal tone suitable for a client letter. "
                "Use complete paragraphs. Be factual and concise."
            ),
            "conversational": (
                "Write in a warm, conversational tone. Use plain English. "
                "Avoid jargon. Write as if speaking directly to the client."
            ),
            "technical": (
                "Write in a detailed technical tone. Include specific figures, "
                "percentages, and accounting terminology where appropriate."
            ),
        }.get(tone, "Write in a professional, formal tone.")

        system_prompt = (
            "You are Eva, the AI assistant for MC & S Chartered Accountants. "
            "You are generating a year-end client commentary based on trial balance data. "
            "This commentary will be included in the client's year-end package and reviewed by the accountant before sending. "
            "Be professional, accurate, and use Australian accounting terminology. "
            "Do NOT include disclaimers, caveats, or notes about limitations — those are handled separately. "
            "Do NOT fabricate figures — only use the data provided."
        )

        user_prompt = f"""Generate a year-end client commentary for {entity_name} for the financial year {fy.start_date} to {fy.end_date}.

{tone_instruction}

The commentary MUST contain exactly these 5 sections with these exact headings (use ## for each):

## Year Snapshot
2-3 sentences. Headline revenue, profit/loss, and the most significant story of the year. Reference specific dollar figures.

## Revenue & Income Analysis
Analyse the income streams. Highlight significant movements vs prior year (if available). Reference the top income lines by name and amount.

## Expense & Margin Analysis
Analyse the cost structure. Comment on the largest expense categories, gross margin (if applicable), and any notable cost movements vs prior year.

## Key Observations
Up to 4 bullet points. Flag items the client should be aware of — unusual balances, concentration risks, balance sheet items of note, or compliance considerations.

## Recommended Actions
1-3 specific, actionable recommendations for the client based on the financial data. Be concrete — name the action, why it matters, and when to act.

Here is the financial data:
{json.dumps(context, indent=2, default=str)}
"""

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=3000,
            temperature=0.4,
        )
        return response.choices[0].message.content

    except Exception as exc:
        logger.exception("LLM call failed for year-end commentary: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Section parser
# ---------------------------------------------------------------------------

def _parse_sections(text):
    """Parse the 5 sections from the LLM output into a dict."""
    sections = {}
    current_key = None
    current_lines = []

    heading_map = {
        "year snapshot": "snapshot",
        "revenue & income": "revenue",
        "revenue and income": "revenue",
        "expense & margin": "costs",
        "expense and margin": "costs",
        "key observations": "watch_items",
        "recommended actions": "actions",
    }

    for line in text.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        lower = stripped.lower()

        matched_key = None
        for heading, key in heading_map.items():
            if heading in lower:
                matched_key = key
                break

        if matched_key:
            if current_key and current_lines:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = matched_key
            current_lines = []
        elif current_key is not None:
            current_lines.append(line)

    if current_key and current_lines:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections
