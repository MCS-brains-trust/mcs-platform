"""
Eva Amber Indicators — Layer 1 Inline Variance Indicators

Computes six types of amber indicators for trial balance lines:
1. Significant Variance ($) — absolute dollar movement exceeds threshold
2. Significant Variance (%) — percentage movement exceeds threshold
3. Account Dropped — account existed in PY but has zero CY balance
4. Account Added — new account with no PY balance
5. Opening Balance Mismatch — CY opening != PY closing
6. Balance Sign Change — account changed from debit to credit or vice versa

These are informational only — no workflow, no dismiss, no resolution.
Displayed as amber ⚠ icons inline after the account name in the TB.
"""
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0")

# Thresholds (matching risk engine defaults)
VARIANCE_PCT_THRESHOLD = Decimal("20")      # 20% for general accounts
REVENUE_PCT_THRESHOLD = Decimal("15")       # 15% for revenue accounts
EXPENSE_PCT_THRESHOLD = Decimal("20")       # 20% for expense accounts
VARIANCE_ABS_THRESHOLD = Decimal("5000")    # $5,000 minimum absolute change

# Accounts excluded from amber indicators (same as risk engine)
_EXCLUDED_CODES = {"4199", "9999"}
_EXCLUDED_KEYWORDS = {
    "retained profit", "retained earning", "accumulated profit",
    "accumulated earning", "profit & loss appropriation",
    "current year earnings", "current earnings",
}
_EXCLUDED_STD_CODES = {"EQ-RE-001", "EQ-RE-002"}


def _derive_section(mapped_line_item):
    """Derive the financial statement section from a mapped line item."""
    if not mapped_line_item:
        return "other"
    code = getattr(mapped_line_item, "code", "") or ""
    if code.startswith("INC-") or code.startswith("REV-"):
        return "revenue"
    elif code.startswith("COS-") or code.startswith("COGS-"):
        return "cost_of_sales"
    elif code.startswith("EXP-") or code.startswith("OE-"):
        return "expenses"
    elif code.startswith("EQ-"):
        return "equity"
    elif code.startswith("CA-") or code.startswith("NCA-"):
        return "assets"
    elif code.startswith("CL-") or code.startswith("NCL-"):
        return "liabilities"
    return "other"


def _is_excluded(line):
    """Check if a TB line should be excluded from amber indicators."""
    if line.account_code in _EXCLUDED_CODES:
        return True
    acct_lower = (line.account_name or "").lower()
    if any(kw in acct_lower for kw in _EXCLUDED_KEYWORDS):
        return True
    if line.mapped_line_item:
        std_code = getattr(line.mapped_line_item, "code", "") or ""
        if std_code in _EXCLUDED_STD_CODES:
            return True
    return False


def compute_amber_indicators(line):
    """
    Compute amber indicators for a single trial balance line.

    Args:
        line: A TrialBalanceLine (or aggregated line) with:
              - display_dr / display_cr (or effective_dr / effective_cr)
              - prior_debit / prior_credit
              - variance_amount, variance_percentage
              - mapped_line_item
              - account_code, account_name

    Returns:
        List of indicator dicts: [{"type": "...", "message": "..."}]
        Empty list if no indicators triggered.
    """
    indicators = []

    if _is_excluded(line):
        return indicators

    # Get balances
    cy_dr = getattr(line, "display_dr", None) or getattr(line, "effective_dr", ZERO) or ZERO
    cy_cr = getattr(line, "display_cr", None) or getattr(line, "effective_cr", ZERO) or ZERO
    py_dr = getattr(line, "prior_debit", ZERO) or ZERO
    py_cr = getattr(line, "prior_credit", ZERO) or ZERO

    current_net = cy_dr - cy_cr
    prior_net = py_dr - py_cr

    variance_dollar = current_net - prior_net
    abs_variance = abs(variance_dollar)

    # Calculate percentage
    if prior_net != ZERO:
        variance_pct = (variance_dollar / abs(prior_net) * Decimal("100")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    elif current_net != ZERO:
        variance_pct = Decimal("100.0")
    else:
        variance_pct = ZERO

    abs_pct = abs(variance_pct)

    # Determine section-specific threshold
    section = _derive_section(getattr(line, "mapped_line_item", None))
    if section == "revenue":
        pct_threshold = REVENUE_PCT_THRESHOLD
    elif section in ("expenses", "cost_of_sales"):
        pct_threshold = EXPENSE_PCT_THRESHOLD
    else:
        pct_threshold = VARIANCE_PCT_THRESHOLD

    # ── Indicator 1: Significant Variance ($) ─────────────────────────
    if abs_variance >= VARIANCE_ABS_THRESHOLD and abs_pct >= pct_threshold:
        direction = "increased" if variance_dollar > 0 else "decreased"
        indicators.append({
            "type": "significant_variance_dollar",
            "message": (
                f"Significant variance: Prior year ${_fmt(prior_net)}. "
                f"Current year ${_fmt(current_net)}. "
                f"Movement: ${_fmt(variance_dollar)} ({variance_pct}%). "
                f"Balance {direction} by ${_fmt(abs_variance)}."
            ),
        })

    # ── Indicator 2: Significant Variance (%) ─────────────────────────
    # Only trigger if $ indicator didn't already fire and % is very high
    if abs_pct >= Decimal("50") and abs_variance >= Decimal("1000") and not indicators:
        indicators.append({
            "type": "significant_variance_pct",
            "message": (
                f"Large percentage movement: {variance_pct}%. "
                f"Prior year ${_fmt(prior_net)}. Current year ${_fmt(current_net)}."
            ),
        })

    # ── Indicator 3: Account Dropped ──────────────────────────────────
    if prior_net != ZERO and current_net == ZERO:
        indicators.append({
            "type": "account_dropped",
            "message": (
                f"Account dropped: Had a prior year balance of ${_fmt(prior_net)} "
                f"but has zero balance this year."
            ),
        })

    # ── Indicator 4: Account Added ────────────────────────────────────
    if prior_net == ZERO and current_net != ZERO and abs(current_net) >= Decimal("1000"):
        indicators.append({
            "type": "account_added",
            "message": (
                f"New account: No prior year balance. "
                f"Current year balance is ${_fmt(current_net)}."
            ),
        })

    # ── Indicator 5: Opening Balance Mismatch ─────────────────────────
    # Check if the line has opening balance data
    opening_dr = getattr(line, "opening_debit", None)
    opening_cr = getattr(line, "opening_credit", None)
    if opening_dr is not None and opening_cr is not None:
        opening_net = (opening_dr or ZERO) - (opening_cr or ZERO)
        if opening_net != prior_net and prior_net != ZERO:
            diff = opening_net - prior_net
            indicators.append({
                "type": "opening_balance_mismatch",
                "message": (
                    f"Opening balance mismatch: Prior year closing was ${_fmt(prior_net)} "
                    f"but current year opening is ${_fmt(opening_net)}. "
                    f"Difference: ${_fmt(diff)}."
                ),
            })

    # ── Indicator 6: Balance Sign Change ──────────────────────────────
    if prior_net != ZERO and current_net != ZERO:
        prior_is_debit = prior_net > ZERO
        current_is_debit = current_net > ZERO
        if prior_is_debit != current_is_debit:
            old_side = "debit" if prior_is_debit else "credit"
            new_side = "debit" if current_is_debit else "credit"
            indicators.append({
                "type": "balance_sign_change",
                "message": (
                    f"Balance sign change: Was {old_side} (${_fmt(prior_net)}) in prior year, "
                    f"now {new_side} (${_fmt(current_net)}) this year."
                ),
            })

    return indicators


def annotate_tb_lines_with_amber(tb_lines):
    """
    Annotate a list of TB lines with amber indicators.

    Adds `amber_indicators` attribute to each line.

    Args:
        tb_lines: List of TrialBalanceLine objects (or aggregated lines)
    """
    for line in tb_lines:
        line.amber_indicators = compute_amber_indicators(line)


def _fmt(value):
    """Format a Decimal value for display in indicator messages."""
    if value is None:
        return "0"
    try:
        abs_val = abs(value)
        formatted = f"{abs_val:,.0f}"
        if value < 0:
            return f"({formatted})"
        return formatted
    except (TypeError, ValueError):
        return str(value)
