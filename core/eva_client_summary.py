"""Eva Client Summary — auto-generates a 5-section client summary when a FY is locked."""
import json
import logging

from core.ai_service import _call_llm
from core.models import (
    EvaClientSummary,
    EvaFinding,
    EvaReview,
    FinancialYear,
    TrialBalanceLine,
)

logger = logging.getLogger(__name__)


def generate_client_summary(financial_year_id, format_type="bullet"):
    """
    Generate a client summary for a locked financial year.
    Called automatically when a FY transitions to LOCKED status.

    Produces 5 sections:
    1. Financial Highlights
    2. Compliance Status
    3. Tax Position
    4. Recommendations
    5. Year-on-Year Comparison
    """
    fy = FinancialYear.objects.select_related("entity").get(pk=financial_year_id)
    entity = fy.entity

    # Build context for the LLM
    context = _build_summary_context(fy, entity)

    # Generate via Claude
    summary_text = _call_llm_for_summary(context, format_type, entity.entity_name, fy)

    if not summary_text:
        logger.error("Failed to generate client summary for FY %s", fy)
        return None

    # Parse sections from LLM output
    sections = _parse_sections(summary_text)

    # Determine version
    existing_count = EvaClientSummary.objects.filter(financial_year=fy).count()

    summary = EvaClientSummary.objects.create(
        financial_year=fy,
        format_type=format_type,
        financial_highlights=sections.get("financial_highlights", ""),
        compliance_status=sections.get("compliance_status", ""),
        tax_position=sections.get("tax_position", ""),
        recommendations=sections.get("recommendations", ""),
        year_on_year_comparison=sections.get("year_on_year_comparison", ""),
        full_content=summary_text,
        version=existing_count + 1,
        model_used="claude",
    )

    logger.info("Generated client summary v%d for %s", summary.version, fy)
    return summary


def _build_summary_context(fy, entity):
    """Build a comprehensive context dict for the summary LLM call."""
    context = {
        "entity_name": entity.entity_name,
        "entity_type": entity.entity_type,
        "abn": entity.abn or "N/A",
        "financial_year": f"{fy.start_date} to {fy.end_date}",
    }

    # Trial Balance data
    tb_lines = TrialBalanceLine.objects.filter(financial_year=fy).select_related("mapped_line_item")
    revenue_total = 0
    expense_total = 0
    asset_total = 0
    liability_total = 0
    equity_total = 0

    for line in tb_lines:
        balance = float(line.closing_balance or 0)
        mapping = line.mapped_line_item
        if not mapping:
            continue
        section = (mapping.statement_section or "").lower()

        if "revenue" in section or "income" in section:
            revenue_total += balance
        elif "expense" in section or "cost of sales" in section:
            expense_total += balance
        elif "asset" in section:
            asset_total += balance
        elif "liabilit" in section:
            liability_total += balance
        elif "equity" in section:
            equity_total += balance

    context["financials"] = {
        "total_revenue": revenue_total,
        "total_expenses": expense_total,
        "net_profit": revenue_total - expense_total,
        "total_assets": asset_total,
        "total_liabilities": liability_total,
        "net_assets": asset_total - liability_total,
        "equity": equity_total,
    }

    # Prior year comparison
    prior_fy = FinancialYear.objects.filter(
        entity=entity,
        end_date__lt=fy.start_date,
    ).order_by("-end_date").first()

    if prior_fy:
        prior_lines = TrialBalanceLine.objects.filter(
            financial_year=prior_fy,
        ).select_related("mapped_line_item")
        prior_revenue = sum(
            float(l.closing_balance or 0)
            for l in prior_lines
            if l.mapped_line_item and any(
                kw in (l.mapped_line_item.statement_section or "").lower()
                for kw in ("revenue", "income")
            )
        )
        prior_expense = sum(
            float(l.closing_balance or 0)
            for l in prior_lines
            if l.mapped_line_item and any(
                kw in (l.mapped_line_item.statement_section or "").lower()
                for kw in ("expense", "cost of sales")
            )
        )
        context["prior_year"] = {
            "label": f"{prior_fy.start_date} to {prior_fy.end_date}",
            "total_revenue": prior_revenue,
            "total_expenses": prior_expense,
            "net_profit": prior_revenue - prior_expense,
        }

    # Eva findings
    review = EvaReview.objects.filter(financial_year=fy).order_by("-triggered_at").first()
    if review:
        findings = EvaFinding.objects.for_domain('financial_statements').filter(eva_review=review)
        context["eva_findings"] = {
            "total": findings.count(),
            "critical": findings.filter(severity="critical").count(),
            "advisory": findings.filter(severity="advisory").count(),
            "addressed": findings.filter(status="addressed").count(),
            "open_items": [
                {"check": f.check_name, "severity": f.severity, "summary": f.plain_english_explanation[:200]}
                for f in findings.filter(status="open")[:10]
            ],
        }

    return context


def _call_llm_for_summary(context, format_type, entity_name, fy):
    """Call Claude to generate the client summary via the tier router."""
    try:
        format_instruction = (
            "Use bullet points for each item within each section."
            if format_type == "bullet"
            else "Write in flowing narrative paragraphs for each section."
        )

        system_prompt = (
            "You are Eva, the AI assistant for MC & S Chartered Accountants. "
            "You are generating a client summary for a financial year that has just been finalised. "
            "The summary will be included in the client's year-end package. "
            "Be professional, concise, and factual. Use Australian accounting terminology. "
            "Do NOT include any disclaimers or caveats — those are handled separately."
        )

        user_prompt = f"""Generate a client summary for {entity_name} for the financial year {fy.start_date} to {fy.end_date}.

{format_instruction}

The summary MUST contain exactly these 5 sections with these exact headings:

## Financial Highlights
Key financial metrics, revenue, expenses, profit/loss, significant changes.

## Compliance Status
Status of all compliance obligations, Eva review findings, any outstanding items.

## Tax Position
Tax obligations, estimated tax payable, any tax planning considerations.

## Recommendations
Actionable recommendations for the client based on the financial data and findings.

## Year-on-Year Comparison
Comparison with prior year (if available), trends, significant variances.

Here is the financial data:
{json.dumps(context, indent=2, default=str)}
"""

        return _call_llm(
            system_prompt,
            user_prompt,
            tier="sonnet",
            temperature=0.3,
            max_tokens=4000,
        )

    except Exception as e:
        logger.exception("LLM call failed for client summary: %s", e)
        return None


def _parse_sections(text):
    """Parse the 5 sections from the LLM output."""
    sections = {}
    current_section = None
    current_content = []

    section_map = {
        "financial highlights": "financial_highlights",
        "compliance status": "compliance_status",
        "tax position": "tax_position",
        "recommendations": "recommendations",
        "year-on-year comparison": "year_on_year_comparison",
        "year on year comparison": "year_on_year_comparison",
    }

    for line in text.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        lower = stripped.lower()

        matched = False
        for heading, key in section_map.items():
            if heading in lower:
                if current_section and current_content:
                    sections[current_section] = "\n".join(current_content).strip()
                current_section = key
                current_content = []
                matched = True
                break

        if not matched and current_section is not None:
            current_content.append(line)

    # Save the last section
    if current_section and current_content:
        sections[current_section] = "\n".join(current_content).strip()

    return sections
