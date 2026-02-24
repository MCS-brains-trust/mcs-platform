"""
Trust Tax Planning — Calculation Engine

All tax calculations are server-side. This module reads rates from
TaxReferenceData and never hardcodes values.

Calculation order (per spec §5.1):
1. Total Taxable Income
2. Gross Tax Payable (Individual: progressive scale / Company: flat rate)
3. Medicare Levy (individuals only)
4. LITO Offset (individuals only)
5. Franking Credit Offset
6. Net Tax Payable (floor at 0)
7. Effective Tax Rate
8. Optimiser Totals
"""
from decimal import Decimal, ROUND_HALF_UP

D = Decimal
ZERO = D("0")
TWO_PLACES = D("0.01")
FOUR_PLACES = D("0.0001")


def get_tax_rates(fy_label):
    """
    Load tax reference data for a given FY label.
    Falls back to FY2025 defaults if not found.
    Returns a dict of key -> Decimal.
    """
    from core.models import TaxReferenceData

    rates = {}
    # Try specific FY first, then fall back to default ("")
    for label in [fy_label, ""]:
        qs = TaxReferenceData.objects.filter(financial_year_label=label)
        for row in qs:
            if row.key not in rates:
                rates[row.key] = D(row.value)

    # Hardcoded fallback only if DB has nothing at all (safety net)
    defaults = {
        "tax_free_threshold": D("18200"),
        "bracket_1_rate": D("0.19"),
        "bracket_1_upper": D("45000"),
        "bracket_2_rate": D("0.325"),
        "bracket_2_upper": D("120000"),
        "bracket_3_rate": D("0.37"),
        "bracket_3_upper": D("180000"),
        "bracket_4_rate": D("0.45"),
        "medicare_levy_rate": D("0.02"),
        "medicare_low_income_threshold": D("26000"),
        "lito_max_offset": D("700"),
        "lito_shade_out_start": D("37500"),
        "lito_shade_out_end": D("66667"),
        "company_base_rate": D("0.25"),
        "company_non_base_rate": D("0.30"),
        "trustee_default_tax_rate": D("0.47"),
    }
    for k, v in defaults.items():
        rates.setdefault(k, v)

    return rates


def calc_individual_gross_tax(taxable_income, rates):
    """
    Apply ATO progressive tax scale.
    Returns gross tax payable (before offsets).
    """
    income = taxable_income
    threshold = rates["tax_free_threshold"]

    if income <= threshold:
        return ZERO

    tax = ZERO
    brackets = [
        (threshold, rates["bracket_1_upper"], rates["bracket_1_rate"]),
        (rates["bracket_1_upper"], rates["bracket_2_upper"], rates["bracket_2_rate"]),
        (rates["bracket_2_upper"], rates["bracket_3_upper"], rates["bracket_3_rate"]),
        (rates["bracket_3_upper"], None, rates["bracket_4_rate"]),
    ]

    for lower, upper, rate in brackets:
        if income <= lower:
            break
        if upper is None:
            taxable_in_bracket = income - lower
        else:
            taxable_in_bracket = min(income, upper) - lower
        tax += (taxable_in_bracket * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return tax


def calc_medicare_levy(taxable_income, rates):
    """
    Calculate Medicare Levy for individuals.
    2% of taxable income, with shade-in below low-income threshold.
    """
    if taxable_income <= ZERO:
        return ZERO

    low_threshold = rates["medicare_low_income_threshold"]
    levy_rate = rates["medicare_levy_rate"]

    if taxable_income <= low_threshold:
        # Shade-in: 10% of excess over shade-in floor
        # Medicare shade-in floor = low_threshold * 0.885 (approx)
        shade_in_floor = (low_threshold * D("0.885")).quantize(TWO_PLACES)
        if taxable_income <= shade_in_floor:
            return ZERO
        levy = ((taxable_income - shade_in_floor) * D("0.10")).quantize(TWO_PLACES)
        normal_levy = (taxable_income * levy_rate).quantize(TWO_PLACES)
        return min(levy, normal_levy)

    return (taxable_income * levy_rate).quantize(TWO_PLACES)


def calc_lito(taxable_income, rates):
    """
    Low Income Tax Offset.
    Full offset below shade_out_start, phases out linearly to shade_out_end.
    """
    if taxable_income <= ZERO:
        return ZERO

    max_offset = rates["lito_max_offset"]
    start = rates["lito_shade_out_start"]
    end = rates["lito_shade_out_end"]

    if taxable_income <= start:
        return max_offset

    if taxable_income >= end:
        return ZERO

    # Linear phase-out
    reduction_rate = max_offset / (end - start)
    offset = max_offset - ((taxable_income - start) * reduction_rate)
    return max(ZERO, offset.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def calculate_beneficiary_tax(
    beneficiary_type,
    outside_income,
    proposed_distribution,
    franking_credits_share,
    rates,
    company_tax_rate_override=None,
):
    """
    Calculate tax for a single beneficiary row.

    Args:
        beneficiary_type: "individual", "company", or "trust"
        outside_income: Decimal
        proposed_distribution: Decimal
        franking_credits_share: Decimal (proportional share of trust's franking credits)
        rates: dict from get_tax_rates()
        company_tax_rate_override: Decimal or None (for non-base-rate companies)

    Returns:
        dict with all calculated fields
    """
    result = {
        "grossed_up_franking_credits": ZERO,
        "total_taxable_income": ZERO,
        "gross_tax_payable": ZERO,
        "medicare_levy": ZERO,
        "lito_offset": ZERO,
        "franking_credit_offset": ZERO,
        "net_tax_payable": ZERO,
        "effective_tax_rate": ZERO,
        "is_trust_beneficiary": False,
    }

    if beneficiary_type == "trust":
        # Trust beneficiary — no calculation, just track distribution for balance check
        result["is_trust_beneficiary"] = True
        result["total_taxable_income"] = ZERO
        return result

    # Step 1: Total Taxable Income
    grossed_up = franking_credits_share
    result["grossed_up_franking_credits"] = grossed_up.quantize(TWO_PLACES)

    if beneficiary_type == "company":
        # Companies: no outside income
        total_taxable = proposed_distribution + grossed_up
    else:
        total_taxable = outside_income + proposed_distribution + grossed_up

    result["total_taxable_income"] = total_taxable.quantize(TWO_PLACES)

    if total_taxable <= ZERO:
        return result

    # Step 2/3: Gross Tax Payable
    if beneficiary_type == "company":
        if company_tax_rate_override:
            rate = company_tax_rate_override
        else:
            rate = rates["company_base_rate"]
        result["gross_tax_payable"] = (total_taxable * rate).quantize(TWO_PLACES)
    else:
        # Individual
        result["gross_tax_payable"] = calc_individual_gross_tax(total_taxable, rates)

    # Step 4: Medicare Levy (individuals only)
    if beneficiary_type == "individual":
        result["medicare_levy"] = calc_medicare_levy(total_taxable, rates)

    # Step 5: LITO (individuals only)
    if beneficiary_type == "individual":
        result["lito_offset"] = calc_lito(total_taxable, rates)

    # Step 6: Franking Credit Offset
    # Cannot reduce below zero
    result["franking_credit_offset"] = min(
        grossed_up,
        result["gross_tax_payable"] + result["medicare_levy"]
    ).quantize(TWO_PLACES)

    # Step 7: Net Tax Payable (floor at 0)
    net = (
        result["gross_tax_payable"]
        + result["medicare_levy"]
        - result["lito_offset"]
        - result["franking_credit_offset"]
    )
    result["net_tax_payable"] = max(ZERO, net.quantize(TWO_PLACES))

    # Step 8: Effective Tax Rate
    if total_taxable > ZERO:
        result["effective_tax_rate"] = (
            result["net_tax_payable"] / total_taxable
        ).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

    return result


def calculate_section1_from_tb(fy):
    """
    Calculate Section 1 — Distributable Income from the trial balance.
    Uses entity COA tags (is_non_deductible, is_non_assessable, is_cgt,
    is_franked_dividend, is_franking_credit).

    Returns dict with all Section 1 fields.
    """
    from core.models import EntityChartOfAccount

    entity = fy.entity
    tb_lines = fy.trial_balance_lines.select_related("mapped_line_item").all()

    # Build lookup of entity COA tags by account_code
    coa_tags = {}
    for ea in EntityChartOfAccount.objects.filter(entity=entity):
        coa_tags[ea.account_code] = {
            "is_non_deductible": ea.is_non_deductible,
            "is_non_assessable": ea.is_non_assessable,
            "is_cgt": ea.is_cgt,
            "is_franked_dividend": ea.is_franked_dividend,
            "is_franking_credit": ea.is_franking_credit,
            "section": ea.section,
        }

    # P&L sections for net profit calculation
    pl_sections = {"revenue", "cost_of_sales", "expenses"}

    net_profit = ZERO
    non_deductible = ZERO
    non_assessable = ZERO
    capital_gains = ZERO
    franked_dividends = ZERO
    franking_credits = ZERO

    for line in tb_lines:
        tags = coa_tags.get(line.account_code, {})
        section = tags.get("section", "")

        # Net profit = sum of all P&L closing balances
        # Revenue is credit-normal (positive closing = credit excess)
        # Expenses are debit-normal (positive closing = debit excess)
        if section in pl_sections or (
            line.mapped_line_item and
            line.mapped_line_item.financial_statement == "income_statement"
        ):
            # For P&L: closing_balance represents the net movement
            # Revenue accounts: credit > debit = positive closing = income
            # Expense accounts: debit > credit = positive closing = expense
            # Net profit = revenue - expenses = credit balances - debit balances
            net_profit += line.credit - line.debit

        # Tagged accounts
        if tags.get("is_non_deductible"):
            non_deductible += abs(line.closing_balance)
        if tags.get("is_non_assessable"):
            non_assessable += abs(line.closing_balance)
        if tags.get("is_cgt"):
            capital_gains += abs(line.closing_balance)
        if tags.get("is_franked_dividend"):
            franked_dividends += abs(line.closing_balance)
        if tags.get("is_franking_credit"):
            franking_credits += abs(line.closing_balance)

    distributable = net_profit + non_deductible - non_assessable

    return {
        "net_profit_before_distributions": net_profit.quantize(TWO_PLACES),
        "non_deductible_expenses": non_deductible.quantize(TWO_PLACES),
        "non_assessable_income": non_assessable.quantize(TWO_PLACES),
        "distributable_income": distributable.quantize(TWO_PLACES),
        "capital_gains": capital_gains.quantize(TWO_PLACES),
        "franked_dividends": franked_dividends.quantize(TWO_PLACES),
        "franking_credits": franking_credits.quantize(TWO_PLACES),
    }


def calculate_all_beneficiaries(worksheet, beneficiary_data, rates):
    """
    Calculate tax for all beneficiaries and return optimiser totals.

    Args:
        worksheet: TaxPlanningWorksheet instance
        beneficiary_data: list of dicts with keys:
            beneficiary_id, beneficiary_type, outside_income, proposed_distribution,
            company_tax_rate_override (optional)
        rates: dict from get_tax_rates()

    Returns:
        {
            "rows": [{ beneficiary_id, ...calculated fields... }, ...],
            "optimiser": { total_distributable, total_distributed, undistributed,
                          total_tax, weighted_effective_rate }
        }
    """
    total_distributed = ZERO
    total_proposed = ZERO

    # First pass: sum proposed distributions to calculate franking credit shares
    for bd in beneficiary_data:
        total_proposed += D(str(bd.get("proposed_distribution", 0)))

    rows = []
    total_tax = ZERO

    for bd in beneficiary_data:
        proposed = D(str(bd.get("proposed_distribution", 0)))
        outside = D(str(bd.get("outside_income", 0)))
        btype = bd.get("beneficiary_type", "individual")
        override = bd.get("company_tax_rate_override")
        if override:
            override = D(str(override))

        # Proportional franking credit share
        if total_proposed > ZERO and proposed > ZERO:
            fc_share = (worksheet.franking_credits * proposed / total_proposed).quantize(TWO_PLACES)
        else:
            fc_share = ZERO

        result = calculate_beneficiary_tax(
            beneficiary_type=btype,
            outside_income=outside,
            proposed_distribution=proposed,
            franking_credits_share=fc_share,
            rates=rates,
            company_tax_rate_override=override,
        )
        result["beneficiary_id"] = str(bd["beneficiary_id"])
        rows.append(result)

        total_distributed += proposed
        if not result.get("is_trust_beneficiary"):
            total_tax += result["net_tax_payable"]

    distributable = worksheet.distributable_income
    undistributed = distributable - total_distributed
    weighted_rate = ZERO
    if total_distributed > ZERO:
        weighted_rate = (total_tax / total_distributed).quantize(FOUR_PLACES)

    return {
        "rows": rows,
        "optimiser": {
            "total_distributable": str(distributable),
            "total_distributed": str(total_distributed.quantize(TWO_PLACES)),
            "undistributed": str(undistributed.quantize(TWO_PLACES)),
            "total_tax": str(total_tax.quantize(TWO_PLACES)),
            "weighted_effective_rate": str(weighted_rate),
        },
    }
