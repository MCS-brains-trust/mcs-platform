"""Canonical JSON snapshot of one financial year's figures.

The Tier 2 golden baseline is a diff of these snapshots, so the format is built for
diffing rather than for reading: decimals as fixed-scale strings, rows in a stable
order, no timestamps or primary keys — anything that changes between runs without the
figures changing would make every run a false positive.
"""
from __future__ import annotations

from decimal import Decimal


def _money(value) -> str:
    """Two-decimal string. Floats and None both normalise to the same shape."""
    return str(Decimal(str(value or "0")).quantize(Decimal("0.01")))


def dump_figures(financial_year) -> dict:
    from core.models import AdjustingJournal, DepreciationAsset, TrialBalanceLine

    tb_rows = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    # Ordered by (account_code, account_name, source, pk) — the app deliberately
    # allows duplicate account codes as separate lines (e.g. two adjustments on the
    # same account), so the first three keys alone are not a total order. Falling
    # back to pk lets the database pick any order it likes on ties without making
    # the dump non-deterministic between runs, since the pk is never emitted.
    lines = TrialBalanceLine.objects.filter(financial_year=financial_year).order_by(
        "account_code", "account_name", "source", "pk"
    )
    for line in lines:
        total_debit += Decimal(str(line.debit or 0))
        total_credit += Decimal(str(line.credit or 0))
        tb_rows.append(
            {
                "account_code": line.account_code,
                "account_name": line.account_name,
                "opening_balance": _money(line.opening_balance),
                "debit": _money(line.debit),
                "credit": _money(line.credit),
                "closing_balance": _money(line.closing_balance),
                "prior_closing_balance": _money(line.prior_closing_balance),
                "is_adjustment": line.is_adjustment,
                "source": line.source,
            }
        )

    journals = []
    for journal in (
        AdjustingJournal.objects.filter(financial_year=financial_year)
        .order_by("journal_type", "journal_date", "description")
        .prefetch_related("lines")
    ):
        journals.append(
            {
                "journal_type": journal.journal_type,
                "status": journal.status,
                "description": journal.description,
                "total_debit": _money(journal.total_debit),
                "total_credit": _money(journal.total_credit),
                "lines": [
                    {
                        "account_code": jl.account_code,
                        "account_name": jl.account_name,
                        "debit": _money(jl.debit),
                        "credit": _money(jl.credit),
                    }
                    for jl in sorted(
                        journal.lines.all(), key=lambda x: (x.account_code, x.line_number)
                    )
                ],
            }
        )

    depreciation = [
        {
            "asset_name": asset.asset_name,
            "opening_wdv": _money(asset.opening_wdv),
            "depreciation_amount": _money(asset.depreciation_amount),
            "private_depreciation": _money(asset.private_depreciation),
            "closing_wdv": _money(asset.closing_wdv),
            "dep_expense_code": asset.dep_expense_code,
            "accum_dep_code": asset.accum_dep_code,
        }
        for asset in DepreciationAsset.objects.filter(
            financial_year=financial_year
        ).order_by("asset_name")
    ]

    return {
        "trial_balance": tb_rows,
        "journals": journals,
        "depreciation": depreciation,
        "totals": {"debit": _money(total_debit), "credit": _money(total_credit)},
    }
