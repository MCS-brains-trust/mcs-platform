"""
Eva Finalisation Gate — Structured Compliance Review Engine

This module handles:
1. Pre-flight checks before Eva review can be triggered
2. Risk engine pre-run (deterministic checks run FIRST)
3. The 8 compliance checks with entity-type filtering
4. LLM-powered analysis for each check, with risk engine findings
   injected as CONFIRMED HARD FACTS
5. Finding creation and resolution workflow
6. Status transitions (PREPARED → PENDING_EVA → EVA_CLEARED / FINDINGS_RAISED)

Architecture (v2.0 — KB v2 spec):
    1. Risk engine deterministic checks (always first)
    2. Knowledge Brain retrieval (mandatory per check)
    3. Prompt assembly (system prompt + KB chunks + financial context + hard facts)
    4. LLM reasons, explains, cites, adds context
    5. Present findings — confirmed (risk engine) separated from additional (LLM)
"""
import json
import logging
import re
import sys
import time
import traceback
import threading
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

# ---------------------------------------------------------------------------
# Loan Detection Keywords (shared with risk_engine.py)
# ---------------------------------------------------------------------------
# Defect 1 fix: expanded from just "loan" to match risk_engine._LOAN_KEYWORDS
LOAN_KEYWORDS = {
    "loan", "director", "shareholder", "associate",
    "advance", "current account", "drawings",
    "related party", "beneficiary",
}

# Related party transaction keywords
RELATED_PARTY_KEYWORDS = {
    "management fee", "consulting fee", "director fee",
    "related party", "intercompany", "inter-company",
    "loan", "advance", "distribution",
}

# ---------------------------------------------------------------------------
# Compliance Check Definitions
# ---------------------------------------------------------------------------
# Each check has: id, name, description, entity_types (which types it applies to),
# and the function that performs the analysis.

COMPLIANCE_CHECKS = [
    {
        "id": "div7a",
        "name": "Division 7A Loan Compliance",
        "description": "Check for potential Division 7A exposure from loans to shareholders/associates",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid"],
        "severity_default": "CRITICAL",
    },
    {
        "id": "gst_reconciliation",
        "name": "GST Reconciliation",
        "description": "Verify GST collected/paid reconciles with BAS lodgement figures",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "sole_trader", "partnership", "smsf"],
        "severity_default": "ADVISORY",
    },
    {
        "id": "related_party",
        "name": "Related Party Transactions",
        "description": "Identify and review related party transactions for arm's length pricing",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid", "partnership"],
        "severity_default": "ADVISORY",
    },
    {
        "id": "smsf_compliance",
        "name": "SMSF Compliance (SIS Act)",
        "description": "Check SMSF compliance with Superannuation Industry (Supervision) Act requirements",
        "entity_types": ["smsf"],
        "severity_default": "CRITICAL",
    },
    {
        "id": "trust_distribution",
        "name": "Trust Distribution Resolution",
        "description": "Verify trust distribution resolutions are in place before year end",
        "entity_types": ["trust_discretionary", "trust_unit", "trust_hybrid"],
        "severity_default": "CRITICAL",
    },
    {
        "id": "depreciation_review",
        "name": "Depreciation Schedule Review",
        "description": "Review depreciation calculations and asset register for accuracy",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "sole_trader", "partnership", "smsf"],
        "severity_default": "ADVISORY",
    },
    {
        "id": "tb_integrity",
        "name": "Trial Balance Integrity",
        "description": "Verify TB is balanced, no orphan accounts, all accounts mapped",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "sole_trader", "partnership", "smsf", "individual"],
        "severity_default": "CRITICAL",
    },
    {
        "id": "comparative_consistency",
        "name": "Comparative Period Consistency",
        "description": "Check that prior year comparatives match the finalised prior year figures",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "sole_trader", "partnership", "smsf"],
        "severity_default": "ADVISORY",
    },
    {
        "id": "super_guarantee",
        "name": "Superannuation Guarantee Compliance",
        "description": "Verify super guarantee obligations are met — correct rate applied, paid on time, all eligible employees covered",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "sole_trader", "partnership"],
        "severity_default": "CRITICAL",
    },
    {
        "id": "ato_benchmarks",
        "name": "ATO Industry Benchmarks",
        "description": "Compare key financial ratios against ATO small business benchmarks for the entity's industry",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "sole_trader", "partnership"],
        "severity_default": "ADVISORY",
    },
    {
        "id": "going_concern",
        "name": "Going Concern Assessment",
        "description": "Assess whether there are indicators the entity may not continue as a going concern within 12 months",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "partnership", "smsf"],
        "severity_default": "CRITICAL",
    },
    {
        "id": "tpar",
        "name": "Taxable Payments Annual Report (TPAR)",
        "description": "Check if entity is required to lodge TPAR and whether contractor payments are properly recorded",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "sole_trader", "partnership"],
        "severity_default": "ADVISORY",
    },
    {
        "id": "thin_capitalisation",
        "name": "Thin Capitalisation",
        "description": "Assess thin capitalisation rules for entities with foreign-controlled debt or international dealings",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "partnership"],
        "severity_default": "ADVISORY",
    },
]


# ---------------------------------------------------------------------------
# Pre-flight Checks
# ---------------------------------------------------------------------------
def run_preflight_checks(financial_year):
    """
    Run pre-flight checks before Eva review can be triggered.

    Returns:
        dict: {"passed": bool, "checks": [{"name": str, "passed": bool, "message": str}]}
    """
    fy = financial_year
    checks = []

    # Check 1: TB must have at least one balance
    tb_count = fy.trial_balance_lines.count()
    checks.append({
        "name": "Trial balance has data",
        "passed": tb_count > 0,
        "message": f"{tb_count} trial balance lines found." if tb_count > 0
                   else "No trial balance data. Import a trial balance first.",
    })

    # Check 2: TB must be balanced (DR == CR)
    from django.db.models import Sum
    totals = fy.trial_balance_lines.aggregate(
        total_dr=Sum("debit"),
        total_cr=Sum("credit"),
    )
    total_dr = totals["total_dr"] or ZERO
    total_cr = totals["total_cr"] or ZERO
    is_balanced = abs(total_dr - total_cr) < Decimal("0.02")
    checks.append({
        "name": "Trial balance is balanced",
        "passed": is_balanced,
        "message": f"DR: ${total_dr:,.2f} | CR: ${total_cr:,.2f}" if is_balanced
                   else f"TB is out of balance by ${abs(total_dr - total_cr):,.2f}. "
                        f"DR: ${total_dr:,.2f} | CR: ${total_cr:,.2f}",
    })

    # Check 3: No unmapped accounts
    unmapped = fy.trial_balance_lines.filter(
        mapped_line_item__isnull=True,
        is_adjustment=False,
    ).count()
    checks.append({
        "name": "All accounts mapped",
        "passed": unmapped == 0,
        "message": "All accounts are mapped." if unmapped == 0
                   else f"{unmapped} account(s) are unmapped. Map them before review.",
    })

    # Check 4: ABN must be recorded
    entity = fy.entity
    has_abn = bool(getattr(entity, 'abn', None) and str(entity.abn).strip())
    checks.append({
        "name": "ABN recorded",
        "passed": has_abn,
        "message": f"ABN: {entity.abn}" if has_abn
                   else "Entity ABN is not recorded. Add the ABN in entity details before submitting.",
    })

    all_passed = all(c["passed"] for c in checks)
    return {"passed": all_passed, "checks": checks}


# ---------------------------------------------------------------------------
# Risk Engine Pre-Run — Deterministic Hard Facts
# ---------------------------------------------------------------------------
def _run_risk_engine_precheck(financial_year):
    """
    Run the risk engine's deterministic Tier 1 + Tier 2 checks BEFORE
    the LLM review. Returns a dict of check_id -> list of risk flags.

    These findings are CONFIRMED HARD FACTS that the LLM cannot override.
    """
    from core.risk_engine import run_risk_engine, _load_trial_balance, _check_div7a_loans
    from core.models import RiskFlag

    # Run the full risk engine (Tier 1 + Tier 2)
    try:
        risk_results = run_risk_engine(financial_year, tiers=[1, 2])
        logger.info(
            f"Risk engine pre-run: {risk_results['flags_created']} flags created, "
            f"{risk_results['flags_auto_resolved']} auto-resolved"
        )
    except Exception as e:
        logger.error(f"Risk engine pre-run failed: {e}")
        risk_results = {"flags_created": 0, "errors": [str(e)]}

    # Collect all open risk flags for this FY, grouped by relevance to each check
    open_flags = RiskFlag.objects.filter(
        financial_year=financial_year,
        status__in=["open", "reviewed"],
    ).order_by("tier", "severity")

    # Map risk flags to Eva compliance check IDs
    check_flags = {
        "div7a": [],
        "gst_reconciliation": [],
        "related_party": [],
        "smsf_compliance": [],
        "trust_distribution": [],
        "depreciation_review": [],
        "tb_integrity": [],
        "comparative_consistency": [],
    }

    for flag in open_flags:
        rule_id = flag.rule_id or ""
        title_lower = (flag.title or "").lower()
        desc_lower = (flag.description or "").lower()

        # Division 7A flags
        if "div7a" in rule_id.lower() or "division 7a" in title_lower or "division 7a" in desc_lower:
            check_flags["div7a"].append(flag)
        # Loan-related flags that aren't explicitly Div 7A
        elif any(kw in title_lower for kw in ("loan", "director", "shareholder", "advance")):
            check_flags["div7a"].append(flag)

        # GST flags
        if "gst" in rule_id.lower() or "gst" in title_lower:
            check_flags["gst_reconciliation"].append(flag)

        # Related party / management fee flags
        if any(kw in title_lower for kw in ("related party", "management fee", "intercompany")):
            check_flags["related_party"].append(flag)

        # Superannuation flags
        if any(kw in title_lower for kw in ("super", "sgc", "sg rate")):
            check_flags["related_party"].append(flag)  # Grouped with related party for now

        # Variance flags — relevant to comparative consistency and TB integrity
        if "variance" in rule_id.lower() or "variance" in title_lower:
            check_flags["comparative_consistency"].append(flag)

        # Revenue/expense benchmark flags
        if "benchmark" in rule_id.lower() or "benchmark" in title_lower:
            check_flags["comparative_consistency"].append(flag)

        # Solvency / balance sign flags
        if "solvency" in rule_id.lower() or "balance sign" in title_lower:
            check_flags["tb_integrity"].append(flag)

    return check_flags, risk_results


def _format_risk_flags_as_hard_facts(flags):
    """
    Format risk engine flags as CONFIRMED HARD FACTS text block for the LLM prompt.
    The LLM MUST acknowledge these — it cannot dismiss or override them.
    """
    if not flags:
        return ""

    lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║  CONFIRMED HARD FACTS — RISK ENGINE (DETERMINISTIC)        ║",
        "║  These findings are mathematically verified. You MUST       ║",
        "║  acknowledge each one. You CANNOT dismiss or override them. ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
    ]

    for i, flag in enumerate(flags, 1):
        lines.append(f"CONFIRMED FINDING #{i}:")
        lines.append(f"  Rule: {flag.rule_id}")
        lines.append(f"  Severity: {flag.severity}")
        lines.append(f"  Title: {flag.title}")
        lines.append(f"  Detail: {flag.description}")
        if flag.recommended_action:
            lines.append(f"  Action Required: {flag.recommended_action}")
        if flag.legislation_ref:
            lines.append(f"  Legislation: {flag.legislation_ref}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build Check Context for LLM (v2.0 — uses effective balances)
# ---------------------------------------------------------------------------
def _build_check_context(financial_year, check_id, risk_flags=None):
    """
    Build the specific context needed for a compliance check.

    Defect 2 fix: Uses netted effective balances (aggregated across
    original TB lines + adjusting journals) instead of raw debit/credit.

    Defect 3 fix: Injects risk engine findings as confirmed hard facts.
    """
    from core.eva_chat import build_context_payload
    from core.risk_engine import _load_trial_balance

    fy = financial_year
    entity = fy.entity

    # Base context (TB, journals, associates)
    base_context = build_context_payload(fy)

    # Load the risk engine's aggregated TB data (with effective balances)
    tb_data = _load_trial_balance(fy)

    # Add check-specific context using EFFECTIVE balances
    extra = []

    if check_id == "div7a":
        # Defect 1+2 fix: Use expanded keywords AND effective balances
        extra.append("=== LOAN & RELATED PARTY ACCOUNTS (EFFECTIVE BALANCES) ===")
        found_any = False
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in LOAN_KEYWORDS):
                net = line.effective_dr - line.effective_cr
                balance_type = "DEBIT (owed TO company)" if net > ZERO else "CREDIT (owed BY company)"
                extra.append(
                    f"  {line.account_code} {line.account_name}: "
                    f"Effective DR ${line.effective_dr:,.2f} / CR ${line.effective_cr:,.2f} "
                    f"→ Net ${net:,.2f} {balance_type}"
                )
                found_any = True
        if not found_any:
            extra.append("  No loan/director/shareholder accounts found in TB.")

    elif check_id == "related_party":
        # Enhanced related party detection with effective balances
        extra.append("=== RELATED PARTY TRANSACTION ACCOUNTS (EFFECTIVE BALANCES) ===")
        found_any = False
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in RELATED_PARTY_KEYWORDS):
                net = line.effective_dr - line.effective_cr
                extra.append(
                    f"  {line.account_code} {line.account_name}: "
                    f"Effective DR ${line.effective_dr:,.2f} / CR ${line.effective_cr:,.2f} "
                    f"→ Net ${net:,.2f}"
                )
                # Include prior year for comparison
                prior_net = (line.prior_debit or ZERO) - (line.prior_credit or ZERO)
                if prior_net != ZERO:
                    extra.append(f"    Prior Year Net: ${prior_net:,.2f}")
                found_any = True
        if not found_any:
            extra.append("  No related party accounts identified by keyword search.")

    elif check_id == "trust_distribution":
        # Check for distribution-related journals
        from core.models import AdjustingJournal
        dist_journals = AdjustingJournal.objects.filter(
            financial_year=fy,
            description__icontains="distribution",
        ).values_list("reference_number", "description", "status")
        if dist_journals:
            extra.append("=== DISTRIBUTION JOURNALS ===")
            for ref, desc, status in dist_journals:
                extra.append(f"{ref}: {desc} ({status})")

    elif check_id == "depreciation_review":
        # Include depreciation asset data
        from core.models import DepreciationAsset
        assets = DepreciationAsset.objects.filter(financial_year=fy)
        if assets.exists():
            extra.append("=== DEPRECIATION ASSETS ===")
            for a in assets:
                extra.append(
                    f"{a.asset_name}: Opening ${a.opening_wdv}, "
                    f"Dep ${a.depreciation_amount}, Closing ${a.closing_wdv}"
                )

    elif check_id == "super_guarantee":
        # Check for superannuation-related accounts
        extra.append("=== SUPERANNUATION ACCOUNTS (EFFECTIVE BALANCES) ===")
        super_kw = ["super", "superannuation", "sgc", "super guarantee", "super payable"]
        found_any = False
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in super_kw):
                net = line.effective_dr - line.effective_cr
                extra.append(
                    f"  {line.account_code} {line.account_name}: "
                    f"Effective DR ${line.effective_dr:,.2f} / CR ${line.effective_cr:,.2f} "
                    f"\u2192 Net ${net:,.2f}"
                )
                found_any = True
        if not found_any:
            extra.append("  No superannuation accounts found in TB.")
        # Check for wages/salary accounts to estimate SG obligation
        wage_kw = ["wage", "salary", "salaries", "payroll", "director fee"]
        extra.append("\n=== WAGES & SALARY ACCOUNTS ===")
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in wage_kw):
                net = line.effective_dr - line.effective_cr
                extra.append(f"  {line.account_code} {line.account_name}: Net ${net:,.2f}")

    elif check_id == "ato_benchmarks":
        # Provide key financial ratios for benchmark comparison
        extra.append("=== KEY FINANCIAL RATIOS FOR ATO BENCHMARK COMPARISON ===")
        total_revenue = ZERO
        total_expenses = ZERO
        total_cogs = ZERO
        for line in tb_data["lines"]:
            section = (getattr(line, 'statement_section', '') or '').lower()
            name_lower = (line.account_name or "").lower()
            net = line.effective_dr - line.effective_cr
            if 'revenue' in section or 'income' in section:
                total_revenue += abs(net)
            elif 'cost of' in name_lower or 'cogs' in name_lower:
                total_cogs += abs(net)
            elif 'expense' in section:
                total_expenses += abs(net)
        gross_profit = total_revenue - total_cogs
        net_profit = total_revenue - total_cogs - total_expenses
        extra.append(f"  Total Revenue: ${total_revenue:,.2f}")
        extra.append(f"  Cost of Goods Sold: ${total_cogs:,.2f}")
        extra.append(f"  Gross Profit: ${gross_profit:,.2f}")
        extra.append(f"  Total Expenses: ${total_expenses:,.2f}")
        extra.append(f"  Net Profit: ${net_profit:,.2f}")
        if total_revenue > ZERO:
            extra.append(f"  Gross Profit Margin: {(gross_profit / total_revenue * 100):.1f}%")
            extra.append(f"  Net Profit Margin: {(net_profit / total_revenue * 100):.1f}%")
            extra.append(f"  Expense Ratio: {(total_expenses / total_revenue * 100):.1f}%")
        extra.append(f"  Industry: {entity.industry or 'Not specified'}")

    elif check_id == "going_concern":
        # Provide indicators for going concern assessment
        extra.append("=== GOING CONCERN INDICATORS ===")
        # Cash position
        cash_kw = ["cash", "bank", "petty cash", "term deposit"]
        total_cash = ZERO
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in cash_kw):
                net = line.effective_dr - line.effective_cr
                total_cash += net
                extra.append(f"  {line.account_code} {line.account_name}: Net ${net:,.2f}")
        extra.append(f"  Total Cash/Bank: ${total_cash:,.2f}")
        # Liabilities vs assets
        total_current_liab = ZERO
        total_current_asset = ZERO
        for line in tb_data["lines"]:
            section = (getattr(line, 'statement_section', '') or '').lower()
            net = line.effective_dr - line.effective_cr
            if 'current liabilit' in section:
                total_current_liab += abs(net)
            elif 'current asset' in section:
                total_current_asset += net
        extra.append(f"  Current Assets: ${total_current_asset:,.2f}")
        extra.append(f"  Current Liabilities: ${total_current_liab:,.2f}")
        if total_current_liab > ZERO:
            extra.append(f"  Current Ratio: {(total_current_asset / total_current_liab):.2f}")
        # Net profit/loss
        total_revenue = ZERO
        total_expenses = ZERO
        for line in tb_data["lines"]:
            section = (getattr(line, 'statement_section', '') or '').lower()
            net = line.effective_dr - line.effective_cr
            if 'revenue' in section or 'income' in section:
                total_revenue += abs(net)
            elif 'expense' in section:
                total_expenses += abs(net)
        extra.append(f"  Revenue: ${total_revenue:,.2f}, Expenses: ${total_expenses:,.2f}")
        extra.append(f"  Net Result: ${(total_revenue - total_expenses):,.2f}")

    elif check_id == "tpar":
        # Check for contractor/subcontractor payment accounts
        extra.append("=== CONTRACTOR & SUBCONTRACTOR ACCOUNTS ===")
        tpar_kw = ["contractor", "subcontractor", "sub-contractor", "labour hire",
                   "building", "cleaning", "courier", "road freight", "it services"]
        found_any = False
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in tpar_kw):
                net = line.effective_dr - line.effective_cr
                extra.append(f"  {line.account_code} {line.account_name}: Net ${net:,.2f}")
                found_any = True
        if not found_any:
            extra.append("  No contractor/subcontractor accounts found.")
        extra.append(f"  Industry: {entity.industry or 'Not specified'}")

    elif check_id == "thin_capitalisation":
        # Check for international-related accounts and debt levels
        extra.append("=== THIN CAPITALISATION INDICATORS ===")
        debt_kw = ["loan", "borrowing", "debt", "mortgage", "finance", "intercompany"]
        equity_kw = ["equity", "capital", "retained", "reserve", "share"]
        total_debt = ZERO
        total_equity = ZERO
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            net = line.effective_dr - line.effective_cr
            if any(kw in name_lower for kw in debt_kw):
                extra.append(f"  DEBT: {line.account_code} {line.account_name}: Net ${net:,.2f}")
                total_debt += abs(net)
            elif any(kw in name_lower for kw in equity_kw):
                total_equity += abs(net)
        extra.append(f"  Total Debt: ${total_debt:,.2f}")
        extra.append(f"  Total Equity: ${total_equity:,.2f}")
        if total_equity > ZERO:
            extra.append(f"  Debt-to-Equity Ratio: {(total_debt / total_equity):.2f}")

    elif check_id == "comparative_consistency":
        # Add summary of significant variances using effective balances
        extra.append("=== SIGNIFICANT YEAR-ON-YEAR MOVEMENTS (EFFECTIVE BALANCES) ===")
        variance_count = 0
        for line in tb_data["lines"]:
            current_net = line.effective_dr - line.effective_cr
            prior_net = (line.prior_debit or ZERO) - (line.prior_credit or ZERO)
            if prior_net != ZERO:
                variance_pct = abs((current_net - prior_net) / abs(prior_net) * 100)
                abs_variance = abs(current_net - prior_net)
                if variance_pct >= 25 and abs_variance >= Decimal("5000"):
                    direction = "increased" if current_net > prior_net else "decreased"
                    extra.append(
                        f"  {line.account_code} {line.account_name}: "
                        f"${prior_net:,.2f} → ${current_net:,.2f} "
                        f"({direction} {variance_pct:.1f}%, ${abs_variance:,.2f})"
                    )
                    variance_count += 1
        if variance_count == 0:
            extra.append("  No significant variances detected (>25% and >$5,000).")

    # Inject risk engine hard facts (Defect 3 fix)
    hard_facts_text = ""
    if risk_flags:
        hard_facts_text = _format_risk_flags_as_hard_facts(risk_flags)

    # Retrieve Knowledge Brain context for this check
    kb_context = ""
    try:
        from core.eva_knowledge import retrieve_relevant_chunks, format_rag_context
        check_def = next((c for c in COMPLIANCE_CHECKS if c["id"] == check_id), None)
        if check_def:
            search_query = f"{check_def['name']} {check_def['description']} Australian tax law"
            chunks = retrieve_relevant_chunks(search_query, top_k=4)
            if chunks:
                kb_context = "\n\n=== KNOWLEDGE BRAIN REFERENCE ===\n"
                kb_context += format_rag_context(chunks)
    except Exception as e:
        logger.warning(f"Knowledge Brain retrieval failed for {check_id}: {e}")

    extra_text = "\n".join(extra) if extra else ""

    # Assemble: Hard Facts first, then KB context, then base context, then extras
    parts = []
    if hard_facts_text:
        parts.append(hard_facts_text)
    if kb_context:
        parts.append(kb_context)
    parts.append(base_context)
    if extra_text:
        parts.append(extra_text)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Eva Review System Prompts (v2.0 — retrieval-first, hard facts mandatory)
# ---------------------------------------------------------------------------
EVA_REVIEW_SYSTEM_PROMPT = """You are Eva, the AI Compliance Reviewer for MC & S Accountants.
You are performing a structured compliance review of a financial year before finalisation.

═══════════════════════════════════════════════════════
CRITICAL RULES
═══════════════════════════════════════════════════════

1. CONFIRMED HARD FACTS: If the prompt contains a "CONFIRMED HARD FACTS" section,
   these are mathematically verified by the risk engine. You MUST acknowledge every one.
   You CANNOT dismiss or contradict them. Set source to "risk_engine".

2. KNOWLEDGE BRAIN: If a "KNOWLEDGE BRAIN REFERENCE" section is provided, you MUST
   cite the specific document title in remediation_firm_procedure. If no Knowledge Brain
   material is provided for this check, include this disclosure in remediation_firm_procedure:
   "No firm-specific procedure found in the Knowledge Brain."

3. ADDITIONAL FINDINGS: You may raise additional findings if clearly supported by
   evidence in the trial balance data. Set source to "eva_analysis".

4. EFFECTIVE BALANCES: Use "Effective" (netted) balances, not raw debit/credit columns.

5. ONE FINDING PER CHECK: Each check produces at most ONE finding. If an account or
   issue is primarily covered by another compliance check, do NOT duplicate the full
   analysis. Instead, add the other check's ID to cross_references and write a single
   sentence in your explanation: "See [other check name] for detailed analysis."

═══════════════════════════════════════════════════════
SEVERITY CLASSIFICATION
═══════════════════════════════════════════════════════

CRITICAL — use ONLY when ALL three conditions are met:
  (a) A specific, identifiable compliance exposure exists (not a general concern).
  (b) It creates quantifiable financial risk to the client OR the practice.
  (c) It requires action BEFORE financial statements can be signed.
  Resolution note must describe what action was taken (not just "reviewed" or "noted").

ADVISORY — use for everything else:
  A matter requiring acknowledgement and documentation, but not blocking finalisation.
  Includes disclosure requirements, best-practice gaps, and matters to monitor.
  Resolution note can be an acknowledgement with documented reasoning.

Severity examples:
  - Director loan debit balance $50,000 with no Div 7A agreement → CRITICAL
  - Depreciation method change with immaterial impact → ADVISORY
  - Missing TPAR lodgement where industry is unconfirmed → ADVISORY
  - Trust distribution resolution not evidenced before 30 June → CRITICAL
  - Year-on-year revenue variance of 30% → ADVISORY (not blocking)

═══════════════════════════════════════════════════════
CONFIDENCE LEVELS
═══════════════════════════════════════════════════════

Confidence reflects DATA AVAILABILITY AND VERIFIABILITY, not issue severity.

HIGH: The factual basis is fully verified from trial balance data, entity records,
or risk engine hard facts. The issue objectively exists and can be confirmed from
the data provided.
  Examples: Director loan debit balance confirmed from TB; depreciation schedule
  shows asset with $0 WDV still being depreciated; TB is out of balance by $X.

MEDIUM: Indicators identified from available data, but the conclusion depends on
information not available in the platform (e.g. external records, client confirmation).
  Examples: Thin capitalisation depends on foreign dealings status; super guarantee
  compliance depends on employee headcount not in TB.

LOW: Inferred from patterns or account names; the issue may not exist. The finding
is speculative based on limited data.
  Examples: Contractor payments suggest TPAR obligation but industry is unconfirmed;
  account name suggests related party but no supporting evidence.

═══════════════════════════════════════════════════════
OUTPUT FORMAT — JSON object only (no markdown, no code fences)
═══════════════════════════════════════════════════════

{
  "has_finding": true or false,
  "title": "Max 12 words — be specific, name the account or issue",
  "severity": "CRITICAL" or "ADVISORY",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "source": "risk_engine" or "eva_analysis",
  "explanation": "MAXIMUM 40-60 words. See brevity rules below.",
  "legislation_reference": "Short citation only, e.g. s.109D ITAA 1936",
  "remediation_firm_procedure": "How MC&S handles this issue. MUST cite Knowledge Brain document title if available. If none found: No firm-specific procedure found in the Knowledge Brain. Max 3 sentences.",
  "remediation_authority": "The specific legislative section, ATO ruling number, or AASB standard that governs this issue. Max 2 sentences.",
  "remediation_fix": "Concrete steps specific to THIS entity. What needs to be done, in what order, by whom. Reference the entity's actual account codes and balances. Max 4 sentences.",
  "cross_references": ["check_id_1", "check_id_2"]
}

═══════════════════════════════════════════════════════
BREVITY IS MANDATORY — THIS IS THE MOST IMPORTANT RULE
═══════════════════════════════════════════════════════

Your explanation MUST be 40-60 words. Not 80. Not 100. Count your words.

GOOD example (47 words):
"Director loan account 2-1200 shows $52,340 debit balance. No Division 7A
compliant loan agreement on file. Without a conforming agreement, the full
balance is deemed an unfranked dividend under s.109D ITAA 1936, creating
immediate tax liability for the shareholder."

BAD example (too long, 95 words):
"Upon reviewing the trial balance, I note that account 2-1200 Director Loan
has a debit balance of $52,340.00 which represents amounts owed by the
director to the company. This is significant because under Division 7A of
the Income Tax Assessment Act 1936, specifically section 109D, any loan
made by a private company to a shareholder or associate that does not have
a compliant loan agreement in place by the lodgement date of the company's
tax return will be treated as an unfranked dividend..."

Rules:
- Lead with the account code and balance (the key number).
- State the issue in one sentence.
- Name the consequence in one sentence.
- Do NOT trace journal entries or calculate percentage movements.
- Do NOT comment on ATO enforcement posture or likelihood of audit.
- Do NOT list every variance — summarise the pattern.
- Do NOT reference more than 3 accounts. If more are involved, write
  "X accounts affected" and name only the top 2-3 by value.
- Save all supporting detail for the three remediation sections.
- Each remediation section: 2-4 sentences max.
- cross_references: list check IDs (e.g. "div7a", "super_guarantee") where
  the same account or issue is also relevant. Use this INSTEAD of duplicating.

═══════════════════════════════════════════════════════
COMPARATIVE CONSISTENCY — SPECIAL RULES
═══════════════════════════════════════════════════════

For the comparative_consistency check:
- Do NOT create a roll-up finding listing all variances.
- Only flag accounts NOT already covered by another specific compliance check.
- If the only significant variances are in accounts covered by div7a,
  related_party, depreciation_review, or super_guarantee, set has_finding to false
  and let those specific checks handle the analysis.
- If you do raise a finding, focus on the single most significant unexplained
  variance and reference at most 2-3 accounts.

═══════════════════════════════════════════════════════
TRIAL BALANCE INTEGRITY — SPECIAL RULES
═══════════════════════════════════════════════════════

For the tb_integrity check:
- Focus on structural issues: TB not balanced, orphan accounts, unmapped accounts.
- Do NOT flag balance sign issues (e.g. negative asset) as CRITICAL unless the
  amount is material (>5% of total assets or >$10,000).
- If multiple structural issues exist, report the most severe one only and
  mention the count of others: "Additionally, X other minor issues detected."

═══════════════════════════════════════════════════════
ADDITIONAL RULES
═══════════════════════════════════════════════════════

1. If confirmed hard facts exist, has_finding MUST be true.
2. Use Australian tax law and accounting standards.
3. If no confirmed hard facts AND no issues found, set has_finding to false.
4. If a finding would reference more than 3 accounts, summarise — never list
   more than 3 specific accounts.
5. The three remediation fields form a hierarchy:
   remediation_firm_procedure = "What does MC&S's internal procedure say?"
   remediation_authority = "What does the law/ATO/AASB say?"
   remediation_fix = "What specific steps should be taken for THIS entity?"
   All three MUST be populated for every finding. They are NOT optional.
"""


# ---------------------------------------------------------------------------
# Robust JSON Extraction from LLM Responses
# ---------------------------------------------------------------------------
def _parse_llm_json(response_text, check_id="unknown"):
    """
    Parse JSON from an LLM response with multiple fallback strategies:
      1. Direct parse after stripping markdown fences
      2. Regex extraction of the first JSON object
      3. Truncation repair — close open strings and braces

    Raises json.JSONDecodeError only if ALL strategies fail.
    """
    # Strategy 1: Strip markdown fences and parse directly
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (possibly with language tag like ```json)
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Regex — extract the first { ... } block
    match = re.search(r'\{[\s\S]*\}', cleaned)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Strategy 3: Truncation repair — the response was cut off mid-JSON
    # Find the opening brace and attempt to close the JSON
    brace_start = cleaned.find("{")
    if brace_start >= 0:
        fragment = cleaned[brace_start:]
        repaired = _repair_truncated_json(fragment)
        if repaired:
            try:
                result = json.loads(repaired)
                logger.info(f"Eva check {check_id}: recovered truncated JSON via repair")
                return result
            except json.JSONDecodeError:
                pass

    # All strategies failed — raise so the caller's except block handles it
    raise json.JSONDecodeError(
        f"All JSON parse strategies failed for check {check_id}",
        cleaned[:200], 0,
    )


def _repair_truncated_json(fragment):
    """
    Attempt to repair a truncated JSON object by:
      1. Closing any open string (find last unescaped quote state)
      2. Closing open braces/brackets
      3. Returning the repaired string

    Returns the repaired JSON string or None if repair seems hopeless.
    """
    # Must start with {
    if not fragment.strip().startswith("{"):
        return None

    # Walk through to determine open/close state
    in_string = False
    escape_next = False
    brace_depth = 0
    bracket_depth = 0
    last_good = 0

    for i, ch in enumerate(fragment):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        # Outside string
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth -= 1

        if brace_depth == 0 and bracket_depth == 0:
            # Fully closed — shouldn't be truncated, but let's try
            return fragment[:i + 1]

    # If we're here, the JSON is truncated. Try to close it.
    repaired = fragment

    # Close open string
    if in_string:
        repaired += '"'

    # Close open brackets then braces
    for _ in range(bracket_depth):
        repaired += "]"
    for _ in range(brace_depth):
        repaired += "}"

    return repaired


# ---------------------------------------------------------------------------
# Run a Single Compliance Check
# ---------------------------------------------------------------------------
def _run_single_check(financial_year, check_def, risk_flags=None, prior_findings=None):
    """
    Run a single compliance check using the LLM, with risk engine
    findings injected as confirmed hard facts.

    Args:
        prior_findings: list of dicts with prior finding titles/check_names
                        from previous reviews, used for deduplication context.

    Returns:
        dict with finding data, or None if no finding
    """
    from core.ai_service import _call_llm

    context = _build_check_context(financial_year, check_def["id"], risk_flags=risk_flags)

    # If there are confirmed hard facts, tell the LLM explicitly
    hard_facts_note = ""
    if risk_flags:
        hard_facts_note = (
            f"\n\nIMPORTANT: The risk engine has identified {len(risk_flags)} confirmed "
            f"finding(s) for this check. You MUST acknowledge each one in your response. "
            f"Set has_finding to true and source to 'risk_engine'."
        )

    # If this is a re-run, inject prior findings context for CLOSED/RE-OPENED logic
    prior_findings_note = ""
    if prior_findings:
        relevant_priors = [
            pf for pf in prior_findings
            if pf['check_name'] == check_def['id']
        ]
        if relevant_priors:
            prior_list = "\n".join(
                f"  - [{pf['severity'].upper()}] {pf['title']} "
                f"(status: {pf['status']}, explanation: {(pf.get('plain_english_explanation', '') or '')[:120]})"
                for pf in relevant_priors
            )
            prior_findings_note = (
                f"\n\n"
                f"╔══════════════════════════════════════════════════════════════╗\n"
                f"║  RE-RUN: PRIOR FINDINGS FOR THIS CHECK                      ║\n"
                f"╚══════════════════════════════════════════════════════════════╝\n"
                f"{prior_list}\n\n"
                f"RE-RUN RULES:\n"
                f"1. If the prior finding was ADDRESSED but the underlying data still shows \n"
                f"   the same issue (e.g. balance unchanged, no correcting journal), RE-OPEN \n"
                f"   it: set has_finding=true and keep the SAME title for continuity.\n"
                f"2. If the prior finding was ADDRESSED and the data confirms it is genuinely \n"
                f"   resolved (e.g. balance cleared, agreement uploaded, journal posted), \n"
                f"   mark it CLOSED: set has_finding=false.\n"
                f"3. If the prior finding was still OPEN and the data is unchanged, re-raise \n"
                f"   it with the same title. Do NOT create a new variant of the same finding.\n"
                f"4. If new data reveals a DIFFERENT issue for this check (not the same as \n"
                f"   the prior finding), you may raise a new finding with a new title."
            )

    user_prompt = f"""COMPLIANCE CHECK: {check_def["name"]}
Description: {check_def["description"]}
Entity Type: {financial_year.entity.get_entity_type_display()}
Default Severity: {check_def["severity_default"]}

{context}

Analyse the above data for this specific compliance check and respond with the JSON structure.{hard_facts_note}{prior_findings_note}
"""

    # Determine tier based on override
    tier = "sonnet"
    if financial_year.eva_model_override == "opus":
        tier = "opus"

    try:
        response_text = _call_llm(
            system_prompt=EVA_REVIEW_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            tier=tier,
            temperature=0.1,
            max_tokens=2000,
        )

        result = _parse_llm_json(response_text, check_def["id"])
        result["check_id"] = check_def["id"]
        result["check_name"] = check_def["name"]
        result["model_used"] = tier
        # Ensure source field exists
        if "source" not in result:
            result["source"] = "risk_engine" if risk_flags else "eva_analysis"
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Eva check {check_def['id']} JSON parse error: {e}")
        logger.error(f"Raw response: {response_text[:500]}")

        # If there were hard facts, create a finding anyway — the risk engine
        # already confirmed the issue, we just couldn't get the LLM to explain it
        if risk_flags:
            flag = risk_flags[0]
            return {
                "check_id": check_def["id"],
                "check_name": check_def["name"],
                "has_finding": True,
                "title": flag.title,
                "severity": flag.severity,
                "explanation": flag.description,
                "recommendation": flag.recommended_action or "",
                "legislation_reference": flag.legislation_ref or "",
                "confidence": "HIGH",
                "source": "risk_engine",
                "model_used": tier,
                "error": f"LLM parse failed, using risk engine finding directly: {e}",
            }

        return {
            "check_id": check_def["id"],
            "check_name": check_def["name"],
            "has_finding": False,
            "error": f"Failed to parse AI response: {e}",
        }
    except Exception as e:
        logger.error(f"Eva check {check_def['id']} error: {e}")

        # Same fallback for hard facts
        if risk_flags:
            flag = risk_flags[0]
            return {
                "check_id": check_def["id"],
                "check_name": check_def["name"],
                "has_finding": True,
                "title": flag.title,
                "severity": flag.severity,
                "explanation": flag.description,
                "recommendation": flag.recommended_action or "",
                "legislation_reference": flag.legislation_ref or "",
                "confidence": "HIGH",
                "source": "risk_engine",
                "model_used": tier,
                "error": f"LLM call failed, using risk engine finding directly: {e}",
            }

        return {
            "check_id": check_def["id"],
            "check_name": check_def["name"],
            "has_finding": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Run Full Eva Review (Background Thread)
# ---------------------------------------------------------------------------
# In-memory task status tracking (same pattern as AI classification)
_eva_review_tasks = {}


def _run_eva_review_background(fy_pk, user_pk):
    """Background thread function to run the full Eva review."""
    import django
    django.setup()

    from core.models import (
        FinancialYear, EvaReview, EvaFinding, ActivityLog,
    )
    from django.contrib.auth import get_user_model

    User = get_user_model()
    task_key = str(fy_pk)
    start_time = time.time()

    try:
        fy = FinancialYear.objects.select_related("entity").get(pk=fy_pk)
        user = User.objects.get(pk=user_pk)
        entity_type = fy.entity.entity_type

        # Create the EvaReview record
        review = EvaReview.objects.create(
            financial_year=fy,
            status="pending",
            triggered_by=user,
            opus_override=(fy.eva_model_override == "opus"),
        )

        # Update FY status to pending_eva
        fy.status = fy.Status.PENDING_EVA
        fy.save(update_fields=["status"])

        # ── STEP 1: Run risk engine FIRST (deterministic checks) ─────
        _eva_review_tasks[task_key] = {
            "status": "running",
            "review_id": str(review.pk),
            "total_checks": 0,
            "completed_checks": 0,
            "current_check": "Running risk engine (deterministic checks)...",
            "findings_count": 0,
        }
        # Persist progress to DB so all gunicorn workers can read it
        review.raw_response = {"progress": _eva_review_tasks[task_key]}
        review.save(update_fields=["raw_response"])

        check_flags, risk_results = _run_risk_engine_precheck(fy)

        # Count total confirmed flags
        total_risk_flags = sum(len(flags) for flags in check_flags.values())
        logger.info(
            f"Risk engine pre-run complete: {total_risk_flags} relevant flags "
            f"mapped to Eva checks"
        )

        # ── STEP 2: Run LLM checks with hard facts injected ─────────
        # Filter checks by entity type
        applicable_checks = [
            c for c in COMPLIANCE_CHECKS
            if entity_type in c["entity_types"]
        ]

        _eva_review_tasks[task_key].update({
            "total_checks": len(applicable_checks),
            "risk_flags_found": total_risk_flags,
        })
        review.raw_response = {"progress": _eva_review_tasks[task_key]}
        review.save(update_fields=["raw_response"])

        findings_created = 0

        # Collect prior findings from previous reviews for deduplication (Issue 7)
        prior_findings = []
        try:
            previous_reviews = EvaReview.objects.filter(
                financial_year=fy,
                status__in=["findings_raised", "cleared"],
            ).exclude(pk=review.pk).order_by("-completed_at")[:1]
            if previous_reviews:
                prev_review = previous_reviews[0]
                prior_findings = list(
                    prev_review.findings.values(
                        "check_name", "title", "severity", "status",
                        "plain_english_explanation",
                    )
                )
                if prior_findings:
                    review.is_rerun = True
                    review.save(update_fields=["is_rerun"])
                    print(f"[Eva] Re-run detected: {len(prior_findings)} prior findings loaded for deduplication", flush=True)
        except Exception as pf_err:
            print(f"[Eva] Prior findings lookup error (non-fatal): {pf_err}", flush=True)

        for i, check_def in enumerate(applicable_checks):
            _eva_review_tasks[task_key]["current_check"] = check_def["name"]
            _eva_review_tasks[task_key]["completed_checks"] = i
            # Persist progress to DB after each check starts
            review.raw_response = {"progress": _eva_review_tasks[task_key]}
            review.save(update_fields=["raw_response"])
            print(f"[Eva] Starting check {i+1}/{len(applicable_checks)}: {check_def['id']}", flush=True)

            # Get risk engine flags relevant to this check
            relevant_flags = check_flags.get(check_def["id"], [])

            try:
                result = _run_single_check(fy, check_def, risk_flags=relevant_flags, prior_findings=prior_findings)
            except Exception as check_err:
                print(f"[Eva] EXCEPTION in _run_single_check for {check_def['id']}: {check_err}", flush=True)
                traceback.print_exc()
                result = None

            print(f"[Eva] Check {check_def['id']} result: has_finding={result.get('has_finding') if result else 'None'}, error={result.get('error', 'none') if result else 'N/A'}", flush=True)

            if result and result.get("has_finding"):
                # Retrieve Knowledge Brain citation if available
                kb_citation = ""
                try:
                    from core.eva_knowledge import retrieve_relevant_chunks
                    chunks = retrieve_relevant_chunks(
                        f"{check_def['name']} {result.get('legislation_reference', '')}",
                        top_k=1,
                    )
                    if chunks:
                        kb_citation = f"{chunks[0]['document_title']} ({chunks[0]['category']})"
                except Exception:
                    pass

                # Normalise severity and confidence to lowercase (model choices are lowercase)
                raw_severity = (result.get("severity", check_def["severity_default"]) or "advisory").lower()
                if raw_severity not in ("critical", "advisory"):
                    raw_severity = "advisory"
                raw_confidence = (result.get("confidence", "medium") or "medium").lower()
                if raw_confidence not in ("high", "medium", "low"):
                    raw_confidence = "medium"

                # Build combined recommendation from old field + new remediation_fix
                recommendation_text = result.get("recommendation", "") or ""
                remediation_fix = result.get("remediation_fix", "") or ""
                if remediation_fix and not recommendation_text:
                    recommendation_text = remediation_fix

                # Determine source
                raw_source = (result.get("source", "") or "").lower()
                finding_source = raw_source if raw_source in ("risk_engine", "eva_analysis") else "eva_analysis"

                # Link to prior finding if this is a re-run and same check
                prior_finding_link = None
                if prior_findings:
                    matching_priors = [
                        pf for pf in prior_findings
                        if pf['check_name'] == check_def['id']
                    ]
                    if matching_priors:
                        # Try to find the actual prior finding object
                        try:
                            prior_finding_link = EvaFinding.objects.filter(
                                eva_review__financial_year=fy,
                                check_name=check_def['id'],
                            ).exclude(
                                eva_review=review
                            ).order_by('-created_at').first()
                        except Exception:
                            pass

                try:
                    finding = EvaFinding.objects.create(
                        eva_review=review,
                        check_name=check_def["id"][:50],
                        severity=raw_severity,
                        title=(result.get("title", check_def["name"]) or "")[:255],
                        plain_english_explanation=result.get("explanation", "") or "",
                        recommendation=recommendation_text,
                        remediation_firm_procedure=result.get("remediation_firm_procedure", "") or "",
                        remediation_authority=result.get("remediation_authority", "") or "",
                        remediation_synthesis=remediation_fix,
                        legislation_reference=(result.get("legislation_reference", "") or "")[:255],
                        knowledge_brain_citation=(kb_citation or "")[:500],
                        confidence=raw_confidence,
                        source=finding_source,
                        prior_finding=prior_finding_link,
                        status="reopened" if prior_finding_link else "open",
                    )
                    # Store cross-references for post-processing
                    cross_refs = result.get("cross_references", []) or []
                    if cross_refs:
                        finding._cross_ref_check_ids = cross_refs
                    findings_created += 1
                    _eva_review_tasks[task_key]["findings_count"] = findings_created
                    review.raw_response = {"progress": _eva_review_tasks[task_key]}
                    review.save(update_fields=["raw_response"])
                    print(f"[Eva] Finding created for {check_def['id']}: {(result.get('title', '')[:60])}", flush=True)
                except Exception as save_err:
                    print(f"[Eva] EXCEPTION saving finding for {check_def['id']}: {save_err}", flush=True)
                    traceback.print_exc()

            # If the LLM didn't raise a finding but there ARE confirmed hard facts,
            # create findings directly from the risk engine flags
            elif relevant_flags and not (result and result.get("has_finding")):
                print(f"[Eva] LLM missed hard facts for {check_def['id']}, creating from risk engine", flush=True)
                for flag in relevant_flags:
                    # Normalise severity from risk engine flags
                    flag_severity = (flag.severity or "advisory").lower()
                    if flag_severity not in ("critical", "advisory"):
                        flag_severity = "advisory"
                    try:
                        EvaFinding.objects.create(
                            eva_review=review,
                            check_name=check_def["id"][:50],
                            severity=flag_severity,
                            title=(flag.title or "")[:255],
                            plain_english_explanation=flag.description or "",
                            recommendation=flag.recommended_action or "",
                            legislation_reference=(flag.legislation_ref or "")[:255],
                            knowledge_brain_citation="",
                            confidence="high",
                            status="open",
                        )
                        findings_created += 1
                        _eva_review_tasks[task_key]["findings_count"] = findings_created
                        review.raw_response = {"progress": _eva_review_tasks[task_key]}
                        review.save(update_fields=["raw_response"])
                    except Exception as save_err:
                        print(f"[Eva] EXCEPTION saving risk flag finding for {check_def['id']}: {save_err}", flush=True)
                        traceback.print_exc()

        # ── STEP 3: Post-processing — mark prior findings as CLOSED ─────
        if prior_findings:
            print(f"[Eva] Post-processing: marking closed prior findings...", flush=True)
            try:
                # Get all check_names that raised findings in this review
                new_finding_checks = set(
                    review.findings.values_list('check_name', flat=True)
                )
                # Get the previous review
                previous_reviews = EvaReview.objects.filter(
                    financial_year=fy,
                    status__in=["findings_raised", "cleared"],
                ).exclude(pk=review.pk).order_by("-completed_at")[:1]
                if previous_reviews:
                    prev_review = previous_reviews[0]
                    # Mark prior findings as CLOSED if they were NOT re-raised
                    for prior_f in prev_review.findings.filter(status__in=["open", "reopened"]):
                        if prior_f.check_name not in new_finding_checks:
                            prior_f.status = "closed"
                            prior_f.resolution_note = (
                                f"Auto-closed by Eva re-run on {timezone.now().strftime('%d %b %Y %H:%M')}. "
                                f"Issue no longer detected in current data."
                            )
                            prior_f.resolved_at = timezone.now()
                            prior_f.save(update_fields=["status", "resolution_note", "resolved_at"])
                            print(f"[Eva] CLOSED prior finding: {prior_f.check_name} — {prior_f.title}", flush=True)
            except Exception as close_err:
                print(f"[Eva] Prior finding closure error (non-fatal): {close_err}", flush=True)

        # ── STEP 4: Post-processing — link cross-references ─────
        print(f"[Eva] Post-processing: linking cross-references...", flush=True)
        try:
            all_findings = list(review.findings.all())
            # Build a map of check_name -> finding for cross-referencing
            finding_by_check = {f.check_name: f for f in all_findings}

            for f in all_findings:
                cross_ref_ids = getattr(f, '_cross_ref_check_ids', [])
                if cross_ref_ids:
                    for ref_check_id in cross_ref_ids:
                        related = finding_by_check.get(ref_check_id)
                        if related and related.pk != f.pk:
                            f.related_findings.add(related)
                            print(f"[Eva] Linked {f.check_name} <-> {ref_check_id}", flush=True)
        except Exception as link_err:
            print(f"[Eva] Cross-reference linking error (non-fatal): {link_err}", flush=True)

        # Update review status
        print(f"[Eva] All checks complete. Findings created: {findings_created}. Saving review...", flush=True)
        duration = time.time() - start_time
        if findings_created > 0:
            review.status = "findings_raised"
            # Set FY back to FINISHED so the page shows findings panel
            # (pending_eva would show the spinner again on reload)
            fy.status = fy.Status.FINISHED
            fy.save(update_fields=["status"])
        else:
            review.status = "cleared"
            fy.status = fy.Status.EVA_CLEARED
            fy.save(update_fields=["status"])

        review.completed_at = timezone.now()
        review.duration_seconds = duration
        review.save(update_fields=["status", "completed_at", "duration_seconds"])
        print(f"[Eva] Review saved with status={review.status}, duration={duration:.1f}s", flush=True)

        # Log activity
        ActivityLog.objects.create(
            user=user,
            event_type="eva_review_complete",
            title=f"Eva Review {'Cleared' if findings_created == 0 else f'Raised {findings_created} Finding(s)'}",
            description=(
                f"Eva compliance review for {fy.entity.entity_name} ({fy.year_label}) "
                f"completed in {duration:.1f}s. "
                f"Risk engine found {total_risk_flags} confirmed issue(s). "
                f"{'No findings — cleared for finalisation.' if findings_created == 0 else f'{findings_created} finding(s) require attention.'}"
            ),
            entity=fy.entity,
            financial_year=fy,
            url=f"/entities/years/{fy.pk}/",
        )

        _eva_review_tasks[task_key] = {
            "status": "complete",
            "review_id": str(review.pk),
            "total_checks": len(applicable_checks),
            "completed_checks": len(applicable_checks),
            "findings_count": findings_created,
            "risk_flags_found": total_risk_flags,
            "review_status": review.status,
            "duration": round(duration, 1),
        }

    except Exception as e:
        print(f"[Eva] FATAL BACKGROUND ERROR: {e}", flush=True)
        traceback.print_exc()
        logger.error(f"Eva review background error: {e}", exc_info=True)
        duration = time.time() - start_time

        # Try to update the review record
        try:
            review.status = "error"
            review.error_message = str(e)[:1000]
            review.completed_at = timezone.now()
            review.duration_seconds = duration
            review.save(update_fields=["status", "error_message", "completed_at", "duration_seconds"])

            fy.status = fy.Status.EVA_ERROR
            fy.save(update_fields=["status"])
        except Exception as inner_e:
            print(f"[Eva] EXCEPTION in error handler: {inner_e}", flush=True)
            traceback.print_exc()

        _eva_review_tasks[task_key] = {
            "status": "error",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@login_required
@require_POST
def ask_eva_review(request, pk):
    """
    Trigger Eva's compliance review for a financial year.

    POST /api/financial-years/<pk>/ask-eva-review/
    """
    from core.models import FinancialYear, EvaReview, ActivityLog

    try:
        fy = FinancialYear.objects.select_related("entity").get(pk=pk)
    except FinancialYear.DoesNotExist:
        return JsonResponse({"error": "Financial year not found"}, status=404)

    # Check if review is already running
    task_key = str(fy.pk)
    if task_key in _eva_review_tasks and _eva_review_tasks[task_key].get("status") == "running":
        return JsonResponse({
            "status": "running",
            "message": "Eva review is already in progress.",
            **_eva_review_tasks[task_key],
        })

    # Pre-flight checks
    preflight = run_preflight_checks(fy)
    if not preflight["passed"]:
        return JsonResponse({
            "status": "blocked",
            "message": "Pre-flight checks failed. Fix the issues before requesting Eva review.",
            "checks": preflight["checks"],
        }, status=400)

    # Check model override from request
    body = {}
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        pass

    if body.get("opus_override"):
        fy.eva_model_override = "opus"
        fy.save(update_fields=["eva_model_override"])

    # Log the trigger
    ActivityLog.objects.create(
        user=request.user,
        event_type="eva_review_triggered",
        title=f"Eva Review Triggered — {fy.entity.entity_name}",
        description=(
            f"Eva compliance review triggered for {fy.entity.entity_name} ({fy.year_label}). "
            f"Model: {'Opus (override)' if fy.eva_model_override == 'opus' else 'Sonnet'}. "
            f"Pipeline: Risk Engine → Knowledge Brain → LLM (v2.0)."
        ),
        entity=fy.entity,
        financial_year=fy,
        url=f"/entities/years/{fy.pk}/",
    )

    # Launch background thread
    thread = threading.Thread(
        target=_run_eva_review_background,
        args=(fy.pk, request.user.pk),
        daemon=True,
    )
    thread.start()

    return JsonResponse({
        "status": "running",
        "message": "Eva review started. Risk engine runs first, then LLM analysis. This may take 30-60 seconds.",
    })


@login_required
@require_GET
def eva_review_status(request, pk):
    """
    Get the status of the current Eva review.

    GET /api/financial-years/<pk>/eva-review-status/
    """
    task_key = str(pk)
    if task_key in _eva_review_tasks:
        return JsonResponse(_eva_review_tasks[task_key])

    # Check database for latest review
    from core.models import EvaReview, FinancialYear

    try:
        fy = FinancialYear.objects.get(pk=pk)
    except FinancialYear.DoesNotExist:
        return JsonResponse({"error": "Financial year not found"}, status=404)

    review = EvaReview.objects.filter(
        financial_year=fy
    ).order_by("-triggered_at").first()

    if not review:
        return JsonResponse({"status": "not_started"})

    # If the review is still pending (running), return progress from raw_response
    if review.status == "pending":
        progress = (review.raw_response or {}).get("progress", {})
        return JsonResponse({
            "status": "running",
            "review_id": str(review.pk),
            "total_checks": progress.get("total_checks", 0),
            "completed_checks": progress.get("completed_checks", 0),
            "current_check": progress.get("current_check", "Processing..."),
            "findings_count": progress.get("findings_count", 0),
        })

    # Review is complete (cleared, findings_raised, or error)
    findings = list(review.findings.values(
        "id", "check_name", "severity", "title",
        "plain_english_explanation", "recommendation",
        "legislation_reference", "knowledge_brain_citation",
        "confidence", "status", "resolution_note",
    ))

    return JsonResponse({
        "status": review.status,
        "review_id": str(review.pk),
        "triggered_at": review.triggered_at.isoformat(),
        "completed_at": review.completed_at.isoformat() if review.completed_at else None,
        "duration": review.duration_seconds,
        "findings_count": len(findings),
        "findings": [
            {
                **f,
                "id": str(f["id"]),
            }
            for f in findings
        ],
        "error_message": review.error_message if review.status == "error" else None,
    })


@login_required
@require_GET
def eva_review_detail(request, pk):
    """
    Get the full Eva review with findings.

    GET /api/financial-years/<pk>/eva-review/
    """
    from core.models import EvaReview, FinancialYear

    try:
        fy = FinancialYear.objects.get(pk=pk)
    except FinancialYear.DoesNotExist:
        return JsonResponse({"error": "Financial year not found"}, status=404)

    review = EvaReview.objects.filter(
        financial_year=fy
    ).order_by("-triggered_at").first()

    if not review:
        return JsonResponse({"status": "not_started", "findings": []})

    findings = []
    for f in review.findings.select_related("resolved_by").prefetch_related("related_findings").all():
        # Build related findings list
        related = [
            {"id": str(rf.pk), "check_name": rf.check_name, "title": rf.title}
            for rf in f.related_findings.all()
        ]

        findings.append({
            "id": str(f.pk),
            "check_name": f.check_name,
            "severity": f.severity,
            "title": f.title,
            "explanation": f.plain_english_explanation,
            "recommendation": f.recommendation,
            "remediation_firm_procedure": f.remediation_firm_procedure,
            "remediation_authority": f.remediation_authority,
            "remediation_synthesis": f.remediation_synthesis,
            "legislation_reference": f.legislation_reference,
            "knowledge_brain_citation": f.knowledge_brain_citation,
            "confidence": f.confidence,
            "status": f.status,
            "resolution_note": f.resolution_note,
            "resolved_by": f.resolved_by.get_full_name() if f.resolved_by else None,
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
            "related_findings": related,
        })

    return JsonResponse({
        "review_id": str(review.pk),
        "status": review.status,
        "triggered_at": review.triggered_at.isoformat(),
        "triggered_by": review.triggered_by.get_full_name() if review.triggered_by else None,
        "completed_at": review.completed_at.isoformat() if review.completed_at else None,
        "duration": review.duration_seconds,
        "opus_override": review.opus_override,
        "findings": findings,
        "fy_status": fy.status,
    })


@login_required
@require_POST
def eva_finding_resolve(request, pk):
    """
    Mark an Eva finding as addressed.

    POST /api/eva-findings/<pk>/resolve/
    Body: {"resolution_note": "..."}
    """
    from core.models import EvaFinding, EvaReview, FinancialYear, ActivityLog

    try:
        finding = EvaFinding.objects.select_related(
            "eva_review__financial_year__entity"
        ).get(pk=pk)
    except EvaFinding.DoesNotExist:
        return JsonResponse({"error": "Finding not found"}, status=404)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    resolution_note = body.get("resolution_note", "").strip()
    if not resolution_note:
        return JsonResponse(
            {"error": "Resolution note is required"},
            status=400,
        )

    finding.status = "addressed"
    finding.resolution_note = resolution_note
    finding.resolved_by = request.user
    finding.resolved_at = timezone.now()
    finding.save(update_fields=[
        "status", "resolution_note", "resolved_by", "resolved_at"
    ])

    # Check if all findings are now addressed
    review = finding.eva_review
    open_findings = review.findings.filter(status="open").count()

    fy = review.financial_year

    if open_findings == 0:
        # All findings addressed — clear the review
        review.status = "cleared"
        review.save(update_fields=["status"])

        fy.status = fy.Status.EVA_CLEARED
        fy.save(update_fields=["status"])

        # Log
        ActivityLog.objects.create(
            user=request.user,
            event_type="eva_cleared",
            title=f"Eva Cleared — {fy.entity.entity_name}",
            description=(
                f"All Eva findings addressed for {fy.entity.entity_name} ({fy.year_label}). "
                f"Financial year is now cleared for finalisation."
            ),
            entity=fy.entity,
            financial_year=fy,
            url=f"/entities/years/{fy.pk}/",
        )

    # Log the resolution
    ActivityLog.objects.create(
        user=request.user,
        event_type="eva_finding_resolved",
        title=f"Eva Finding Addressed — {finding.title or finding.check_name}",
        description=(
            f"Finding '{finding.title or finding.check_name}' addressed for "
            f"{fy.entity.entity_name} ({fy.year_label}). "
            f"Note: {resolution_note[:200]}"
        ),
        entity=fy.entity,
        financial_year=fy,
        url=f"/entities/years/{fy.pk}/",
    )

    return JsonResponse({
        "status": "success",
        "finding_status": "addressed",
        "open_findings_remaining": open_findings,
        "review_status": review.status,
        "fy_status": fy.status,
    })


@login_required
@require_GET
def eva_preflight(request, pk):
    """
    Run pre-flight checks and return results.

    GET /api/financial-years/<pk>/eva-preflight/
    """
    from core.models import FinancialYear

    try:
        fy = FinancialYear.objects.get(pk=pk)
    except FinancialYear.DoesNotExist:
        return JsonResponse({"error": "Financial year not found"}, status=404)

    result = run_preflight_checks(fy)
    return JsonResponse(result)


# ---------------------------------------------------------------------------
# Knowledge Brain API Endpoints
# ---------------------------------------------------------------------------
@login_required
@require_POST
def knowledge_sync(request):
    """
    Trigger a manual SharePoint sync.

    POST /api/knowledge/sync/
    """
    from core.eva_knowledge import sync_sharepoint_library
    from core.models import ActivityLog

    try:
        counts = sync_sharepoint_library()

        ActivityLog.objects.create(
            user=request.user,
            event_type="kb_sync",
            title="Knowledge Brain Sync",
            description=(
                f"SharePoint sync completed: {counts['synced']} synced, "
                f"{counts['skipped']} skipped, {counts['errors']} errors."
            ),
        )

        return JsonResponse({
            "status": "success",
            **counts,
        })
    except Exception as e:
        logger.error(f"Knowledge sync error: {e}")
        return JsonResponse({
            "status": "error",
            "error": str(e),
        }, status=500)


@login_required
@require_GET
def knowledge_documents(request):
    """
    List Knowledge Brain documents.

    GET /api/knowledge/documents/?category=firm_procedures
    """
    from core.models import KnowledgeDocument

    qs = KnowledgeDocument.objects.filter(is_archived=False)

    category = request.GET.get("category")
    if category:
        qs = qs.filter(category=category)

    docs = qs.values(
        "id", "title", "category", "sync_status", "synced_at",
        "chunk_count", "file_type", "file_size_bytes",
    )

    return JsonResponse({
        "documents": [
            {
                **d,
                "id": str(d["id"]),
                "synced_at": d["synced_at"].isoformat() if d["synced_at"] else None,
            }
            for d in docs
        ],
        "total": qs.count(),
    })


@login_required
@require_GET
def knowledge_search(request):
    """
    Semantic search across Knowledge Brain chunks.

    GET /api/knowledge/search/?q=division+7a&category=ato_rulings&limit=8
    """
    from core.eva_service import search_knowledge_brain

    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"error": "Query parameter 'q' is required."}, status=400)

    category = request.GET.get("category", None)
    limit = min(int(request.GET.get("limit", 8)), 20)

    try:
        results = search_knowledge_brain(query, category_filter=category, top_k=limit)
        return JsonResponse({
            "query": query,
            "results": results,
            "count": len(results),
        })
    except Exception as e:
        logger.error(f"Knowledge search error: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_GET
def knowledge_status(request):
    """
    Return Knowledge Brain sync status and statistics.

    GET /api/knowledge/status/
    """
    from core.models import KnowledgeDocument, KnowledgeChunk

    total_docs = KnowledgeDocument.objects.filter(is_archived=False).count()
    synced_docs = KnowledgeDocument.objects.filter(
        is_archived=False,
        sync_status=KnowledgeDocument.SyncStatus.SYNCED,
    ).count()
    error_docs = KnowledgeDocument.objects.filter(
        is_archived=False,
        sync_status=KnowledgeDocument.SyncStatus.ERROR,
    ).count()
    total_chunks = KnowledgeChunk.objects.count()

    # Category breakdown
    from django.db.models import Count
    categories = (
        KnowledgeDocument.objects
        .filter(is_archived=False)
        .values("category")
        .annotate(count=Count("id"))
        .order_by("category")
    )

    last_sync = (
        KnowledgeDocument.objects
        .filter(synced_at__isnull=False)
        .order_by("-synced_at")
        .values_list("synced_at", flat=True)
        .first()
    )

    return JsonResponse({
        "total_documents": total_docs,
        "synced_documents": synced_docs,
        "error_documents": error_docs,
        "total_chunks": total_chunks,
        "categories": [
            {"category": c["category"], "count": c["count"]}
            for c in categories
        ],
        "last_sync": last_sync.isoformat() if last_sync else None,
    })
