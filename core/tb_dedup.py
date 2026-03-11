"""
Trial Balance Row Deduplication
================================
Detects duplicate account codes in a list of raw TB row dicts and merges
them by summing debit/credit values.  Pure transformation — never touches
the database.

Usage:
    from core.tb_dedup import merge_duplicate_accounts
    cleaned, warnings = merge_duplicate_accounts(raw_lines)
"""

from collections import OrderedDict
from decimal import Decimal, InvalidOperation


def _to_decimal(value):
    """Safely convert a value to Decimal, defaulting to 0."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def merge_duplicate_accounts(rows):
    """Merge rows that share the same account_code.

    Args:
        rows: list of dicts, each with at least ``account_code``,
              ``account_name``, ``debit``, ``credit``.  May also contain
              ``opening_balance``, ``prior_debit``, ``prior_credit``, and
              any other keys (preserved from the first occurrence).

    Returns:
        (cleaned_rows, warnings)
        - cleaned_rows: list of dicts with duplicates merged (order preserved)
        - warnings: list of plain-English strings describing each merge
    """
    if not rows:
        return [], []

    # Accumulate by account_code, preserving first-seen order
    merged = OrderedDict()  # code -> dict
    counts = {}             # code -> int (original row count)

    for row in rows:
        code = (row.get("account_code") or "").strip()
        if not code:
            # Pass through rows without a code unchanged
            merged.setdefault(code, dict(row))
            continue

        if code not in merged:
            # First occurrence — copy all fields
            merged[code] = dict(row)
            counts[code] = 1
        else:
            # Duplicate — sum the numeric columns
            existing = merged[code]
            for field in ("debit", "credit", "opening_balance",
                          "prior_debit", "prior_credit"):
                if field in row or field in existing:
                    existing[field] = str(
                        _to_decimal(existing.get(field))
                        + _to_decimal(row.get(field))
                    )
            counts[code] = counts.get(code, 1) + 1

    # Build warnings for every code that appeared more than once
    warnings = []
    for code, count in counts.items():
        if count > 1:
            entry = merged[code]
            name = entry.get("account_name", "")
            dr = _to_decimal(entry.get("debit"))
            cr = _to_decimal(entry.get("credit"))
            warnings.append(
                f"Account {code} ({name}) appeared {count} times — "
                f"merged into one row (total debit ${dr:,.2f}, credit ${cr:,.2f})."
            )

    return list(merged.values()), warnings
