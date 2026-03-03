"""
BAS Period utility functions.

Provides period date computation, status calculation, bank coverage
analysis, and GST calculation filtering for the period-aware BAS
redesign.
"""
import calendar
from datetime import date
from decimal import Decimal
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


def get_period_dates(fy, period_type, period_number):
    """
    Return (start_date, end_date) for a given period within a financial year.
    Uses standard July–June boundaries for Sprint 3.
    """
    fy_start_year = fy.start_date.year  # e.g. 2025 for FY2026 (Jul 2025 – Jun 2026)

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


def compute_period_status(fy, period_start, period_end, bas_period=None):
    """
    Compute the dynamic status of a BAS period based on bank transaction
    coverage. If the period is explicitly lodged, that status is preserved.

    Returns one of: 'lodged', 'ready', 'partial', 'empty'
    """
    if bas_period and bas_period.status == "lodged":
        return "lodged"

    coverage = get_bank_coverage(fy, period_start, period_end)
    if coverage["status"] == "complete":
        return "ready"
    elif coverage["status"] == "partial":
        return "partial"
    else:
        return "empty"


# ── GST Calculation Engine (period-filtered) ─────────────────────────────────

def calculate_gst_for_period(fy, period_start=None, period_end=None):
    """
    Calculate GST figures (G1–G20, 1A, 1B, Net) for a financial year,
    optionally filtered to a specific date range.

    When period_start/period_end are None, calculates for the full year
    (identical to the pre-redesign behaviour).

    Returns a dict with:
        - bas_data: {G1, G2, ..., G20, 1A, 1B, gst_payable}
        - sales_lines: [...]
        - purchase_lines: [...]
        - capital_lines: [...]
        - excluded_lines: [...]
        - sales_transactions: [...]   (individual transaction details)
        - purchase_transactions: [...] (individual transaction details)
    """
    from .models import (
        ChartOfAccount, EntityChartOfAccount, TrialBalanceLine,
        AdjustingJournal, JournalLine,
    )

    entity = fy.entity
    entity_type = entity.entity_type

    # Build COA lookups
    coa_lookup = {}
    for coa in ChartOfAccount.objects.filter(entity_type=entity_type, is_active=True):
        coa_lookup[coa.account_code] = coa

    entity_coa_lookup = {}
    for ecoa in EntityChartOfAccount.objects.filter(entity=entity):
        entity_coa_lookup[ecoa.account_code] = ecoa

    # Get TB lines — for period filtering we need to handle two cases:
    # 1. Non-adjustment TB lines (from imports/bank statements) — these represent
    #    full-year balances and don't have individual dates. For period filtering,
    #    we only include bank_statement-sourced lines that fall within the period.
    # 2. Adjustment TB lines (from journals) — these have a linked journal with
    #    a journal_date that we can filter on.

    if period_start and period_end:
        # Period-specific filtering
        # For bank_statement lines, we need to match by the transaction dates
        # For now, include all non-adjustment lines (they represent aggregated
        # balances) and filter adjustment lines by journal date.
        # The key insight: bank_statement TB lines are created per-account with
        # aggregated balances. For period filtering, we need to look at the
        # underlying PendingTransactions.
        #
        # APPROACH: For period-specific views, we recalculate from
        # PendingTransactions (bank statement source) + JournalLines (adjustments)
        # rather than using pre-aggregated TB lines.
        return _calculate_gst_from_transactions(
            fy, entity, entity_type, coa_lookup, entity_coa_lookup,
            period_start, period_end
        )
    else:
        # Full year — use existing TB line approach (preserves pre-redesign behaviour)
        return _calculate_gst_from_tb_lines(
            fy, entity, entity_type, coa_lookup, entity_coa_lookup
        )


def _classify_line(account_code, tax_code, section, amount, coa_lookup, entity_coa_lookup, source=None):
    """
    Classify a single account into BAS labels and return the
    contribution to each G-label bucket.

    Returns (bas_label, g_contributions) where g_contributions is a dict
    of label -> amount to add.
    """
    # BAS requires GROSS amounts (including GST).
    # Bank statement TB lines store NET amounts (ex-GST) with GST in 3380 (GST payable control account).
    if tax_code in ('INP', 'GST') and source == 'bank_statement':
        amount = (amount * Decimal('11') / Decimal('10')).quantize(Decimal('0.01'))

    contributions = {}
    bas_label = ""

    if section in ("revenue", "Revenue"):
        contributions["G1"] = amount
        bas_label = "G1"

        if tax_code == "GST":
            bas_label = "G1 (Taxable)"
        elif tax_code == "ITS":
            contributions["G4"] = amount
            bas_label = "G4 (Input Taxed)"
        elif tax_code == "ADS":
            contributions["G7"] = amount
            bas_label = "G7 (Adjustment)"
        elif tax_code in ("", "FRE", "N-T"):
            contributions["G3"] = amount
            bas_label = "G3 (GST-Free)"

    elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
        if tax_code in ("INP", "GST"):
            contributions["G11"] = amount
            bas_label = "G11 (Non-Capital)"
        elif tax_code == "IOA":
            contributions["G11"] = amount
            contributions["G13"] = amount
            bas_label = "G11/G13 (Input Taxed)"
        elif tax_code in ("FOA", "FRE"):
            contributions["G11"] = amount
            contributions["G14"] = amount
            bas_label = "G11/G14 (GST-Free)"
        elif tax_code == "ADS":
            contributions["G11"] = amount
            contributions["G18"] = amount
            bas_label = "G11/G18 (Adjustment)"
        else:
            contributions["G11"] = amount
            contributions["G14"] = amount
            bas_label = "G11/G14 (No GST)"

    elif section in ("assets", "Assets"):
        if tax_code == "CAP":
            contributions["G10"] = amount
            bas_label = "G10 (Capital)"
        elif tax_code == "FCA":
            contributions["G10"] = amount
            contributions["G14"] = amount
            bas_label = "G10/G14 (GST-Free Capital)"

    return bas_label, contributions, amount


def _resolve_section_and_tax(account_code, coa_lookup, entity_coa_lookup, line_tax=""):
    """Resolve section and tax_code for an account using the COA hierarchy."""
    coa = coa_lookup.get(account_code)
    ecoa = entity_coa_lookup.get(account_code)

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
        if mapped and mapped[0]:
            return mapped[0], mapped[1], None
        else:
            return None, None, f"Not in chart of accounts" + (f" (tax: {line_tax})" if line_tax else "")
    else:
        section = ecoa.section if ecoa else coa.section
        ecoa_tax = (ecoa.tax_code or "").upper().strip() if ecoa else ""
        coa_tax = (coa.tax_code or "").upper().strip() if coa else ""
        base_tax = ecoa_tax or coa_tax

        if not base_tax and line_tax:
            tax_map = {
                'GST on Income': 'GST',
                'GST on Expenses': 'INP',
                'GST Free Income': 'FRE',
                'GST Free Expenses': 'FRE',
                'BAS Excluded': 'N-T',
                'N-T': 'N-T',
            }
            tax_code = tax_map.get(line_tax, base_tax)
        else:
            tax_code = base_tax

        return section, tax_code, None


def _calculate_gst_from_tb_lines(fy, entity, entity_type, coa_lookup, entity_coa_lookup):
    """
    Full-year GST calculation using TB lines — identical to pre-redesign logic.
    Also collects individual transaction details from PendingTransactions for
    the GST Detail Report when available.
    """
    tb_lines = fy.trial_balance_lines.all()

    g_totals = {f"G{i}": Decimal("0") for i in range(1, 21)}
    sales_lines = []
    purchase_lines = []
    capital_lines = []
    excluded_lines = []
    sales_transactions = []
    purchase_transactions = []

    for line in tb_lines:
        line_tax = (getattr(line, 'tax_type', '') or '').strip()
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
            continue

        if exclude_reason:
            excluded_lines.append({
                "code": line.account_code,
                "name": line.account_name,
                "amount": abs(line.closing_balance),
                "reason": exclude_reason,
            })
            continue

        # Determine amount
        if line.closing_balance != 0:
            amount = abs(line.closing_balance)
        else:
            amount = max(line.debit, line.credit)

        bas_label, contributions, amount = _classify_line(
            line.account_code, tax_code, section, amount,
            coa_lookup, entity_coa_lookup, source=line.source
        )

        for label, val in contributions.items():
            g_totals[label] = g_totals[label] + val

        if section in ("revenue", "Revenue"):
            sales_lines.append({
                "code": line.account_code,
                "name": line.account_name,
                "tax_code": tax_code or "N-T",
                "amount": amount,
                "bas_label": bas_label,
            })
        elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
            purchase_lines.append({
                "code": line.account_code,
                "name": line.account_name,
                "tax_code": tax_code or "N-T",
                "amount": amount,
                "bas_label": bas_label,
            })
        elif section in ("assets", "Assets") and bas_label:
            capital_lines.append({
                "code": line.account_code,
                "name": line.account_name,
                "tax_code": tax_code or "N-T",
                "amount": amount,
                "bas_label": bas_label,
            })

    # ── Collect individual transaction details from PendingTransactions ──
    from review.models import PendingTransaction, ReviewJob
    from datetime import datetime as _dt

    jobs = ReviewJob.objects.filter(entity=entity)
    if jobs.exists():
        all_confirmed = PendingTransaction.objects.filter(
            job__in=jobs,
            is_confirmed=True,
        )

        def _parse_txn_date_fy(date_str):
            if not date_str:
                return None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
                try:
                    return _dt.strptime(date_str.strip(), fmt).date()
                except (ValueError, AttributeError):
                    continue
            return None

        for txn in all_confirmed:
            txn_date = _parse_txn_date_fy(txn.date)
            code = txn.confirmed_code or txn.ai_suggested_code
            if not code:
                continue
            name = txn.confirmed_name or txn.ai_suggested_name or code
            tax_type = txn.confirmed_tax_type or txn.ai_suggested_tax_type or ""

            section, tax_code, exclude_reason = _resolve_section_and_tax(
                code, coa_lookup, entity_coa_lookup, tax_type
            )
            if exclude_reason:
                continue

            # Use transaction-level tax_type for GST classification
            gst_bearing_tax_types = ("GST on Income", "GST on Expenses")
            non_gst_tax_types = ("GST Free Income", "GST Free Expenses", "BAS Excluded", "N-T", "Input Taxed")
            if tax_type in gst_bearing_tax_types:
                has_gst = True
            elif tax_type in non_gst_tax_types:
                has_gst = False
            else:
                has_gst = tax_code in ("GST", "INP")

            gross = abs(txn.amount)
            gst_amt = abs(txn.confirmed_gst_amount or txn.gst_amount or Decimal("0")) if has_gst else Decimal("0")
            taxable = gross - gst_amt if has_gst else gross

            # Determine display tax code from transaction tax type
            display_tax_code = tax_code or "N-T"
            if tax_type in ("GST Free Income", "GST Free Expenses"):
                display_tax_code = "FRE"
            elif tax_type in ("BAS Excluded", "N-T"):
                display_tax_code = "N-T"
            elif tax_type == "Input Taxed":
                display_tax_code = "ITS"

            txn_row = {
                "date": txn_date or fy.start_date,
                "txn_type": "Deposit" if txn.amount > 0 else "Expense",
                "description": txn.description or "",
                "account_code": code,
                "account_name": name,
                "tax_code": display_tax_code,
                "has_gst": has_gst,
                "gst_rate": Decimal("10.00") if has_gst else Decimal("0"),
                "taxable_amount": taxable,
                "gst_amount": gst_amt,
                "gross_amount": gross,
            }
            if section in ("revenue", "Revenue"):
                sales_transactions.append(txn_row)
            elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
                purchase_transactions.append(txn_row)
            elif section in ("assets", "Assets"):
                purchase_transactions.append(txn_row)
            elif section is None:
                if txn.amount > 0:
                    sales_transactions.append(txn_row)
                else:
                    purchase_transactions.append(txn_row)

    # Also include journal entries as transaction details
    from .models import AdjustingJournal
    for journal in AdjustingJournal.objects.filter(financial_year=fy, status="posted"):
        for jl in journal.lines.all():
            section, tax_code, exclude_reason = _resolve_section_and_tax(
                jl.account_code, coa_lookup, entity_coa_lookup, ""
            )
            if exclude_reason:
                continue
            amount = max(jl.debit, jl.credit)
            if amount == 0:
                continue

            has_gst = tax_code in ("GST", "INP")
            txn_row = {
                "date": journal.journal_date,
                "txn_type": "Journal",
                "description": f"{journal.reference_number}: {jl.description or journal.description}",
                "account_code": jl.account_code,
                "account_name": jl.account_name,
                "tax_code": tax_code or "N-T",
                "has_gst": has_gst,
                "gst_rate": Decimal("10.00") if has_gst else Decimal("0"),
                "taxable_amount": amount,
                "gst_amount": (amount / Decimal("11")).quantize(Decimal("0.01")) if has_gst else Decimal("0"),
                "gross_amount": amount,
            }
            if section in ("revenue", "Revenue"):
                sales_transactions.append(txn_row)
            elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
                purchase_transactions.append(txn_row)

    sales_transactions.sort(key=lambda x: x["date"])
    purchase_transactions.sort(key=lambda x: x["date"])

    return _build_bas_result(
        g_totals, sales_lines, purchase_lines, capital_lines, excluded_lines,
        sales_transactions=sales_transactions,
        purchase_transactions=purchase_transactions,
    )


def _calculate_gst_from_transactions(fy, entity, entity_type, coa_lookup, entity_coa_lookup,
                                      period_start, period_end):
    """
    Period-specific GST calculation.

    Uses the same TB-line approach but filters lines that are relevant to
    the period. For bank_statement-sourced lines, we check the underlying
    PendingTransaction dates. For adjustment lines, we check journal dates.
    For tb_import lines (annual imports), we pro-rate or include based on
    the full-year approach (they don't have per-transaction dates).

    PRACTICAL APPROACH for Sprint 3:
    Since TB lines from bank statements are aggregated per-account and don't
    carry individual transaction dates, and the spec says to filter by
    transaction_date, we take a hybrid approach:
    - bank_statement TB lines: Recalculate from PendingTransactions in the
      date range, grouped by confirmed_code.
    - adjustment TB lines: Include if the parent journal's journal_date
      falls within the period.
    - tb_import lines: Only include in Full Year view (handled by caller
      passing period_start=None).
    """
    from .models import AdjustingJournal, JournalLine, TrialBalanceLine
    from review.models import PendingTransaction, ReviewJob

    g_totals = {f"G{i}": Decimal("0") for i in range(1, 21)}
    sales_lines = []
    purchase_lines = []
    capital_lines = []
    excluded_lines = []
    # Individual transaction details for GST Detail Report
    sales_transactions = []
    purchase_transactions = []

    # ── 1. Bank statement transactions in this period ──
    jobs = ReviewJob.objects.filter(entity=entity)
    if jobs.exists():
        # Get confirmed transactions with dates in the period range.
        # PendingTransaction.date is a CharField stored in various formats
        # (e.g. "1/08/2025", "2025-08-01", "15/10/2025"). We cannot rely
        # on lexicographic string comparison, so we fetch all confirmed
        # transactions and filter in Python after parsing the date.
        from datetime import datetime as _dt

        all_confirmed = PendingTransaction.objects.filter(
            job__in=jobs,
            is_confirmed=True,
        )

        def _parse_txn_date(date_str):
            """Parse a PendingTransaction date string into a date object."""
            if not date_str:
                return None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
                try:
                    return _dt.strptime(date_str.strip(), fmt).date()
                except (ValueError, AttributeError):
                    continue
            return None

        # ── Collect individual transactions AND aggregate by account ──
        account_totals = {}
        period_txns = []  # individual transactions within period
        for txn in all_confirmed:
            txn_date = _parse_txn_date(txn.date)
            if not txn_date or txn_date < period_start or txn_date > period_end:
                continue

            code = txn.confirmed_code or txn.ai_suggested_code
            if not code:
                continue
            name = txn.confirmed_name or txn.ai_suggested_name or code
            tax_type = txn.confirmed_tax_type or txn.ai_suggested_tax_type or ""

            key = (code, name, tax_type)
            if key not in account_totals:
                account_totals[key] = Decimal("0")
            # Use net_amount (ex-GST) as that's what gets posted to TB
            net = abs(txn.net_amount or txn.amount)
            account_totals[key] += net

            # Collect individual transaction detail
            period_txns.append({
                "date": txn_date,
                "description": txn.description or "",
                "gross_amount": abs(txn.amount),
                "gst_amount": abs(txn.confirmed_gst_amount or txn.gst_amount or Decimal("0")),
                "net_amount": net,
                "account_code": code,
                "account_name": name,
                "tax_type": tax_type,
                "txn_type": "Deposit" if txn.amount > 0 else "Expense",
                "source": "bank_statement",
            })

        for (code, name, tax_type), total in account_totals.items():
            section, tax_code, exclude_reason = _resolve_section_and_tax(
                code, coa_lookup, entity_coa_lookup, tax_type
            )

            if exclude_reason == "gst_clearing":
                continue
            if exclude_reason:
                excluded_lines.append({
                    "code": code, "name": name,
                    "amount": total, "reason": exclude_reason,
                })
                continue

            bas_label, contributions, amount = _classify_line(
                code, tax_code, section, total,
                coa_lookup, entity_coa_lookup, source="bank_statement"
            )

            for label, val in contributions.items():
                g_totals[label] = g_totals[label] + val

            line_data = {
                "code": code, "name": name,
                "tax_code": tax_code or "N-T",
                "amount": amount, "bas_label": bas_label,
            }
            if section in ("revenue", "Revenue"):
                sales_lines.append(line_data)
            elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
                purchase_lines.append(line_data)
            elif section in ("assets", "Assets") and bas_label:
                capital_lines.append(line_data)

        # ── Classify individual transactions into sales/purchase detail ──
        for txn_detail in period_txns:
            section, tax_code, exclude_reason = _resolve_section_and_tax(
                txn_detail["account_code"], coa_lookup, entity_coa_lookup,
                txn_detail["tax_type"]
            )
            if exclude_reason:
                continue

            # Use the transaction-level tax_type to determine GST status.
            # The COA-derived tax_code is used for BAS label aggregation, but
            # individual transactions should reflect their actual confirmed
            # tax treatment (e.g. a transaction confirmed as "GST Free Expenses"
            # should appear in the GST-free section even if the COA account
            # defaults to GST).
            txn_tax_type = txn_detail.get("tax_type", "")
            gst_bearing_tax_types = ("GST on Income", "GST on Expenses")
            non_gst_tax_types = ("GST Free Income", "GST Free Expenses", "BAS Excluded", "N-T", "Input Taxed")

            if txn_tax_type in gst_bearing_tax_types:
                has_gst = True
            elif txn_tax_type in non_gst_tax_types:
                has_gst = False
            else:
                # Fallback to COA-derived tax_code
                has_gst = tax_code in ("GST", "INP")

            gst_rate = Decimal("10.00") if has_gst else Decimal("0")
            gross = txn_detail["gross_amount"]
            gst_amt = txn_detail["gst_amount"] if has_gst else Decimal("0")
            taxable = gross - gst_amt if has_gst else gross

            # Determine display tax code from the transaction tax type
            display_tax_code = tax_code or "N-T"
            if txn_tax_type in ("GST Free Income", "GST Free Expenses"):
                display_tax_code = "FRE"
            elif txn_tax_type == "BAS Excluded":
                display_tax_code = "N-T"
            elif txn_tax_type == "N-T":
                display_tax_code = "N-T"
            elif txn_tax_type == "Input Taxed":
                display_tax_code = "ITS"

            txn_row = {
                "date": txn_detail["date"],
                "txn_type": txn_detail["txn_type"],
                "description": txn_detail["description"],
                "account_code": txn_detail["account_code"],
                "account_name": txn_detail["account_name"],
                "tax_code": display_tax_code,
                "has_gst": has_gst,
                "gst_rate": gst_rate,
                "taxable_amount": taxable,
                "gst_amount": gst_amt,
                "gross_amount": gross,
            }

            # Route to sales or purchases based on section.
            # If section is None (e.g. BAS Excluded), fall back to
            # transaction type: Deposit → sales, Expense → purchases.
            if section in ("revenue", "Revenue"):
                sales_transactions.append(txn_row)
            elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
                purchase_transactions.append(txn_row)
            elif section in ("assets", "Assets"):
                purchase_transactions.append(txn_row)  # capital purchases in purchase detail
            elif section is None:
                # Fallback: use transaction direction
                if txn_detail["txn_type"] == "Deposit":
                    sales_transactions.append(txn_row)
                else:
                    purchase_transactions.append(txn_row)

    # ── 2. TB lines from bank_statement source (fallback if no PendingTransactions) ──
    # If no review jobs exist, fall back to TB lines with source=bank_statement
    if not jobs.exists():
        bank_tb_lines = TrialBalanceLine.objects.filter(
            financial_year=fy,
            source="bank_statement",
        )
        for line in bank_tb_lines:
            line_tax = (getattr(line, 'tax_type', '') or '').strip()
            section, tax_code, exclude_reason = _resolve_section_and_tax(
                line.account_code, coa_lookup, entity_coa_lookup, line_tax
            )
            if exclude_reason:
                if exclude_reason != "gst_clearing":
                    excluded_lines.append({
                        "code": line.account_code, "name": line.account_name,
                        "amount": abs(line.closing_balance), "reason": exclude_reason,
                    })
                continue

            if line.closing_balance != 0:
                amount = abs(line.closing_balance)
            else:
                amount = max(line.debit, line.credit)

            bas_label, contributions, amount = _classify_line(
                line.account_code, tax_code, section, amount,
                coa_lookup, entity_coa_lookup, source=line.source
            )
            for label, val in contributions.items():
                g_totals[label] = g_totals[label] + val

            line_data = {
                "code": line.account_code, "name": line.account_name,
                "tax_code": tax_code or "N-T",
                "amount": amount, "bas_label": bas_label,
            }
            if section in ("revenue", "Revenue"):
                sales_lines.append(line_data)
            elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
                purchase_lines.append(line_data)
            elif section in ("assets", "Assets") and bas_label:
                capital_lines.append(line_data)

    # ── 3. Adjusting journal lines within the period ──
    journals = AdjustingJournal.objects.filter(
        financial_year=fy,
        status="posted",
        journal_date__gte=period_start,
        journal_date__lte=period_end,
    )
    for journal in journals:
        for jl in journal.lines.all():
            line_tax = ""
            section, tax_code, exclude_reason = _resolve_section_and_tax(
                jl.account_code, coa_lookup, entity_coa_lookup, line_tax
            )
            if exclude_reason:
                continue

            amount = max(jl.debit, jl.credit)
            if amount == 0:
                continue

            bas_label, contributions, amount = _classify_line(
                jl.account_code, tax_code, section, amount,
                coa_lookup, entity_coa_lookup, source="manual_journal"
            )
            for label, val in contributions.items():
                g_totals[label] = g_totals[label] + val

            line_data = {
                "code": jl.account_code, "name": jl.account_name,
                "tax_code": tax_code or "N-T",
                "amount": amount, "bas_label": bas_label,
            }
            if section in ("revenue", "Revenue"):
                sales_lines.append(line_data)
            elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
                purchase_lines.append(line_data)
            elif section in ("assets", "Assets") and bas_label:
                capital_lines.append(line_data)

            # Also add to transaction detail for journals
            has_gst = tax_code in ("GST", "INP")
            txn_row = {
                "date": journal.journal_date,
                "txn_type": "Journal",
                "description": f"{journal.reference_number}: {jl.description or journal.description}",
                "account_code": jl.account_code,
                "account_name": jl.account_name,
                "tax_code": tax_code or "N-T",
                "has_gst": has_gst,
                "gst_rate": Decimal("10.00") if has_gst else Decimal("0"),
                "taxable_amount": amount,
                "gst_amount": (amount / Decimal("11")).quantize(Decimal("0.01")) if has_gst else Decimal("0"),
                "gross_amount": amount,
            }
            if section in ("revenue", "Revenue"):
                sales_transactions.append(txn_row)
            elif section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales"):
                purchase_transactions.append(txn_row)

    # Sort transactions by date
    sales_transactions.sort(key=lambda x: x["date"])
    purchase_transactions.sort(key=lambda x: x["date"])

    return _build_bas_result(
        g_totals, sales_lines, purchase_lines, capital_lines, excluded_lines,
        sales_transactions=sales_transactions,
        purchase_transactions=purchase_transactions,
    )


def _build_bas_result(g_totals, sales_lines, purchase_lines, capital_lines, excluded_lines,
                      sales_transactions=None, purchase_transactions=None):
    """Build the final BAS result dict from G-label totals and line lists."""
    g = g_totals

    # Calculated fields
    g["G5"] = g["G2"] + g["G3"] + g["G4"]
    g["G6"] = g["G1"] - g["G5"]
    g["G8"] = g["G6"] + g["G7"]
    g["G9"] = (g["G8"] / Decimal("11")).quantize(Decimal("0.01")) if g["G8"] else Decimal("0")

    g["G12"] = g["G10"] + g["G11"]
    g["G16"] = g["G13"] + g["G14"] + g["G15"]
    g["G17"] = g["G12"] - g["G16"]
    g["G19"] = g["G17"] + g["G18"]
    g["G20"] = (g["G19"] / Decimal("11")).quantize(Decimal("0.01")) if g["G19"] else Decimal("0")

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
