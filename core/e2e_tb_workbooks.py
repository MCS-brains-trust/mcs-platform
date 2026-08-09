"""Trial-balance workbooks uploaded by the Tier 2 year-end close spec.

Generated rather than committed as binaries, so the numbers a spec asserts on are
visible as source next to the assertions.

Format is the simple one _parse_tb_excel supports: [code, name, debit, credit] with
the first row treated as a header.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import openpyxl

HEADER = ("Account Code", "Account Name", "Debit", "Credit")

# Current-year TB. Carries the prior year's balance sheet forward and adds a trading
# result, so the import produces a year that can then be depreciated and closed.
BALANCED_ROWS = [
    ("1-1000", "Cash at Bank", Decimal("62000.00"), Decimal("0.00")),
    ("1-2000", "Plant and Equipment", Decimal("20000.00"), Decimal("0.00")),
    ("1-2100", "Accumulated Depreciation", Decimal("0.00"), Decimal("4000.00")),
    ("2-1000", "Trade Creditors", Decimal("0.00"), Decimal("8000.00")),
    ("3-1000", "Retained Earnings", Decimal("0.00"), Decimal("60000.00")),
    ("4-1000", "Sales", Decimal("0.00"), Decimal("40000.00")),
    ("5-1000", "Cost of Sales", Decimal("18000.00"), Decimal("0.00")),
    ("6-1000", "Administration", Decimal("12000.00"), Decimal("0.00")),
]


def _write(path: Path, rows) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Trial Balance"
    sheet.append(HEADER)
    for code, name, debit, credit in rows:
        sheet.append([code, name, float(debit), float(credit)])
    workbook.save(path)
    return path


def write_tb_workbooks(directory: str | Path) -> dict[str, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    balanced = _write(directory / "tb_balanced.xlsx", BALANCED_ROWS)

    # $500 out — far beyond the $0.02 tolerance, so commit_tb_import must refuse it.
    unbalanced_rows = list(BALANCED_ROWS)
    unbalanced_rows[0] = ("1-1000", "Cash at Bank", Decimal("62500.00"), Decimal("0.00"))
    unbalanced = _write(directory / "tb_unbalanced.xlsx", unbalanced_rows)

    # 1 cent out — inside tolerance, so it is refused without the acknowledgement
    # checkbox and accepted with it.
    rounding_rows = list(BALANCED_ROWS)
    rounding_rows[0] = ("1-1000", "Cash at Bank", Decimal("62000.01"), Decimal("0.00"))
    rounding = _write(directory / "tb_rounding.xlsx", rounding_rows)

    return {"balanced": balanced, "unbalanced": unbalanced, "rounding": rounding}
