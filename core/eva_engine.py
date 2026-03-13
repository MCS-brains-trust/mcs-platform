"""
Eva Finalisation Gate — Structured Compliance Review Engine

This module handles:
1. Pre-flight checks before Eva review can be triggered
2. Risk engine pre-run (deterministic checks run FIRST)
3. The 10 compliance checks with entity-type filtering
4. LLM-powered analysis for each check, with risk engine findings
   injected as CONFIRMED HARD FACTS
5. Finding creation and resolution workflow
6. Finding suppression (prevents resolved findings from re-appearing on re-run)

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


def _is_finding_suppressed(financial_year, rule_category, account_refs=None):
    """Check if a finding with this fingerprint has been suppressed."""
    from core.models import EvaFindingSuppression
    fingerprint = EvaFindingSuppression.generate_fingerprint(
        str(financial_year.entity_id),
        str(financial_year.pk),
        rule_category,
        account_refs or [],
    )
    return EvaFindingSuppression.objects.filter(
        financial_year=financial_year,
        fingerprint=fingerprint,
    ).exists()


def _is_finding_addressed(financial_year, finding_key):
    """Return True if a prior EvaFinding with this key was addressed/dismissed.

    Addressed findings must NOT reappear on re-review.  We look across ALL
    reviews for this financial year, checking for status=addressed or a
    clarification with outcome=dismissed.
    """
    if not finding_key:
        return False
    from core.models import EvaFinding
    return EvaFinding.objects.filter(
        eva_review__financial_year=financial_year,
        finding_key=finding_key,
        status__in=["addressed", "closed"],
    ).exists()


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
# TB Line ↔ EvaFinding Tagging Helpers
# ---------------------------------------------------------------------------

def tag_tb_lines_with_finding(financial_year, finding, account_codes):
    """Tag TrialBalanceLine rows by appending the finding's check_name to eva_flags.

    Called after each EvaFinding is saved during the Eva review task.

    Args:
        financial_year: FinancialYear instance
        finding: EvaFinding instance (must have check_name set)
        account_codes: list of account code strings Eva identified
    """
    if not account_codes:
        return
    from core.models import TrialBalanceLine

    check_name = finding.check_name
    lines = list(
        TrialBalanceLine.objects.filter(
            financial_year=financial_year,
            account_code__in=account_codes,
        )
    )
    updated = []
    for line in lines:
        flags = line.eva_flags or []
        if check_name not in flags:
            flags.append(check_name)
            line.eva_flags = flags
            updated.append(line)
    if updated:
        TrialBalanceLine.objects.bulk_update(updated, ["eva_flags"])


def untag_tb_lines_for_finding(financial_year, check_name):
    """Remove *check_name* from eva_flags on all TB lines for this FY.

    Called when a finding is addressed so the TB rows are no longer tagged.
    """
    from core.models import TrialBalanceLine

    lines = list(
        TrialBalanceLine.objects.filter(
            financial_year=financial_year,
            eva_flags__contains=check_name,
        )
    )
    updated = []
    for line in lines:
        flags = line.eva_flags or []
        if check_name in flags:
            flags.remove(check_name)
            line.eva_flags = flags
            updated.append(line)
    if updated:
        TrialBalanceLine.objects.bulk_update(updated, ["eva_flags"])


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
    # comparative_consistency: superseded by amber indicators (TrialBalanceLine variance flags).
    # Tier 1 variance analysis and amber indicators handle year-on-year movements.
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
    # going_concern: deliberately excluded. Directors address solvency via Solvency Resolution document.
    # The detection module (GoingConcernModule) is also disabled in registry.py.
    {
        "id": "tpar",
        "name": "Taxable Payments Annual Report (TPAR)",
        "description": "Check if entity is required to lodge TPAR and whether contractor payments are properly recorded",
        "entity_types": ["company", "trust_discretionary", "trust_unit", "trust_hybrid",
                         "sole_trader", "partnership"],
        "severity_default": "ADVISORY",
    },
    # thin_capitalisation: deferred. Add to roadmap when targeting large proprietary companies.
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
class _SyntheticFlag:
    """Lightweight adapter that mimics RiskFlag attributes for the LLM pipeline.

    Detection modules produce EvaFinding / assessment model records, not
    RiskFlag rows.  This adapter lets the existing
    ``_format_risk_flags_as_hard_facts`` and the fallback safety-net in
    ``_run_eva_review_background`` consume module results without changing
    the downstream contract.
    """

    def __init__(self, *, rule_id, severity, title, description,
                 recommended_action="", legislation_ref=""):
        self.rule_id = rule_id
        self.severity = severity
        self.title = title
        self.description = description
        self.recommended_action = recommended_action
        self.legislation_ref = legislation_ref


def _collect_module_flags(financial_year):
    """Query detection-module assessment models and convert their results
    into ``_SyntheticFlag`` objects keyed by Eva compliance-check ID.

    Returns a dict of check_id -> list[_SyntheticFlag].
    """
    from core.models import Div7AAssessment
    from decimal import Decimal

    ZERO = Decimal("0.00")
    flags = {}

    # ── Division 7A ──────────────────────────────────────────────────
    try:
        d7a = Div7AAssessment.objects.get(financial_year=financial_year)
        if d7a.overall_severity != "CLEAR":
            acct_lines = []
            for a in (d7a.direct_loan_accounts or []):
                acct_lines.append(
                    f"{a['account_code']} {a['account_name']}: "
                    f"${Decimal(a['balance']):,.2f} "
                    f"(PY: ${Decimal(a.get('py_balance', '0')):,.2f})"
                )
            acct_str = "; ".join(acct_lines) if acct_lines else "See assessment"

            desc_parts = [
                f"Total Div 7A exposure: ${d7a.total_exposure:,.2f}.",
            ]
            if d7a.direct_loan_balance > ZERO:
                desc_parts.append(f"Direct loans: ${d7a.direct_loan_balance:,.2f}.")
            if d7a.upe_exposure > ZERO:
                desc_parts.append(f"UPE exposure: ${d7a.upe_exposure:,.2f}.")
            if d7a.s109e_payments > ZERO:
                desc_parts.append(f"s 109E payments: ${d7a.s109e_payments:,.2f}.")
            desc_parts.append(f"Accounts: {acct_str}.")
            desc_parts.append(f"Complying agreement: {'Yes' if d7a.has_complying_agreement else 'No'}.")
            desc_parts.append(f"Interest compliant: {'Yes' if d7a.interest_compliant else 'No'}.")
            desc_parts.append(f"Rules fired: {', '.join(d7a.rules_fired)}.")

            remediation = []
            if not d7a.has_complying_agreement:
                remediation.append(
                    f"Execute a Div 7A complying loan agreement covering "
                    f"${d7a.total_exposure:,.2f} before lodgement day."
                )
            if not d7a.interest_compliant and d7a.expected_interest > ZERO:
                remediation.append(
                    f"Record benchmark interest of ${d7a.expected_interest:,.2f} "
                    f"as assessable income."
                )
            if d7a.expected_myr > ZERO and not d7a.myr_compliant:
                remediation.append(
                    f"Ensure MYR of ${d7a.expected_myr:,.2f} is paid before 30 June."
                )

            flags.setdefault("div7a", []).append(_SyntheticFlag(
                rule_id="MODULE:div7a",
                severity=d7a.overall_severity,
                title=f"Division 7A Exposure — ${d7a.total_exposure:,.2f}",
                description=" ".join(desc_parts),
                recommended_action=" ".join(remediation) if remediation else "Review Div 7A compliance.",
                legislation_ref="Division 7A ITAA 1936 (ss 109C-109Q)",
            ))
    except Div7AAssessment.DoesNotExist:
        pass
    except Exception as e:
        logger.warning("Failed to collect Div 7A module flags: %s", e)

    # going_concern: deliberately excluded. Directors address solvency via Solvency Resolution document.
    # GoingConcernAssessment model retained — used by GoingConcernModule (disabled in registry.py).

    # ── Section 100A (from EvaFinding created by module) ─────────────
    try:
        from core.models import EvaFinding
        s100a_findings = EvaFinding.objects.filter(
            eva_review__financial_year=financial_year,
            check_name="trust_distribution",
            source="risk_engine",
            status__in=["open", "reopened"],
        ).order_by("-created_at")[:1]
        for f in s100a_findings:
            flags.setdefault("trust_distribution", []).append(_SyntheticFlag(
                rule_id="MODULE:section100a",
                severity=f.severity.upper() if f.severity else "ADVISORY",
                title=f.title or "Section 100A Risk",
                description=f.plain_english_explanation or "",
                recommended_action=f.recommendation or "",
                legislation_ref=f.legislation_reference or "s 100A ITAA 1936",
            ))
    except Exception as e:
        logger.warning("Failed to collect Section 100A module flags: %s", e)

    # ── SGC / Super Guarantee (from EvaFinding created by module) ────
    try:
        from core.models import EvaFinding
        sgc_findings = EvaFinding.objects.filter(
            eva_review__financial_year=financial_year,
            check_name="super_guarantee",
            source="risk_engine",
            status__in=["open", "reopened"],
        ).order_by("-created_at")[:1]
        for f in sgc_findings:
            flags.setdefault("super_guarantee", []).append(_SyntheticFlag(
                rule_id="MODULE:cluster_sgc",
                severity=f.severity.upper() if f.severity else "ADVISORY",
                title=f.title or "Superannuation Guarantee Shortfall",
                description=f.plain_english_explanation or "",
                recommended_action=f.recommendation or "",
                legislation_ref=f.legislation_reference or "SG Act 1992",
            ))
    except Exception as e:
        logger.warning("Failed to collect SGC module flags: %s", e)

    # ── TPAR (from EvaFinding created by module) ─────────────────────
    try:
        from core.models import EvaFinding
        tpar_findings = EvaFinding.objects.filter(
            eva_review__financial_year=financial_year,
            check_name="tpar",
            source="risk_engine",
            status__in=["open", "reopened"],
        ).order_by("-created_at")[:1]
        for f in tpar_findings:
            flags.setdefault("tpar", []).append(_SyntheticFlag(
                rule_id="MODULE:cluster_tpar",
                severity=f.severity.upper() if f.severity else "ADVISORY",
                title=f.title or "TPAR Obligation",
                description=f.plain_english_explanation or "",
                recommended_action=f.recommendation or "",
                legislation_ref=f.legislation_reference or "TAA 1953 Sch 1 s 396-55",
            ))
    except Exception as e:
        logger.warning("Failed to collect TPAR module flags: %s", e)

    # ── Related Party (from EvaFinding created by module) ────────────
    try:
        from core.models import EvaFinding
        rp_findings = EvaFinding.objects.filter(
            eva_review__financial_year=financial_year,
            check_name="related_party",
            source="risk_engine",
            status__in=["open", "reopened"],
        ).order_by("-created_at")[:1]
        for f in rp_findings:
            flags.setdefault("related_party", []).append(_SyntheticFlag(
                rule_id="MODULE:cluster_rp",
                severity=f.severity.upper() if f.severity else "ADVISORY",
                title=f.title or "Related Party Transactions",
                description=f.plain_english_explanation or "",
                recommended_action=f.recommendation or "",
                legislation_ref=f.legislation_reference or "AASB 124",
            ))
    except Exception as e:
        logger.warning("Failed to collect Related Party module flags: %s", e)

    return flags


def _run_risk_engine_precheck(financial_year):
    """
    Run the risk engine's deterministic Tier 1 + Tier 2 checks BEFORE
    the LLM review. Returns a dict of check_id -> list of risk flags
    (real RiskFlag objects + synthetic flags from detection modules).

    These findings are CONFIRMED HARD FACTS that the LLM cannot override.
    """
    from core.risk_engine import run_risk_engine, _load_trial_balance, _check_div7a_loans
    from core.models import RiskFlag

    # Run the full risk engine (Tier 1 + Tier 2 + detection modules)
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

    # Initialise check_flags with active Eva compliance check IDs (10 checks)
    check_flags = {
        "div7a": [],
        "gst_reconciliation": [],
        "related_party": [],
        "smsf_compliance": [],
        "trust_distribution": [],
        "depreciation_review": [],
        "tb_integrity": [],
        "super_guarantee": [],
        "ato_benchmarks": [],
        "tpar": [],
    }

    for flag in open_flags:
        rule_id = flag.rule_id or ""
        title_lower = (flag.title or "").lower()
        desc_lower = (flag.description or "").lower()

        # Division 7A flags
        if "div7a" in rule_id.lower() or "division 7a" in title_lower or "division 7a" in desc_lower:
            check_flags["div7a"].append(flag)
        elif any(kw in title_lower for kw in ("loan", "director", "shareholder", "advance")):
            check_flags["div7a"].append(flag)

        # GST flags
        if "gst" in rule_id.lower() or "gst" in title_lower:
            check_flags["gst_reconciliation"].append(flag)

        # Related party / management fee flags
        if any(kw in title_lower for kw in ("related party", "management fee", "intercompany")):
            check_flags["related_party"].append(flag)

        # Superannuation flags → super_guarantee (fixed: was going to related_party)
        if any(kw in title_lower for kw in ("super", "sgc", "sg rate")):
            check_flags["super_guarantee"].append(flag)

        # TPAR / contractor flags
        if any(kw in title_lower for kw in ("tpar", "contractor", "subcontractor")):
            check_flags["tpar"].append(flag)

        # Revenue/expense benchmark flags
        if "benchmark" in rule_id.lower() or "benchmark" in title_lower:
            check_flags["ato_benchmarks"].append(flag)

        # Balance sign / TB integrity flags
        if "balance sign" in title_lower:
            check_flags["tb_integrity"].append(flag)

    # ── Collect detection module assessment results as synthetic flags ──
    # Modules create EvaFinding/assessment records, not RiskFlag rows.
    # This bridge converts them into flag-like objects so the existing
    # LLM pipeline gets hard facts injected and the fallback safety net works.
    try:
        module_flags = _collect_module_flags(financial_year)
        for check_id, synth_flags in module_flags.items():
            check_flags.setdefault(check_id, []).extend(synth_flags)
            logger.info(
                "Module flags for %s: %d synthetic flag(s) injected",
                check_id, len(synth_flags),
            )
    except Exception as e:
        logger.error("Failed to collect module flags: %s", e)

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
        # Upgraded: inject Div7AAssessment data as confirmed hard facts
        from core.models import Div7AAssessment
        try:
            assessment = Div7AAssessment.objects.get(financial_year=fy)
            extra.append("=== DIV 7A ASSESSMENT (CONFIRMED — DETERMINISTIC) ===")
            extra.append(f"  Overall Severity: {assessment.overall_severity}")
            extra.append(f"  Total Exposure: ${assessment.total_exposure:,.2f}")
            extra.append(f"  Direct Loan Balance: ${assessment.direct_loan_balance:,.2f}")
            extra.append(f"  UPE Exposure: ${assessment.upe_exposure:,.2f}")
            extra.append(f"  s 109E Payments: ${assessment.s109e_payments:,.2f}")
            extra.append(f"  Complying Agreement: {'Yes' if assessment.has_complying_agreement else 'No'}")
            extra.append(f"  Agreement Covers Balance: {'Yes' if assessment.agreement_covers_balance else 'No'}")
            extra.append(f"  Interest Compliant: {'Yes' if assessment.interest_compliant else 'No'}")
            extra.append(f"  Expected Interest: ${assessment.expected_interest:,.2f}")
            extra.append(f"  Recorded Interest: ${assessment.recorded_interest:,.2f}")
            if assessment.expected_myr > ZERO:
                extra.append(f"  Expected MYR: ${assessment.expected_myr:,.2f}")
                if assessment.actual_repayments is not None:
                    extra.append(f"  Actual Repayments: ${assessment.actual_repayments:,.2f}")
                    extra.append(f"  MYR Compliant: {'Yes' if assessment.myr_compliant else 'No'}")
            extra.append(f"  Escalation Required: {'Yes' if assessment.escalation_required else 'No'}")
            extra.append(f"  Rules Fired: {', '.join(assessment.rules_fired)}")
            if assessment.direct_loan_accounts:
                extra.append("  --- Loan Accounts ---")
                for acct in assessment.direct_loan_accounts:
                    extra.append(
                        f"    {acct['account_code']} {acct['account_name']}: "
                        f"${Decimal(acct['balance']):,.2f} (PY: ${Decimal(acct.get('py_balance', '0')):,.2f})"
                    )
            if assessment.upe_details:
                extra.append("  --- UPE Details ---")
                for upe in assessment.upe_details:
                    extra.append(
                        f"    {upe['trust_name']}: ${Decimal(upe['upe_amount']):,.2f} ({upe['regime']})"
                    )
        except Div7AAssessment.DoesNotExist:
            pass

        # Also include raw loan account data for LLM context
        # NOTE: Annotate each account with its Div 7A risk status based on
        # net balance.  Only DEBIT net balances trigger Division 7A — zero
        # and credit balances are explicitly marked as NO risk so the LLM
        # does not raise false positives for cleared or credit-balance loans.
        extra.append("=== LOAN & RELATED PARTY ACCOUNTS (EFFECTIVE BALANCES) ===")
        extra.append("  NOTE: Only accounts with DEBIT net balance trigger Division 7A.")
        found_any = False
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in LOAN_KEYWORDS):
                net = line.effective_dr - line.effective_cr
                if net > ZERO:
                    balance_type = "DEBIT (loan TO shareholder — POTENTIAL DIV 7A)"
                elif net == ZERO:
                    balance_type = "ZERO (account cleared — NO Div 7A risk)"
                else:
                    balance_type = "CREDIT (company OWES the person — NO Div 7A risk)"
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

    # going_concern: deliberately excluded. Directors address solvency via Solvency Resolution document.

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

    elif check_id == "gst_reconciliation":
        # GST accounts with pre-computed net movements to prevent LLM doubling
        from core.models import BASPeriod
        gst_kw = ["gst", "goods and services tax", "bas", "input tax", "output tax"]
        extra.append("=== GST ACCOUNTS (EFFECTIVE BALANCES — NET MOVEMENTS) ===")
        total_gst_collected = ZERO
        total_input_credits = ZERO
        found_any = False
        for line in tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in gst_kw):
                cy_net = line.effective_dr - line.effective_cr
                py_net = (line.prior_debit or ZERO) - (line.prior_credit or ZERO)
                net_movement = cy_net - py_net
                if py_net != ZERO:
                    movement_pct = (net_movement / abs(py_net) * 100)
                    pct_str = f" ({'+' if movement_pct >= 0 else ''}{movement_pct:.1f}%)"
                else:
                    pct_str = ""
                extra.append(
                    f"  Account {line.account_code} {line.account_name}: "
                    f"CY Net ${cy_net:,.2f}, PY Net ${py_net:,.2f}, "
                    f"Movement ${net_movement:,.2f}{pct_str}"
                )
                found_any = True
                # Classify for totals
                if any(kw in name_lower for kw in ["gst collected", "gst on sales", "output tax"]):
                    total_gst_collected += abs(cy_net)
                elif any(kw in name_lower for kw in ["gst paid", "gst on purchases", "input tax"]):
                    total_input_credits += abs(cy_net)
        if not found_any:
            extra.append("  No GST accounts found in TB.")
        else:
            extra.append(f"\n  Total GST Collected: ${total_gst_collected:,.2f}")
            extra.append(f"  Total Input Tax Credits: ${total_input_credits:,.2f}")
            net_gst = total_gst_collected - total_input_credits
            extra.append(f"  Net GST Position: ${net_gst:,.2f}")
        extra.append(
            "  IMPORTANT: Use the Net Movement figures above. "
            "Do NOT sum raw debit/credit columns."
        )

        # BAS lodgement snapshot data if available
        bas_periods = BASPeriod.objects.filter(
            financial_year=fy, status="lodged"
        ).order_by("period_number")
        if bas_periods.exists():
            extra.append("\n=== BAS LODGEMENT SNAPSHOTS ===")
            for bp in bas_periods:
                label = f"Period {bp.period_number} ({bp.period_start} to {bp.period_end})"
                s1a = bp.snapshot_1a if bp.snapshot_1a is not None else "N/A"
                s1b = bp.snapshot_1b if bp.snapshot_1b is not None else "N/A"
                s_net = bp.snapshot_net if bp.snapshot_net is not None else "N/A"
                extra.append(
                    f"  {label}: 1A (GST on Sales) ${s1a}, "
                    f"1B (GST on Purchases) ${s1b}, Net ${s_net} — {bp.status}"
                )

    # comparative_consistency: superseded by amber indicators (TrialBalanceLine variance flags).

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
  Examples: Super guarantee compliance depends on employee headcount not in TB;
  related party assessment depends on external ownership records.

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
  "cross_references": ["check_id_1", "check_id_2"],
  "affected_account_codes": ["1-1100", "2-1200"]
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
- affected_account_codes: list of account codes (e.g. "2-1200") that this
  finding relates to. Extract these from the trial balance data provided.
  Empty list if no specific accounts are involved.

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

        # Status stays as in_review — no status change needed when Eva is triggered

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
        checks_with_errors = []  # Track checks where LLM/parse failed

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

                # Check suppression before creating finding
                _account_refs = result.get("affected_account_codes", []) or []
                if _is_finding_suppressed(fy, check_def["id"], _account_refs):
                    print(f"[Eva] SUPPRESSED finding for {check_def['id']} — skipping creation", flush=True)
                else:
                    # Build a deterministic finding_key for cross-review dedup
                    _finding_key = EvaFinding.build_finding_key(
                        check_def["id"], account_codes=_account_refs,
                    )

                    # Skip if this finding was previously addressed
                    if _is_finding_addressed(fy, _finding_key):
                        print(f"[Eva] ADDRESSED finding for {check_def['id']} (key={_finding_key}) — skipping", flush=True)
                    else:
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
                                finding_key=_finding_key,
                                status="reopened" if prior_finding_link else "open",
                            )
                            # Store cross-references for post-processing
                            cross_refs = result.get("cross_references", []) or []
                            if cross_refs:
                                finding._cross_ref_check_ids = cross_refs
                            # Tag affected TB lines with this finding's check_name
                            affected_codes = _account_refs
                            if affected_codes:
                                try:
                                    tag_tb_lines_with_finding(fy, finding, affected_codes)
                                except Exception as tag_err:
                                    print(f"[Eva] WARNING: failed to tag TB lines for {check_def['id']}: {tag_err}", flush=True)
                            findings_created += 1
                            _eva_review_tasks[task_key]["findings_count"] = findings_created
                            review.raw_response = {"progress": _eva_review_tasks[task_key]}
                            review.save(update_fields=["raw_response"])
                            print(f"[Eva] Finding created for {check_def['id']}: {(result.get('title', '')[:60])}", flush=True)
                        except Exception as save_err:
                            print(f"[Eva] EXCEPTION saving finding for {check_def['id']}: {save_err}", flush=True)
                            traceback.print_exc()
                            checks_with_errors.append({
                                "check_id": check_def["id"],
                                "error": f"Finding save failed: {save_err}",
                            })

            # Track checks that returned errors (LLM parse failure, API error, etc.)
            elif result and result.get("error") and not result.get("has_finding"):
                error_msg = result.get("error", "unknown")
                checks_with_errors.append({"check_id": check_def["id"], "error": error_msg})
                print(f"[Eva] CHECK ERROR for {check_def['id']}: {error_msg} (no risk flags to fall back on)", flush=True)

            # If the LLM didn't raise a finding but there ARE confirmed hard facts,
            # create findings directly from the risk engine flags
            if relevant_flags and not (result and result.get("has_finding")):
                print(f"[Eva] LLM missed hard facts for {check_def['id']}, creating from risk engine", flush=True)
                for flag in relevant_flags:
                    # Normalise severity from risk engine flags
                    flag_severity = (flag.severity or "advisory").lower()
                    if flag_severity not in ("critical", "advisory"):
                        flag_severity = "advisory"
                    # Extract account codes for suppression fingerprint
                    flag_codes = [
                        a["account_code"] for a in (flag.affected_accounts or [])
                        if isinstance(a, dict) and a.get("account_code")
                    ]
                    # Check suppression before creating fallback finding
                    if _is_finding_suppressed(fy, check_def["id"], flag_codes):
                        print(f"[Eva] SUPPRESSED fallback finding for {check_def['id']} — skipping", flush=True)
                        continue
                    # Build finding_key for fallback findings too
                    _fb_finding_key = EvaFinding.build_finding_key(
                        check_def["id"], account_codes=flag_codes,
                    )
                    if _is_finding_addressed(fy, _fb_finding_key):
                        print(f"[Eva] ADDRESSED fallback finding for {check_def['id']} (key={_fb_finding_key}) — skipping", flush=True)
                        continue
                    try:
                        fallback_finding = EvaFinding.objects.create(
                            eva_review=review,
                            check_name=check_def["id"][:50],
                            severity=flag_severity,
                            title=(flag.title or "")[:255],
                            plain_english_explanation=flag.description or "",
                            recommendation=flag.recommended_action or "",
                            legislation_reference=(flag.legislation_ref or "")[:255],
                            knowledge_brain_citation="",
                            confidence="high",
                            finding_key=_fb_finding_key,
                            status="open",
                        )
                        if flag_codes:
                            try:
                                tag_tb_lines_with_finding(fy, fallback_finding, flag_codes)
                            except Exception as tag_err:
                                print(f"[Eva] WARNING: failed to tag TB lines for fallback {check_def['id']}: {tag_err}", flush=True)
                        findings_created += 1
                        _eva_review_tasks[task_key]["findings_count"] = findings_created
                        review.raw_response = {"progress": _eva_review_tasks[task_key]}
                        review.save(update_fields=["raw_response"])
                    except Exception as save_err:
                        print(f"[Eva] EXCEPTION saving risk flag finding for {check_def['id']}: {save_err}", flush=True)
                        traceback.print_exc()
                        checks_with_errors.append({
                            "check_id": check_def["id"],
                            "error": f"Risk flag finding save failed: {save_err}",
                        })

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
        print(f"[Eva] All checks complete. Findings created: {findings_created}. Errors: {len(checks_with_errors)}. Saving review...", flush=True)
        duration = time.time() - start_time

        # Determine review outcome — FY stays in_review regardless.
        # The accountant must click Finalise manually once all findings are resolved.
        if checks_with_errors and findings_created == 0:
            review.status = "error"
            review.error_message = (
                f"{len(checks_with_errors)} check(s) failed without producing findings: "
                + "; ".join(e["check_id"] for e in checks_with_errors[:5])
            )
            logger.warning(
                "Eva review for FY %s completed with errors in %d check(s) and 0 findings — "
                "marking review as error.",
                fy.pk, len(checks_with_errors),
            )
        elif findings_created > 0:
            review.status = "findings_raised"
        else:
            review.status = "cleared"

        review.completed_at = timezone.now()
        review.duration_seconds = duration
        review.save(update_fields=["status", "completed_at", "duration_seconds"])
        print(f"[Eva] Review saved with status={review.status}, duration={duration:.1f}s", flush=True)

        # Log activity
        if review.status == "error":
            _activity_title = f"Eva Review Error"
            _activity_desc = (
                f"Eva compliance review for {fy.entity.entity_name} ({fy.year_label}) "
                f"completed with errors in {duration:.1f}s. "
                f"{review.error_message}"
            )
        elif findings_created == 0:
            _activity_title = "Eva Review Complete — No findings"
            _activity_desc = (
                f"Eva compliance review for {fy.entity.entity_name} ({fy.year_label}) "
                f"completed in {duration:.1f}s. "
                f"Eva found no compliance issues."
            )
        else:
            # Collect categories of findings
            finding_checks = list(
                review.findings.values_list('check_name', flat=True).distinct()
            )
            _activity_title = f"Eva Review Complete — {findings_created} finding(s) raised"
            _activity_desc = (
                f"Eva compliance review for {fy.entity.entity_name} ({fy.year_label}) "
                f"completed in {duration:.1f}s. "
                f"Risk engine found {total_risk_flags} confirmed issue(s). "
                f"{findings_created} finding(s) require attention. "
                f"Categories: {', '.join(finding_checks)}."
            )
        ActivityLog.objects.create(
            user=user,
            event_type="eva_review_complete",
            title=_activity_title,
            description=_activity_desc,
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

        # Try to update the review record — FY stays in_review
        try:
            review.status = "error"
            review.error_message = str(e)[:1000]
            review.completed_at = timezone.now()
            review.duration_seconds = duration
            review.save(update_fields=["status", "error_message", "completed_at", "duration_seconds"])

            ActivityLog.objects.create(
                user=user,
                event_type="eva_review_complete",
                title="Eva Review Error",
                description=f"Eva review failed for {fy.entity.entity_name} ({fy.year_label}): {str(e)[:500]}",
                entity=fy.entity,
                financial_year=fy,
                url=f"/entities/years/{fy.pk}/",
            )
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

    # If the review is still pending (running), check for staleness
    if review.status == "pending":
        from django.utils import timezone as tz
        import datetime

        age = tz.now() - review.triggered_at
        stale_threshold = datetime.timedelta(minutes=10)

        if age > stale_threshold:
            # Auto-recover: the background thread likely died
            findings_count = review.findings.count()
            progress = (review.raw_response or {}).get("progress", {})
            last_check = progress.get("current_check", "unknown")

            if findings_count > 0:
                # Partial results exist — save them
                review.status = "findings_raised"
                review.error_message = (
                    f"Review thread terminated unexpectedly during '{last_check}' check "
                    f"after {age.seconds // 60}m. {findings_count} partial finding(s) preserved."
                )
            else:
                review.status = "error"
                review.error_message = (
                    f"Review thread terminated unexpectedly during '{last_check}' check "
                    f"after {age.seconds // 60}m. No findings were created. Please re-run."
                )
            review.completed_at = tz.now()
            review.save(update_fields=["status", "completed_at", "error_message"])

            # FY stays in_review — no status reset needed in new workflow

            # Clean up in-memory task state if present
            task_key_cleanup = str(pk)
            _eva_review_tasks.pop(task_key_cleanup, None)

            logger.warning(
                f"Auto-recovered stale Eva review {review.pk} for {fy}. "
                f"Was stuck on '{last_check}' for {age.seconds // 60}m. "
                f"Findings preserved: {findings_count}."
            )
            # Fall through to the completed response below
        else:
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
    # Order findings by severity (critical first), then status (open first), then check_name
    from django.db.models import Case, When, Value, IntegerField
    severity_order = Case(
        When(severity="critical", then=Value(0)),
        When(severity="advisory", then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    status_order = Case(
        When(status="open", then=Value(0)),
        When(status="reopened", then=Value(1)),
        When(status="addressed", then=Value(2)),
        When(status="closed", then=Value(3)),
        default=Value(4),
        output_field=IntegerField(),
    )
    ordered_findings = (
        review.findings
        .select_related("resolved_by")
        .prefetch_related("related_findings", "clarifications__answered_by")
        .exclude(status__in=["addressed", "closed"])
        .annotate(_sev_order=severity_order, _status_order=status_order)
        .order_by("_sev_order", "_status_order", "check_name")
    )
    for f in ordered_findings:
        # Build related findings list
        related = [
            {"id": str(rf.pk), "check_name": rf.check_name, "title": rf.title}
            for rf in f.related_findings.all()
        ]
        # Build clarifications list
        clarifications = [
            {
                "id": str(c.pk),
                "question_id": c.question_id,
                "answer_value": c.answer_value,
                "answer_label": c.answer_label,
                "answer_detail": c.answer_detail,
                "outcome": c.outcome,
                "outcome_message": c.outcome_message,
                "answered_by": c.answered_by.get_full_name() if c.answered_by else "Unknown",
                "answered_at": c.answered_at.isoformat(),
            }
            for c in f.clarifications.all()
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
            "clarifications": clarifications,
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
        "can_finalise": fy.can_finalise,
        "can_assemble_package": fy.can_assemble_package,
    })


@login_required
@require_POST
def eva_finding_resolve(request, pk):
    """
    Mark an Eva finding as addressed — atomically.

    POST /api/eva-findings/<pk>/resolve/
    Body: {"resolution_note": "..."}

    Inside a single transaction.atomic() block this endpoint:
      a) Sets the finding status to ADDRESSED with resolution metadata.
      b) Creates an ActivityLog with EVA_FINDING_ADDRESSED event type.
      c) Removes the finding's check_name from eva_flags on all
         TrialBalanceLine rows for the same financial year.
    """
    from django.db import transaction
    from core.models import EvaFinding, ActivityLog

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

    review = finding.eva_review
    fy = review.financial_year

    try:
        with transaction.atomic():
            # (a) Mark the finding as addressed
            finding.status = "addressed"
            finding.resolution_note = resolution_note
            finding.resolved_by = request.user
            finding.resolved_at = timezone.now()
            finding.save(update_fields=[
                "status", "resolution_note", "resolved_by", "resolved_at"
            ])

            # (b) Create suppression record so this finding isn't re-raised on re-run
            from core.models import EvaFindingSuppression
            fingerprint = EvaFindingSuppression.generate_fingerprint(
                str(fy.entity_id), str(fy.pk), finding.check_name, [],
            )
            suppression, _ = EvaFindingSuppression.objects.get_or_create(
                financial_year=fy,
                fingerprint=fingerprint,
                defaults={
                    "rule_category": finding.check_name,
                    "suppressed_by": request.user,
                    "accountant_note": resolution_note,
                },
            )

            # (c) Create ActivityLog with finding FK and full resolution note
            ActivityLog.objects.create(
                user=request.user,
                event_type=ActivityLog.EventType.EVA_FINDING_ADDRESSED,
                title=f"Eva Finding Addressed — {finding.title or finding.check_name}",
                description=(
                    f"[{finding.severity.upper()}] "
                    f"'{finding.title or finding.check_name}' addressed for "
                    f"{fy.entity.entity_name} ({fy.year_label}).\n"
                    f"Resolution: {resolution_note}"
                ),
                entity=fy.entity,
                financial_year=fy,
                eva_finding=finding,
                metadata={
                    "check_name": finding.check_name,
                    "severity": finding.severity,
                    "suppression_id": str(suppression.pk),
                },
                url=f"/entities/years/{fy.pk}/",
            )

            # (d) Remove check_name from eva_flags on tagged TB lines
            untag_tb_lines_for_finding(fy, finding.check_name)

    except Exception as exc:
        logger.exception(
            "Failed to resolve Eva finding (finding=%s): %s", pk, exc
        )
        return JsonResponse(
            {"error": f"Failed to save resolution: {exc}"},
            status=500,
        )

    # Check if all findings are now addressed (outside the atomic block
    # so we read committed state)
    open_findings = review.findings.filter(status="open").count()

    if open_findings == 0:
        # All findings addressed — mark review as cleared
        # FY stays in_review — accountant clicks Finalise manually
        review.status = "cleared"
        review.save(update_fields=["status"])

        ActivityLog.objects.create(
            user=request.user,
            event_type=ActivityLog.EventType.EVA_REVIEW_CLEARED,
            title=f"Eva Cleared — {fy.entity.entity_name}",
            description=(
                f"All Eva findings addressed for {fy.entity.entity_name} ({fy.year_label}). "
                f"Financial year is ready for finalisation."
            ),
            entity=fy.entity,
            financial_year=fy,
            url=f"/entities/years/{fy.pk}/",
        )

    return JsonResponse({
        "status": "ok",
        "finding_status": "addressed",
        "open_findings_remaining": open_findings,
        "review_status": review.status,
        "fy_status": fy.status,
        "can_finalise": fy.can_finalise,
        "can_assemble_package": fy.can_assemble_package,
    })


@login_required
@require_POST
def eva_auto_disclose_rp(request, pk):
    """
    Auto-disclose Related Party Transactions in workpaper notes.

    POST /api/eva-findings/<pk>/auto-disclose-rp/

    Reads the finding's explanation to extract RP details, creates or
    updates a WorkpaperNote with AASB 124 format disclosure text, and
    marks the finding as addressed.
    """
    from core.models import EvaFinding, WorkpaperNote, ActivityLog

    try:
        finding = EvaFinding.objects.select_related(
            "eva_review__financial_year__entity"
        ).get(pk=pk)
    except EvaFinding.DoesNotExist:
        return JsonResponse({"error": "Finding not found"}, status=404)

    if finding.check_name != "related_party":
        return JsonResponse(
            {"error": "Auto-disclosure is only available for Related Party findings"},
            status=400,
        )

    fy = finding.eva_review.financial_year
    entity = fy.entity

    # Build AASB 124 format disclosure text from the finding
    explanation = finding.plain_english_explanation or finding.title or ""
    entity_name = entity.entity_name
    year_label = fy.year_label

    disclosure_lines = [
        f"Related Party Transactions — {entity_name} ({year_label})",
        "",
        "Disclosure prepared in accordance with AASB 124 Related Party Disclosures.",
        "",
        "Key management personnel (KMP) and related parties of the entity have "
        "transacted with the entity during the financial year as follows:",
        "",
    ]

    # Extract account details from finding explanation
    disclosure_lines.append(explanation)
    disclosure_lines.append("")
    disclosure_lines.append(
        "All related party transactions were conducted on normal commercial "
        "terms and conditions unless otherwise stated."
    )
    disclosure_lines.append("")
    disclosure_lines.append(
        "Amounts receivable from and payable to related parties at "
        "reporting date are shown in the financial statements."
    )

    disclosure_text = "\n".join(disclosure_lines)

    # Create or update the WorkpaperNote for "Related Party Transactions"
    rp_note, created = WorkpaperNote.objects.update_or_create(
        financial_year=fy,
        account_code="RP-DISCLOSURE",
        note_type="preparer",
        defaults={
            "account_name": "Related Party Transactions (AASB 124)",
            "content": disclosure_text,
            "status": "completed",
            "author": request.user,
        },
    )

    # Mark the finding as addressed
    finding.status = "addressed"
    finding.resolution_note = (
        f"Auto-disclosed in workpaper notes (AASB 124 format). "
        f"Note ID: {rp_note.pk}"
    )
    finding.resolved_by = request.user
    finding.resolved_at = timezone.now()
    finding.save(update_fields=[
        "status", "resolution_note", "resolved_by", "resolved_at"
    ])

    # Create suppression record
    from core.models import EvaFindingSuppression
    fingerprint = EvaFindingSuppression.generate_fingerprint(
        str(fy.entity_id), str(fy.pk), finding.check_name, [],
    )
    EvaFindingSuppression.objects.get_or_create(
        financial_year=fy,
        fingerprint=fingerprint,
        defaults={
            "rule_category": finding.check_name,
            "suppressed_by": request.user,
            "accountant_note": f"Auto-disclosed in workpaper notes (AASB 124 format).",
        },
    )

    # Check if all findings are now addressed
    review = finding.eva_review
    open_findings = review.findings.filter(status="open").count()

    if open_findings == 0:
        review.status = "cleared"
        review.save(update_fields=["status"])
        # FY stays in_review — accountant clicks Finalise manually

    # Log
    ActivityLog.objects.create(
        user=request.user,
        event_type=ActivityLog.EventType.EVA_FINDING_ADDRESSED,
        title=f"RP Auto-Disclosed — {entity_name}",
        description=(
            f"Related Party finding auto-disclosed for {entity_name} ({year_label}). "
            f"AASB 124 disclosure note {'created' if created else 'updated'}."
        ),
        entity=entity,
        financial_year=fy,
        url=f"/entities/years/{fy.pk}/",
    )

    return JsonResponse({
        "status": "ok",
        "finding_status": "addressed",
        "note_created": created,
        "note_id": str(rp_note.pk),
        "open_findings_remaining": open_findings,
        "review_status": review.status,
        "fy_status": fy.status,
        "can_finalise": fy.can_finalise,
        "can_assemble_package": fy.can_assemble_package,
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


# ===========================================================================
# Eva Clarification Endpoint
# ===========================================================================

@login_required
@require_POST
def eva_clarify_finding(request, pk):
    """
    Submit a clarification answer for an Eva finding.
    POST /api/eva-findings/<pk>/clarify/

    Body (JSON):
    {
        "answer_value": "related_company",
        "answer_detail": "Optional free-text note"
    }

    Returns:
    {
        "status": "ok",
        "outcome": "dismissed" | "confirmed" | "reduced" | "pending",
        "outcome_message": "...",
        "new_severity": "critical" | "advisory",
        "new_status": "open" | "addressed",
        "new_confidence": "high" | "medium" | "low",
        "should_clear_review": bool
    }
    """
    from core.models import EvaFinding, EvaClarification, ActivityLog
    from core.eva_service import CLARIFICATION_QUESTIONS, _reevaluate_finding

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

    answer_value = body.get("answer_value", "").strip()
    answer_detail = body.get("answer_detail", "").strip()

    if not answer_value:
        return JsonResponse({"error": "answer_value is required"}, status=400)

    # Look up the question definition for this check_name
    check_defn = CLARIFICATION_QUESTIONS.get(finding.check_name)
    if not check_defn:
        return JsonResponse(
            {"error": f"No clarification questions defined for check '{finding.check_name}'"},
            status=400,
        )

    # Find the matching option
    option = next(
        (o for o in check_defn["options"] if o["value"] == answer_value),
        None,
    )
    if not option:
        return JsonResponse(
            {"error": f"Unknown answer value '{answer_value}' for check '{finding.check_name}'"},
            status=400,
        )

    # Extract the account name from the finding title for the question text
    # (best-effort: use the finding title, or fall back to check_name)
    account_name = finding.title or finding.check_name

    # Build the question text (with account_name substituted)
    question_text = check_defn["question_text"].replace(
        "{account_name}", account_name
    )

    # Create the clarification record
    clarification = EvaClarification.objects.create(
        finding=finding,
        question_id=check_defn["question_id"],
        question_text=question_text,
        answer_value=answer_value,
        answer_label=option["label"],
        answer_detail=answer_detail,
        outcome_hint=option["outcome_hint"],
        outcome_message=option["outcome_message"],
        learning_note=option["learning_note"],
        answered_by=request.user,
        outcome="pending",  # will be updated by _reevaluate_finding
    )

    # Re-evaluate the finding based on the answer
    result = _reevaluate_finding(finding, clarification)

    fy = finding.eva_review.financial_year
    entity = fy.entity

    # If all findings are now addressed, clear the review
    # FY stays in_review — accountant clicks Finalise manually
    if result["should_clear_review"]:
        review = finding.eva_review
        review.status = "cleared"
        review.save(update_fields=["status"])
        ActivityLog.objects.create(
            user=request.user,
            event_type="eva_review_cleared",
            title=f"Eva Cleared — {entity.entity_name}",
            description=(
                f"All Eva findings addressed for {entity.entity_name} ({fy.year_label}). "
                f"Financial year is ready for finalisation."
            ),
            entity=entity,
            financial_year=fy,
            url=f"/entities/years/{fy.pk}/",
        )

    # Log the clarification
    ActivityLog.objects.create(
        user=request.user,
        event_type="eva_clarification",
        title=f"Eva Clarification — {finding.title or finding.check_name}",
        description=(
            f"Clarification submitted for finding '{finding.title or finding.check_name}' "
            f"({entity.entity_name}, {fy.year_label}). "
            f"Answer: {option['label']}. Outcome: {result['outcome']}."
        ),
        entity=entity,
        financial_year=fy,
        eva_finding=finding,
        url=f"/years/{fy.pk}/",
    )

    # If the finding was dismissed or addressed via clarification, log it as a finding addressed event
    if result["outcome"] in ("dismissed", "addressed") or result["new_status"] == "addressed":
        ActivityLog.objects.create(
            user=request.user,
            event_type=ActivityLog.EventType.EVA_FINDING_ADDRESSED,
            title=f"Eva Finding Dismissed — {finding.title or finding.check_name}",
            description=(
                f"{request.user.get_full_name() or request.user.email} dismissed Eva finding "
                f"'{finding.title or finding.check_name}' for {entity.entity_name} ({fy.year_label}). "
                f"Reason: {option['label']} — {option['outcome_message']}"
            ),
            entity=entity,
            financial_year=fy,
            eva_finding=finding,
            metadata={"check_name": finding.check_name, "severity": finding.severity, "outcome": result["outcome"]},
            url=f"/years/{fy.pk}/",
        )

    return JsonResponse({
        "status": "ok",
        "outcome": result["outcome"],
        "outcome_message": result["outcome_message"],
        "new_severity": result["new_severity"],
        "new_status": result["new_status"],
        "new_confidence": result["new_confidence"],
        "should_clear_review": result["should_clear_review"],
    })


@login_required
@require_GET
def eva_clarification_question(request, pk):
    """
    Get the clarification question for a finding.
    GET /api/eva-findings/<pk>/clarify/

    Returns:
    {
        "has_question": bool,
        "question_id": str,
        "question_text": str,
        "options": [...],
        "existing_clarifications": [...]
    }
    """
    from core.models import EvaFinding, EvaClarification
    from core.eva_service import get_clarification_question

    try:
        finding = EvaFinding.objects.prefetch_related("clarifications__answered_by").get(pk=pk)
    except EvaFinding.DoesNotExist:
        return JsonResponse({"error": "Finding not found"}, status=404)

    question = get_clarification_question(finding.check_name)

    # Fetch existing clarifications for this finding
    existing = []
    for c in finding.clarifications.all():
        existing.append({
            "id": str(c.pk),
            "question_id": c.question_id,
            "answer_value": c.answer_value,
            "answer_label": c.answer_label,
            "answer_detail": c.answer_detail,
            "outcome": c.outcome,
            "outcome_message": c.outcome_message,
            "answered_by": c.answered_by.get_full_name() if c.answered_by else "Unknown",
            "answered_at": c.answered_at.isoformat(),
        })

    return JsonResponse({
        "has_question": question is not None,
        "question": question,
        "existing_clarifications": existing,
    })


# ---------------------------------------------------------------------------
# Financial Year Activity Feed
# ---------------------------------------------------------------------------

@login_required
@require_GET
def financial_year_activity(request, pk):
    """
    Return ActivityLog records for a financial year.

    GET /api/financial-years/<pk>/activity/
    Query params:
        event_type  — filter by event_type value (optional)
        limit       — max records to return (default 100, max 500)
    """
    from core.models import FinancialYear, ActivityLog

    try:
        fy = FinancialYear.objects.get(pk=pk)
    except FinancialYear.DoesNotExist:
        return JsonResponse({"error": "Financial year not found"}, status=404)

    qs = (
        ActivityLog.objects
        .filter(financial_year=fy)
        .select_related("user", "eva_finding")
        .order_by("-created_at")
    )

    event_type = request.GET.get("event_type", "").strip()
    if event_type:
        qs = qs.filter(event_type=event_type)

    try:
        limit = min(int(request.GET.get("limit", 100)), 500)
    except (ValueError, TypeError):
        limit = 100

    records = []
    for log in qs[:limit]:
        entry = {
            "id": str(log.pk),
            "event_type": log.event_type,
            "description": log.description,
            "user": log.user.get_full_name() or log.user.username if log.user else "System",
            "created_at": log.created_at.isoformat(),
            "metadata": log.metadata,
        }
        if log.eva_finding_id:
            f = log.eva_finding
            entry["eva_finding"] = {
                "id": str(f.pk),
                "title": f.title,
                "severity": f.severity,
                "check_name": f.check_name,
            }
        records.append(entry)

    return JsonResponse({"results": records})
