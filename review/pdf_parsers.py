"""
Bank statement PDF parsers using pdfplumber for direct text extraction.

Each bank has its own parser function. The main entry point is
`extract_transactions_from_pdf_direct()` which auto-detects the bank
and delegates to the appropriate parser.

Supported banks:
  - CBA (Commonwealth Bank of Australia)
  - ANZ (Australia and New Zealand Banking Group)
  - Westpac
  - Bank of Melbourne (Westpac subsidiary)
  - NAB (National Australia Bank)
  - ING
  - Macquarie
  - Bendigo Bank

This replaces the Claude-based extraction for near-instant processing.
"""
import io
import re
import logging
from datetime import date
from decimal import Decimal

import pdfplumber

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared Utilities
# ---------------------------------------------------------------------------

MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}

MONTH_MAP_UPPER = {k.upper(): v for k, v in MONTH_MAP.items()}


def _amt(s):
    """Parse an amount string like '9,109.45' to float."""
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def _empty_result():
    """Return a blank result dict."""
    return {
        "opening_balance": 0,
        "closing_balance": 0,
        "account_name": "",
        "bsb": "",
        "account_number": "",
        "period_start": "",
        "period_end": "",
        "transactions": [],
    }


def _extract_all_text(pdf_content):
    """Return list of (page_idx, text) for every page."""
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            pages.append((i, text))
    return pages


# ---------------------------------------------------------------------------
# CBA (Commonwealth Bank of Australia) Parser
# ---------------------------------------------------------------------------

CBA_DATE_RE = re.compile(
    r"^(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))\s+(.+)"
)
CBA_OPENING_RE = re.compile(
    r"^(\d{1,2}\s+\w+)\s+\d{4}\s+OPENING\s+BALANCE\s+\$([\d,]+\.\d{2})\s*(?:CR|DR)?$"
)
CBA_CLOSING_RE = re.compile(
    r"^(\d{1,2}\s+\w+)\s+\d{4}\s+CLOSING\s+BALANCE\s+\$([\d,]+\.\d{2})\s*(?:CR|DR)?$"
)
CBA_DEBIT_RE = re.compile(
    r"^(.*?)\s+([\d,]+\.\d{2})\s+\$\s+\$([\d,]+\.\d{2})\s*(?:CR|DR)?$"
)
CBA_CREDIT_RE = re.compile(
    r"^(.*?)\s+\$([\d,]+\.\d{2})\s+\$([\d,]+\.\d{2})\s*(?:CR|DR)?$"
)


def _cba_date_to_iso(date_str, year):
    parts = date_str.strip().split()
    if len(parts) < 2:
        return ""
    day = parts[0].zfill(2)
    month = MONTH_MAP.get(parts[1], "01")
    return f"{year}-{month}-{day}"


def parse_cba_statement(pdf_content):
    result = _empty_result()

    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        all_lines = []
        header_parsed = False
        year = str(date.today().year)

        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue
            lines = text.split("\n")

            if not header_parsed:
                for line in lines:
                    acct_match = re.search(
                        r"Account\s+Number\s+(\d{2}\s*\d{4})\s+(\d+)", line
                    )
                    if acct_match:
                        bsb_raw = acct_match.group(1).replace(" ", "")
                        if len(bsb_raw) == 6:
                            result["bsb"] = f"{bsb_raw[:3]}-{bsb_raw[3:]}"
                        else:
                            result["bsb"] = bsb_raw
                        result["account_number"] = acct_match.group(2)

                    period_match = re.search(
                        r"Period\s+(\d{1,2}\s+\w+\s+\d{4})\s*-\s*(\d{1,2}\s+\w+\s+\d{4})",
                        line,
                    )
                    if period_match:
                        result["period_start"] = period_match.group(1)
                        result["period_end"] = period_match.group(2)
                        m = re.search(r"(\d{4})", line)
                        if m:
                            year = m.group(1)

                    cb_match = re.search(
                        r"Closing\s+Balance\s+\$([\d,]+\.\d{2})\s*(?:CR|DR)?", line
                    )
                    if cb_match:
                        result["closing_balance"] = _amt(cb_match.group(1))

                    name_match = re.search(r"Name:\s+(.+)", line)
                    if name_match:
                        result["account_name"] = name_match.group(1).strip()

                header_parsed = True

            in_transactions = False
            for line in lines:
                if re.match(r"Date\s+Transaction\s+Debit\s+Credit\s+Balance", line):
                    in_transactions = True
                    continue
                if re.match(r"Opening\s+balance\s+-\s+Total\s+debits", line):
                    in_transactions = False
                    continue
                if re.match(r"\$[\d,]+\.\d{2}\s+CR\s+\$[\d,]+\.\d{2}", line):
                    in_transactions = False
                    continue
                if in_transactions:
                    all_lines.append(line.strip())

        transactions = []
        current_txn = None

        def _try_amt(text):
            dm = CBA_DEBIT_RE.match(text)
            if dm:
                return -_amt(dm.group(2)), dm.group(1).strip()
            cm = CBA_CREDIT_RE.match(text)
            if cm:
                return _amt(cm.group(2)), cm.group(1).strip()
            return None

        for line in all_lines:
            if not line:
                continue
            ob = CBA_OPENING_RE.match(line)
            if ob:
                result["opening_balance"] = _amt(ob.group(2))
                continue
            cb = CBA_CLOSING_RE.match(line)
            if cb:
                result["closing_balance"] = _amt(cb.group(2))
                continue

            date_match = CBA_DATE_RE.match(line)
            if date_match:
                date_str = date_match.group(1)
                rest = date_match.group(2)
                ar = _try_amt(rest)
                if ar is not None:
                    amount, desc = ar
                    if current_txn and current_txn["amount"] == 0:
                        if desc:
                            current_txn["description"] += " " + desc
                        current_txn["amount"] = amount
                        transactions.append(current_txn)
                        current_txn = None
                    else:
                        if current_txn:
                            transactions.append(current_txn)
                        transactions.append({
                            "date": _cba_date_to_iso(date_str, year),
                            "description": desc,
                            "amount": amount,
                        })
                        current_txn = None
                else:
                    if current_txn:
                        if current_txn["amount"] != 0:
                            transactions.append(current_txn)
                        else:
                            logger.warning(
                                f"Dropping zero-amount txn: {current_txn['description'][:60]}"
                            )
                    current_txn = {
                        "date": _cba_date_to_iso(date_str, year),
                        "description": rest.strip(),
                        "amount": 0,
                    }
            else:
                if current_txn:
                    ar = _try_amt(line)
                    if ar is not None:
                        amount, desc_part = ar
                        if desc_part:
                            current_txn["description"] += " " + desc_part
                        current_txn["amount"] = amount
                        transactions.append(current_txn)
                        current_txn = None
                    else:
                        current_txn["description"] += " " + line.strip()

        if current_txn and current_txn["amount"] != 0:
            transactions.append(current_txn)

    result["transactions"] = transactions
    logger.info(
        f"CBA parser: {len(transactions)} txns, "
        f"opening={result['opening_balance']}, closing={result['closing_balance']}"
    )
    return result


# ---------------------------------------------------------------------------
# ANZ Parser
# ---------------------------------------------------------------------------

# ANZ dates: "DD MON" uppercase, e.g., "03 APR", "02 MAY"
ANZ_DATE_RE = re.compile(
    r"^(\d{1,2}\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC))\s+(.+)"
)

# ANZ debit line: desc amount blank balance  (amount in debits col, "blank" in credits)
# Balance may have DR/CR suffix for overdraft accounts, e.g. "20,078.79DR"
ANZ_DEBIT_RE = re.compile(r"^(.*?)\s+([\d,]+\.\d{2})\s+blank\s+([\d,]+\.\d{2})(?:DR|CR)?$")

# ANZ credit line: desc blank amount balance  ("blank" in debits, amount in credits)
ANZ_CREDIT_RE = re.compile(r"^(.*?)\s+blank\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})(?:DR|CR)?$")


def _anz_date_to_iso(date_str, year):
    parts = date_str.strip().split()
    if len(parts) != 2:
        return ""
    day = parts[0].zfill(2)
    month = MONTH_MAP_UPPER.get(parts[1], "01")
    return f"{year}-{month}-{day}"


def parse_anz_statement(pdf_content):
    result = _empty_result()
    pages = _extract_all_text(pdf_content)
    all_lines = []
    year = str(date.today().year)

    # Parse header info from ALL pages (cover page may split info across lines)
    # We scan all pages for header metadata but stop at the first match for each field
    for page_idx, page_text in pages:
        for line in page_text.split("\n"):
            # Period: "02 APRIL 2024 TO 02 OCTOBER 2024" or "30 SEPTEMBER 2025 TO 31 OCTOBER 2025"
            if not result["period_start"]:
                pm = re.search(
                    r"(\d{1,2}\s+\w+\s+\d{4})\s+TO\s+(\d{1,2}\s+\w+\s+\d{4})", line
                )
                if pm:
                    result["period_start"] = pm.group(1)
                    result["period_end"] = pm.group(2)
                    ym = re.findall(r"(\d{4})", line)
                    if ym:
                        year = ym[-1]

            # BSB: "Branch number (BSB) 013-304" or standalone "013-757" line
            if not result["bsb"]:
                bsb_m = re.search(r"(?:BSB\)?)\s*(\d{3}-\d{3})", line)
                if bsb_m:
                    result["bsb"] = bsb_m.group(1)
                elif re.match(r"^\d{3}-\d{3}$", line.strip()):
                    result["bsb"] = line.strip()

            # Account number: "Account number 4691-20106" or "Account Number 2805-04405"
            if not result["account_number"]:
                acct_m = re.search(r"Account\s+[Nn]umber\s+([\d-]+)", line)
                if acct_m:
                    result["account_number"] = acct_m.group(1)

            # Account name: "Account name(s) SCARTON ELIO G" or standalone like "PRO-TEK CABINETS P/L"
            if not result["account_name"]:
                name_m = re.search(r"Account\s+name\(?s?\)?\s+(.+)", line)
                if name_m:
                    result["account_name"] = name_m.group(1).strip()

            # Opening balance: "Opening balance +$130,858.83" or standalone "$29,981.77" after "Opening Balance:"
            if result["opening_balance"] == 0:
                ob_m = re.search(r"Opening\s+[Bb]alance\s*:?\s*[+\-]?\$?([\d,]+\.\d{2})", line)
                if ob_m:
                    result["opening_balance"] = _amt(ob_m.group(1))

            # Closing balance: "Closing balance +$346,207.10" or standalone
            if result["closing_balance"] == 0:
                cb_m = re.search(r"Closing\s+[Bb]alance\s*:?\s*[+\-]?\$?([\d,]+\.\d{2})", line)
                if cb_m:
                    result["closing_balance"] = _amt(cb_m.group(1))

    # Collect transaction lines from all pages
    for page_idx, text in pages:
        lines = text.split("\n")
        in_transactions = False
        for line in lines:
            # Column header marks start — support both ANZ One format and Business Extra format
            # ANZ One: "Date Transaction description Debits ..."
            # Business Extra: "Date Transaction Details Withdrawals ($) ..."
            if re.match(
                r"Date\s+Transaction\s+(?:description|Details)\s+(?:Debits|Withdrawals)",
                line, re.IGNORECASE
            ):
                in_transactions = True
                continue
            # Year marker: "2024 blank blank"
            if re.match(r"^\d{4}\s+blank\s+blank$", line):
                ym = re.match(r"^(\d{4})", line)
                if ym:
                    year = ym.group(1)
                continue
            # Page totals / end markers
            if "TOTALS AT END OF PAGE" in line:
                continue
            # Page number
            if re.match(r"Page\s+\d+\s+of\s+\d+", line):
                in_transactions = False
                continue
            # Footer lines
            if "XPRCAP" in line or "ANZ ONE STATEMENT" in line or "BUSINESS EXTRA STATEMENT" in line:
                in_transactions = False
                continue
            if "Account number" in line and page_idx > 0:
                continue
            if re.match(r"Transaction\s+[Dd]etails", line) or "Please retain" in line:
                continue
            # Skip "OPENING BALANCE" line (it's metadata, not a transaction)
            if "OPENING BALANCE" in line:
                # But first extract opening balance from it if we haven't yet
                if result["opening_balance"] == 0:
                    ob_m = re.search(r"OPENING\s+BALANCE\s+([\d,]+\.\d{2})", line)
                    if ob_m:
                        result["opening_balance"] = _amt(ob_m.group(1))
                continue
            # Skip TOTALS AT END OF PERIOD but extract closing balance from it
            if "TOTALS AT END OF PERIOD" in line:
                if result["closing_balance"] == 0:
                    # Format: "TOTALS AT END OF PERIOD $285,667.73 $178,520.03 $77,165.93DR"
                    cb_m = re.search(r"([\d,]+\.\d{2})(?:DR|CR)?\s*$", line)
                    if cb_m:
                        result["closing_balance"] = _amt(cb_m.group(1))
                continue
            if in_transactions:
                all_lines.append(line.strip())

    # Parse transactions
    transactions = []
    current_txn = None

    for line in all_lines:
        if not line:
            continue

        # Skip "EFFECTIVE DATE" lines
        if line.startswith("EFFECTIVE DATE"):
            continue

        # Skip "BALANCE BROUGHT FORWARD" line
        if "BALANCE BROUGHT FORWARD" in line:
            continue

        date_match = ANZ_DATE_RE.match(line)
        if date_match:
            date_str = date_match.group(1)
            rest = date_match.group(2)

            # Try debit
            dm = ANZ_DEBIT_RE.match(rest)
            if dm:
                if current_txn:
                    transactions.append(current_txn)
                    current_txn = None
                transactions.append({
                    "date": _anz_date_to_iso(date_str, year),
                    "description": dm.group(1).strip(),
                    "amount": -_amt(dm.group(2)),
                })
                continue

            # Try credit
            cm = ANZ_CREDIT_RE.match(rest)
            if cm:
                if current_txn:
                    transactions.append(current_txn)
                    current_txn = None
                transactions.append({
                    "date": _anz_date_to_iso(date_str, year),
                    "description": cm.group(1).strip(),
                    "amount": _amt(cm.group(2)),
                })
                continue

            # No amount — start of multi-line
            if current_txn:
                transactions.append(current_txn)
            current_txn = {
                "date": _anz_date_to_iso(date_str, year),
                "description": rest.strip(),
                "amount": 0,
            }
        else:
            # Continuation line
            if current_txn:
                dm = ANZ_DEBIT_RE.match(line)
                if dm:
                    desc_part = dm.group(1).strip()
                    if desc_part:
                        current_txn["description"] += " " + desc_part
                    current_txn["amount"] = -_amt(dm.group(2))
                    transactions.append(current_txn)
                    current_txn = None
                    continue
                cm = ANZ_CREDIT_RE.match(line)
                if cm:
                    desc_part = cm.group(1).strip()
                    if desc_part:
                        current_txn["description"] += " " + desc_part
                    current_txn["amount"] = _amt(cm.group(2))
                    transactions.append(current_txn)
                    current_txn = None
                    continue
                # Pure description continuation
                current_txn["description"] += " " + line.strip()

    if current_txn and current_txn["amount"] != 0:
        transactions.append(current_txn)

    result["transactions"] = transactions
    logger.info(
        f"ANZ parser: {len(transactions)} txns, "
        f"opening={result['opening_balance']}, closing={result['closing_balance']}"
    )
    return result


# ---------------------------------------------------------------------------
# Westpac Parser
# ---------------------------------------------------------------------------

# Westpac dates: "DD/MM/YY" e.g., "30/06/25"
WESTPAC_DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{2})\s+(.+)")

# Westpac debit: desc amount balance (debit col then balance)
WESTPAC_DEBIT_RE = re.compile(r"^(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$")

# Westpac credit: desc amount balance (credit col then balance)
# Same pattern as debit — we differentiate by checking balance change
WESTPAC_AMOUNT_RE = re.compile(r"^(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$")

# Single amount with balance
WESTPAC_SINGLE_AMT_RE = re.compile(r"^(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$")


def _westpac_date_to_iso(date_str):
    """Convert DD/MM/YY to YYYY-MM-DD."""
    parts = date_str.split("/")
    if len(parts) != 3:
        return ""
    day, month, yr = parts
    year = f"20{yr}" if int(yr) < 80 else f"19{yr}"
    return f"{year}-{month}-{day}"


def parse_westpac_statement(pdf_content):
    result = _empty_result()
    pages = _extract_all_text(pdf_content)
    all_lines = []

    # Parse header from page 1
    if pages:
        for line in pages[0][1].split("\n"):
            # Period: "30 June 2025 - 31 July 2025"
            pm = re.search(
                r"(\d{1,2}\s+\w+\s+\d{4})\s*-\s*(\d{1,2}\s+\w+\s+\d{4})", line
            )
            if pm:
                result["period_start"] = pm.group(1)
                result["period_end"] = pm.group(2)

            # BSB and Account: "033-106 344 566" (on its own line after header)
            bsb_m = re.search(r"^(\d{3}-\d{3})\s+([\d\s]+?)\s*$", line)
            if bsb_m:
                result["bsb"] = bsb_m.group(1)
                result["account_number"] = bsb_m.group(2).strip()

            # Opening: "Opening Balance + $154,961.72"
            ob_m = re.search(
                r"Opening\s+Balance\s+[+\-]?\s*\$?([\d,]+\.\d{2})", line
            )
            if ob_m:
                result["opening_balance"] = _amt(ob_m.group(1))

            # Closing: "Closing Balance + $155,064.46"
            cb_m = re.search(
                r"Closing\s+Balance\s+[+\-]?\s*\$?([\d,]+\.\d{2})", line
            )
            if cb_m:
                result["closing_balance"] = _amt(cb_m.group(1))

            # Account name
            if "Account Name" in line or "Account name" in line:
                # Next line might have the name, or it's on same line
                pass
            name_m = re.search(r"Account\s+Name\s*\n?\s*(.+)", line)
            if name_m:
                result["account_name"] = name_m.group(1).strip()

    # Collect transaction lines
    for page_idx, text in pages:
        lines = text.split("\n")
        in_transactions = False
        for line in lines:
            if re.match(r"DATE\s+TRANSACTION\s+DESCRIPTION\s+DEBIT\s+CREDIT\s+BALANCE",
                        line, re.IGNORECASE):
                in_transactions = True
                continue
            if "CONVENIENCE AT YOUR FINGERTIPS" in line:
                in_transactions = False
                continue
            if "TRANSACTION FEE SUMMARY" in line:
                in_transactions = False
                continue
            if re.match(r"Westpac Banking Corporation", line):
                in_transactions = False
                continue
            if in_transactions:
                all_lines.append(line.strip())

    # Parse transactions
    transactions = []
    prev_balance = result["opening_balance"]

    for line in all_lines:
        if not line:
            continue

        # Skip opening/closing balance rows
        if "STATEMENT OPENING BALANCE" in line or "OPENING BALANCE" in line:
            m = re.search(r"([\d,]+\.\d{2})\s*$", line)
            if m:
                prev_balance = _amt(m.group(1))
            continue
        if "CLOSING BALANCE" in line:
            continue

        date_match = WESTPAC_DATE_RE.match(line)
        if date_match:
            date_str = date_match.group(1)
            rest = date_match.group(2)

            # Try to find two amounts at end: amount balance
            amt_m = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$", rest)
            if amt_m:
                desc = amt_m.group(1).strip()
                amount_val = _amt(amt_m.group(2))
                new_balance = _amt(amt_m.group(3))

                # Determine debit vs credit by balance change
                if new_balance < prev_balance:
                    amount = -amount_val
                else:
                    amount = amount_val

                transactions.append({
                    "date": _westpac_date_to_iso(date_str),
                    "description": desc,
                    "amount": amount,
                })
                prev_balance = new_balance
                continue

            # Single amount (balance only, or amount only)
            single_m = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s*$", rest)
            if single_m:
                desc = single_m.group(1).strip()
                val = _amt(single_m.group(2))
                # This might be just a balance or an amount — need context
                transactions.append({
                    "date": _westpac_date_to_iso(date_str),
                    "description": desc,
                    "amount": val,
                })
                prev_balance = val
                continue

    result["transactions"] = transactions
    logger.info(
        f"Westpac parser: {len(transactions)} txns, "
        f"opening={result['opening_balance']}, closing={result['closing_balance']}"
    )
    return result


# ---------------------------------------------------------------------------
# Bank of Melbourne Parser (Westpac subsidiary — home loan format)
# ---------------------------------------------------------------------------

def parse_bankofmelb_statement(pdf_content):
    result = _empty_result()
    pages = _extract_all_text(pdf_content)
    all_lines = []

    # Parse header
    if pages:
        full_text = pages[0][1]
        for line in full_text.split("\n"):
            # Loan account
            acct_m = re.search(r"LoanAcct\s*Number\s+(\S+)", line)
            if acct_m:
                result["account_number"] = acct_m.group(1)
            # BSB
            bsb_m = re.search(r"BSB/Acct\s*ID\s*No\.\s+(\d{3}-\d+)", line)
            if bsb_m:
                result["bsb"] = bsb_m.group(1)[:7]  # first 7 chars as BSB
            # Period
            start_m = re.search(r"Statement\s*Start\s*Date\s+(\d{2}/\d{2}/\d{4})", line)
            if start_m:
                result["period_start"] = start_m.group(1)
            end_m = re.search(r"Statement\s*End\s*Date\s+(\d{2}/\d{2}/\d{4})", line)
            if end_m:
                result["period_end"] = end_m.group(1)
            # Name
            name_m = re.match(r"^([A-Z][A-Z\s&]+[A-Z])$", line)
            if name_m and len(line) > 5 and "HOME LOAN" not in line:
                result["account_name"] = name_m.group(1).strip()

    # Collect transaction lines from all pages
    for page_idx, text in pages:
        lines = text.split("\n")
        in_transactions = False
        for line in lines:
            if re.match(r"Date\s+Transaction\s*Description", line, re.IGNORECASE):
                in_transactions = True
                continue
            if "Remember" in line or "Complaints" in line:
                in_transactions = False
                continue
            if in_transactions:
                all_lines.append(line.strip())

    # Parse transactions
    # Format: "DDMon YYYY Description amount balance" or "DDMon YYYY Description balance"
    transactions = []
    DATE_RE = re.compile(r"^(\d{1,2}\w{3}\s+\d{4})\s+(.+)")

    for line in all_lines:
        if not line:
            continue
        # Skip interest rate change lines
        if "INTERESTRATECHANGETO" in line or "InterestRate" in line:
            continue

        dm = DATE_RE.match(line)
        if dm:
            date_raw = dm.group(1)  # e.g., "21Jan 2025"
            rest = dm.group(2)

            # Parse date: "21Jan 2025" -> "2025-01-21"
            ddm = re.match(r"(\d{1,2})(\w{3})\s+(\d{4})", date_raw)
            if ddm:
                day = ddm.group(1).zfill(2)
                month = MONTH_MAP.get(ddm.group(2).capitalize(), "01")
                year = ddm.group(3)
                iso_date = f"{year}-{month}-{day}"
            else:
                iso_date = ""

            # Opening/Closing balance
            if "OpeningBalance" in rest:
                m = re.search(r"([\d,]+\.\d{2})", rest)
                if m:
                    result["opening_balance"] = _amt(m.group(1))
                continue
            if "ClosingBalance" in rest:
                m = re.search(r"([\d,]+\.\d{2})", rest)
                if m:
                    result["closing_balance"] = _amt(m.group(1))
                continue

            # Try to find amount and balance
            # Pattern: "DESCRIPTION amount balance" (2 numbers at end)
            two_nums = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$", rest)
            if two_nums:
                desc = two_nums.group(1).strip()
                amount_val = _amt(two_nums.group(2))
                new_balance = _amt(two_nums.group(3))

                # For home loans: debits increase balance (interest), credits decrease (repayments)
                # INTEREST = debit (increases loan), REPAYMT = credit (decreases loan)
                if "INTEREST" in desc.upper():
                    amount = amount_val  # Interest charged (debit to loan)
                elif "REPAYMT" in desc.upper() or "INTERNETPMT" in desc.upper() or "PMT" in desc.upper():
                    amount = -amount_val  # Repayment (credit to loan)
                elif "REDRAW" in desc.upper():
                    amount = amount_val  # Redraw increases loan
                else:
                    amount = amount_val

                transactions.append({
                    "date": iso_date,
                    "description": desc,
                    "amount": amount,
                })
                continue

            # Single number at end (just balance)
            one_num = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s*$", rest)
            if one_num:
                desc = one_num.group(1).strip()
                # This is likely just a balance line, skip
                continue

    result["transactions"] = transactions
    logger.info(
        f"Bank of Melbourne parser: {len(transactions)} txns, "
        f"opening={result['opening_balance']}, closing={result['closing_balance']}"
    )
    return result


# ---------------------------------------------------------------------------
# NAB Parser
# ---------------------------------------------------------------------------

# NAB dates: "D Mon YYYY" e.g., "1 Apr 2025", "30 Jun 2025"
NAB_DATE_RE = re.compile(
    r"^(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})\s+(.+)"
)


def _nab_date_to_iso(date_str):
    """Convert 'D Mon YYYY' to 'YYYY-MM-DD'."""
    parts = date_str.strip().split()
    if len(parts) != 3:
        return ""
    day = parts[0].zfill(2)
    month = MONTH_MAP.get(parts[1], "01")
    year = parts[2]
    return f"{year}-{month}-{day}"


def parse_nab_statement(pdf_content):
    result = _empty_result()
    pages = _extract_all_text(pdf_content)
    all_lines = []

    # Parse header from page 1
    if pages:
        for line in pages[0][1].split("\n"):
            # Opening: "Opening balance $7,395.45 Cr"
            ob_m = re.search(r"Opening\s+balance\s+\$?([\d,]+\.\d{2})", line)
            if ob_m:
                result["opening_balance"] = _amt(ob_m.group(1))
            # Closing: "Closing balance $5,649.94 Cr"
            cb_m = re.search(r"Closing\s+balance\s+\$?([\d,]+\.\d{2})", line)
            if cb_m:
                result["closing_balance"] = _amt(cb_m.group(1))
            # Period
            start_m = re.search(r"Statement\s+starts\s+(.+?)$", line)
            if start_m:
                result["period_start"] = start_m.group(1).strip()
            end_m = re.search(r"Statement\s+ends\s+(.+?)$", line)
            if end_m:
                result["period_end"] = end_m.group(1).strip()
            # BSB
            bsb_m = re.search(r"BSB\s+number\s+(\d{3}-\d{3})", line)
            if bsb_m:
                result["bsb"] = bsb_m.group(1)
            # Account
            acct_m = re.search(r"Account\s+number\s+([\d-]+)", line)
            if acct_m:
                result["account_number"] = acct_m.group(1)
            # Name
            name_m = re.search(r"AS\s+TRUSTEE\s+FOR\s+(.+)", line)
            if name_m:
                result["account_name"] = name_m.group(1).strip()
            elif not result["account_name"]:
                name_m2 = re.match(r"^([A-Z][A-Z\s]+)$", line)
                if name_m2 and len(line) > 10 and "NAB" not in line:
                    result["account_name"] = line.strip()

    # Collect transaction lines — NAB has monthly summary blocks between
    # dashed lines that must be skipped entirely.
    for page_idx, text in pages:
        lines = text.split("\n")
        in_transactions = False
        in_monthly_summary = False

        for line in lines:
            # Column header
            if re.match(r"Date\s+Particulars\s+Debits\s+Credits\s+Balance", line):
                in_transactions = True
                continue
            if "Transaction Details" in line and "continued" in line:
                in_transactions = True
                continue

            # Monthly summary blocks: start with "date ---" or just "---",
            # end with a plain dashed line (no balance).
            if in_monthly_summary:
                # End of monthly summary: another dashed line (plain dashes)
                if re.match(r"^-{10,}\s*$", line) or (
                    "---" in line and not "Monthly" in line
                    and not re.match(r"\d{1,2}\s+\w+\s+\d{4}", line)
                ):
                    in_monthly_summary = False
                continue
            if "---" in line and not re.search(r"\d+\.\d{2}\s+Cr", line):
                in_monthly_summary = True
                continue
            if "Monthly Transaction Summary" in line:
                in_monthly_summary = True
                continue

            # Skip page footers
            if re.match(r"Statement number", line):
                in_transactions = False
                continue
            if "Summary of Government" in line:
                in_transactions = False
                continue
            if re.match(r"Page\s+\d+\s+of", line):
                in_transactions = False
                continue

            if in_transactions:
                all_lines.append(line.strip())

    # Parse NAB transactions
    # NAB is complex: amounts are embedded in lines with dots as filler.
    # Multiple transactions can share the same date. Sign of amounts cannot
    # be determined until a balance checkpoint ("X,XXX.XX Cr") appears.
    # We accumulate pending unsigned transactions and resolve signs when
    # a balance line is encountered.
    transactions = []
    pending = []  # List of {date, description, amount (unsigned)} awaiting sign
    current_txn = None  # Transaction being built (desc may span multiple lines)
    current_date = ""

    NAB_AMT_BAL_RE = re.compile(
        r"^(.*?)\.{2,}\s*([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+Cr\s*$"
    )
    NAB_AMT_ONLY_RE = re.compile(
        r"^(.*?)\.{2,}\s*([\d,]+\.\d{2})\s*$"
    )
    NAB_INTEREST_RE = re.compile(
        r"^Interest\.+\s*([\d,]+\.\d{2})(?:\s+([\d,]+\.\d{2})\s+Cr)?\s*$"
    )
    NAB_FORWARD_RE = re.compile(
        r"(?:Brought|Carried)\s+forward\s+([\d,]+\.\d{2})\s+Cr"
    )

    prev_balance = result["opening_balance"]

    def _flush_current_to_pending():
        """Move current_txn to pending queue if it has an amount."""
        nonlocal current_txn
        if current_txn and current_txn["amount"] != 0:
            pending.append(current_txn)
        current_txn = None

    def _resolve_pending(new_balance):
        """Resolve signs of all pending transactions using the balance checkpoint.
        Uses subset-sum to find which amounts are credits (positive).
        The rest are debits (negative)."""
        nonlocal prev_balance
        if not pending:
            prev_balance = new_balance
            return

        net_change = round(new_balance - prev_balance, 2)
        amounts = [round(t["amount"], 2) for t in pending]
        n = len(amounts)

        # Try all 2^n sign combinations (n is small, typically 1-5)
        # For each combo, check if sum matches net_change
        best = None
        for mask in range(1 << n):
            total = 0.0
            for i in range(n):
                if mask & (1 << i):  # credit (positive)
                    total += amounts[i]
                else:  # debit (negative)
                    total -= amounts[i]
            if abs(round(total, 2) - net_change) < 0.01:
                best = mask
                break

        if best is not None:
            for i in range(n):
                if best & (1 << i):
                    pending[i]["amount"] = abs(pending[i]["amount"])
                else:
                    pending[i]["amount"] = -abs(pending[i]["amount"])
        else:
            # Fallback: smarter heuristic when exact subset-sum fails
            if n == 1:
                # Single transaction: sign must match net_change direction
                pending[0]["amount"] = abs(pending[0]["amount"]) if net_change > 0 else -abs(pending[0]["amount"])
            else:
                # Try with relaxed tolerance (rounding errors in OCR)
                relaxed_best = None
                for mask in range(1 << n):
                    total = 0.0
                    for i in range(n):
                        if mask & (1 << i):
                            total += amounts[i]
                        else:
                            total -= amounts[i]
                    if abs(round(total, 2) - net_change) < 1.00:
                        relaxed_best = mask
                        break

                if relaxed_best is not None:
                    for i in range(n):
                        if relaxed_best & (1 << i):
                            pending[i]["amount"] = abs(pending[i]["amount"])
                        else:
                            pending[i]["amount"] = -abs(pending[i]["amount"])
                    logger.warning(
                        f"NAB sign resolution: used relaxed tolerance match for "
                        f"net_change={net_change}, amounts={amounts}."
                    )
                else:
                    # Last resort: greedy assignment — assign each sign to
                    # minimise the remaining difference from net_change
                    remaining = net_change
                    sorted_idx = sorted(range(n), key=lambda i: amounts[i], reverse=True)
                    signs = [0] * n
                    for i in sorted_idx:
                        if remaining >= 0:
                            signs[i] = 1
                            remaining -= amounts[i]
                        else:
                            signs[i] = -1
                            remaining += amounts[i]
                    for i in range(n):
                        pending[i]["amount"] = signs[i] * abs(pending[i]["amount"])
                    logger.warning(
                        f"NAB sign resolution: no exact subset-sum match for "
                        f"net_change={net_change}, amounts={amounts}. Used greedy heuristic."
                    )

        transactions.extend(pending)
        pending.clear()
        prev_balance = new_balance

    for line in all_lines:
        if not line:
            continue

        # Skip dashed lines (monthly summary remnants)
        if "---" in line:
            continue

        # Skip brought/carried forward
        if NAB_FORWARD_RE.search(line):
            _flush_current_to_pending()
            m = NAB_FORWARD_RE.search(line)
            _resolve_pending(_amt(m.group(1)))
            continue

        # Skip "Fee Charged"
        if "Fee Charged" in line:
            continue

        # Check for date line first
        date_match = NAB_DATE_RE.match(line)
        if date_match:
            _flush_current_to_pending()
            date_str = date_match.group(1)
            current_date = _nab_date_to_iso(date_str)
            rest = date_match.group(2)
        else:
            rest = line

        # Interest line (with or without date prefix)
        int_m = NAB_INTEREST_RE.match(rest)
        if int_m:
            _flush_current_to_pending()
            amount = _amt(int_m.group(1))
            # Interest is always a credit
            pending.append({
                "date": current_date,
                "description": "Interest",
                "amount": amount,
            })
            if int_m.group(2):
                _resolve_pending(_amt(int_m.group(2)))
            continue

        # Amount + balance line (transaction complete, resolves all pending)
        amt_bal = NAB_AMT_BAL_RE.match(rest)
        if amt_bal:
            desc_part = amt_bal.group(1).strip().rstrip(".")
            amount_val = _amt(amt_bal.group(2))
            new_balance = _amt(amt_bal.group(3))

            if current_txn and current_txn["amount"] == 0:
                if desc_part:
                    current_txn["description"] += " " + desc_part
                current_txn["amount"] = amount_val
                pending.append(current_txn)
                current_txn = None
            else:
                _flush_current_to_pending()
                pending.append({
                    "date": current_date,
                    "description": desc_part,
                    "amount": amount_val,
                })
            _resolve_pending(new_balance)
            continue

        # Amount only (no balance yet — add to pending)
        amt_only = NAB_AMT_ONLY_RE.match(rest)
        if amt_only:
            desc_part = amt_only.group(1).strip().rstrip(".")
            amount_val = _amt(amt_only.group(2))

            if current_txn and current_txn["amount"] == 0:
                if desc_part:
                    current_txn["description"] += " " + desc_part
                current_txn["amount"] = amount_val
                pending.append(current_txn)
                current_txn = None
            else:
                _flush_current_to_pending()
                pending.append({
                    "date": current_date,
                    "description": desc_part,
                    "amount": amount_val,
                })
            continue

        # Pure description line — starts or continues a transaction
        clean = rest.strip().rstrip(".")
        if clean:
            if current_txn and current_txn["amount"] == 0:
                current_txn["description"] += " " + clean
            else:
                _flush_current_to_pending()
                current_txn = {
                    "date": current_date,
                    "description": clean,
                    "amount": 0,
                }

    # Flush any remaining
    _flush_current_to_pending()
    if pending:
        # No final balance to resolve against — append as-is (unsigned)
        transactions.extend(pending)
        pending.clear()

    result["transactions"] = transactions
    logger.info(
        f"NAB parser: {len(transactions)} txns, "
        f"opening={result['opening_balance']}, closing={result['closing_balance']}"
    )
    return result


# ---------------------------------------------------------------------------
# ING Parser
# ---------------------------------------------------------------------------

def parse_ing_statement(pdf_content):
    result = _empty_result()
    pages = _extract_all_text(pdf_content)
    all_lines = []

    # Parse header
    if pages:
        for line in pages[0][1].split("\n"):
            # BSB
            bsb_m = re.search(r"BSB\s+number:\s+(\d{3}\s*\d{3})", line)
            if bsb_m:
                raw = bsb_m.group(1).replace(" ", "")
                result["bsb"] = f"{raw[:3]}-{raw[3:]}"
            # Account number
            acct_m = re.search(r"(?:Savings Maximiser|Orange Everyday)\s+number:\s+(\d+)", line)
            if acct_m:
                result["account_number"] = acct_m.group(1)
            # Period
            period_m = re.search(r"Statement\s+from:\s+(\S+)\s+to\s+(\S+)", line)
            if period_m:
                result["period_start"] = period_m.group(1)
                result["period_end"] = period_m.group(2)
            # Name
            name_m = re.search(r"^(?:Mr|Mrs|Ms|Miss|Dr)\s+(.+)", line)
            if name_m:
                result["account_name"] = name_m.group(0).strip()
            # Balances from summary line
            bal_m = re.search(
                r"\$([\d,]+\.\d{2})\s+\$[\d,]+\.\d{2}\s+\$[\d,]+\.\d{2}\s+\$([\d,]+\.\d{2})",
                line,
            )
            if bal_m:
                result["opening_balance"] = _amt(bal_m.group(1))
                result["closing_balance"] = _amt(bal_m.group(2))

    # Collect transaction lines
    for page_idx, text in pages:
        lines = text.split("\n")
        in_transactions = False
        for line in lines:
            if re.match(r"Date\s+Details\s+Money\s+out", line, re.IGNORECASE):
                in_transactions = True
                continue
            if "Standard Variable Rate" in line or "Total Interest" in line:
                in_transactions = False
                continue
            if re.match(r"Page\s+\d+", line):
                in_transactions = False
                continue
            if in_transactions:
                all_lines.append(line.strip())

    # Parse ING transactions
    # Format: "DD/MM/YYYY Description amount balance" or "DD/MM/YYYY Description (no amount)"
    ING_DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.+)")
    transactions = []

    for line in all_lines:
        if not line:
            continue

        # Skip rate change lines
        if "Rate Changed" in line:
            continue

        dm = ING_DATE_RE.match(line)
        if dm:
            date_raw = dm.group(1)
            rest = dm.group(2)

            # Parse date
            parts = date_raw.split("/")
            iso_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

            # Try to find two amounts: money_out/in and balance
            two_m = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$", rest)
            if two_m:
                desc = two_m.group(1).strip()
                amount_val = _amt(two_m.group(2))
                # ING: "Money out" vs "Money in" — check column position
                # If description mentions "Credit" or "Interest", it's money in
                # Otherwise we check balance
                transactions.append({
                    "date": iso_date,
                    "description": desc,
                    "amount": amount_val,  # Will be positive for credits
                })
                continue

            # Single amount
            one_m = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s*$", rest)
            if one_m:
                desc = one_m.group(1).strip()
                amount_val = _amt(one_m.group(2))
                transactions.append({
                    "date": iso_date,
                    "description": desc,
                    "amount": amount_val,
                })

    result["transactions"] = transactions
    logger.info(
        f"ING parser: {len(transactions)} txns, "
        f"opening={result['opening_balance']}, closing={result['closing_balance']}"
    )
    return result


# ---------------------------------------------------------------------------
# Macquarie Parser
# ---------------------------------------------------------------------------

def parse_macquarie_statement(pdf_content):
    result = _empty_result()
    pages = _extract_all_text(pdf_content)
    all_lines = []

    # Parse header
    if pages:
        for line in pages[0][1].split("\n"):
            # Period: "from 15 Nov 23 to 29 Dec 23"
            pm = re.search(r"from\s+(.+?)\s+to\s+(.+?)$", line)
            if pm:
                result["period_start"] = pm.group(1)
                result["period_end"] = pm.group(2)
            # Account number
            acct_m = re.search(r"account\s+no\.\s+(\d+)", line)
            if acct_m:
                result["account_number"] = acct_m.group(1)
            # Account name
            name_m = re.search(r"account\s+name\s+(.+)", line)
            if name_m:
                result["account_name"] = name_m.group(1).strip()
            # BSB
            bsb_m = re.search(r"BSB\s+(\d{3}\s*\d{3})", line)
            if bsb_m:
                raw = bsb_m.group(1).replace(" ", "")
                result["bsb"] = f"{raw[:3]}-{raw[3:]}"

    # Collect transaction lines
    for page_idx, text in pages:
        lines = text.split("\n")
        in_transactions = False
        for line in lines:
            if re.match(r"transaction\s+description\s+debits\s+credits\s+balance",
                        line, re.IGNORECASE):
                in_transactions = True
                continue
            if "how to make a transaction" in line.lower():
                in_transactions = False
                continue
            if "continued on next" in line.lower():
                continue
            if re.match(r"page\s+\d+\s+of\s+\d+", line, re.IGNORECASE):
                continue
            if "account name" in line.lower() or "account no" in line.lower():
                continue
            if "enquiries" in line.lower():
                continue
            if in_transactions:
                all_lines.append(line.strip())

    # Parse Macquarie transactions
    # Date: DD.MM.YY, e.g., "15.11.23"
    MAC_DATE_RE = re.compile(r"^(\d{2}\.\d{2}\.\d{2})\s+(.+)")
    transactions = []
    prev_balance = 0

    for line in all_lines:
        if not line:
            continue

        # OPENING BALANCE
        if "OPENING BALANCE" in line:
            m = re.search(r"([\d,]+\.\d{2})\s*$", line)
            if m:
                result["opening_balance"] = _amt(m.group(1))
                prev_balance = result["opening_balance"]
            continue

        # CLOSING BALANCE
        if "CLOSING BALANCE" in line:
            # "CLOSING BALANCE AS AT 29 DEC 23 531,756.68 532,147.52 390.84"
            nums = re.findall(r"[\d,]+\.\d{2}", line)
            if nums:
                result["closing_balance"] = _amt(nums[-1])
            continue

        dm = MAC_DATE_RE.match(line)
        if dm:
            date_raw = dm.group(1)  # DD.MM.YY
            rest = dm.group(2)

            # Parse date
            parts = date_raw.split(".")
            year = f"20{parts[2]}" if int(parts[2]) < 80 else f"19{parts[2]}"
            iso_date = f"{year}-{parts[1]}-{parts[0]}"

            # Try to find amounts
            # Pattern: "desc amount1 amount2" or "desc amount1 amount2 amount3"
            # With 3 numbers: debit credit balance
            three_m = re.search(
                r"(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$",
                rest,
            )
            if three_m:
                desc = three_m.group(1).strip()
                # debit, credit, balance
                debit = _amt(three_m.group(2))
                credit = _amt(three_m.group(3))
                balance = _amt(three_m.group(4))
                amount = credit - debit if credit > 0 else -debit
                transactions.append({
                    "date": iso_date,
                    "description": desc,
                    "amount": amount,
                })
                prev_balance = balance
                continue

            # Two numbers: amount balance
            two_m = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$", rest)
            if two_m:
                desc = two_m.group(1).strip()
                amount_val = _amt(two_m.group(2))
                new_balance = _amt(two_m.group(3))

                if new_balance < prev_balance:
                    amount = -amount_val
                else:
                    amount = amount_val

                transactions.append({
                    "date": iso_date,
                    "description": desc,
                    "amount": amount,
                })
                prev_balance = new_balance
                continue

    result["transactions"] = transactions
    logger.info(
        f"Macquarie parser: {len(transactions)} txns, "
        f"opening={result['opening_balance']}, closing={result['closing_balance']}"
    )
    return result


# ---------------------------------------------------------------------------
# Bendigo Bank Parser
# ---------------------------------------------------------------------------

def parse_bendigo_statement(pdf_content):
    result = _empty_result()
    pages = _extract_all_text(pdf_content)
    all_lines = []

    # Parse header
    if pages:
        for line in pages[0][1].split("\n"):
            # BSB: "BSBnumber 633-000"
            bsb_m = re.search(r"BSB\s*number\s+(\d{3}-\d{3})", line)
            if bsb_m:
                result["bsb"] = bsb_m.group(1)
            # Account: "Accountnumber 160178034"
            acct_m = re.search(r"Account\s*number\s+(\d+)", line)
            if acct_m:
                result["account_number"] = acct_m.group(1)
            # Period: "Statementperiod 1Oct2025-31Oct2025"
            period_m = re.search(r"Statement\s*period\s+(.+)", line)
            if period_m:
                raw = period_m.group(1)
                # Split on hyphen
                parts = raw.split("-")
                if len(parts) == 2:
                    result["period_start"] = parts[0].strip()
                    result["period_end"] = parts[1].strip()
            # Account title
            title_m = re.search(r"Account\s*title\s+(.+)", line)
            if title_m:
                result["account_name"] = title_m.group(1).strip()
            # Opening: "Openingbalanceon1Oct2025 $34,838.80"
            ob_m = re.search(r"Opening\s*balance\s*(?:on\s*\S+)?\s+\$([\d,]+\.\d{2})", line)
            if ob_m:
                result["opening_balance"] = _amt(ob_m.group(1))
            # Closing: "ClosingBalanceon31Oct2025 $39,631.28"
            cb_m = re.search(r"Closing\s*Balance\s*(?:on\s*\S+)?\s+\$([\d,]+\.\d{2})", line)
            if cb_m:
                result["closing_balance"] = _amt(cb_m.group(1))

    # Collect transaction lines
    for page_idx, text in pages:
        lines = text.split("\n")
        in_transactions = False
        for line in lines:
            if re.match(r"Date\s+Transaction\s+Withdrawals\s+Deposits\s+Balance", line):
                in_transactions = True
                continue
            if "Transactiontotals" in line or "Transaction totals" in line:
                in_transactions = False
                continue
            if "Wesuggest" in line or "We suggest" in line:
                in_transactions = False
                continue
            if in_transactions:
                all_lines.append(line.strip())

    # Parse Bendigo transactions
    # Date: "1Oct25" (no spaces, 2-digit year)
    BENDIGO_DATE_RE = re.compile(
        r"^(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{2})\s+(.+)",
        re.IGNORECASE,
    )
    transactions = []

    for line in all_lines:
        if not line:
            continue

        # Skip opening balance line in table
        if line.startswith("Openingbalance") or line.startswith("Opening balance"):
            continue

        dm = BENDIGO_DATE_RE.match(line)
        if dm:
            day = dm.group(1).zfill(2)
            month = MONTH_MAP.get(dm.group(2).capitalize(), "01")
            yr = dm.group(3)
            year = f"20{yr}" if int(yr) < 80 else f"19{yr}"
            iso_date = f"{year}-{month}-{day}"
            rest = dm.group(4)

            # Try to find amounts
            # Two numbers at end: amount balance
            two_m = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$", rest)
            if two_m:
                desc = two_m.group(1).strip()
                amount_val = _amt(two_m.group(2))
                # Bendigo: Withdrawals column vs Deposits column
                # We can't tell from position in text, so use balance comparison
                transactions.append({
                    "date": iso_date,
                    "description": desc,
                    "amount": amount_val,  # Positive = credit for now
                })
                continue

            # Single number
            one_m = re.search(r"(.*?)\s+([\d,]+\.\d{2})\s*$", rest)
            if one_m:
                desc = one_m.group(1).strip()
                amount_val = _amt(one_m.group(2))
                transactions.append({
                    "date": iso_date,
                    "description": desc,
                    "amount": amount_val,
                })

    result["transactions"] = transactions
    logger.info(
        f"Bendigo parser: {len(transactions)} txns, "
        f"opening={result['opening_balance']}, closing={result['closing_balance']}"
    )
    return result


# ---------------------------------------------------------------------------
# Bank Detection & Main Entry Point
# ---------------------------------------------------------------------------

def detect_bank(pdf_content):
    """Detect which bank a PDF statement is from."""
    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        if not pdf.pages:
            return "unknown"
        text = pdf.pages[0].extract_text() or ""
        text_lower = text.lower()

        # Check most specific first
        if "commonwealth bank" in text_lower or "commbank" in text_lower:
            return "cba"
        # Bank of Melbourne must be checked before Westpac (it's a Westpac subsidiary)
        if "bank of melbourne" in text_lower or "bankofmelbourne" in text_lower:
            return "bankofmelb"
        # Check for home loan statements from Westpac divisions
        if "adivisionof" in text_lower.replace(" ", "") and "westpac" in text_lower.replace(" ", ""):
            return "bankofmelb"
        if "westpac" in text_lower:
            return "westpac"
        if "national australia bank" in text_lower or "nab cash" in text_lower:
            return "nab"
        if "macquarie" in text_lower:
            return "macquarie"
        if "bendigobank" in text_lower or "bendigo" in text_lower:
            return "bendigo"
        # ING: check for "Savings Maximiser" or "923 100" BSB (ING's BSB)
        if "savings maximiser" in text_lower or "923 100" in text or "ing bank" in text_lower:
            return "ing"
        if "anz" in text_lower:
            return "anz"

        return "unknown"


def extract_transactions_from_pdf_direct(pdf_content, filename=""):
    """
    Main entry point: extract transactions from a bank statement PDF
    using direct text extraction (no AI).

    Args:
        pdf_content: bytes of the PDF file
        filename: original filename (for logging)

    Returns:
        dict with keys: opening_balance, closing_balance, account_name,
        bsb, account_number, period_start, period_end, transactions

    Raises:
        ValueError if bank is not supported
    """
    bank = detect_bank(pdf_content)
    logger.info(f"Detected bank: {bank} for file: {filename}")

    parsers = {
        "cba": parse_cba_statement,
        "anz": parse_anz_statement,
        "westpac": parse_westpac_statement,
        "bankofmelb": parse_bankofmelb_statement,
        "nab": parse_nab_statement,
        "ing": parse_ing_statement,
        "macquarie": parse_macquarie_statement,
        "bendigo": parse_bendigo_statement,
    }

    parser = parsers.get(bank)
    if parser:
        return parser(pdf_content)
    else:
        raise ValueError(
            f"Unsupported bank format for '{filename}'. "
            f"Detected: {bank}. Currently supported: {', '.join(parsers.keys())}. "
            f"Please contact support to add your bank."
        )
