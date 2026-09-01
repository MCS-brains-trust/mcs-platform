"""
BAS Period utility functions.

Provides period date computation, status calculation, bank coverage
analysis, and GST calculation filtering for the period-aware BAS
redesign.
"""
import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from collections import OrderedDict

from django.db.models import Q


# ── Period date helpers ──────────────────────────────────────────────────────

# Standard Australian FY quarterly boundaries (July–June)
QUARTERLY_BOUNDARIES = {
    1: ("07-01", "09-30"),  # Q1 Jul–Sep
    2: ("10-01", "12-31"),  # Q2 Oct–Dec
    3: ("01-01", "03-31"),  # Q3 Jan–Mar
    4: ("04-01", "06-30"),  # Q4 Apr–Jun
}

# Monthly boundaries in FY order: period 1 = July, period 12 = June
MONTHLY_FY_ORDER = [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]


def _fy_start_year(fy):
    """The calendar year the financial year's July falls in.

    Derived from end_date, not start_date. An entity that began trading part
    way through a year has a stub financial year — 1 Apr 2026 to 30 Jun 2026,
    say — but it still ends 30 June and its BAS quarters are still the
    statutory ones. Reading start_date.year as "the July this year opened in"
    is only true for a full year: on that stub it yielded 2026, putting Q4 in
    April–June 2027 and returning an empty BAS for every quarter of a year
    whose ledger was fully allocated and reconciled.

    A financial year is identified by the June it ENDS in, which holds for both
    shapes.
    """
    if fy.end_date.month >= 7:
        return fy.end_date.year
    return fy.end_date.year - 1


def get_period_dates(fy, period_type, period_number):
    """
    Return (start_date, end_date) for a given period within a financial year.
    Uses standard July–June boundaries for Sprint 3.
    """
    fy_start_year = _fy_start_year(fy)  # e.g. 2025 for FY2026 (Jul 2025 – Jun 2026)

    if period_type == "quarterly":
        mm_start, mm_end = QUARTERLY_BOUNDARIES[period_number]
        start_month, start_day = int(mm_start.split("-")[0]), int(mm_start.split("-")[1])
        end_month, end_day = int(mm_end.split("-")[0]), int(mm_end.split("-")[1])

        # Q1 (Jul–Sep) and Q2 (Oct–Dec) are in fy_start_year
        # Q3 (Jan–Mar) and Q4 (Apr–Jun) are in fy_start_year + 1
        if period_number <= 2:
            start_year = fy_start_year
            end_year = fy_start_year
        else:
            start_year = fy_start_year + 1
            end_year = fy_start_year + 1

        return date(start_year, start_month, start_day), date(end_year, end_month, end_day)

    else:  # monthly
        cal_month = MONTHLY_FY_ORDER[period_number - 1]
        if cal_month >= 7:
            year = fy_start_year
        else:
            year = fy_start_year + 1
        last_day = calendar.monthrange(year, cal_month)[1]
        return date(year, cal_month, 1), date(year, cal_month, last_day)


def get_all_period_dates(fy, period_type):
    """Return a list of (period_number, start_date, end_date) for all periods."""
    count = 4 if period_type == "quarterly" else 12
    return [
        (n, *get_period_dates(fy, period_type, n))
        for n in range(1, count + 1)
    ]


def ensure_bas_periods(fy, period_type):
    """
    Ensure BASPeriod records exist for all periods of the given type.
    Creates missing ones lazily. Returns the queryset of all periods.
    """
    from .models import BASPeriod

    existing = set(
        BASPeriod.objects.filter(
            financial_year=fy, period_type=period_type
        ).values_list("period_number", flat=True)
    )

    to_create = []
    for num, start, end in get_all_period_dates(fy, period_type):
        if num not in existing:
            to_create.append(BASPeriod(
                financial_year=fy,
                period_type=period_type,
                period_number=num,
                period_start=start,
                period_end=end,
            ))

    if to_create:
        BASPeriod.objects.bulk_create(to_create, ignore_conflicts=True)

    return BASPeriod.objects.filter(
        financial_year=fy, period_type=period_type
    ).order_by("period_number")


# ── Bank coverage analysis ───────────────────────────────────────────────────

def get_bank_coverage(fy, period_start, period_end):
    """
    Analyse bank statement coverage for a period.

    Returns a dict:
        {
            "status": "complete" | "partial" | "none",
            "months": [
                {"month": "Oct 2025", "covered": True},
                {"month": "Nov 2025", "covered": True},
                {"month": "Dec 2025", "covered": False},
            ],
            "missing": ["Dec 2025"],
        }

    Coverage is determined by checking whether approved bank transactions
    (TrialBalanceLine with source='bank_statement') exist for each calendar
    month within the period.
    """
    from .models import TrialBalanceLine

    # Get all bank-statement-sourced TB lines in this period
    bank_lines = TrialBalanceLine.objects.filter(
        financial_year=fy,
        source="bank_statement",
    )

    # We need to check transaction dates. TB lines from bank statements
    # have a created_at timestamp, but the spec says to check by transaction
    # date. Since TB lines don't have a transaction_date field, we check
    # the AdjustingJournal dates and the PendingTransaction dates.
    # For bank_statement TB lines, we use the description or created_at
    # as a proxy. However, the most reliable approach is to check
    # PendingTransaction records from the review app.
    from review.models import PendingTransaction, ReviewJob

    # Get all review jobs for this entity
    entity = fy.entity
    jobs = ReviewJob.objects.filter(entity=entity)

    # Get all confirmed transactions from those jobs
    txns = PendingTransaction.objects.filter(
        job__in=jobs,
        is_confirmed=True,
    )

    # Build month coverage map
    months = []
    missing = []
    current = date(period_start.year, period_start.month, 1)
    end_month = date(period_end.year, period_end.month, 1)

    while current <= end_month:
        month_label = current.strftime("%b %Y")
        month_start = current
        last_day = calendar.monthrange(current.year, current.month)[1]
        month_end = date(current.year, current.month, last_day)

        # Check if any bank-sourced TB lines exist that were created in this
        # month range. Also check PendingTransactions with dates in this month.
        has_bank_txns = False

        # Check PendingTransactions (date field is a CharField in various
        # formats: "2025-10-15", "15/10/2025", "1/08/2025", etc.)
        # Try ISO prefix first, then fall back to parsing all dates.
        month_prefix = current.strftime("%Y-%m")
        txn_count = txns.filter(date__startswith=month_prefix).count()
        if txn_count > 0:
            has_bank_txns = True
        else:
            # Dates may be in d/m/Y format — parse and check in Python
            from datetime import datetime as _dt
            for txn in txns:
                parsed = None
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
                    try:
                        parsed = _dt.strptime(txn.date.strip(), fmt).date()
                        break
                    except (ValueError, AttributeError):
                        continue
                if parsed and month_start <= parsed <= month_end:
                    has_bank_txns = True
                    break

        # Also check if bank_statement TB lines were created in this month
        if not has_bank_txns:
            bank_count = bank_lines.filter(
                created_at__date__gte=month_start,
                created_at__date__lte=month_end,
            ).count()
            if bank_count > 0:
                has_bank_txns = True

        months.append({"month": month_label, "covered": has_bank_txns})
        if not has_bank_txns:
            missing.append(month_label)

        # Advance to next month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    if not months:
        status = "none"
    elif len(missing) == len(months):
        status = "none"
    elif len(missing) == 0:
        status = "complete"
    else:
        status = "partial"

    return {
        "status": status,
        "months": months,
        "missing": missing,
    }


def get_period_coverage(fy, period_start, period_end):
    """Is this BAS period accounted for?

    ``get_bank_coverage`` answers a narrower question -- is the *bank* data
    complete -- and stays the primitive. This one adds the other way a period
    can be accounted for: a posted cashbook journal, which is the accountant
    asserting the quarter is written up, the same assertion that importing and
    confirming every bank transaction makes.

    The journal only counts when the period has no bank activity at all. The
    moment one bank month is present the month-by-month rule governs
    unchanged, so a forgotten import is still flagged on a mixed period.

    Adds two keys to the bank-coverage dict:
        source        -- "bank" | "journal" | "none"
        journal_refs  -- reference numbers, empty unless source == "journal"

    A journalled period reports no months and nothing missing: JournalLine has
    no date (only AdjustingJournal.journal_date), so month-level coverage is
    not derivable and naming months would be inventing data.
    """
    from .models import AdjustingJournal

    coverage = get_bank_coverage(fy, period_start, period_end)

    if any(m["covered"] for m in coverage["months"]):
        coverage["source"] = "bank"
        coverage["journal_refs"] = []
        return coverage

    journal_refs = list(
        AdjustingJournal.objects.filter(
            financial_year=fy,
            journal_type=AdjustingJournal.JournalType.CASHBOOK,
            status="posted",
            journal_date__gte=period_start,
            journal_date__lte=period_end,
        )
        .order_by("reference_number")
        .values_list("reference_number", flat=True)
    )
    if journal_refs:
        return {
            "status": "complete",
            "months": [],
            "missing": [],
            "source": "journal",
            "journal_refs": journal_refs,
        }

    coverage["source"] = "none"
    coverage["journal_refs"] = []
    return coverage


def compute_period_status(fy, period_start, period_end, bas_period=None):
    """
    Compute the dynamic status of a BAS period from its coverage -- bank
    transactions, or a posted cashbook journal. If the period is explicitly
    lodged, that status is preserved.

    Returns one of: 'lodged', 'ready', 'partial', 'empty'
    """
    if bas_period and bas_period.status == "lodged":
        return "lodged"

    coverage = get_period_coverage(fy, period_start, period_end)
    if coverage["status"] == "complete":
        return "ready"
    elif coverage["status"] == "partial":
        return "partial"
    else:
        return "empty"


# ── GST Calculation Engine (period-filtered) ─────────────────────────────────

# ── Tax-treatment vocabulary ────────────────────────────────────────────────
#
# Two different vocabularies describe GST treatment in this codebase:
#
#   * "tax types"  — the long-form labels in PendingTransaction.TAX_TYPE_CHOICES
#                    ("GST on Income", "GST Free Expenses", ...)
#   * "tax codes"  — the MYOB-style codes on ChartOfAccount.tax_code
#                    ("GST", "INP", "FRE", "CAP", "ITS", "N-T", ...)
#
# Several write paths store a tax *code* in PendingTransaction.confirmed_tax_type
# without validating it against TAX_TYPE_CHOICES (core/views.py
# review_approve_selected / review_confirm_transaction, core/views_bas.py
# bas_reallocate_transaction and bas_bulk_reallocate). The engine therefore has
# to understand both vocabularies: an unrecognised value used to fall through to
# the account's default tax code, which is how transactions explicitly coded as
# taxable ended up reported as GST-free (and vice versa).
TAX_TREATMENT_ALIASES = {
    # Canonical long-form tax types
    "GST ON INCOME": "GST",
    "GST ON EXPENSES": "INP",
    "GST FREE INCOME": "FRE",
    "GST FREE EXPENSES": "FRE",
    "GST FREE": "FRE",
    "INPUT TAXED": "ITS",
    "BAS EXCLUDED": "N-T",
    "NOT REPORTABLE": "N-T",
    "N-T": "N-T",
    # MYOB / chart-of-accounts tax codes
    "GST": "GST",
    "INP": "INP",
    "GNR": "GST",
    "ADS": "ADS",
    "CAP": "CAP",
    "FCA": "FCA",
    "FRE": "FRE",
    "FOA": "FOA",
    "ITS": "ITS",
    "IOA": "IOA",
}

# Treatment groups, direction-agnostic. A revenue line carrying "INP" (or an
# expense line carrying "GST") is a direction mix-up in the source data, not a
# reason to drop the amount or flip its GST treatment.
TAXABLE_CODES = {"GST", "INP", "GNR", "CAP", "ADS"}
GST_FREE_CODES = {"FRE", "FOA", "FCA"}
INPUT_TAXED_CODES = {"ITS", "IOA"}
CAPITAL_CODES = {"CAP", "FCA"}
# Explicitly out of scope for the BAS: not merely GST-free, but excluded from
# the worksheet entirely (drawings, loan principal, transfers, wages).
NON_REPORTABLE_CODES = {"N-T"}


def normalise_tax_treatment(raw):
    """
    Map either vocabulary onto a single internal tax code.

    Returns "" when the value is empty or unrecognised, so callers can apply
    their own fallback (usually the account's default tax code).
    """
    if not raw:
        return ""
    return TAX_TREATMENT_ALIASES.get(str(raw).strip().upper(), "")


def calculate_gst_for_period(fy, period_start=None, period_end=None):
    """
    Calculate GST figures (G1–G20, 1A, 1B, Net) for a financial year,
    optionally filtered to a specific date range.

    When period_start/period_end are None the full year is calculated, which
    additionally picks up undated balances (imported trial balances, rollover
    lines, journal-sourced adjustment lines) that cannot be attributed to a
    single period.

    The bank-statement portion is always derived from the underlying confirmed
    PendingTransactions, for both the full-year and the period views. It used to
    be calculated from the aggregated per-account TrialBalanceLine in the
    full-year view, which destroyed the per-transaction tax treatment: a revenue
    account holding both GST-free and taxable sales was reported 100% taxable
    at the account's default tax code, and the whole balance was grossed up by
    11/10. Sharing one source also makes the four quarters reconcile to the
    full year whenever every dollar is bank-statement sourced.

    Returns a dict with:
        - bas_data: {G1, G2, ..., G20, 1A, 1B, gst_payable}
        - sales_lines: [...]
        - purchase_lines: [...]
        - capital_lines: [...]
        - excluded_lines: [...]
        - sales_transactions: [...]   (individual transaction details)
        - purchase_transactions: [...] (individual transaction details)
    """
    from .models import ChartOfAccount, EntityChartOfAccount, template_entity_type

    entity = fy.entity
    entity_type = entity.entity_type

    # Build COA lookups
    coa_lookup = {}
    for coa in ChartOfAccount.objects.filter(entity_type=template_entity_type(entity_type), is_active=True):
        coa_lookup[coa.account_code] = coa

    entity_coa_lookup = {}
    for ecoa in EntityChartOfAccount.objects.filter(entity=entity):
        entity_coa_lookup[ecoa.account_code] = ecoa

    full_year = not (period_start and period_end)
    return _calculate_gst(
        fy, entity, entity_type, coa_lookup, entity_coa_lookup,
        period_start or fy.start_date,
        period_end or fy.end_date,
        full_year=full_year,
    )


def _classify_line(account_code, tax_code, section, amount, coa_lookup,
                   entity_coa_lookup, source=None, gross_up=None):
    """
    Classify a single account into BAS labels and return the
    contribution to each G-label bucket.

    Returns (bas_label, g_contributions, amount) where g_contributions is a dict
    of label -> amount to add.

    `amount` must already be GROSS (GST-inclusive) unless `gross_up` applies.
    Aggregated TrialBalanceLines store NET amounts (ex-GST) with the GST in the
    3380 control account, so those are grossed up by 11/10. Per-transaction
    amounts come straight off the bank statement and are already gross — passing
    gross_up=False for those avoids fabricating a further 10%.
    """
    if gross_up is None:
        gross_up = source == "bank_statement"
    if gross_up and tax_code in TAXABLE_CODES:
        amount = (amount * Decimal("11") / Decimal("10")).quantize(Decimal("0.01"))

    contributions = {}
    bas_label = ""

    # Explicitly non-reportable amounts stay off the worksheet entirely. They
    # used to be reported as GST-free sales (G1/G3) or GST-free purchases
    # (G11/G14), which overstated turnover and purchases on a lodged figure.
    if tax_code in NON_REPORTABLE_CODES:
        return "", {}, amount

    if section in ("revenue", "Revenue"):
        contributions["G1"] = amount
        bas_label = "G1"

        if tax_code in INPUT_TAXED_CODES:
            contributions["G4"] = amount
            bas_label = "G4 (Input Taxed)"
        elif tax_code == "ADS":
            contributions["G7"] = amount
            bas_label = "G7 (Adjustment)"
        elif tax_code in GST_FREE_CODES or not tax_code:
            contributions["G3"] = amount
            bas_label = "G3 (GST-Free)"
        elif tax_code in TAXABLE_CODES:
            bas_label = "G1 (Taxable)"

    elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
        if tax_code in CAPITAL_CODES:
            contributions["G10"] = amount
            bas_label = "G10 (Capital)"
            if tax_code == "FCA":
                contributions["G14"] = amount
                bas_label = "G10/G14 (GST-Free Capital)"
        elif tax_code in INPUT_TAXED_CODES:
            contributions["G11"] = amount
            contributions["G13"] = amount
            bas_label = "G11/G13 (Input Taxed)"
        elif tax_code == "ADS":
            contributions["G11"] = amount
            contributions["G18"] = amount
            bas_label = "G11/G18 (Adjustment)"
        elif tax_code in TAXABLE_CODES:
            contributions["G11"] = amount
            bas_label = "G11 (Non-Capital)"
        else:
            contributions["G11"] = amount
            contributions["G14"] = amount
            bas_label = "G11/G14 (GST-Free)" if tax_code else "G11/G14 (No GST)"

    elif section in ("assets", "Assets"):
        # Capital acquisitions belong at G10. Accounts with no tax code at all
        # (bank accounts, receivables, loans) contribute nothing.
        if tax_code == "FCA":
            contributions["G10"] = amount
            contributions["G14"] = amount
            bas_label = "G10/G14 (GST-Free Capital)"
        elif tax_code in TAXABLE_CODES:
            contributions["G10"] = amount
            bas_label = "G10 (Capital)"

    return bas_label, contributions, amount


def _display_tax_code(tax_code):
    """Short label shown in the BAS detail tabs."""
    if tax_code in NON_REPORTABLE_CODES:
        return "N-T"
    if tax_code in INPUT_TAXED_CODES:
        return "ITS"
    if tax_code in GST_FREE_CODES:
        return "FRE"
    return tax_code or "N-T"


def _resolve_section_and_tax(account_code, coa_lookup, entity_coa_lookup, line_tax=""):
    """Resolve section and tax_code for an account using the COA hierarchy."""
    coa = coa_lookup.get(account_code)
    ecoa = entity_coa_lookup.get(account_code)

    # Section inferred from the tax type alone, for accounts that are not in any
    # chart of accounts. Only the income/expense-specific labels imply a section.
    tax_type_map = {
        'GST on Income': ('revenue', 'GST'),
        'GST on Expenses': ('expenses', 'INP'),
        'GST Free Income': ('revenue', 'FRE'),
        'GST Free Expenses': ('expenses', 'FRE'),
        'BAS Excluded': (None, 'N-T'),
        'N-T': (None, 'N-T'),
        'Input Taxed': (None, 'ITS'),
    }

    if not coa and not ecoa:
        if account_code in ('3380', '9100', '9110'):
            return None, None, "gst_clearing"

        mapped = tax_type_map.get(line_tax)
        if mapped is None:
            # Not a long-form tax type — it may still be a recognised tax code
            # ("GST", "INP", "FRE", ...) written by an unvalidated confirm path.
            normalised = normalise_tax_treatment(line_tax)
            if normalised:
                mapped = (None, normalised)
        if mapped and mapped[0]:
            return mapped[0], mapped[1], None
        elif mapped:
            # N-T / BAS Excluded / Input Taxed — section is None but NOT an
            # error.  Return section=None with no exclude_reason so the
            # caller can still include the transaction in the detail tabs
            # using a direction-based fallback.
            return None, mapped[1], None
        else:
            return None, None, f"Not in chart of accounts" + (f" (tax: {line_tax})" if line_tax else "")
    else:
        section = ecoa.section if ecoa else coa.section
        ecoa_tax = normalise_tax_treatment(ecoa.tax_code) if ecoa else ""
        coa_tax = normalise_tax_treatment(coa.tax_code) if coa else ""
        base_tax = ecoa_tax or coa_tax

        # Transaction-level tax type (line_tax) takes precedence over the
        # account-level default (base_tax).  The account default is only
        # used as a fallback when no transaction-level type was supplied.
        # This ensures that a transaction confirmed as e.g. "GST Free Income"
        # is classified as FRE even when the account's default is GST — the
        # defect that reported a medical practice's GST-free income as taxable.
        tax_code = normalise_tax_treatment(line_tax) or base_tax

        return section, tax_code, None


def _parse_txn_date(date_str):
    """
    Parse a PendingTransaction.date string (a free-text CharField) into a date.

    Returns None when no known format matches; callers must decide what to do
    with an undatable transaction rather than silently dropping it.
    """
    from datetime import datetime as _dt

    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return _dt.strptime(date_str.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _confirmed_transactions(fy, entity):
    """
    All confirmed bank-statement transactions that belong to this financial year.

    Jobs are matched on the financial year when they carry one (legacy jobs may
    not), so a second open year cannot leak its transactions into this year's
    worksheet.
    """
    from review.models import PendingTransaction, ReviewJob

    jobs = ReviewJob.objects.filter(entity=entity).filter(
        Q(financial_year=fy) | Q(financial_year__isnull=True)
    )
    if not jobs.exists():
        return PendingTransaction.objects.none()
    return PendingTransaction.objects.filter(job__in=jobs, is_confirmed=True)


#: Sections whose natural movement is money OUT (a purchase or an asset
#: acquisition). Revenue's natural movement is money in.
_OUTGOING_SECTIONS = ("expenses", "Expenses", "cost_of_sales", "Cost of Sales",
                      "assets", "Assets")


def _is_contra(section, inflow):
    """True when a movement runs against its account's natural direction.

    A customer refund is money OUT of a revenue account; a supplier refund is
    money IN to an expense account. Both are reversals, and both must be
    SUBTRACTED from their own side of the worksheet rather than added to it or
    to the other side.

    ``inflow`` must be normalised so POSITIVE means money in, whatever the
    caller's own convention. The two callers disagree, which is exactly why
    this is spelled out rather than left to each of them:

        transactions        txn.amount              (already: + is money in)
        trial balance       -line.closing_balance   (closing_balance is
                                                     debit - credit, so revenue
                                                     sits negative when normal)

    Before this existed the figure went through abs() and direction was
    inferred from the section alone, so a refund was reported as another sale.
    On the live June 2026 quarter that turned 1,872.96 of customer refunds into
    a 3,745.92 overstatement of G1 and put 340.53 of GST on 1A that was not
    owed.
    """
    if inflow == 0:
        return False
    if section in ("revenue", "Revenue"):
        return inflow < 0
    if section in _OUTGOING_SECTIONS:
        return inflow > 0
    return False


def _txn_gross_and_gst(txn, tax_code):
    """
    Return (gross, gst) for a transaction under the resolved tax code.

    The bank statement amount IS the gross (GST-inclusive) figure the BAS
    worksheet asks for, so it is used directly — no 11/10 gross-up.

    A stored GST amount is respected when present (it carries accountant
    overrides and partial input tax credits). A zero on a taxable transaction
    means "never calculated" rather than "no GST": several confirm paths force
    confirmed_gst_amount to 0.00 whenever the stored tax type is not one of the
    two long-form GST labels, so 91 transactions on the live file were taxable
    with no GST recorded. Fall back to the ATO 1/11th method in that case.
    """
    gross = abs(txn.amount)
    if tax_code not in TAXABLE_CODES:
        return gross, Decimal("0.00")

    stored = txn.confirmed_gst_amount
    if stored is None:
        stored = txn.gst_amount
    stored = abs(stored or Decimal("0"))
    if stored:
        return gross, stored
    return gross, (gross / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _jl_gross_and_gst(jl, tax_code):
    """Return (gross, gst) for one journal line under the resolved tax code.

    Mirrors _txn_gross_and_gst: a stored GST amount wins, because it carries
    the accountant's overrides and partial input tax credits; otherwise fall
    back to the ATO 1/11th method.

    Cashbook lines store NET with the GST alongside, so gross is reconstructed
    by adding it back. Every pre-existing journal line has gst_amount = 0, so
    gross is unchanged and the 1/11th fallback reproduces today's behaviour
    exactly.
    """
    stored = abs(jl.gst_amount or Decimal("0"))
    gross = max(jl.debit, jl.credit) + stored
    if tax_code not in TAXABLE_CODES:
        return gross, Decimal("0.00")
    if stored:
        return gross, stored
    return gross, (gross / Decimal("11")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _accumulate_actual_gst(section, gst, collected, paid):
    """Add one line's GST to the collected/paid running totals.

    Revenue sections feed 1A; expense, cost-of-sales and asset sections feed
    1B. Sections that classify to nothing (N-T lines, accounts absent from the
    chart) contribute to neither, which matches their G-label treatment.
    """
    if section in ("revenue", "Revenue"):
        return collected + gst, paid
    if section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales",
                   "assets", "Assets"):
        return collected, paid + gst
    return collected, paid


def _calculate_gst(fy, entity, entity_type, coa_lookup, entity_coa_lookup,
                   window_start, window_end, full_year):
    """
    Build the GST calculation worksheet for a date window.

    Three sources contribute, and each is read from exactly one place so that
    the full-year view and the period views cannot disagree:

    1. Bank statements — from the confirmed PendingTransactions dated inside the
       window, using each transaction's own tax treatment and gross amount.
       Falls back to bank_statement TrialBalanceLines only for entities that
       have no review data at all.
    2. Posted adjusting journals dated inside the window.
    3. Undated balances (imported trial balances, rollover lines, journal-sourced
       adjustment lines). These carry no transaction dates and so can only be
       reported in the full-year view — unchanged from previous behaviour.
    """
    from .models import AdjustingJournal, TrialBalanceLine

    g_totals = {f"G{i}": Decimal("0") for i in range(1, 21)}
    actual_gst_collected = Decimal("0.00")
    actual_gst_paid = Decimal("0.00")
    sales_lines = []
    purchase_lines = []
    capital_lines = []
    excluded_lines = []
    sales_transactions = []
    purchase_transactions = []

    def _bucket_line(section, bas_label, line_data):
        if section in ("revenue", "Revenue"):
            sales_lines.append(line_data)
        elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
            purchase_lines.append(line_data)
        elif section in ("assets", "Assets") and bas_label:
            capital_lines.append(line_data)

    def _bucket_txn(section, txn_row, is_deposit):
        if section in ("revenue", "Revenue"):
            sales_transactions.append(txn_row)
        elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
            purchase_transactions.append(txn_row)
        elif section in ("assets", "Assets"):
            purchase_transactions.append(txn_row)
        else:
            # No section (BAS Excluded, N-T, not in the chart of accounts).
            # Still listed in the detail tabs so it can be found and reallocated;
            # it contributes nothing to the G-labels.
            (sales_transactions if is_deposit else purchase_transactions).append(txn_row)

    # ── 1. Bank statement transactions ──────────────────────────────────────
    all_confirmed = _confirmed_transactions(fy, entity)
    has_review_data = all_confirmed.exists()

    if has_review_data:
        account_totals = {}
        for txn in all_confirmed:
            txn_date = _parse_txn_date(txn.date)
            if not txn_date or txn_date < window_start or txn_date > window_end:
                continue

            code = txn.confirmed_code or txn.ai_suggested_code
            if not code:
                # Confirmed but never given an account code — it cannot be
                # classified, but it must not vanish from a lodged figure
                # without trace.
                excluded_lines.append({
                    "code": "—",
                    "name": (txn.description or "")[:60] or "Uncoded transaction",
                    "amount": abs(txn.amount),
                    "reason": "No account code — not included in any BAS label",
                })
                continue
            name = txn.confirmed_name or txn.ai_suggested_name or code
            tax_type = txn.confirmed_tax_type or txn.ai_suggested_tax_type or ""

            section, tax_code, exclude_reason = _resolve_section_and_tax(
                code, coa_lookup, entity_coa_lookup, tax_type
            )
            if exclude_reason == "gst_clearing":
                continue

            gross, gst = _txn_gross_and_gst(txn, tax_code)
            # A refund reduces its own side of the worksheet. _txn_gross_and_gst
            # returns magnitudes; the sign belongs to the movement.
            if _is_contra(section, txn.amount):
                gross, gst = -gross, -gst

            actual_gst_collected, actual_gst_paid = _accumulate_actual_gst(
                section, gst, actual_gst_collected, actual_gst_paid,
            )

            # Aggregate by account *and* resolved tax treatment, so one account
            # carrying both GST-free and taxable amounts produces one line per
            # treatment instead of a single line at the account default.
            if exclude_reason:
                key = (code, name, tax_code, exclude_reason)
                bucket = account_totals.setdefault(
                    key, {"gross": Decimal("0"), "section": section,
                          "exclude_reason": exclude_reason}
                )
            else:
                key = (code, name, tax_code, "")
                bucket = account_totals.setdefault(
                    key, {"gross": Decimal("0"), "section": section,
                          "exclude_reason": ""}
                )
            bucket["gross"] += gross

            has_gst = tax_code in TAXABLE_CODES
            _bucket_txn(section, {
                "date": txn_date,
                "txn_type": "Deposit" if txn.amount > 0 else "Expense",
                "description": txn.description or "",
                "account_code": code,
                "account_name": name,
                "tax_code": _display_tax_code(tax_code),
                "has_gst": has_gst,
                "gst_rate": Decimal("10.00") if has_gst else Decimal("0"),
                "taxable_amount": gross - gst,
                "gst_amount": gst,
                "gross_amount": gross,
                "txn_id": str(txn.id),
            }, txn.amount > 0)

        for (code, name, tax_code, _reason), bucket in account_totals.items():
            if bucket["exclude_reason"]:
                excluded_lines.append({
                    "code": code, "name": name,
                    "amount": bucket["gross"],
                    "reason": bucket["exclude_reason"],
                })
                continue

            bas_label, contributions, amount = _classify_line(
                code, tax_code, bucket["section"], bucket["gross"],
                coa_lookup, entity_coa_lookup, gross_up=False,
            )
            for label, val in contributions.items():
                g_totals[label] = g_totals[label] + val
            _bucket_line(bucket["section"], bas_label, {
                "code": code, "name": name,
                "tax_code": _display_tax_code(tax_code),
                "amount": amount, "bas_label": bas_label,
            })
    else:
        # No review data — fall back to the aggregated bank_statement TB lines.
        for line in TrialBalanceLine.objects.filter(
            financial_year=fy, source="bank_statement"
        ):
            _add_tb_line(
                line, coa_lookup, entity_coa_lookup, g_totals,
                excluded_lines, _bucket_line,
            )

    # ── 2. Posted adjusting journals dated inside the window ────────────────
    counted_journal_ids = set()
    for journal in AdjustingJournal.objects.filter(
        financial_year=fy, status="posted",
        journal_date__gte=window_start, journal_date__lte=window_end,
    ):
        counted_journal_ids.add(journal.pk)
        for jl in journal.lines.all():
            # The line's own tax code wins over the chart. That hardcoded ""
            # meant a journal line could never override its account default —
            # which is what let an explicit N-T on Drawings be ignored.
            # Pre-existing lines carry "", so they still fall through to the
            # chart exactly as before.
            section, tax_code, exclude_reason = _resolve_section_and_tax(
                jl.account_code, coa_lookup, entity_coa_lookup, jl.tax_code
            )
            if exclude_reason:
                continue
            amount, jl_gst = _jl_gross_and_gst(jl, tax_code)
            if amount == 0:
                continue

            bas_label, contributions, amount = _classify_line(
                jl.account_code, tax_code, section, amount,
                coa_lookup, entity_coa_lookup, source="manual_journal",
            )
            for label, val in contributions.items():
                g_totals[label] = g_totals[label] + val
            _bucket_line(section, bas_label, {
                "code": jl.account_code, "name": jl.account_name,
                "tax_code": _display_tax_code(tax_code),
                "amount": amount, "bas_label": bas_label,
            })

            has_gst = tax_code in TAXABLE_CODES
            gst = jl_gst
            actual_gst_collected, actual_gst_paid = _accumulate_actual_gst(
                section, gst, actual_gst_collected, actual_gst_paid,
            )
            txn_row = {
                "date": journal.journal_date,
                "txn_type": "Journal",
                "description": f"{journal.reference_number}: {jl.description or journal.description}",
                "account_code": jl.account_code,
                "account_name": jl.account_name,
                "tax_code": _display_tax_code(tax_code),
                "has_gst": has_gst,
                "gst_rate": Decimal("10.00") if has_gst else Decimal("0"),
                "taxable_amount": amount - gst,
                "gst_amount": gst,
                "gross_amount": amount,
            }
            if section in ("revenue", "Revenue"):
                sales_transactions.append(txn_row)
            elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
                purchase_transactions.append(txn_row)

    # ── 3. Undated balances — full-year view only ───────────────────────────
    # Imported trial balances and rollover lines have no transaction dates, so
    # they cannot be attributed to a quarter. Lines produced by a journal that
    # step 2 already counted are skipped to avoid double counting; lines from a
    # bulk journal upload (which creates no AdjustingJournal) are still picked
    # up here, so they are not silently lost.
    if full_year:
        undated = TrialBalanceLine.objects.filter(financial_year=fy).exclude(
            source="bank_statement"
        ).exclude(source_journal_id__in=counted_journal_ids)
        for line in undated:
            _add_tb_line(
                line, coa_lookup, entity_coa_lookup, g_totals,
                excluded_lines, _bucket_line,
            )

    sales_transactions.sort(key=lambda x: x["date"])
    purchase_transactions.sort(key=lambda x: x["date"])

    # Imported/rollover TB lines carry no per-line GST, so they cannot feed the
    # accounts method. When nothing at all contributed a recorded GST figure,
    # fall back to the worksheet division rather than reporting nil.
    if actual_gst_collected == 0 and actual_gst_paid == 0:
        actual_gst_collected = actual_gst_paid = None

    return _build_bas_result(
        g_totals, sales_lines, purchase_lines, capital_lines, excluded_lines,
        sales_transactions=sales_transactions,
        purchase_transactions=purchase_transactions,
        actual_gst_collected=actual_gst_collected,
        actual_gst_paid=actual_gst_paid,
    )


def _add_tb_line(line, coa_lookup, entity_coa_lookup, g_totals,
                 excluded_lines, bucket_line):
    """
    Fold one aggregated TrialBalanceLine into the G-label totals.

    Used for imported/rollover balances and, for entities with no review data,
    for bank_statement lines too. These are per-account aggregates carrying only
    the account's default tax treatment; where confirmed transactions exist they
    are superseded by the per-transaction calculation.
    """
    line_tax = (getattr(line, "tax_type", "") or "").strip()
    section, tax_code, exclude_reason = _resolve_section_and_tax(
        line.account_code, coa_lookup, entity_coa_lookup, line_tax
    )

    if exclude_reason == "gst_clearing":
        excluded_lines.append({
            "code": line.account_code,
            "name": line.account_name,
            "amount": abs(line.closing_balance),
            "reason": "GST clearing account",
        })
        return
    if exclude_reason:
        excluded_lines.append({
            "code": line.account_code,
            "name": line.account_name,
            "amount": abs(line.closing_balance),
            "reason": exclude_reason,
        })
        return

    if line.closing_balance != 0:
        amount = abs(line.closing_balance)
        # closing_balance is debit - credit, so money in is its negation. A
        # revenue account sitting in debit, or an expense account in credit, is
        # a net refund position and must reduce its own side.
        if _is_contra(section, -line.closing_balance):
            amount = -amount
    else:
        amount = max(line.debit, line.credit)
    if amount == 0:
        return

    bas_label, contributions, amount = _classify_line(
        line.account_code, tax_code, section, amount,
        coa_lookup, entity_coa_lookup, source=line.source,
    )
    for label, val in contributions.items():
        g_totals[label] = g_totals[label] + val

    bucket_line(section, bas_label, {
        "code": line.account_code,
        "name": line.account_name,
        "tax_code": _display_tax_code(tax_code),
        "amount": amount,
        "bas_label": bas_label,
    })

def _build_bas_result(g_totals, sales_lines, purchase_lines, capital_lines, excluded_lines,
                      sales_transactions=None, purchase_transactions=None,
                      actual_gst_collected=None, actual_gst_paid=None):
    """Build the final BAS result dict from G-label totals and line lists."""
    g = g_totals

    # Calculated fields
    g["G5"] = g["G2"] + g["G3"] + g["G4"]
    g["G6"] = g["G1"] - g["G5"]
    g["G8"] = g["G6"] + g["G7"]
    # Accounts method: 1A and 1B are the GST actually recorded, not G8/11.
    # Summing per-line GST and dividing an aggregate by 11 disagree by cents —
    # 418.68 against 418.67 on the first live cashbook quarter — and only one
    # can be authoritative if the 3380 control account is to tie to the BAS.
    # The recorded figure wins, so a hand-overridden partial input tax credit
    # reaches the BAS instead of being averaged away.
    #
    # G9/G20 move with 1A/1B rather than keeping the worksheet division: left
    # as G8/11 they would print 418.67 directly above 1B's 418.68 on the same
    # screen. G1 and G11 stay gross — they are turnover and purchases.
    if actual_gst_collected is not None:
        g["G9"] = Decimal(actual_gst_collected).quantize(Decimal("0.01"))
    else:
        g["G9"] = (g["G8"] / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if g["G8"] else Decimal("0")

    g["G12"] = g["G10"] + g["G11"]
    g["G16"] = g["G13"] + g["G14"] + g["G15"]
    g["G17"] = g["G12"] - g["G16"]
    g["G19"] = g["G17"] + g["G18"]
    if actual_gst_paid is not None:
        g["G20"] = Decimal(actual_gst_paid).quantize(Decimal("0.01"))
    else:
        g["G20"] = (g["G19"] / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if g["G19"] else Decimal("0")

    label_1a = g["G9"]
    label_1b = g["G20"]
    gst_payable = label_1a - label_1b

    bas_data = {
        "G1": g["G1"], "G2": g["G2"], "G3": g["G3"], "G4": g["G4"],
        "G5": g["G5"], "G6": g["G6"], "G7": g["G7"], "G8": g["G8"], "G9": g["G9"],
        "G10": g["G10"], "G11": g["G11"], "G12": g["G12"], "G13": g["G13"],
        "G14": g["G14"], "G15": g["G15"], "G16": g["G16"], "G17": g["G17"],
        "G18": g["G18"], "G19": g["G19"], "G20": g["G20"],
        "1A": label_1a, "1B": label_1b,
        "gst_payable": gst_payable,
    }

    return {
        "bas_data": bas_data,
        "sales_lines": sales_lines,
        "purchase_lines": purchase_lines,
        "capital_lines": capital_lines,
        "excluded_lines": excluded_lines,
        "sales_transactions": sales_transactions or [],
        "purchase_transactions": purchase_transactions or [],
    }
