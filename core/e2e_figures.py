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
    # Ordered by (journal_type, journal_date, description, pk). The first three
    # alone tie for real: depreciation_post_to_tb() reverses and re-posts on every
    # call, so a year can hold several DEPRECIATION/DEPRECIATION_REVERSAL journals
    # that all share journal_type, the fixed year-end journal_date, and a
    # description built from a fixed template — exactly the rows a later task's
    # idempotency check (dump after first post vs. dump after second post) depends
    # on comparing byte-for-byte. Without the pk tie-break that comparison could
    # fail on row order alone, which is not the idempotency bug it would appear to be.
    for journal in (
        AdjustingJournal.objects.filter(financial_year=financial_year)
        .order_by("journal_type", "journal_date", "description", "pk")
        .prefetch_related("lines")
    ):
        journals.append(
            {
                "journal_type": journal.journal_type,
                "status": journal.status,
                "description": journal.description,
                "total_debit": _money(journal.total_debit),
                "total_credit": _money(journal.total_credit),
                # (account_code, line_number) is not a DB-enforced unique key, but
                # every JournalLine creation path in the app assigns strictly
                # increasing line_number values within one journal (and edits
                # renumber from 1 on save), so the pair is unique in practice.
                # Belt-and-braces: journal.lines.all() already comes back ordered
                # by JournalLine.Meta.ordering = ["line_number", "id"], and
                # Python's sorted() is stable, so even a hypothetical tie would
                # keep a deterministic id-based sub-order rather than an arbitrary
                # one — no explicit pk key needed here.
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

    # Ordered by (asset_name, pk) for the same reason as the trial balance and
    # journals above: nothing stops two assets sharing a name (e.g. two "Laptop"
    # entries bought in the same year), so asset_name alone is not a total order.
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
        ).order_by("asset_name", "pk")
    ]

    return {
        "trial_balance": tb_rows,
        "journals": journals,
        "depreciation": depreciation,
        "totals": {"debit": _money(total_debit), "credit": _money(total_credit)},
    }
