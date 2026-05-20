"""
Xero General Ledger Summary XLSX parser.

Parses Xero's "General Ledger Summary" report export into staged line dicts
that the cloud-import pipeline (StagedImport -> review_import ->
commit_import) can consume directly.

CONTRACT: the staged dicts produced here OMIT the `opening_balance` key.
commit_import (post-222f57b at integrations/views.py:888-907) treats key
absence specifically as the signal to fall back to its snapshot's captured
`opening_balance` per account_code, which holds the rolled-forward prior
closing.  Posting then composes correctly as
    closing = rolled_forward_opening + period_net_movement
for balance-sheet accounts.  Setting `opening_balance` to "0" explicitly
would defeat this and silently zero-out the rolled-forward opening on every
import.  Do not "helpfully" fill it in.
"""
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import openpyxl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Xero Account Type -> AccountMapping.standard_code lookup
# ---------------------------------------------------------------------------
# v1 signed-off table per phase2_xero_gl_summary_upload.md. Equity is
# special-cased per entity_type via resolve_equity_code().  Depreciation
# accounts land on IS-EXP-016 via the Expense default; ClientAccountMapping
# then learns the specific remap and overlays it on subsequent imports.
#
# Keys are lowercased to match the parser's case-insensitive comparison.

XERO_TYPE_TO_STANDARD_CODE = {
    "bank":                  "BS-CA-001",   # Cash and cash equivalents
    "current asset":         "BS-CA-005",   # Other current assets
    "inventory":             "BS-CA-003",   # Inventories
    "prepayment":            "BS-CA-006",   # Prepayments
    "fixed asset":           "BS-NCA-001",  # Property, plant and equipment
    "non-current asset":     "BS-NCA-007",  # Other non-current assets
    "current liability":     "BS-CL-005",   # Other current liabilities
    "liability":             "BS-CL-005",   # Other current liabilities
    "non-current liability": "BS-NCL-005",  # Other non-current liabilities
    "revenue":               "IS-REV-001",  # Revenue
    "sales":                 "IS-REV-001",  # Revenue
    "other income":          "IS-REV-002",  # Other income
    "direct costs":          "IS-COS-001",  # Cost of sales
    "expense":               "IS-EXP-016",  # Other expenses (catch-all)
    "overhead":              "IS-EXP-016",  # Other expenses
    "other expense":         "IS-EXP-016",  # Other expenses
}


def resolve_equity_code(entity_type):
    """Return the default BS-EQ-xxx standard_code for the given entity_type.

    Falls back to BS-EQ-002 (Retained earnings — the company default) for
    unrecognised entity types, incl. ``smsf`` for which the seeded mappings
    do not define a dedicated equity line.  The accountant can override in
    the wizard, and ClientAccountMapping will learn the override.
    """
    return {
        "company":     "BS-EQ-002",  # Retained earnings
        "trust":       "BS-EQ-005",  # Undistributed income
        "partnership": "BS-EQ-007",  # Partners' current accounts
        "sole_trader": "BS-EQ-008",  # Proprietor's equity
    }.get(entity_type, "BS-EQ-002")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_REQUIRED_HEADERS = {
    "account", "account code", "debit", "credit",
    "net movement", "account type",
}

_PERIOD_RE = re.compile(
    r"For\s+the\s+period\s+"
    r"(\d{1,2})\s+(\w+)\s+(\d{4})\s+to\s+"
    r"(\d{1,2})\s+(\w+)\s+(\d{4})",
    re.IGNORECASE,
)


def _to_decimal(value):
    """Tolerantly convert a Xero cell value to Decimal.  None / empty /
    unparseable returns Decimal("0").  Strips commas, currency symbols, and
    whitespace.
    """
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _parse_long_date(day, month_name, year):
    """Parse '1', 'July', '2024' -> date(2024, 7, 1).

    Raises ValueError on unrecognised month names so the caller can
    fail-closed with a clear error message.
    """
    return datetime.strptime(
        f"{int(day)} {month_name} {int(year)}", "%d %B %Y"
    ).date()


def _slugify_account_name(name):
    """Build a deterministic slug for synthetic account codes.

    Xero's GL Summary can leave the Account Code column blank — commonly for
    bank accounts named after the entity ("HAZAWAY OPERATIONS PTY LTD") or
    credit cards.  We replace those with ``xero:<slug>`` so re-imports of
    the same account always produce the same code; that keeps the rolled-
    forward opening lookup and ClientAccountMapping learning stable across
    imports.
    """
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "blank"


def parse_xero_gl_summary(file):
    """Parse a Xero General Ledger Summary XLSX export.

    Returns
    -------
    (raw_lines, period_from, period_to)
        raw_lines : list of dicts with keys
            account_code      - Xero code, or ``xero:<slug>`` if blank
            account_name      - Xero account name
            debit             - Decimal, single-sided from Net Movement sign
            credit            - Decimal, single-sided from Net Movement sign
            movement_amount   - Decimal, signed (= net movement)
            account_type      - lowercased Xero Account Type column
            # NB: opening_balance is INTENTIONALLY ABSENT — see module
            # docstring.  commit_import's snapshot fallback supplies it.
        period_from, period_to : date objects parsed from the
            "For the period D Month YYYY to D Month YYYY" header.

    Raises
    ------
    ValueError
        - the period header row is missing or unparseable,
        - the column header row (Account / Account Code / Debit / Credit /
          Net Movement / Account Type) cannot be located.
    """
    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    try:
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    period_from = None
    period_to = None
    header_idx = None
    col_index = {}

    for idx, row in enumerate(all_rows):
        if row is None:
            continue

        if period_from is None:
            joined = " ".join(str(c) for c in row if c is not None)
            m = _PERIOD_RE.search(joined)
            if m:
                try:
                    period_from = _parse_long_date(m.group(1), m.group(2), m.group(3))
                    period_to = _parse_long_date(m.group(4), m.group(5), m.group(6))
                except ValueError as e:
                    raise ValueError(
                        f"Could not parse the period header '{joined.strip()}'. "
                        f"Expected 'For the period D Month YYYY to D Month YYYY' "
                        f"with the full English month name (e.g. 'July', not 'Jul')."
                    ) from e

        cell_strs = [
            str(c).strip().lower() if c is not None else ""
            for c in row
        ]
        if _REQUIRED_HEADERS.issubset(set(cell_strs)):
            header_idx = idx
            col_index = {label: i for i, label in enumerate(cell_strs) if label}
            break

    if period_from is None or period_to is None:
        raise ValueError(
            "Could not find the 'For the period D Month YYYY to D Month YYYY' "
            "header row in the file. Is this a Xero General Ledger Summary "
            "export?"
        )
    if header_idx is None:
        raise ValueError(
            "Could not find the column header row containing Account, "
            "Account Code, Debit, Credit, Net Movement, Account Type. "
            "Is this a Xero General Ledger Summary export?"
        )

    name_col   = col_index["account"]
    code_col   = col_index["account code"]
    debit_col  = col_index["debit"]
    credit_col = col_index["credit"]
    net_col    = col_index["net movement"]
    type_col   = col_index["account type"]

    def _cell(row, col):
        return row[col] if col < len(row) else None

    raw_lines = []
    seen_total = False
    total_dr = total_cr = total_net = Decimal("0")

    for row in all_rows[header_idx + 1:]:
        if row is None:
            continue
        name_val = _cell(row, name_col)
        name = str(name_val).strip() if name_val is not None else ""
        if not name:
            continue

        if name.lower() == "total":
            seen_total = True
            total_dr  = _to_decimal(_cell(row, debit_col))
            total_cr  = _to_decimal(_cell(row, credit_col))
            total_net = _to_decimal(_cell(row, net_col))
            break

        code_val = _cell(row, code_col)
        code = str(code_val).strip() if code_val is not None else ""
        type_val = _cell(row, type_col)
        acct_type = str(type_val).strip() if type_val is not None else ""
        net = _to_decimal(_cell(row, net_col))

        if not code:
            code = f"xero:{_slugify_account_name(name)}"[:50]

        if net >= 0:
            debit = net
            credit = Decimal("0")
        else:
            debit = Decimal("0")
            credit = -net

        raw_lines.append({
            "account_code": code,
            "account_name": name,
            "debit": debit,
            "credit": credit,
            "movement_amount": net,
            "account_type": acct_type.lower(),
            # NB: opening_balance intentionally absent — commit_import's
            # snapshot fallback supplies the rolled-forward prior closing.
        })

    if seen_total and (total_dr or total_cr or total_net):
        # Some Xero exports leave a small rounding on the Total row.
        # Surface it as a warning but don't fail; commit_import's existing
        # $0.02 TB balance check is the authoritative gate.
        logger.warning(
            "Xero GL Summary 'Total' row is non-zero "
            "(debit=%s, credit=%s, net=%s); continuing.",
            total_dr, total_cr, total_net,
        )

    return raw_lines, period_from, period_to
