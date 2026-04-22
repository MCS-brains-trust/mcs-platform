"""
Eva Division 7A Detection Module
=================================
Dedicated 8-rule detection engine for Division 7A compliance.

Rules execute sequentially as a coordinated multi-check sequence on every
company entity.  The module produces one Div7AAssessment record per entity
per FY, which generates one consolidated EvaFinding card.

Rule Categories:
    Category A — Position Detection (T2-D7A-01 to T2-D7A-03)
    Category B — Compliance Verification (T2-D7A-04 to T2-D7A-06)
    Category C — Cross-Entity Detection (T2-D7A-07 to T2-D7A-08)

Legislative Foundation:
    ss 109C–109D, s 109E, s 109F, s 109N, s 109R, s 109RB ITAA 1936
    TR 2010/3, TD 2022/11, QC 17928

Reference: Div7ADetectionModuleSpec.docx — March 2026
"""

import logging
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django.utils import timezone

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
ESCALATION_THRESHOLD = Decimal("200000")
INTEREST_TOLERANCE = Decimal("0.95")  # 5% tolerance for rounding / mid-year drawdowns
S109E_THRESHOLD = Decimal("5000")  # Per-shareholder aggregate threshold

# Account type keywords for detection (expanded set)
_LOAN_ACCOUNT_KEYWORDS = {
    "director loan", "shareholder loan", "loan to director",
    "loan to shareholder", "related party loan", "loan - director",
    "loan – director", "loan receivable", "advance to director",
    "advance to shareholder", "current account - director",
}

_PERSONAL_BENEFIT_ACCOUNT_TYPES = {
    "drawings", "personal_expense", "motor_vehicle_private",
    "home_office", "private_health_insurance", "private",
    "personal", "school fees", "credit card repayment",
}

_INTEREST_RECEIVED_KEYWORDS = {
    "interest received", "interest income", "interest revenue",
    "interest - related", "interest – related", "benchmark interest",
    "loan interest income",
}

_UPE_KEYWORDS = {
    "distribution payable", "unpaid present entitlement", "upe",
    "amount owing to beneficiary", "distribution owing",
    "payable to beneficiary",
}

# ---------------------------------------------------------------------------
# ATO Division 7A Benchmark Interest Rates — Complete Historical Table
# Source: https://www.ato.gov.au/tax-rates-and-codes/division-7a-benchmark-interest-rate
# and https://atotaxrates.info/businesses/division-7a-benchmark-interest-rates/
#
# These are fallback values.  The engine first checks RiskReferenceData (DB)
# so new rates can be added via admin without a code deploy.
# ---------------------------------------------------------------------------
_HISTORICAL_BENCHMARK_RATES = {
    # FY label  →  rate as decimal (e.g. 8.37% = 0.0837)
    # --- Current & recent ---
    2026: Decimal("0.0837"),   # RBA rate published 6 Jun 2025
    2025: Decimal("0.0877"),   # RBA rate published 7 Jun 2024
    2024: Decimal("0.0827"),   # RBA rate published 7 Jun 2023
    2023: Decimal("0.0477"),   # RBA rate published 2 Jun 2022
    2022: Decimal("0.0452"),   # RBA rate published 2 Jun 2021
    2021: Decimal("0.0452"),   # RBA rate published 2 Jun 2020
    2020: Decimal("0.0537"),   # RBA rate published May 2019
    2019: Decimal("0.0520"),   # TD 2018/14
    2018: Decimal("0.0530"),   # TD 2017/17
    2017: Decimal("0.0540"),   # TD 2016/11
    2016: Decimal("0.0545"),   # TD 2015/15
    2015: Decimal("0.0595"),   # TD 2014/20
    2014: Decimal("0.0620"),   # TD 2013/17
    2013: Decimal("0.0705"),   # TD 2012/15
    2012: Decimal("0.0780"),   # TD 2011/20
    2011: Decimal("0.0740"),   # TD 2010/18
    2010: Decimal("0.0575"),   # TD 2009/16
    2009: Decimal("0.0945"),   # TD 2008/19
    2008: Decimal("0.0805"),   # TD 2007/23
    2007: Decimal("0.0755"),   # TD 2006/45
    2006: Decimal("0.0730"),   # TD 2005/31
    2005: Decimal("0.0705"),   # TD 2004/28
    2004: Decimal("0.0655"),   # TD 2003/19
    2003: Decimal("0.0630"),   # TD 2002/15
    2002: Decimal("0.0680"),   # TD 2001/20
    2001: Decimal("0.0780"),   # TD 2001/1
    2000: Decimal("0.0650"),   # TD 1999/39
    1999: Decimal("0.0670"),   # TD 98/21
}


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def run_div7a_assessment(financial_year_id, triggered_by=None):
    """
    Run the full Division 7A assessment for a financial year.

    This is the main entry point called by the Celery task or directly.

    Args:
        financial_year_id: UUID string of the FinancialYear
        triggered_by: optional string describing the trigger source

    Returns:
        dict with assessment results
    """
    from core.models import FinancialYear

    try:
        fy = FinancialYear.objects.select_related("entity").get(pk=financial_year_id)
    except FinancialYear.DoesNotExist:
        logger.error("Div 7A assessment: FY %s not found", financial_year_id)
        return {"error": "Financial year not found"}

    entity = fy.entity

    # Step 1: Validate entity_type = COMPANY. If not, exit silently.
    if entity.entity_type != "company":
        logger.debug(
            "Div 7A assessment skipped: %s is %s (not company)",
            entity.entity_name, entity.entity_type,
        )
        return {"skipped": True, "reason": "Not a company entity"}

    # Load trial balance data
    from core.risk_engine import _load_trial_balance
    tb_data = _load_trial_balance(fy)
    if not tb_data["lines"]:
        return {"skipped": True, "reason": "No trial balance data"}

    # Load reference data (benchmark rates)
    benchmark_rate = _get_benchmark_rate(fy.year_label)

    # Initialize assessment context
    ctx = _AssessmentContext(fy, entity, tb_data, benchmark_rate)

    # Step 2–3: Calculate net_debit_balance across loan accounts
    _detect_loan_accounts(ctx)

    # Execute rules sequentially
    # Category A — Position Detection
    _rule_t2_d7a_01(ctx)   # Shareholder/Director Loan Debit Balance
    _rule_t2_d7a_02(ctx)   # Loan Balance Increase (Escalation Modifier)
    _rule_t2_d7a_03(ctx)   # Payments to/for Shareholders (s 109E)

    # Category B — Compliance Verification (only if T2-D7A-01 fired)
    if "T2-D7A-01" in ctx.rules_fired:
        _rule_t2_d7a_04(ctx)   # Missing Complying Loan Agreement
        _rule_t2_d7a_05(ctx)   # Missing Benchmark Interest Income
        _rule_t2_d7a_06(ctx)   # Minimum Yearly Repayment Shortfall

    # Category C — Cross-Entity Detection
    _rule_t2_d7a_07(ctx)   # Unpaid Present Entitlements (Trust → Company)
    _rule_t2_d7a_08(ctx)   # Interposed Entity Loans (ss 109T–109V)

    # Calculate total exposure
    ctx.total_exposure = ctx.direct_loan_balance + ctx.upe_exposure + ctx.s109e_payments

    # Determine escalation
    ctx.escalation_required = ctx.total_exposure > ESCALATION_THRESHOLD

    # Determine overall severity
    ctx.overall_severity = _determine_severity(ctx)

    # Persist assessment record
    assessment = _persist_assessment(ctx)

    # Create/update consolidated finding card if severity != CLEAR
    if ctx.overall_severity != "CLEAR":
        _create_consolidated_finding(ctx, assessment)

    # Log to Activity tab
    _log_activity(ctx, assessment)

    logger.info(
        "Div 7A assessment complete: %s — %s (exposure: $%s, rules: %s)",
        entity.entity_name, ctx.overall_severity,
        ctx.total_exposure, ctx.rules_fired,
    )

    return {
        "entity_name": entity.entity_name,
        "overall_severity": ctx.overall_severity,
        "total_exposure": str(ctx.total_exposure),
        "rules_fired": ctx.rules_fired,
        "escalation_required": ctx.escalation_required,
        "assessment_id": str(assessment.pk),
    }


# ============================================================================
# ASSESSMENT CONTEXT
# ============================================================================

class _AssessmentContext:
    """Mutable context object passed through all rules."""

    def __init__(self, fy, entity, tb_data, benchmark_rate):
        self.fy = fy
        self.entity = entity
        self.tb_data = tb_data
        self.benchmark_rate = benchmark_rate

        # Position Detection results
        self.loan_accounts = []          # list of dicts
        self.direct_loan_balance = ZERO
        self.direct_loan_accounts = []   # JSON-serialisable
        self.balance_increase = ZERO
        self.upe_exposure = ZERO
        self.upe_details = []
        self.s109e_payments = ZERO
        self.s109e_details = []
        self.total_exposure = ZERO

        # Compliance Verification results
        self.has_complying_agreement = False
        self.agreement_covers_balance = False
        self.compliance_records = []     # Div7ACompliance queryset
        self.expected_interest = ZERO
        self.recorded_interest = ZERO
        self.interest_compliant = False
        self.expected_myr = ZERO
        self.actual_repayments = None
        self.myr_compliant = None

        # Escalation & Severity
        self.escalation_required = False
        self.rules_fired = []
        self.overall_severity = "CLEAR"

        # Finding card content
        self.finding_lines = []          # text blocks for the card


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _extract_fy_year(year_label):
    """
    Extract the numeric year from a year_label like 'FY2026', '2026', '2025-26', etc.

    Returns an int (e.g. 2026) or None.
    """
    import re
    if not year_label:
        return None
    s = str(year_label).strip()

    # "FY2026" or "FY 2026"
    m = re.match(r"FY\s*(\d{4})", s, re.IGNORECASE)
    if m:
        return int(m.group(1))

    # "2025-26" or "2025/26" → ending year is 2026
    m = re.match(r"(\d{4})[\-/](\d{2,4})", s)
    if m:
        start = int(m.group(1))
        end_part = m.group(2)
        if len(end_part) == 2:
            return start + 1  # "2025-26" → 2026
        return int(end_part)  # "2025-2026" → 2026

    # Plain "2026"
    m = re.match(r"(\d{4})$", s)
    if m:
        return int(m.group(1))

    return None


def _get_benchmark_rate(year_label):
    """
    Resolve the ATO Division 7A benchmark interest rate for the given FY.

    Lookup priority:
      1. RiskReferenceData table (DB) — allows admin to add/override rates
         without a code deploy.  Searches for key matching the specific FY.
      2. Built-in historical table (_HISTORICAL_BENCHMARK_RATES) — covers
         all years from 1998-99 to 2025-26.
      3. Most recent known rate as a last-resort fallback.

    Returns a Decimal (e.g. Decimal('0.0837') for 8.37%).
    """
    from core.models import RiskReferenceData

    fy_year = _extract_fy_year(year_label)

    # --- Priority 1: Database lookup (exact FY match) ---
    if fy_year:
        # Try FY-specific key first, e.g. "div7a_benchmark_rate_fy2026"
        db_keys = [
            f"div7a_benchmark_rate_fy{fy_year}",
            f"div7a_benchmark_rate_FY{fy_year}",
        ]
        # Also try the generic key if it has matching applicable_fy
        for key in db_keys:
            try:
                ref = RiskReferenceData.objects.filter(key=key).first()
                if ref:
                    rate = Decimal(ref.value) / Decimal("100")
                    logger.debug(
                        "Div 7A benchmark rate for %s resolved from DB key '%s': %s%%",
                        year_label, key, ref.value,
                    )
                    return rate
            except (InvalidOperation, ValueError):
                continue

        # Try generic key with applicable_fy filter
        try:
            fy_label = f"FY{fy_year}"
            ref = RiskReferenceData.objects.filter(
                key="div7a_benchmark_rate",
                applicable_fy=fy_label,
            ).first()
            if ref:
                rate = Decimal(ref.value) / Decimal("100")
                logger.debug(
                    "Div 7A benchmark rate for %s resolved from DB (applicable_fy=%s): %s%%",
                    year_label, fy_label, ref.value,
                )
                return rate
        except (InvalidOperation, ValueError):
            pass

    # --- Priority 2: Built-in historical table ---
    if fy_year and fy_year in _HISTORICAL_BENCHMARK_RATES:
        rate = _HISTORICAL_BENCHMARK_RATES[fy_year]
        logger.debug(
            "Div 7A benchmark rate for %s resolved from historical table: %s",
            year_label, rate,
        )
        return rate

    # --- Priority 3: Most recent known rate (fallback) ---
    # Check DB for the most recent rate by key pattern
    try:
        ref = RiskReferenceData.objects.filter(
            key__startswith="div7a_benchmark_rate",
        ).order_by("-applicable_fy").first()
        if ref:
            rate = Decimal(ref.value) / Decimal("100")
            logger.warning(
                "Div 7A benchmark rate for %s not found for exact year; "
                "using most recent DB rate (%s from %s): %s%%",
                year_label, ref.key, ref.applicable_fy, ref.value,
            )
            return rate
    except (InvalidOperation, ValueError):
        pass

    # Absolute last resort: highest year in historical table
    max_year = max(_HISTORICAL_BENCHMARK_RATES.keys())
    fallback = _HISTORICAL_BENCHMARK_RATES[max_year]
    logger.warning(
        "Div 7A benchmark rate for %s: no DB or historical match found. "
        "Falling back to most recent historical rate (FY%d = %s)",
        year_label, max_year, fallback,
    )
    return fallback


def _is_loan_account(line):
    """Determine if a TB line is a director/shareholder loan account."""
    name_lower = (line.account_name or "").lower()
    code = line.account_code or ""

    # Check against expanded keyword set
    if any(kw in name_lower for kw in _LOAN_ACCOUNT_KEYWORDS):
        return True

    # Check mapped account type if available
    if line.mapped_line_item:
        mapped_code = getattr(line.mapped_line_item, 'code', '') or ''
        section = (getattr(line.mapped_line_item, 'statement_section', '') or '').lower()
        # Liability section with loan keywords
        if ('liabilit' in section or mapped_code.startswith("BS-LIA")):
            if any(kw in name_lower for kw in {"loan", "director", "shareholder", "advance"}):
                return True

    # Heuristic: code starts with "3" (liabilities) + loan keyword
    if code.startswith("3") and any(kw in name_lower for kw in {"loan", "director", "shareholder", "advance", "drawings"}):
        return True

    return False


def _is_interest_income_account(line):
    """Determine if a TB line is an interest income account from related parties."""
    name_lower = (line.account_name or "").lower()
    return any(kw in name_lower for kw in _INTEREST_RECEIVED_KEYWORDS)


def _is_personal_benefit_account(line):
    """Determine if a TB line is a personal benefit / drawings account."""
    name_lower = (line.account_name or "").lower()
    return any(kw in name_lower for kw in _PERSONAL_BENEFIT_ACCOUNT_TYPES)


def _detect_loan_accounts(ctx):
    """Scan TB for all loan accounts and compute net debit balance.

    All loan accounts are collected here (debit, zero, and credit balances).
    The ``is_debit`` flag on each entry indicates whether the net balance is
    positive (i.e. money owed BY a shareholder/director TO the company).
    Downstream rules (T2-D7A-01 etc.) MUST check ``is_debit`` before treating
    an account as a Div 7A exposure.  Zero and credit-balance accounts do NOT
    represent Div 7A risk — they indicate the loan has been repaid or the
    company owes the person.
    """
    for line in ctx.tb_data["lines"]:
        if _is_loan_account(line):
            # Use closing_balance as the authoritative net position when available.
            # Some TB imports (e.g. Access Ledger, MYOB) populate only the debit
            # or credit movement column without the offsetting entry, which causes
            # effective_dr - effective_cr to reflect the gross movement rather than
            # the closing balance.  closing_balance is always the net position.
            raw_closing = getattr(line, 'closing_balance', None)
            if raw_closing is not None and raw_closing != ZERO:
                # closing_balance positive = debit (asset/receivable from company's view)
                # closing_balance negative = credit (liability owed to the person)
                net = Decimal(str(raw_closing))
            else:
                net = line.effective_dr - line.effective_cr

            # Prior year: use prior_closing_balance when available, else fall back
            # to prior_debit - prior_credit.
            raw_prior_closing = getattr(line, 'prior_closing_balance', None)
            if raw_prior_closing is not None and raw_prior_closing != ZERO:
                prior_net = Decimal(str(raw_prior_closing))
            else:
                prior_net = (line.prior_debit or ZERO) - (line.prior_credit or ZERO)

            ctx.loan_accounts.append({
                "line": line,
                "account_code": line.account_code,
                "account_name": line.account_name,
                "net_balance": net,
                "prior_balance": prior_net,
                "is_debit": net > ZERO,
            })


def _calculate_myr(loan_balance, rate, remaining_years):
    """
    Calculate Minimum Yearly Repayment per s 109R.

    MYR = Loan Balance × [ r / (1 − (1 + r)^(−n)) ]

    Args:
        loan_balance: Decimal opening balance
        rate: Decimal benchmark rate (e.g. 0.0837)
        remaining_years: int remaining years on loan term

    Returns:
        Decimal MYR amount
    """
    if remaining_years <= 0 or rate <= ZERO or loan_balance <= ZERO:
        return ZERO

    one_plus_r = Decimal("1") + rate
    # (1 + r)^(-n)
    denominator = Decimal("1") - (one_plus_r ** (-remaining_years))
    if denominator == ZERO:
        return loan_balance  # Edge case: return full balance

    myr = loan_balance * (rate / denominator)
    return myr.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _determine_severity(ctx):
    """Determine overall severity based on rules fired."""
    critical_rules = {"T2-D7A-01", "T2-D7A-04", "T2-D7A-05", "T2-D7A-06", "T2-D7A-07"}
    if any(r in critical_rules for r in ctx.rules_fired):
        return "CRITICAL"
    if ctx.rules_fired:
        return "ADVISORY"
    return "CLEAR"


# ============================================================================
# CATEGORY A — POSITION DETECTION
# ============================================================================

def _rule_t2_d7a_01(ctx):
    """
    T2-D7A-01: Shareholder/Director Loan Debit Balance

    Fires when ALL of the following are true:
      1. The CLOSING BALANCE (net_balance) is a positive DEBIT — i.e. the
         company has a net receivable from the shareholder/director at year end.
      2. The CURRENT YEAR debit MOVEMENT is > 0 — i.e. new lending occurred
         this year (balance increased or moved from credit into debit).

    The dual guard prevents false positives caused by TB imports that store
    only the gross debit-column movement for accounts that are nil or in credit
    at year end.  A nil or credit closing balance means there is no outstanding
    Div 7A loan regardless of intra-year movement.
    """
    debit_accounts = []
    total_debit = ZERO

    for acct in ctx.loan_accounts:
        # Guard 1: closing balance must be a net DEBIT (positive).
        # Zero and credit closing balances are NOT Div 7A exposures.
        if not acct["is_debit"] or acct["net_balance"] <= ZERO:
            continue

        # Guard 2: current year debit MOVEMENT must be positive.
        # A static loan that hasn't increased does not trigger a new finding.
        cy_balance = acct["net_balance"]
        py_balance = acct["prior_balance"] if acct["prior_balance"] > ZERO else ZERO
        cy_movement = cy_balance - py_balance

        if cy_movement <= ZERO:
            # No new lending this year — skip
            continue

        # Check false positive guard: exclude if Div7ACompliance exists
        # with status=COMPLIANT and loan_balance >= net_debit_balance
        if _has_compliant_coverage(ctx, acct["net_balance"]):
            continue

        debit_accounts.append({
            "account_code": acct["account_code"],
            "account_name": acct["account_name"],
            "balance": str(acct["net_balance"]),
            "py_balance": str(acct["prior_balance"]),
            "cy_movement": str(cy_movement),
        })
        total_debit += cy_movement

    if total_debit > ZERO:
        ctx.rules_fired.append("T2-D7A-01")
        ctx.direct_loan_balance = total_debit
        ctx.direct_loan_accounts = debit_accounts

        for acct in debit_accounts:
            ctx.finding_lines.append(
                f"Director/Shareholder Loan — {acct['account_name']} "
                f"({acct['account_code']}): current year debit movement of "
                f"${Decimal(acct['cy_movement']):,.2f} (closing balance "
                f"${Decimal(acct['balance']):,.2f}). Without a complying "
                f"Div 7A loan agreement, this increase is assessable as an "
                f"unfranked deemed dividend under ss 109C–109D ITAA 1936."
            )


def _has_compliant_coverage(ctx, balance):
    """Check if a Div7ACompliance record covers this balance."""
    from core.models import Div7ACompliance

    compliant = Div7ACompliance.objects.filter(
        entity=ctx.entity,
        status="COMPLIANT",
        loan_amount__gte=balance,
    ).exists()
    return compliant


def _rule_t2_d7a_02(ctx):
    """
    T2-D7A-02: Loan Balance Increase (Escalation Modifier)

    Enriches T2-D7A-01 — not a separate finding.
    Fires when CY balance > PY balance (both debits), or CY is debit where PY was credit/zero.
    """
    if "T2-D7A-01" not in ctx.rules_fired:
        return

    total_increase = ZERO
    for acct in ctx.loan_accounts:
        if not acct["is_debit"]:
            continue
        cy = acct["net_balance"]
        py = acct["prior_balance"]

        if py > ZERO:
            # Both debit: increase = cy - py
            increase = cy - py
        elif py <= ZERO:
            # PY was credit or zero: increase = cy + abs(py)
            increase = cy + abs(py)
        else:
            increase = ZERO

        if increase > ZERO:
            total_increase += increase

    if total_increase > ZERO:
        ctx.rules_fired.append("T2-D7A-02")
        ctx.balance_increase = total_increase

        ctx.finding_lines.append(
            f"Loan balance has INCREASED by ${total_increase:,.2f} from prior year. "
            f"The complying agreement must cover the full current year balance."
        )

        if total_increase > ESCALATION_THRESHOLD:
            ctx.finding_lines.append(
                "⚠ Flag to Elio per firm escalation policy — "
                f"increase exceeds $200,000."
            )


def _rule_t2_d7a_03(ctx):
    """
    T2-D7A-03: Payments to/for Shareholders (s 109E)

    Detects company expense transactions payable to directors/shareholders
    coded to personal expense categories.
    Severity: ADVISORY. Threshold: aggregate > $5,000 per shareholder.
    """
    # Get directors/shareholders for this entity
    from core.models import EntityOfficer
    officers = EntityOfficer.objects.filter(
        entity=ctx.entity,
    ).filter(
        models_Q_director_or_shareholder()
    ).values_list("full_name", flat=True)
    officer_names = {n.lower() for n in officers}

    personal_accounts = []
    total_personal = ZERO

    for line in ctx.tb_data["lines"]:
        if _is_personal_benefit_account(line):
            net = abs(line.effective_dr - line.effective_cr)
            if net > ZERO:
                personal_accounts.append({
                    "account_code": line.account_code,
                    "account_name": line.account_name,
                    "amount": str(net),
                    "payee": "",  # Would need transaction-level data
                    "description": line.account_name,
                })
                total_personal += net

    if total_personal > S109E_THRESHOLD:
        ctx.rules_fired.append("T2-D7A-03")
        ctx.s109e_payments = total_personal
        ctx.s109e_details = personal_accounts

        ctx.finding_lines.append(
            f"Payments of a personal nature totalling ${total_personal:,.2f} detected. "
            f"These may constitute deemed dividends under s 109E ITAA 1936 if paid "
            f"to or on behalf of a shareholder/associate."
        )


def models_Q_director_or_shareholder():
    """Build a Q filter for directors/shareholders."""
    from django.db.models import Q
    return (
        Q(role__in=["director", "shareholder"]) |
        Q(roles__contains="director") |
        Q(roles__contains="shareholder")
    )


# ============================================================================
# CATEGORY B — COMPLIANCE VERIFICATION
# ============================================================================

def _rule_t2_d7a_04(ctx):
    """
    T2-D7A-04: Missing Complying Loan Agreement

    Only runs if T2-D7A-01 fired.
    Checks for LegalDocument with document_type='div7a_loan_agreement',
    status=EXECUTED, loan_amount >= net_debit_balance.
    """
    from core.models import LegalDocument

    # Query for executed Div 7A loan agreements covering this entity/FY
    agreements = LegalDocument.objects.filter(
        entity=ctx.entity,
        document_type="div7a_loan_agreement",
        status="executed",
    ).filter(
        # Agreement must cover the current FY
        financial_year=ctx.fy,
    )

    if not agreements.exists():
        # Also check agreements without FY link but for this entity
        agreements = LegalDocument.objects.filter(
            entity=ctx.entity,
            document_type="div7a_loan_agreement",
            status="executed",
        )

    if agreements.exists():
        ctx.has_complying_agreement = True
        # Check if agreement covers the full balance
        total_covered = ZERO
        for agreement in agreements:
            params = agreement.parameters or {}
            loan_amt = params.get("loan_amount", 0)
            try:
                total_covered += Decimal(str(loan_amt))
            except (InvalidOperation, ValueError):
                pass

        ctx.agreement_covers_balance = total_covered >= ctx.direct_loan_balance

        if not ctx.agreement_covers_balance:
            ctx.rules_fired.append("T2-D7A-04")
            ctx.finding_lines.append(
                f"Complying loan agreement exists but covers "
                f"${total_covered:,.2f} — less than the actual debit balance "
                f"of ${ctx.direct_loan_balance:,.2f}. Agreement must be updated "
                f"to cover the full balance."
            )
    else:
        ctx.rules_fired.append("T2-D7A-04")
        ctx.has_complying_agreement = False
        ctx.agreement_covers_balance = False
        ctx.finding_lines.append(
            f"No complying Division 7A loan agreement on file for "
            f"${ctx.direct_loan_balance:,.2f} debit balance. Without an executed "
            f"agreement, the full balance is treated as an unfranked deemed dividend."
        )


def _rule_t2_d7a_05(ctx):
    """
    T2-D7A-05: Missing Benchmark Interest Income

    Only runs if T2-D7A-01 fired AND complying agreement EXISTS.
    Checks interest income in P&L against expected benchmark interest.
    """
    if not ctx.has_complying_agreement:
        return

    # Calculate expected interest: opening_loan_balance × benchmark_rate
    # Use prior year balance as opening balance (or current if no prior)
    opening_balance = ZERO
    for acct in ctx.loan_accounts:
        if acct["is_debit"]:
            py = acct["prior_balance"]
            if py > ZERO:
                opening_balance += py
            else:
                # If no prior year, use current year as proxy
                opening_balance += acct["net_balance"]

    ctx.expected_interest = (opening_balance * ctx.benchmark_rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # Find recorded interest income from related parties
    recorded = ZERO
    for line in ctx.tb_data["lines"]:
        if _is_interest_income_account(line):
            net = abs(line.effective_dr - line.effective_cr)
            recorded += net

    ctx.recorded_interest = recorded

    # Check compliance with 5% tolerance
    threshold = ctx.expected_interest * INTEREST_TOLERANCE
    ctx.interest_compliant = recorded >= threshold

    if not ctx.interest_compliant:
        ctx.rules_fired.append("T2-D7A-05")
        ctx.finding_lines.append(
            f"Benchmark interest income shortfall. Expected: "
            f"${ctx.expected_interest:,.2f} (opening balance ${opening_balance:,.2f} "
            f"× {ctx.benchmark_rate * 100:.2f}%). Recorded: ${recorded:,.2f}. "
            f"Without benchmark interest, the loan agreement is non-compliant "
            f"and the loan reverts to a deemed dividend."
        )


def _rule_t2_d7a_06(ctx):
    """
    T2-D7A-06: Minimum Yearly Repayment Shortfall

    Only runs if T2-D7A-01 fired AND complying agreement EXISTS.
    Calculates MYR using s 109R formula and compares to actual repayments.
    """
    if not ctx.has_complying_agreement:
        return

    from core.models import Div7ACompliance

    # Look for compliance records to get loan terms
    compliance_records = Div7ACompliance.objects.filter(
        entity=ctx.entity,
        status__in=["COMPLIANT", "PENDING"],
    )

    if not compliance_records.exists():
        # No compliance record — can't calculate MYR precisely
        # Use default 7-year unsecured term
        opening_balance = ZERO
        for acct in ctx.loan_accounts:
            if acct["is_debit"] and acct["prior_balance"] > ZERO:
                opening_balance += acct["prior_balance"]
            elif acct["is_debit"]:
                opening_balance += acct["net_balance"]

        if opening_balance > ZERO:
            ctx.expected_myr = _calculate_myr(opening_balance, ctx.benchmark_rate, 7)
    else:
        # Use compliance records for precise calculation
        total_myr = ZERO
        for record in compliance_records:
            fy_num = _extract_fy_number(ctx.fy.year_label)
            remaining = record.loan_term - (fy_num - record.loan_start_year)
            if remaining > 0:
                myr = _calculate_myr(record.loan_amount, ctx.benchmark_rate, remaining)
                total_myr += myr
        ctx.expected_myr = total_myr
        ctx.compliance_records = list(compliance_records)

    # Calculate actual repayments (credits on loan accounts during FY)
    # This requires transaction-level data; use credit movements as proxy
    actual = ZERO
    has_transaction_data = False
    for acct in ctx.loan_accounts:
        if acct["is_debit"]:
            line = acct["line"]
            # Credits on the account represent repayments
            if hasattr(line, 'effective_cr') and line.effective_cr > ZERO:
                actual += line.effective_cr
                has_transaction_data = True

    if has_transaction_data:
        ctx.actual_repayments = actual
        ctx.myr_compliant = actual >= ctx.expected_myr

        if not ctx.myr_compliant and ctx.expected_myr > ZERO:
            shortfall = ctx.expected_myr - actual
            ctx.rules_fired.append("T2-D7A-06")
            ctx.finding_lines.append(
                f"Minimum yearly repayment shortfall. Required MYR: "
                f"${ctx.expected_myr:,.2f}. Actual repayments: ${actual:,.2f}. "
                f"Shortfall: ${shortfall:,.2f}. The shortfall amount is treated "
                f"as a deemed unfranked dividend under s 109R."
            )
    else:
        # Transaction data unavailable — advisory flag
        if ctx.expected_myr > ZERO:
            ctx.finding_lines.append(
                f"Unable to verify minimum yearly repayment of "
                f"${ctx.expected_myr:,.2f} — transaction-level data required. "
                f"Confirm repayments manually."
            )


def _extract_fy_number(year_label):
    """Extract numeric year from label like 'FY2026' -> 2026."""
    import re
    match = re.search(r'(\d{4})', year_label)
    return int(match.group(1)) if match else 2026


# ============================================================================
# CATEGORY C — CROSS-ENTITY DETECTION
# ============================================================================

def _rule_t2_d7a_07(ctx):
    """
    T2-D7A-07: Unpaid Present Entitlements (Trust → Company)

    Cross-entity rule: checks if related trusts have distributed to this
    company and the entitlement remains unpaid at year end.

    UPEs arising from 1 July 2022 must be on complying 7-year loan terms.
    Pre-2022 UPEs may remain under sub-trust arrangement.
    """
    from core.models import EntityRelationship, FinancialYear
    from core.risk_engine import _load_trial_balance

    # Find related trusts where this company is a beneficiary
    relationships = EntityRelationship.objects.filter(
        from_entity=ctx.entity,
        relationship_type__in=["beneficiary_of", "associated_entity"],
    ).select_related("to_entity")

    # Also check reverse: trusts that have this company as beneficiary
    reverse_rels = EntityRelationship.objects.filter(
        to_entity=ctx.entity,
        relationship_type__in=["beneficiary_of", "associated_entity"],
    ).select_related("from_entity")

    related_trusts = set()
    for rel in relationships:
        if rel.to_entity.entity_type in ("trust", "trust_discretionary", "trust_unit", "trust_hybrid"):
            related_trusts.add(rel.to_entity)
    for rel in reverse_rels:
        if rel.from_entity.entity_type in ("trust", "trust_discretionary", "trust_unit", "trust_hybrid"):
            related_trusts.add(rel.from_entity)

    if not related_trusts:
        return

    total_upe = ZERO
    upe_details = []

    for trust in related_trusts:
        # Get the trust's current FY
        trust_fy = FinancialYear.objects.filter(
            entity=trust,
            year_label=ctx.fy.year_label,
        ).first()

        if not trust_fy:
            ctx.finding_lines.append(
                f"UPE assessment deferred — {trust.entity_name} "
                f"{ctx.fy.year_label} not finalised."
            )
            continue

        # Check trust TB for distribution payable to this company
        trust_tb = _load_trial_balance(trust_fy)
        for line in trust_tb["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in _UPE_KEYWORDS):
                net = line.effective_cr - line.effective_dr  # Credit = payable
                if net > ZERO:
                    total_upe += net
                    # Determine regime (pre/post 2022)
                    regime = "post_2022"  # Default for current FYs
                    if trust_fy.end_date and trust_fy.end_date.year <= 2022:
                        regime = "pre_2022"

                    upe_details.append({
                        "trust_entity_id": str(trust.pk),
                        "trust_name": trust.entity_name,
                        "upe_amount": str(net),
                        "distribution_date": str(trust_fy.end_date),
                        "regime": regime,
                    })

        # Also check company's own receivable from this trust
        for line in ctx.tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            trust_name_lower = trust.entity_name.lower()
            if trust_name_lower in name_lower or "distribution receivable" in name_lower:
                net = line.effective_dr - line.effective_cr
                if net > ZERO and not any(
                    d["trust_entity_id"] == str(trust.pk) for d in upe_details
                ):
                    total_upe += net
                    upe_details.append({
                        "trust_entity_id": str(trust.pk),
                        "trust_name": trust.entity_name,
                        "upe_amount": str(net),
                        "distribution_date": str(ctx.fy.end_date),
                        "regime": "post_2022",
                    })

    if total_upe > ZERO:
        ctx.rules_fired.append("T2-D7A-07")
        ctx.upe_exposure = total_upe
        ctx.upe_details = upe_details

        for detail in upe_details:
            regime_text = (
                "Must be on complying 7-year loan terms or repaid by lodgement day."
                if detail["regime"] == "post_2022"
                else "Confirm sub-trust arrangement per PS LA 2010/4."
            )
            ctx.finding_lines.append(
                f"Unpaid Present Entitlement of ${Decimal(detail['upe_amount']):,.2f} "
                f"from {detail['trust_name']}. {regime_text} "
                f"This UPE is treated as a Division 7A loan under TD 2022/11."
            )


def _rule_t2_d7a_08(ctx):
    """
    T2-D7A-08: Interposed Entity Loans (ss 109T–109V)

    Detects: Company → Trust/Company (intermediary) → Individual (shareholder).
    Severity: ADVISORY — requires manual review.
    """
    from core.models import EntityRelationship, EntityOfficer

    # Get shareholders of this company
    shareholders = set(
        EntityOfficer.objects.filter(
            entity=ctx.entity,
        ).filter(
            models_Q_director_or_shareholder()
        ).values_list("full_name", flat=True)
    )

    if not shareholders:
        return

    # Find entities this company has lent to (receivables)
    intermediaries = EntityRelationship.objects.filter(
        from_entity=ctx.entity,
        relationship_type__in=["associated_entity", "parent_entity"],
    ).select_related("to_entity")

    for rel in intermediaries:
        intermediary = rel.to_entity

        # Check if company has a receivable from this intermediary
        has_receivable = False
        receivable_amount = ZERO
        for line in ctx.tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            intermediary_lower = intermediary.entity_name.lower()
            if intermediary_lower in name_lower or "intercompany" in name_lower:
                net = line.effective_dr - line.effective_cr
                if net > ZERO:
                    has_receivable = True
                    receivable_amount += net

        if not has_receivable:
            continue

        # Check if intermediary has loans to shareholders
        intermediary_officers = set(
            EntityOfficer.objects.filter(
                entity=intermediary,
            ).values_list("full_name", flat=True)
        )

        # Check for overlap between intermediary's payees and company's shareholders
        overlap = shareholders & intermediary_officers
        if overlap:
            for person in overlap:
                ctx.rules_fired.append("T2-D7A-08")
                ctx.finding_lines.append(
                    f"Potential interposed entity arrangement: "
                    f"{ctx.entity.entity_name} has ${receivable_amount:,.2f} "
                    f"receivable from {intermediary.entity_name}, which has a "
                    f"relationship with {person} (shareholder). Division 7A may "
                    f"apply under ss 109T–109V."
                )
            # Only fire once for T2-D7A-08
            break


# ============================================================================
# PERSISTENCE
# ============================================================================

def _persist_assessment(ctx):
    """Create or update the Div7AAssessment record."""
    from core.models import Div7AAssessment

    assessment, created = Div7AAssessment.objects.update_or_create(
        financial_year=ctx.fy,
        defaults={
            "direct_loan_balance": ctx.direct_loan_balance,
            "direct_loan_accounts": ctx.direct_loan_accounts,
            "upe_exposure": ctx.upe_exposure,
            "upe_details": ctx.upe_details,
            "s109e_payments": ctx.s109e_payments,
            "s109e_details": ctx.s109e_details,
            "total_exposure": ctx.total_exposure,
            "has_complying_agreement": ctx.has_complying_agreement,
            "agreement_covers_balance": ctx.agreement_covers_balance,
            "expected_interest": ctx.expected_interest,
            "recorded_interest": ctx.recorded_interest,
            "interest_compliant": ctx.interest_compliant,
            "expected_myr": ctx.expected_myr,
            "actual_repayments": ctx.actual_repayments,
            "myr_compliant": ctx.myr_compliant,
            "escalation_required": ctx.escalation_required,
            "rules_fired": ctx.rules_fired,
            "overall_severity": ctx.overall_severity,
        },
    )

    logger.info(
        "Div7AAssessment %s for %s: %s",
        "created" if created else "updated",
        ctx.fy, ctx.overall_severity,
    )
    return assessment


def _create_consolidated_finding(ctx, assessment):
    """Create one EvaFinding per loan account (instead of one combined card).

    Non-loan findings (UPE, s 109E, interposed entity) still produce
    a single consolidated card.

    Findings that were previously *addressed* (status=addressed/closed) for the
    same financial year and finding_key are skipped — they must not reappear.
    """
    from core.models import EvaFinding, EvaReview
    from core.eva_engine import _is_finding_addressed

    # Get or create the latest EvaReview for this FY
    review = EvaReview.objects.filter(
        financial_year=ctx.fy,
    ).order_by("-triggered_at").first()

    if not review:
        review = EvaReview.objects.create(
            financial_year=ctx.fy,
            status="findings_raised",
            model_used="haiku",
            applicable_checks=["div7a"],
        )

    severity = "critical" if ctx.overall_severity == "CRITICAL" else "advisory"
    legislation = "Division 7A, ITAA 1936 (ss 109C–109Q, s 109N, s 109R). QC 17928 benchmark rate."
    if ctx.upe_exposure > ZERO:
        legislation += " TD 2022/11 for UPEs."

    remediation = _build_remediation_steps(ctx)
    checklist = _build_compliance_checklist(ctx)

    findings_created = []

    # --- One finding per loan account ---
    for acct in ctx.direct_loan_accounts:
        acct_code = acct["account_code"]
        acct_name = acct["account_name"]
        cy_movement = acct.get("cy_movement", acct["balance"])
        balance = acct["balance"]

        # Deterministic finding_key for this loan account
        fk = EvaFinding.build_finding_key("div7a", account_codes=[acct_code])

        # Skip if previously addressed
        if _is_finding_addressed(ctx.fy, fk):
            logger.info("Div7A finding for %s addressed — skipping", acct_code)
            continue

        title = f"Div 7A — {acct_name} ({acct_code})"
        explanation = (
            f"Director/Shareholder Loan — {acct_name} ({acct_code}): "
            f"current year debit movement of ${Decimal(cy_movement):,.2f} "
            f"(closing balance ${Decimal(balance):,.2f}) for "
            f"{ctx.entity.entity_name} {ctx.fy.year_label}.\n\n"
            f"Without a complying Div 7A loan agreement, this increase is "
            f"assessable as an unfranked deemed dividend under "
            f"ss 109C–109D ITAA 1936."
        )

        finding_defaults = {
            "check_name": "div7a",
            "severity": severity,
            "title": title,
            "plain_english_explanation": explanation,
            "recommendation": remediation,
            "remediation_authority": legislation,
            "remediation_synthesis": checklist,
            "legislation_reference": legislation,
            "confidence": "high",
            "source": "risk_engine",
            "finding_key": fk,
        }

        # Use finding_key as dedup key within the review (preferred over title__contains)
        existing = EvaFinding.objects.for_domain('financial_statements').filter(  # Sprint 1b: scope to FS domain
            eva_review=review,
            finding_key=fk,
        ).first()
        if not existing:
            # Fallback: legacy dedup via title
            existing = EvaFinding.objects.for_domain('financial_statements').filter(  # Sprint 1b: scope to FS domain
                eva_review=review,
                check_name="div7a",
                title__contains=acct_code,
            ).first()

        if existing:
            for key, value in finding_defaults.items():
                setattr(existing, key, value)
            existing.save()
            findings_created.append(existing)
        else:
            finding = EvaFinding.objects.create(
                eva_review=review,
                **finding_defaults,
            )
            findings_created.append(finding)

    # --- Non-loan findings (UPE, s 109E, interposed) as one card ---
    # Guard: only create the "Other Exposures" card if there is genuine
    # non-loan exposure (UPE or s109E).  When all loan accounts have zero
    # or credit balances and no UPE/s109E amounts exist, total_exposure
    # will be <= 0 and we should not generate a misleading finding.
    non_loan_lines = [
        line for line in ctx.finding_lines
        if not line.startswith("Director/Shareholder Loan")
    ]
    non_loan_exposure = ctx.upe_exposure + ctx.s109e_payments
    if non_loan_lines and non_loan_exposure > ZERO:
        other_fk = EvaFinding.build_finding_key("div7a", qualifier="OTHER_EXPOSURES")

        # Skip if previously addressed
        if _is_finding_addressed(ctx.fy, other_fk):
            logger.info("Div7A Other Exposures finding addressed — skipping")
        else:
            non_loan_title = f"Div 7A — Other Exposures — {ctx.entity.entity_name} {ctx.fy.year_label}"
            non_loan_explanation = (
                f"Division 7A — Other Exposures for "
                f"{ctx.entity.entity_name} {ctx.fy.year_label}\n\n"
                + "\n\n".join(non_loan_lines)
            )

            existing_other = EvaFinding.objects.for_domain('financial_statements').filter(  # Sprint 1b: scope to FS domain
                eva_review=review,
                finding_key=other_fk,
            ).first()
            if not existing_other:
                existing_other = EvaFinding.objects.for_domain('financial_statements').filter(  # Sprint 1b: scope to FS domain
                    eva_review=review,
                    check_name="div7a",
                    title__contains="Other Exposures",
                ).first()

            other_defaults = {
                "check_name": "div7a",
                "severity": severity,
                "title": non_loan_title,
                "plain_english_explanation": non_loan_explanation,
                "recommendation": remediation,
                "remediation_authority": legislation,
                "remediation_synthesis": checklist,
                "legislation_reference": legislation,
                "confidence": "high",
                "source": "risk_engine",
                "finding_key": other_fk,
            }

            if existing_other:
                for key, value in other_defaults.items():
                    setattr(existing_other, key, value)
                existing_other.save()
                findings_created.append(existing_other)
            else:
                finding = EvaFinding.objects.create(
                    eva_review=review,
                    **other_defaults,
                )
                findings_created.append(finding)

    # Link assessment to first finding (for backwards compat)
    if findings_created:
        assessment.eva_finding = findings_created[0]
        assessment.save(update_fields=["eva_finding"])

    return findings_created[0] if findings_created else None


def _build_remediation_steps(ctx):
    """Generate dynamic Fix steps based on which rules fired."""
    steps = []
    step_num = 1

    if "T2-D7A-04" in ctx.rules_fired:
        steps.append(
            f"{step_num}. Execute a Div 7A complying loan agreement covering "
            f"${ctx.total_exposure:,.2f} before lodgement day."
        )
        step_num += 1

    if ctx.benchmark_rate:
        rate_pct = ctx.benchmark_rate * 100
        steps.append(
            f"{step_num}. Ensure agreement specifies benchmark interest rate of "
            f"{rate_pct:.2f}% for {ctx.fy.year_label}."
        )
        step_num += 1

    if ctx.expected_myr > ZERO:
        steps.append(
            f"{step_num}. Calculate MYR: ${ctx.expected_myr:,.2f} based on loan terms "
            f"at {ctx.benchmark_rate * 100:.2f}%."
        )
        step_num += 1
        steps.append(
            f"{step_num}. Confirm repayment of ${ctx.expected_myr:,.2f} made or "
            f"will be made before 30 June."
        )
        step_num += 1

    if "T2-D7A-05" in ctx.rules_fired:
        steps.append(
            f"{step_num}. Record benchmark interest of ${ctx.expected_interest:,.2f} "
            f"as assessable income (Item 8N)."
        )
        step_num += 1

    if ctx.upe_exposure > ZERO:
        for detail in ctx.upe_details:
            steps.append(
                f"{step_num}. For UPE of ${Decimal(detail['upe_amount']):,.2f} from "
                f"{detail['trust_name']}: execute complying 7-year loan or repay "
                f"before lodgement."
            )
            step_num += 1

    steps.append(
        f"{step_num}. Document the purpose of each drawdown in workpapers."
    )

    if ctx.escalation_required:
        steps.append(
            "\n⚠ ESCALATION: Total exposure exceeds $200,000 — "
            "Flag to Elio per firm escalation policy."
        )

    return "\n".join(steps)


def _build_compliance_checklist(ctx):
    """Build the compliance checklist section."""
    checks = [
        ("Complying loan agreement", ctx.has_complying_agreement and ctx.agreement_covers_balance),
        ("Benchmark interest charged", ctx.interest_compliant),
        ("Minimum yearly repayment met", ctx.myr_compliant if ctx.myr_compliant is not None else None),
        ("UPE on complying terms", ctx.upe_exposure == ZERO),
    ]

    lines = ["COMPLIANCE CHECKLIST:"]
    for label, status in checks:
        if status is True:
            icon = "✅"
        elif status is False:
            icon = "❌"
        else:
            icon = "⬜"
        lines.append(f"  {icon} {label}")

    return "\n".join(lines)


def _log_activity(ctx, assessment):
    """Log the Div 7A assessment to the Activity trail."""
    from core.models import ActivityLog

    try:
        if ctx.overall_severity == "CLEAR":
            description = (
                f"Division 7A assessment: CLEAR — no exposure detected "
                f"for {ctx.entity.entity_name}."
            )
        else:
            description = (
                f"Division 7A assessment: {ctx.overall_severity} — "
                f"${ctx.total_exposure:,.2f} exposure detected. "
                f"Rules fired: {', '.join(ctx.rules_fired)}."
            )
            if ctx.escalation_required:
                description += " ESCALATION REQUIRED."

        ActivityLog.objects.create(
            financial_year=ctx.fy,
            event_type="audit_run",
            title=f"Div 7A Assessment ({ctx.overall_severity})",
            description=description,
        )
    except Exception:
        logger.exception("Failed to log Div 7A assessment activity")


# ============================================================================
# BATCH ASSESSMENT
# ============================================================================

def run_batch_div7a_assessment(entity_ids=None, year_label=None):
    """
    Run Div 7A assessment across multiple entities.

    Args:
        entity_ids: optional list of entity UUIDs to assess
        year_label: optional FY label (defaults to current)

    Returns:
        dict with batch results
    """
    from core.models import Entity, FinancialYear

    if entity_ids:
        entities = Entity.objects.filter(
            pk__in=entity_ids,
            entity_type="company",
        )
    else:
        entities = Entity.objects.filter(entity_type="company")

    results = {
        "total": 0,
        "assessed": 0,
        "skipped": 0,
        "critical": 0,
        "advisory": 0,
        "clear": 0,
        "errors": [],
    }

    for entity in entities:
        results["total"] += 1

        # Get the most recent FY
        fy_qs = FinancialYear.objects.filter(entity=entity)
        if year_label:
            fy_qs = fy_qs.filter(year_label=year_label)
        fy = fy_qs.order_by("-end_date").first()

        if not fy:
            results["skipped"] += 1
            continue

        try:
            result = run_div7a_assessment(str(fy.pk))
            if result.get("skipped"):
                results["skipped"] += 1
            else:
                results["assessed"] += 1
                severity = result.get("overall_severity", "CLEAR")
                if severity == "CRITICAL":
                    results["critical"] += 1
                elif severity == "ADVISORY":
                    results["advisory"] += 1
                else:
                    results["clear"] += 1
        except Exception as e:
            results["errors"].append(f"{entity.entity_name}: {str(e)}")
            logger.exception("Batch Div 7A assessment failed for %s", entity.entity_name)

    logger.info(
        "Batch Div 7A assessment complete: %d assessed, %d critical, %d advisory, %d clear",
        results["assessed"], results["critical"], results["advisory"], results["clear"],
    )
    return results
