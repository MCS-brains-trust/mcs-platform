"""
Access Ledger (HSoft) ZIP Importer for StatementHub

Parses the ZIP export format from Access Ledger and imports:
- Entity details (name, ABN, TFN, address, entity type, officers)
- Multi-year financial years with prior year linking
- Trial balance data with current and prior year comparatives
- Depreciation schedules with asset details

ZIP Structure Expected:
    HL_CODE.zip
    ├── HL_CODE_2016/
    │   ├── Client.txt
    │   ├── Entity.txt
    │   ├── Year.txt
    │   ├── Chart.txt
    │   ├── Balance.txt
    │   ├── AssetMaster.txt
    │   ├── AssetDetail.txt
    │   ├── DepSchedule.txt
    │   ├── Owner.txt
    │   ├── Address.txt
    │   └── ...
    ├── HL_CODE_2017/
    │   └── ...
    └── ...
"""
import csv
import io
import logging
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from collections import OrderedDict
from pathlib import PurePosixPath

from django.db import transaction

from .models import (
    Entity, EntityOfficer, FinancialYear, TrialBalanceLine,
    DepreciationAsset, ClientAccountMapping, AccountMapping,
    ChartOfAccount,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Entity Type Mapping
# =============================================================================
ENTITY_TYPE_MAP = {
    1: "sole_trader",
    2: "partnership",
    3: "company",
    4: "trust",
    5: "smsf",
}

# Officer role mapping based on entity type
OFFICER_ROLE_MAP = {
    "company": "director",
    "trust": "trustee",
    "partnership": "partner",
    "sole_trader": "sole_trader",
    "smsf": "trustee",
}


# =============================================================================
# CSV Parsing Helpers
# =============================================================================

def _parse_csv_line(line_text):
    """Parse a single CSV line, handling quoted fields."""
    reader = csv.reader(io.StringIO(line_text))
    for row in reader:
        return row
    return []


def _parse_csv_file(file_content):
    """Parse a CSV file content into list of rows."""
    rows = []
    for line in file_content.strip().split("\n"):
        line = line.strip()
        if line:
            rows.append(_parse_csv_line(line))
    return rows


def _safe_decimal(val):
    """Convert a value to Decimal, returning 0 if invalid."""
    if val is None:
        return Decimal("0")
    val = str(val).strip().replace(",", "")
    if not val or val == "":
        return Decimal("0")
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _safe_int(val, default=0):
    """Convert a value to int, returning default if invalid."""
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return default


def _safe_str(val):
    """Clean a string value, stripping quotes and whitespace."""
    if val is None:
        return ""
    return str(val).strip().strip('"').strip()


def _parse_date(val):
    """Parse a DD/MM/YYYY date string, returning None if invalid."""
    val = _safe_str(val)
    if not val:
        return None
    try:
        return datetime.strptime(val, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


# =============================================================================
# ZIP File Reader
# =============================================================================

class AccessLedgerZipReader:
    """Reads and organises files from an Access Ledger ZIP export."""

    def __init__(self, zip_file):
        """
        Args:
            zip_file: A file-like object or path to the ZIP file.
        """
        self.zf = zipfile.ZipFile(zip_file, "r")
        self.year_folders = self._discover_year_folders()

    def _discover_year_folders(self):
        """Find all year folders in the ZIP, sorted chronologically."""
        folders = {}
        for name in self.zf.namelist():
            parts = PurePosixPath(name).parts
            if len(parts) >= 1:
                folder = parts[0]
                # Match pattern like HL_FOOT_2025 or CODE_2025
                match = re.search(r'_(\d{4})/?$', folder)
                if match:
                    year = int(match.group(1))
                    folders[year] = folder
        return OrderedDict(sorted(folders.items()))

    def read_file(self, year, filename):
        """Read a text file from a specific year folder."""
        folder = self.year_folders.get(year)
        if not folder:
            return None
        path = f"{folder}/{filename}"
        try:
            with self.zf.open(path) as f:
                return f.read().decode("utf-8", errors="replace")
        except KeyError:
            return None

    def read_csv(self, year, filename):
        """Read and parse a CSV file from a specific year folder."""
        content = self.read_file(year, filename)
        if not content:
            return []
        return _parse_csv_file(content)

    @property
    def years(self):
        """List of years available in the ZIP, sorted ascending."""
        return list(self.year_folders.keys())

    @property
    def latest_year(self):
        """The most recent year in the ZIP."""
        return self.years[-1] if self.years else None

    def close(self):
        self.zf.close()


# =============================================================================
# Entity Parser
# =============================================================================

def _parse_entity_info(reader):
    """
    Extract entity information from the latest year folder.
    Returns a dict with entity details.
    """
    year = reader.latest_year
    if not year:
        raise ValueError("No year folders found in ZIP file.")

    info = {
        "entity_name": "",
        "entity_type": "company",
        "abn": "",
        "tfn": "",
        "trading_as": "",
        "address": {},
        "officers": [],
        "client_code": "",
        "email": "",
    }

    # --- Client.txt ---
    rows = reader.read_csv(year, "Client.txt")
    if rows:
        row = rows[0]
        info["client_code"] = _safe_str(row[1]) if len(row) > 1 else ""
        info["entity_name"] = _safe_str(row[2]) if len(row) > 2 else ""
        info["trading_as"] = _safe_str(row[3]) if len(row) > 3 else ""

        # TFN is at index 8 (may contain spaces)
        if len(row) > 8:
            tfn = _safe_str(row[8]).replace(" ", "")
            if tfn and tfn.isdigit():
                info["tfn"] = tfn

        # ABN is at index 32
        if len(row) > 32:
            abn = _safe_str(row[32]).replace(" ", "")
            if abn:
                info["abn"] = abn

    # --- Entity.txt ---
    rows = reader.read_csv(year, "Entity.txt")
    if rows:
        row = rows[0]
        if len(row) > 1:
            type_code = _safe_int(row[1])
            info["entity_type"] = ENTITY_TYPE_MAP.get(type_code, "company")

    # --- Address.txt ---
    rows = reader.read_csv(year, "Address.txt")
    if rows:
        row = rows[0]
        info["address"] = {
            "street1": _safe_str(row[1]) if len(row) > 1 else "",
            "street2": _safe_str(row[2]) if len(row) > 2 else "",
            "suburb": _safe_str(row[3]) if len(row) > 3 else "",
            "state": _safe_str(row[4]) if len(row) > 4 else "",
            "postcode": _safe_str(row[5]) if len(row) > 5 else "",
        }

    # --- Owner.txt (directors/trustees/partners) ---
    rows = reader.read_csv(year, "Owner.txt")
    for row in rows:
        if len(row) >= 4:
            surname = _safe_str(row[2])
            first_name = _safe_str(row[3])
            if surname or first_name:
                full_name = f"{first_name} {surname}".strip()
                info["officers"].append({
                    "full_name": full_name,
                    "display_order": _safe_int(row[1]),
                })

    # --- Email.txt ---
    rows = reader.read_csv(year, "Email.txt")
    if rows:
        for row in rows:
            if len(row) > 2:
                email = _safe_str(row[2])
                if email and "@" in email:
                    info["email"] = email
                    break

    return info


# =============================================================================
# Chart of Accounts Parser
# =============================================================================

def _parse_chart(reader, year):
    """
    Parse Chart.txt to build account code -> name mapping.
    Uses (code, sub) tuples as keys to properly handle sub-accounts
    (e.g., 1798.01 Management fees - MAJOTI, 3565.03 Loan - Matt).
    Returns dict: {(code, sub): {"name": str, "type": str, "formatted_code": str}}
    """
    rows = reader.read_csv(year, "Chart.txt")
    chart = {}
    for row in rows:
        if len(row) < 7:
            continue
        code = _safe_int(row[0])
        sub = _safe_int(row[1]) if len(row) > 1 else 0
        name = _safe_str(row[3])
        acc_type = _safe_str(row[6])  # "C" or "D"
        formatted = _safe_str(row[13]) if len(row) > 13 else f"{code:04d}"
        if name and name != "********** SUSPENSE **********":
            chart[(code, sub)] = {
                "name": name,
                "type": acc_type,
                "formatted_code": formatted,
            }
    return chart


# =============================================================================
# Balance (Trial Balance) Parser
# =============================================================================

def _parse_balance(reader, year, chart, prior_year_lines=None):
    """
    Parse Balance.txt to extract trial balance data.

    Access Ledger Balance.txt format (20 columns, comma-separated):
        [0]  Year
        [1]  Division (0=current, 1=divisional breakdown)
        [2]  Account code (integer)
        [3]  Sub-account code (integer, 0 = parent)
        [4]  Opening balance (for BS accounts; 0 for P&L)
        [5]-[16] Monthly amounts (12 columns: months 1-12)
        [17] Always 0.00 for most accounts
        [18] Net / closing balance (absolute value for P&L; profit for equity)
        [19] Unknown (always 0)

    NOTE: Column [16] is month 12, NOT a YTD summary. For most accounts
    months 1-11 are zero and only month 12 has a value, but for accounts
    with activity across multiple months (e.g. depreciation), all 12
    monthly columns must be summed to get the correct movement.

    Sign conventions in Access Ledger (determined by Chart.txt type D/C):
        - Type D accounts: negative values = debit balance (normal)
        - Type C accounts: positive values = credit balance (normal)
        - Assets (2000-2999, type=D): opening/closing negative = debit
        - Accum depreciation (type=C): opening/closing positive = credit
        - Liabilities (3000-3999, type=C): positive = credit
        - Income (0-999, type=C): positive ytd = credit
        - Expenses (1000-1999, type=D): negative ytd = debit
        - Equity (4000+): varies by account type in Chart.txt

    Closing balance calculation:
        - BS accounts: closing = opening + movement (ytd or sum of monthly cols)
        - P&L accounts: closing = movement (ytd or sum of monthly cols)
        - Retained profits (4199): closing = opening (pre-close working TB)

    Args:
        prior_year_lines: Optional list of TB line dicts from the previous
            year's parse.  Used to populate prior_debit / prior_credit
            comparatives.  HandiLedger does NOT store prior year data in
            division=2; instead the prior year's Balance.txt (division=0)
            must be parsed separately and passed here.

    Returns list of dicts with trial balance line data.
    """
    rows = reader.read_csv(year, "Balance.txt")

    # Group by (account_code, sub_code) — only division 0 (current year)
    current_data = {}  # (code, sub) -> row

    for row in rows:
        if len(row) < 20:
            continue
        division = _safe_int(row[1])
        account_code = _safe_int(row[2])
        sub_code = _safe_int(row[3])

        if account_code == 0:
            continue  # Skip suspense

        key = (account_code, sub_code)
        if division == 0:
            current_data[key] = row

    # Build prior year lookup from the previous year's parsed TB lines
    # (passed in from the caller, which processes years chronologically)
    prior_lookup = {}  # account_code -> {debit, credit}
    if prior_year_lines:
        for pl in prior_year_lines:
            prior_lookup[pl["account_code"]] = {
                "debit": pl["debit"],
                "credit": pl["credit"],
            }

    def _sum_all_months(row):
        """Sum all 12 monthly columns [5]-[16]."""
        total = Decimal("0")
        for i in range(5, 17):  # columns 5 through 16 inclusive
            total += _safe_decimal(row[i])
        return total

    def _extract_closing_balance(row, code, chart_entry):
        """
        Extract the closing balance from a Balance.txt row.
        Uses Chart.txt account type (D/C) for sign convention.
        Returns (debit, credit) as positive Decimal values.
        """
        opening = _safe_decimal(row[4])
        acc_type = chart_entry.get("type", "D") if chart_entry else "D"

        # Calculate movement: sum all 12 monthly columns [5]-[16]
        # Column [16] is month 12, NOT a YTD total.
        movement = _sum_all_months(row)

        # Determine the raw closing balance
        if code == 4199:
            # Retained profits: use opening balance (pre-close working TB)
            # The P&L accounts show the current year profit separately
            raw = opening
        elif code >= 2000:
            # Balance Sheet + Equity: closing = opening + movement
            raw = opening + movement
        else:
            # P&L accounts: closing = movement only (no opening balance)
            raw = movement

        # Convert to Dr/Cr based on sign and Chart.txt account type
        # Type D: negative = debit (normal), positive = credit (abnormal)
        # Type C: positive = credit (normal), negative = debit (abnormal)
        if acc_type == "D":
            if raw <= 0:
                return (abs(raw), Decimal("0"))
            else:
                return (Decimal("0"), abs(raw))
        else:
            if raw >= 0:
                return (Decimal("0"), abs(raw))
            else:
                return (abs(raw), Decimal("0"))

    # ---------------------------------------------------------------
    # Determine which sub-accounts are "named" (have their own Chart.txt
    # entry with a distinct formatted_code like "1798.01") vs "unnamed"
    # (no own Chart.txt entry — they fall back to the parent and would
    # produce a duplicate code like "1803").  Unnamed sub-accounts must
    # be aggregated into their parent to avoid partial/duplicate TB lines.
    # ---------------------------------------------------------------
    def _is_named_sub(code, sub):
        """Return True if (code, sub) has its own Chart.txt entry with a
        formatted_code that differs from the parent (e.g. '1798.01')."""
        if sub == 0:
            return True  # Parent is always "named"
        own_entry = chart.get((code, sub))
        if not own_entry:
            return False  # No chart entry at all — unnamed
        parent_entry = chart.get((code, 0), {})
        # If the sub has the same formatted_code as the parent, treat as unnamed
        return own_entry.get("formatted_code") != parent_entry.get("formatted_code", f"{code:04d}")

    # Build trial balance lines
    # First pass: compute Dr/Cr for every (code, sub) key in current year
    raw_lines = {}  # key -> {debit, credit, opening, chart_entry, formatted_code, account_name}
    all_keys = sorted(current_data.keys())

    for key in all_keys:
        code, sub = key
        chart_entry = chart.get(key, chart.get((code, 0), {}))
        account_name = chart_entry.get("name", f"Account {code}")
        formatted_code = chart_entry.get("formatted_code", f"{code:04d}")

        cur_row = current_data[key]
        opening = _safe_decimal(cur_row[4])
        debit, credit = _extract_closing_balance(cur_row, code, chart_entry)

        raw_lines[key] = {
            "debit": debit, "credit": credit,
            "opening": opening,
            "chart_entry": chart_entry,
            "formatted_code": formatted_code,
            "account_name": account_name,
        }


    # Second pass: aggregate unnamed sub-accounts into their parent
    aggregated = {}  # formatted_code -> aggregated dict
    code_order = []  # maintain insertion order

    for key in all_keys:
        code, sub = key
        data = raw_lines[key]

        if _is_named_sub(code, sub):
            # Named sub-account or parent — keep as its own line
            fc = data["formatted_code"]
            if fc in aggregated:
                agg = aggregated[fc]
                agg["debit"] += data["debit"]
                agg["credit"] += data["credit"]
                agg["opening"] += data["opening"]
            else:
                aggregated[fc] = {
                    "account_code": fc,
                    "account_name": data["account_name"],
                    "opening": data["opening"],
                    "debit": data["debit"],
                    "credit": data["credit"],
                }
                code_order.append(fc)
        else:
            # Unnamed sub-account — aggregate into parent (code, 0)
            parent_entry = chart.get((code, 0), {})
            parent_fc = parent_entry.get("formatted_code", f"{code:04d}")
            parent_name = parent_entry.get("name", f"Account {code}")

            if parent_fc in aggregated:
                agg = aggregated[parent_fc]
                agg["debit"] += data["debit"]
                agg["credit"] += data["credit"]
                agg["opening"] += data["opening"]
            else:
                aggregated[parent_fc] = {
                    "account_code": parent_fc,
                    "account_name": parent_name,
                    "opening": data["opening"],
                    "debit": data["debit"],
                    "credit": data["credit"],
                }
                code_order.append(parent_fc)

            logger.debug(
                f"  Aggregated unnamed sub ({code}.{sub:02d}) into parent {parent_fc}"
            )

    # Third pass: attach prior year comparatives from the previous year's
    # parsed TB lines.  Also include prior-year-only accounts (accounts
    # that existed in the prior year but have $0 in the current year).
    if prior_lookup:
        for py_code, py_data in prior_lookup.items():
            if py_code not in aggregated:
                # Account exists only in prior year — add it with $0 current
                chart_entry_for_py = None
                for (c, s), ce in chart.items():
                    if ce.get("formatted_code") == py_code:
                        chart_entry_for_py = ce
                        break
                py_name = chart_entry_for_py.get("name", f"Account {py_code}") if chart_entry_for_py else f"Account {py_code}"
                aggregated[py_code] = {
                    "account_code": py_code,
                    "account_name": py_name,
                    "opening": Decimal("0"),
                    "debit": Decimal("0"),
                    "credit": Decimal("0"),
                }
                code_order.append(py_code)

    # Build final lines list
    lines = []
    for fc in code_order:
        agg = aggregated[fc]
        # Look up prior year from the prior_lookup
        py = prior_lookup.get(fc, {"debit": Decimal("0"), "credit": Decimal("0")})
        prior_debit = py["debit"]
        prior_credit = py["credit"]

        if agg["debit"] == 0 and agg["credit"] == 0 and prior_debit == 0 and prior_credit == 0:
            continue
        lines.append({
            "account_code": agg["account_code"],
            "account_name": agg["account_name"],
            "opening_balance": abs(agg["opening"]),
            "debit": agg["debit"],
            "credit": agg["credit"],
            "closing_balance": agg["debit"] - agg["credit"],
            "prior_debit": prior_debit,
            "prior_credit": prior_credit,
        })

    return lines


# =============================================================================
# Depreciation Schedule Parser
# =============================================================================

def _parse_depreciation(reader, year):
    """
    Parse DepSchedule.txt, AssetMaster.txt, and AssetDetail.txt
    to build depreciation asset records.

    Returns list of dicts with depreciation asset data.
    """
    # Parse categories
    cat_rows = reader.read_csv(year, "DepSchedule.txt")
    categories = {}
    for row in cat_rows:
        if len(row) >= 2:
            cat_id = _safe_int(row[0])
            cat_name = _safe_str(row[1])
            if cat_name:
                categories[cat_id] = cat_name

    if not categories:
        return []

    # Parse asset master (static info: name, cost, dates)
    master_rows = reader.read_csv(year, "AssetMaster.txt")
    assets_master = {}  # (cat_id, seq) -> master data
    for row in master_rows:
        if len(row) < 6:
            continue
        cat_id = _safe_int(row[0])
        seq = _safe_int(row[1])
        assets_master[(cat_id, seq)] = {
            "asset_name": _safe_str(row[2]),
            "total_cost": _safe_decimal(row[3]),
            "purchase_date": _parse_date(row[4]),
            "disposal_date": _parse_date(row[5]),
            "disposal_consideration": _safe_decimal(row[6]) if len(row) > 6 else Decimal("0"),
        }

    # Parse asset detail (annual calculations)
    detail_rows = reader.read_csv(year, "AssetDetail.txt")
    assets_detail = {}  # (cat_id, seq) -> detail data
    for row in detail_rows:
        if len(row) < 12:
            continue
        cat_id = _safe_int(row[0])
        seq = _safe_int(row[1])
        detail_year = _safe_int(row[2])

        # Only include detail for the matching year
        if detail_year != year:
            continue

        assets_detail[(cat_id, seq)] = {
            "cost_basis": _safe_decimal(row[3]),
            "disposal_consid_detail": _safe_decimal(row[4]),
            "depreciation_amount": _safe_decimal(row[5]),
            "addition_cost": _safe_decimal(row[7]) if len(row) > 7 else Decimal("0"),
            "rate": _safe_decimal(row[8]) if len(row) > 8 else Decimal("0"),
            "method_code": _safe_int(row[9]) if len(row) > 9 else 0,
            "private_use_amount": _safe_decimal(row[10]) if len(row) > 10 else Decimal("0"),
            "business_depreciation": _safe_decimal(row[11]) if len(row) > 11 else Decimal("0"),
            "private_depreciation": _safe_decimal(row[13]) if len(row) > 13 else Decimal("0"),
        }

    # Combine master + detail into asset records
    result = []
    for (cat_id, seq), master in sorted(assets_master.items()):
        cat_name = categories.get(cat_id, f"Category {cat_id}")
        detail = assets_detail.get((cat_id, seq), {})

        # Calculate opening WDV and closing WDV
        total_cost = master["total_cost"]
        depreciation = detail.get("depreciation_amount", Decimal("0"))
        addition_cost = detail.get("addition_cost", Decimal("0"))
        cost_basis = detail.get("cost_basis", total_cost)
        business_dep = detail.get("business_depreciation", Decimal("0"))
        private_dep = detail.get("private_depreciation", Decimal("0"))

        # Method: 0 = Diminishing Value, 1 = Prime Cost
        method_code = detail.get("method_code", 0)
        method = "D" if method_code == 0 else "P"

        rate = detail.get("rate", Decimal("0"))

        # Calculate private use percentage
        total_dep = depreciation
        if total_dep > 0 and private_dep > 0:
            priv_pct = (private_dep / total_dep * 100).quantize(Decimal("0.01"))
        else:
            priv_pct = Decimal("0")

        # Depreciable value = cost_basis (from detail) or total_cost
        depreciable_value = cost_basis if cost_basis else total_cost

        # Opening WDV: we estimate from cost_basis - depreciation
        # (Access Ledger doesn't directly export OWDV, but we can derive it)
        opening_wdv = depreciable_value  # Will be refined if we have prior year detail

        # Closing WDV = Opening WDV - Depreciation + Additions - Disposals
        closing_wdv = opening_wdv - depreciation + addition_cost

        # Skip fully disposed assets with no current year activity
        if (master["disposal_date"] and depreciation == 0 and
                addition_cost == 0 and closing_wdv <= 0):
            continue

        result.append({
            "category": cat_name,
            "asset_name": master["asset_name"],
            "purchase_date": master["purchase_date"],
            "total_cost": total_cost,
            "private_use_pct": priv_pct,
            "opening_wdv": opening_wdv,
            "disposal_date": master["disposal_date"],
            "disposal_consideration": master.get("disposal_consideration", Decimal("0")),
            "addition_date": None,  # Not directly available
            "addition_cost": addition_cost,
            "depreciable_value": depreciable_value,
            "method": method,
            "rate": rate,
            "depreciation_amount": depreciation,
            "private_depreciation": private_dep,
            "closing_wdv": closing_wdv,
            "display_order": seq,
        })

    return result


# =============================================================================
# Auto-Mapping Logic
# =============================================================================

# Account code ranges from Access Ledger Alias.txt structure
# Maps (code_range_start, code_range_end) -> financial_statement section
# =============================================================================
# HARD SECTION RULES — Account code range ALWAYS determines the section.
# These are the definitive boundaries used by HandiLedger / Access Ledger.
# Keyword matching may only refine the mapping WITHIN a section, never
# override the section itself.  This is a hard rule.
# =============================================================================
_AL_CODE_RANGES = [
    # Revenue / Income (0-999)
    (0, 999, "income_statement", "Revenue"),
    # Cost of Sales (1000-1499)
    # HandiLedger uses 1000-1499 for COS items including:
    #   1000-1099: Direct purchases / materials
    #   1100-1149: Opening stock, COGS, closing stock, WIP
    #   1150-1499: Other direct costs (subcontractors, freight, etc.)
    (1000, 1499, "income_statement", "Cost of Sales"),
    # Expenses (1500-1999)
    (1500, 1999, "income_statement", "Expenses"),
    # Current Assets (2000-2499)
    (2000, 2499, "balance_sheet", "Current Assets"),
    # Non-Current Assets (2500-2999)
    (2500, 2999, "balance_sheet", "Non-Current Assets"),
    # Current Liabilities (3000-3499)
    (3000, 3499, "balance_sheet", "Current Liabilities"),
    # Non-Current Liabilities (3500-3999)
    (3500, 3999, "balance_sheet", "Non-Current Liabilities"),
    # P&L Appropriation (4100-4198)
    (4100, 4198, "appropriation", "Appropriation"),
    # Equity (4199-4999)
    (4199, 4999, "balance_sheet", "Equity"),
]

# Keyword-based mapping rules: (keywords_in_name, standard_code)
# These are checked in order; first match wins
_KEYWORD_MAP_RULES = [
    # --- Revenue ---
    (["interest received", "interest income"], "IS-REV-003"),
    (["dividend"], "IS-REV-004"),
    (["rent received", "rental income", "rents received", "rent income"], "IS-REV-005"),
    (["distribution from", "distribution income"], "IS-REV-007"),
    (["government support", "government subsid", "government industry", "jobkeeper", "cashflow boost"], "IS-REV-008"),
    (["capital gain"], "IS-REV-009"),
    (["profit on sale", "gain on disposal", "gain on sale"], "IS-REV-006"),
    # --- Cost of Sales ---
    (["opening stock", "opening raw", "opening finished", "opening work in progress", "opening wip", "opening inventory"], "IS-COS-002"),
    (["cost of goods sold", "cost of goods", "cost of sales", "cogs", "direct cost", "direct material", "direct labour", "direct labor"], "IS-COS-003"),
    (["closing stock", "closing raw", "closing finished", "closing work in progress", "closing wip", "closing inventory"], "IS-COS-004"),
    (["purchases", "material", "freight inward", "cartage inward", "import duty", "customs"], "IS-COS-003"),
    (["subcontract", "sub-contract", "contract labour", "contract labor"], "IS-COS-001"),
    # --- Expenses ---
    (["accountancy", "accounting fee", "audit fee", "auditor", "professional fee", "tax agent"], "IS-EXP-001"),
    (["advertising", "promotion", "marketing"], "IS-EXP-002"),
    (["bad debt"], "IS-EXP-003"),
    (["bank charge", "bank fee", "merchant fee"], "IS-EXP-004"),
    (["interest expense", "interest paid", "borrowing cost", "interest deduction", "loan interest", "m/v car - interest", "m/v commercial - interest", "m/v other - interest"], "IS-EXP-005"),
    (["contractor", "subcontractor", "sub-contractor", "commission"], "IS-EXP-006"),
    (["depreciation", "amortisation", "amortization", "asset write off", "write-down"], "IS-EXP-007"),
    (["wage", "salary", "salaries", "casual labour", "direct labour", "director fee", "director salary", "staff amenities", "fringe benefit", "fbt", "workcover", "workers comp"], "IS-EXP-008"),
    (["insurance"], "IS-EXP-009"),
    (["motor vehicle", "m/v car", "m/v commercial", "m/v other", "fuel", "registration"], "IS-EXP-010"),
    (["occupancy", "cleaning", "electricity", "gas ", "water ", "council rate", "land tax", "body corporate"], "IS-EXP-011"),
    (["repair", "maintenance"], "IS-EXP-012"),
    (["telephone", "internet", "communication", "mobile"], "IS-EXP-013"),
    (["travel", "accommodation expense"], "IS-EXP-014"),
    (["utilities", "power"], "IS-EXP-015"),
    (["superannuation"], "IS-EXP-017"),
    (["rent expense", "rent on land", "rent paid", "lease payment"], "IS-EXP-018"),
    (["loss on disposal", "loss on sale"], "IS-EXP-019"),
    (["computer expense", "software expense", "computer and software", "it expense", "it cost"], "IS-EXP-016"),
    # --- Current Assets ---
    (["cash at bank", "cash on hand", "petty cash", "bank account", "cheque account", "savings account", "term deposit", "cba", "anz", "nab", "westpac", "commonwealth"], "BS-CA-001"),
    (["trade debtor", "accounts receivable", "sundry debtor"], "BS-CA-002"),
    (["stock on hand", "inventory", "stock "], "BS-CA-003"),
    (["prepayment", "prepaid"], "BS-CA-006"),
    (["loan to", "loan -", "loans to", "upe", "receivable from"], "BS-CA-007"),
    # --- Non-Current Assets ---
    (["furniture", "fixture", "plant and equipment", "office equipment", "computer", "at cost", "accumulated depreciation", "accumulated amortisation"], "BS-NCA-001"),
    (["investment property", "building"], "BS-NCA-002"),
    (["goodwill", "intangible", "patent", "trademark", "licence"], "BS-NCA-003"),
    (["investment", "shares in", "units in", "financial asset"], "BS-NCA-004"),
    (["deferred tax asset", "future income tax"], "BS-NCA-005"),
    # --- Current Liabilities ---
    (["trade creditor", "accounts payable", "sundry creditor", "accrual"], "BS-CL-001"),
    (["bank overdraft", "bank loan", "credit card"], "BS-CL-002"),
    (["provision for tax", "income tax payable", "tax payable", "provision for income"], "BS-CL-003"),
    (["employee entitlement", "provision for leave", "annual leave", "long service", "provision for holiday", "wages payable", "salary payable", "salaries payable", "wage payable"], "BS-CL-004"),
    (["gst payable", "gst liability", "gst control", "gst collected", "gst adjustment", "amounts withheld", "payg withholding", "other ato"], "BS-CL-006"),
    (["provision for super", "superannuation payable"], "BS-CL-005"),
    (["lease liab"], "BS-CL-007"),
    (["loan from", "loans from", "director loan", "shareholder loan", "related party loan"], "BS-CL-008"),
    (["deposit received", "advance payment", "unearned"], "BS-CL-005"),
    (["dividend payable", "dividend provided", "dividend declared"], "BS-CL-005"),
    # --- Non-Current Liabilities ---
    (["deferred tax liab", "provision for deferred"], "BS-NCL-002"),
    (["loan to", "loan -", "loans to", "loan from", "loans from", "director loan", "shareholder loan", "related party loan"], "BS-NCL-004"),
    (["mortgage", "bank loan"], "BS-NCL-001"),
    # --- Equity ---
    (["share capital", "issued capital", "paid up capital", "ordinary shares"], "BS-EQ-001"),
    (["retained profit", "retained earning", "accumulated profit", "accumulated loss"], "BS-EQ-002"),
    (["reserve", "revaluation"], "BS-EQ-003"),
    (["trust corpus", "trust capital", "settled sum"], "BS-EQ-004"),
    (["undistributed", "beneficiary entitlement"], "BS-EQ-005"),
    (["drawing"], "BS-EQ-009"),
    (["dividend paid", "dividend provided"], "BS-EQ-010"),
]

# Fallback mapping by code range section -> default standard_code
_FALLBACK_MAP = {
    ("income_statement", "Revenue"): "IS-REV-002",       # Other income
    ("income_statement", "Cost of Sales"): "IS-COS-003",  # Purchases
    ("income_statement", "Expenses"): "IS-EXP-016",       # Other expenses
    ("balance_sheet", "Current Assets"): "BS-CA-005",      # Other current assets
    ("balance_sheet", "Non-Current Assets"): "BS-NCA-007", # Other non-current assets
    ("balance_sheet", "Current Liabilities"): "BS-CL-005", # Other current liabilities
    ("balance_sheet", "Non-Current Liabilities"): "BS-NCL-005",  # Other non-current liabilities
    ("balance_sheet", "Equity"): "BS-EQ-002",              # Retained earnings
    ("appropriation", "Appropriation"): "BS-EQ-002",       # Retained earnings
}

# Appropriation sub-mapping by code range
_APPROPRIATION_MAP = [
    (4110, 4120, "IS-TAX-001"),    # Income tax expense
    (4160, 4169, "BS-EQ-010"),     # Dividends paid/provided
    (4199, 4199, "BS-EQ-002"),     # Retained profits
    (4140, 4159, "BS-EQ-003"),     # Transfers from reserves
    (4170, 4179, "BS-EQ-003"),     # Transfers to reserves
]


# Map from section_info -> the ONLY standard_code prefix allowed for that section
_SECTION_PREFIX_MAP = {
    ("income_statement", "Revenue"): "IS-REV",
    ("income_statement", "Cost of Sales"): "IS-COS",
    ("income_statement", "Expenses"): "IS-EXP",
    ("balance_sheet", "Current Assets"): "BS-CA",
    ("balance_sheet", "Non-Current Assets"): "BS-NCA",
    ("balance_sheet", "Current Liabilities"): "BS-CL",
    ("balance_sheet", "Non-Current Liabilities"): "BS-NCL",
    ("balance_sheet", "Equity"): "BS-EQ",
}


def _auto_map_account(account_code_int, account_name, chart_entry=None):
    """
    Determine the best AccountMapping standard_code for a given Access Ledger
    account.

    HARD RULE: The numeric account code range ALWAYS determines the section.
    Keyword matching may only select a more specific standard_code WITHIN
    that section — it can NEVER move an account to a different section.

    Section boundaries (HandiLedger / Access Ledger convention):
        0-999      Revenue
        1000-1499  Cost of Sales
        1500-1999  Expenses
        2000-2499  Current Assets
        2500-2999  Non-Current Assets
        3000-3499  Current Liabilities
        3500-3999  Non-Current Liabilities
        4000+      Equity (with 4100-4198 as Appropriation sub-range)

    Args:
        account_code_int: The integer account code from Access Ledger
        account_name: The account name string
        chart_entry: Optional chart dict with 'type' and 'name' fields

    Returns:
        standard_code string (e.g., 'IS-REV-001') or None if no match
    """
    name_lower = account_name.lower().strip()

    # ------------------------------------------------------------------
    # Step 1: Determine the section from code range (HARD RULE)
    # ------------------------------------------------------------------
    section_info = None
    for range_start, range_end, fin_stmt, section in _AL_CODE_RANGES:
        if range_start <= account_code_int <= range_end:
            section_info = (fin_stmt, section)
            break

    if not section_info:
        return None

    # ------------------------------------------------------------------
    # Step 2: Appropriation accounts — specific sub-mapping
    # ------------------------------------------------------------------
    if section_info[0] == "appropriation":
        for app_start, app_end, std_code in _APPROPRIATION_MAP:
            if app_start <= account_code_int <= app_end:
                return std_code
        return _FALLBACK_MAP.get(section_info)

    # ------------------------------------------------------------------
    # Step 3: Keyword matching — ONLY within the section determined above
    # ------------------------------------------------------------------
    expected_prefix = _SECTION_PREFIX_MAP.get(section_info, "")

    for keywords, std_code in _KEYWORD_MAP_RULES:
        # HARD RULE: only consider keywords whose standard_code belongs
        # to the SAME section as determined by the code range.
        if expected_prefix and not std_code.startswith(expected_prefix):
            continue
        for kw in keywords:
            if kw in name_lower:
                return std_code

    # ------------------------------------------------------------------
    # Step 4: Special handling for revenue accounts (code 0-999)
    # ------------------------------------------------------------------
    if section_info == ("income_statement", "Revenue"):
        if account_code_int < 200 or (500 <= account_code_int < 700):
            return "IS-REV-001"  # Revenue / Sales
        return "IS-REV-002"  # Other income

    # ------------------------------------------------------------------
    # Step 5: Fallback to section default (still within the same section)
    # ------------------------------------------------------------------
    return _FALLBACK_MAP.get(section_info)


def _apply_auto_mappings(entity, chart_by_year):
    """
    Apply auto-mapping to all ClientAccountMapping records for an entity,
    and then apply those mappings to TrialBalanceLine records.
    
    Args:
        entity: Entity instance
        chart_by_year: dict of {year: chart_dict} from _parse_chart
    """
    # Build a cache of AccountMapping by standard_code
    am_cache = {}
    for am in AccountMapping.objects.all():
        am_cache[am.standard_code] = am
    
    if not am_cache:
        logger.warning("No AccountMapping records found. Skipping auto-mapping.")
        return
    
    # Get the latest chart for name lookups
    latest_chart = {}
    for year in sorted(chart_by_year.keys(), reverse=True):
        latest_chart = chart_by_year[year]
        break
    
    # Map each ClientAccountMapping
    mapped_count = 0
    unmapped_codes = []
    
    for cam in ClientAccountMapping.objects.filter(entity=entity):
        code_str = cam.client_account_code.lstrip("0") or "0"
        # Handle decimal account codes like 2565.01 -> (2565, 1)
        sub_int = 0
        if "." in code_str:
            parts = code_str.split(".")
            code_str = parts[0]
            try:
                sub_int = int(parts[1])
            except (ValueError, IndexError):
                sub_int = 0
        try:
            code_int = int(code_str)
        except ValueError:
            unmapped_codes.append(cam.client_account_code)
            continue
        
        chart_entry = latest_chart.get((code_int, sub_int), latest_chart.get((code_int, 0)))
        std_code = _auto_map_account(code_int, cam.client_account_name, chart_entry)
        
        if std_code and std_code in am_cache:
            cam.mapped_line_item = am_cache[std_code]
            cam.save(update_fields=["mapped_line_item"])
            mapped_count += 1
        else:
            unmapped_codes.append(f"{cam.client_account_code} ({cam.client_account_name})")
    
    logger.info(f"Auto-mapped {mapped_count} client accounts. "
                f"{len(unmapped_codes)} unmapped.")
    if unmapped_codes:
        logger.info(f"Unmapped accounts: {unmapped_codes[:20]}")
    
    # Now apply mappings to TrialBalanceLine records
    # Build lookup: client_account_code -> AccountMapping
    cam_lookup = {}
    for cam in ClientAccountMapping.objects.filter(
        entity=entity, mapped_line_item__isnull=False
    ).select_related("mapped_line_item"):
        cam_lookup[cam.client_account_code] = cam.mapped_line_item
    
    # Apply to all financial years
    applied = 0
    for fy in entity.financial_years.all():
        for tb_line in fy.trial_balance_lines.filter(mapped_line_item__isnull=True):
            am = cam_lookup.get(tb_line.account_code)
            if am:
                tb_line.mapped_line_item = am
                tb_line.save(update_fields=["mapped_line_item"])
                applied += 1
    
    logger.info(f"Applied mappings to {applied} trial balance lines.")


# =============================================================================
# Main Import Function
# =============================================================================

@transaction.atomic
def import_access_ledger_zip(zip_file, client=None, entity=None, replace_existing=False):
    """
    Import an Access Ledger ZIP export into StatementHub.
    Args:
        zip_file: File-like object or path to the ZIP file.
        client: Optional Client instance to associate the entity with.
        entity: Optional Entity instance to import data into directly.
        replace_existing: If True, delete existing data for this entity
                         before importing. If False, skip if entity exists.

    Returns:
        dict with import results:
        {
            "entity": Entity instance,
            "entity_created": bool,
            "years_imported": int,
            "total_tb_lines": int,
            "total_dep_assets": int,
            "officers_created": int,
            "errors": list of str,
            "warnings": list of str,
        }
    """
    result = {
        "entity": None,
        "entity_created": False,
        "years_imported": 0,
        "total_tb_lines": 0,
        "total_dep_assets": 0,
        "officers_created": 0,
        "errors": [],
        "warnings": [],
    }

    try:
        reader = AccessLedgerZipReader(zip_file)
    except zipfile.BadZipFile:
        result["errors"].append("Invalid ZIP file.")
        return result

    if not reader.years:
        result["errors"].append("No year folders found in ZIP file.")
        reader.close()
        return result

    logger.info(f"Found {len(reader.years)} year folders: {reader.years}")

    # --- Step 1: Parse entity info from latest year ---
    try:
        entity_info = _parse_entity_info(reader)
    except Exception as e:
        result["errors"].append(f"Failed to parse entity info: {str(e)}")
        reader.close()
        return result

    entity_name = entity_info["entity_name"]
    if not entity_name:
        result["errors"].append("Could not determine entity name from ZIP.")
        reader.close()
        return result

    logger.info(f"Processing entity (type={entity_info['entity_type']})")

    # --- Step 2: Create or find Entity ---
    if entity is None:
        entity = Entity.objects.filter(entity_name__iexact=entity_name).first()
    if entity and not replace_existing:
        result["warnings"].append(
            f"Entity '{entity_name}' already exists. "
            f"Use replace_existing=True to overwrite."
        )
        result["entity"] = entity
    elif entity and replace_existing:
        # Delete existing financial years (cascades to TB lines, dep assets)
        deleted_fy = entity.financial_years.all().delete()
        entity.officers.all().delete()
        logger.info(f"Cleared existing data for {entity_name}")
        result["entity"] = entity
    else:
        # Auto-create a Client if none provided
        from .models import Client
        if not client:
            client, _ = Client.objects.get_or_create(
                name=entity_name,
                defaults={
                    "contact_email": entity_info.get("email", ""),
                },
            )
            logger.info(f"Auto-created client: {client.name} (pk={client.pk})")

        # Create new entity
        entity = Entity.objects.create(
            entity_name=entity_name,
            entity_type=entity_info["entity_type"],
            abn=entity_info["abn"],
            trading_as=entity_info.get("trading_as", ""),
            client=client,
            metadata={
                "access_ledger_code": entity_info["client_code"],
                "address": entity_info["address"],
                "email": entity_info.get("email", ""),
                "tfn": entity_info.get("tfn", ""),
                "imported_from": "access_ledger",
            },
        )
        result["entity_created"] = True
        result["entity"] = entity
        logger.info(f"Created entity: {entity_name} (pk={entity.pk})")

    # --- Step 3: Create officers ---
    officer_role = OFFICER_ROLE_MAP.get(entity_info["entity_type"], "director")
    for off in entity_info["officers"]:
        if off["full_name"]:
            obj, created = EntityOfficer.objects.get_or_create(
                entity=entity,
                full_name=off["full_name"],
                defaults={
                    "role": officer_role,
                    "display_order": off.get("display_order", 0),
                    "is_signatory": True,
                },
            )
            if created:
                result["officers_created"] += 1

    # --- Step 4: Import financial years (chronological order) ---
    prior_fy = None
    fy_map = {}  # year_int -> FinancialYear instance
    chart_by_year = {}  # year_int -> chart dict (for auto-mapping)
    prior_year_tb_lines = None  # TB lines from previous year, used as comparatives

    # Pre-build ChartOfAccount mapping cache for this entity type
    # This maps account_code -> AccountMapping for instant lookup during import
    coa_mapping_cache = {}
    entity_type = entity_info["entity_type"]
    for coa in ChartOfAccount.objects.filter(
        entity_type=entity_type, maps_to__isnull=False, is_active=True
    ).select_related("maps_to"):
        coa_mapping_cache[coa.account_code] = coa.maps_to
    logger.info(f"Loaded {len(coa_mapping_cache)} ChartOfAccount mappings for {entity_type}")

    for year in reader.years:
        # Parse Year.txt for dates
        year_rows = reader.read_csv(year, "Year.txt")
        if not year_rows:
            result["warnings"].append(f"No Year.txt found for {year}, skipping.")
            continue

        year_row = year_rows[0]
        start_date = _parse_date(year_row[2]) if len(year_row) > 2 else None
        end_date = _parse_date(year_row[3]) if len(year_row) > 3 else None

        if not start_date or not end_date:
            result["warnings"].append(f"Invalid dates for year {year}, skipping.")
            continue

        # Check if this year's data is finalised in HandiLedger
        # (columns 4,5,6 = "Y" indicate finalised).
        # Unfinalised years are imported as "draft" so that partially-
        # completed clients being transferred from HandiLedger can be
        # continued in StatementHub.
        is_finalised = (
            len(year_row) > 4 and _safe_str(year_row[4]) == "Y"
        )
        fy_status = "finalised" if is_finalised else "draft"
        if not is_finalised:
            result["warnings"].append(
                f"Year {year} is unfinalised in HandiLedger — "
                f"importing as Draft."
            )

        # Create FinancialYear
        fy, fy_created = FinancialYear.objects.get_or_create(
            entity=entity,
            year_label=str(year),
            defaults={
                "start_date": start_date,
                "end_date": end_date,
                "prior_year": prior_fy,
                "status": fy_status,
            },
        )

        if not fy_created:
            # Update prior year link if it was missing
            if prior_fy and not fy.prior_year:
                fy.prior_year = prior_fy
                fy.save(update_fields=["prior_year"])
            # Clear existing data for re-import
            if replace_existing:
                fy.trial_balance_lines.all().delete()
                fy.depreciation_assets.all().delete()

        fy_map[year] = fy
        result["years_imported"] += 1

        # --- Parse chart of accounts ---
        chart = _parse_chart(reader, year)
        chart_by_year[year] = chart

        # --- Parse and import trial balance ---
        tb_lines = _parse_balance(reader, year, chart, prior_year_lines=prior_year_tb_lines)
        tb_objects = []
        mapped_in_year = 0
        for line_data in tb_lines:
            # Look up mapping from ChartOfAccount (pre-populated by map_accounts)
            mapped_item = coa_mapping_cache.get(line_data["account_code"])
            if mapped_item:
                mapped_in_year += 1

            tb_objects.append(TrialBalanceLine(
                financial_year=fy,
                account_code=line_data["account_code"],
                account_name=line_data["account_name"],
                opening_balance=line_data["opening_balance"],
                debit=line_data["debit"],
                credit=line_data["credit"],
                closing_balance=line_data["closing_balance"],
                prior_debit=line_data["prior_debit"],
                prior_credit=line_data["prior_credit"],
                mapped_line_item=mapped_item,
                is_adjustment=False,
                source='tb_import',
            ))

        if tb_objects:
            TrialBalanceLine.objects.bulk_create(tb_objects)
            result["total_tb_lines"] += len(tb_objects)
            logger.info(f"  Year {year}: {len(tb_objects)} TB lines imported ({mapped_in_year} mapped via ChartOfAccount)")

        # --- Parse and import depreciation ---
        dep_assets = _parse_depreciation(reader, year)
        dep_objects = []
        for asset_data in dep_assets:
            dep_objects.append(DepreciationAsset(
                financial_year=fy,
                category=asset_data["category"],
                asset_name=asset_data["asset_name"],
                purchase_date=asset_data["purchase_date"],
                total_cost=asset_data["total_cost"],
                private_use_pct=asset_data["private_use_pct"],
                opening_wdv=asset_data["opening_wdv"],
                disposal_date=asset_data["disposal_date"],
                disposal_consideration=asset_data["disposal_consideration"],
                addition_date=asset_data["addition_date"],
                addition_cost=asset_data["addition_cost"],
                depreciable_value=asset_data["depreciable_value"],
                method=asset_data["method"],
                rate=asset_data["rate"],
                depreciation_amount=asset_data["depreciation_amount"],
                private_depreciation=asset_data["private_depreciation"],
                closing_wdv=asset_data["closing_wdv"],
                display_order=asset_data["display_order"],
            ))

        if dep_objects:
            DepreciationAsset.objects.bulk_create(dep_objects)
            result["total_dep_assets"] += len(dep_objects)
            logger.info(f"  Year {year}: {len(dep_objects)} depreciation assets imported")

        # --- Create account mappings ---
        for line_data in tb_lines:
            mapped_item = coa_mapping_cache.get(line_data["account_code"])
            defaults = {"client_account_name": line_data["account_name"]}
            if mapped_item:
                defaults["mapped_line_item"] = mapped_item
            ClientAccountMapping.objects.update_or_create(
                entity=entity,
                client_account_code=line_data["account_code"],
                defaults=defaults,
            )

        # Store this year's TB lines as prior year data for the next iteration
        prior_year_tb_lines = tb_lines

        prior_fy = fy

    reader.close()

    # --- Step 5: Auto-map accounts ---
    if entity and chart_by_year:
        try:
            _apply_auto_mappings(entity, chart_by_year)
            result["auto_mapped"] = True
        except Exception as e:
            result["warnings"].append(f"Auto-mapping failed: {str(e)}")
            result["auto_mapped"] = False
            logger.exception("Auto-mapping failed")

    logger.info(
        f"Import complete: {result['years_imported']} years, "
        f"{result['total_tb_lines']} TB lines, "
        f"{result['total_dep_assets']} dep assets"
    )

    return result
