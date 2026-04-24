"""
Eva Amber Indicators — Layer 1 Inline Variance Indicators

Computes five types of amber indicators for trial balance lines:
1. Significant Variance ($) — absolute dollar movement exceeds threshold
2. Significant Variance (%) — percentage movement exceeds threshold
3. Account Dropped — account existed in PY but has zero CY balance
4. Opening Balance Mismatch — CY opening != PY closing
5. Balance Sign Change — account changed from debit to credit or vice versa

Note: "Account Added" (formerly Trigger 4) was removed because a new account
appearing in the trial balance is almost always intentional and provides no
meaningful analytical signal — it simply duplicates information already visible
on screen.

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
        threshold_label = f"15% revenue materiality threshold"
    elif section in ("expenses", "cost_of_sales"):
        pct_threshold = EXPENSE_PCT_THRESHOLD
        threshold_label = f"20% expense materiality threshold"
    else:
        pct_threshold = VARIANCE_PCT_THRESHOLD
        threshold_label = f"20% materiality threshold"

    # ── Indicator 1: Significant Variance ($) ─────────────────────────
    # Fires when the movement is both material in dollar terms (≥$5,000)
    # and material as a percentage of the prior year balance.
    if abs_variance >= VARIANCE_ABS_THRESHOLD and abs_pct >= pct_threshold:
        direction = "increased" if variance_dollar > 0 else "decreased"
        sign = "+" if variance_dollar > 0 else ""
        indicators.append({
            "type": "significant_variance_dollar",
            "message": (
                f"⚠ Significant Variance — this account has moved by "
                f"${_fmt(abs_variance)} ({sign}{variance_pct}%) compared to prior year. "
                f"Prior year: ${_fmt(prior_net)} → Current year: ${_fmt(current_net)}. "
                f"This exceeds the {threshold_label} of ${_fmt(VARIANCE_ABS_THRESHOLD)} "
                f"and requires explanation or review."
            ),
        })

    # ── Indicator 2: Significant Variance (%) ─────────────────────────
    # Fires when the percentage movement is very large (≥50%) even if the
    # dollar amount is below the $5,000 threshold. Only fires if Indicator 1
    # did not already fire to avoid duplicate messages.
    if abs_pct >= Decimal("50") and abs_variance >= Decimal("1000") and not indicators:
        sign = "+" if variance_dollar > 0 else ""
        indicators.append({
            "type": "significant_variance_pct",
            "message": (
                f"⚠ Large Percentage Movement — this account has moved by "
                f"{sign}{variance_pct}% compared to prior year. "
                f"Prior year: ${_fmt(prior_net)} → Current year: ${_fmt(current_net)}. "
                f"While the dollar amount (${_fmt(abs_variance)}) is below the $5,000 "
                f"materiality threshold, the percentage movement is significant and "
                f"warrants review."
            ),
        })

    # ── Indicator 3: Account Dropped ──────────────────────────────────
    # Fires when an account had a balance last year but is zero this year.
    # This may indicate a write-off, disposal, settlement, or data omission.
    if prior_net != ZERO and current_net == ZERO:
        indicators.append({
            "type": "account_dropped",
            "message": (
                f"⚠ Account Dropped — this account had a prior year balance of "
                f"${_fmt(prior_net)} but has a zero balance this year. "
                f"This may indicate a write-off, disposal, settlement, or that "
                f"the account was omitted from this year's trial balance import. "
                f"Confirm the nil balance is correct."
            ),
        })

    # ── Indicator 4: Opening Balance Mismatch ─────────────────────────
    # Fires when the current year opening balance does not match the prior
    # year closing balance, which indicates a rollover or data integrity issue.
    opening_dr = getattr(line, "opening_debit", None)
    opening_cr = getattr(line, "opening_credit", None)
    if opening_dr is not None and opening_cr is not None:
        opening_net = (opening_dr or ZERO) - (opening_cr or ZERO)
        if opening_net != prior_net and prior_net != ZERO:
            diff = opening_net - prior_net
            sign = "+" if diff > 0 else ""
            indicators.append({
                "type": "opening_balance_mismatch",
                "message": (
                    f"⚠ Opening Balance Mismatch — the prior year closing balance "
                    f"(${_fmt(prior_net)}) does not match the current year opening balance "
                    f"(${_fmt(opening_net)}). Difference: {sign}${_fmt(diff)}. "
                    f"This may indicate an incorrect roll-forward, a manual adjustment "
                    f"to opening balances, or a data import issue. Investigate before finalising."
                ),
            })

    # ── Indicator 5: Balance Sign Change ──────────────────────────────
    # Fires when an account has flipped from debit to credit or vice versa.
    # This is almost always significant and warrants explanation.
    if prior_net != ZERO and current_net != ZERO:
        prior_is_debit = prior_net > ZERO
        current_is_debit = current_net > ZERO
        if prior_is_debit != current_is_debit:
            old_side = "debit" if prior_is_debit else "credit"
            new_side = "credit" if prior_is_debit else "debit"
            indicators.append({
                "type": "balance_sign_change",
                "message": (
                    f"⚠ Balance Side Change — this account was on the {old_side} side "
                    f"(${_fmt(prior_net)}) last year but is now on the {new_side} side "
                    f"(${_fmt(current_net)}) this year. "
                    f"This is unusual and almost always requires explanation — it may "
                    f"indicate an overpayment, reversal, reclassification, or data error."
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
