"""Reconcile depreciation schedules against the trial balance. Writes nothing.

Twenty-four assets carry a negative closing written-down value, which cannot be
true of an asset. The trial balance holds the defensible position — cost less
accumulated depreciation — and the schedule is what drifted away from it.

Two things stop this being a subtraction and a save.

Cost less accumulated depreciation is the OPENING written-down value only while
the year's depreciation is still unposted. Once posted, the accumulated account
already contains it and the same subtraction yields the CLOSING value. Reading
one as the other misstates the asset by a full year's charge.

Depreciation and accumulated-depreciation accounts are also frequently shared
between assets. Dr Services Family Trust's account 1617 carried 11,147.00 in
FY2024 while the vehicle's accumulated depreciation moved only 4,229.00, so the
remainder belonged elsewhere. Where an account pair serves more than one asset,
no automated rule can apportion it — that is an accounting judgement. Those
assets are reported as unreconcilable rather than guessed at.

Which assets to repair, and to what, is the accountant's decision. This module
supplies the evidence for it.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.db.models import Q

ZERO = Decimal("0.00")


@dataclass
class Reconciliation:
    """What the trial balance says about one asset, beside what the schedule says."""

    asset: object
    current_opening_wdv: Decimal
    current_depreciation: Decimal
    current_closing_wdv: Decimal
    tb_cost: Optional[Decimal] = None
    tb_accumulated: Optional[Decimal] = None
    tb_depreciation_expense: Optional[Decimal] = None
    cost_account_code: str = ""
    accum_account_code: str = ""
    is_reconcilable: bool = False
    reason: str = ""

    @property
    def tb_written_down_value(self) -> Optional[Decimal]:
        """Cost less accumulated depreciation, as the trial balance has it."""
        if self.tb_cost is None or self.tb_accumulated is None:
            return None
        return (self.tb_cost - self.tb_accumulated).quantize(Decimal("0.01"))

    @property
    def depreciation_is_posted(self) -> bool:
        """Whether this year's charge already sits in the accumulated account."""
        return bool(self.tb_depreciation_expense) and self.tb_depreciation_expense != ZERO

    @property
    def proposed_depreciation(self) -> Optional[Decimal]:
        """This year's charge on the proposed opening, by the asset's own terms.

        The schedule's recorded charge cannot be reused here: on the assets this
        audit exists to find it is itself corrupt. Dr Services FY2026 holds
        -8,534.00, and subtracting that from a 25,591.00 opening produced a
        closing of 34,125.00 — higher than the opening, which is impossible.
        """
        opening = self.proposed_opening_wdv
        if opening is None:
            return None
        if self.depreciation_is_posted:
            return self.tb_depreciation_expense
        method = (self.asset.method or "").upper()
        rate = self.asset.rate or ZERO
        if method == "W":
            return opening
        if method == "D":
            return (opening * rate / Decimal("100")).quantize(Decimal("0.01"))
        if method == "P":
            base = self.tb_cost if self.tb_cost is not None else opening
            return min((base * rate / Decimal("100")).quantize(Decimal("0.01")), opening)
        return ZERO

    @property
    def proposed_closing_wdv(self) -> Optional[Decimal]:
        wdv = self.tb_written_down_value
        if wdv is None:
            return None
        if self.depreciation_is_posted:
            # The accumulated account already holds this year's charge, so the
            # trial balance position IS the closing value.
            return wdv
        charge = self.proposed_depreciation or ZERO
        return (wdv - charge).quantize(Decimal("0.01"))

    @property
    def proposed_opening_wdv(self) -> Optional[Decimal]:
        wdv = self.tb_written_down_value
        if wdv is None:
            return None
        if not self.depreciation_is_posted:
            return wdv
        # Posted: the trial balance position is the closing, so the opening is
        # that plus the charge the accumulated account has already absorbed.
        return (wdv + self.tb_depreciation_expense).quantize(Decimal("0.01"))

    @property
    def needs_correction(self) -> bool:
        proposed = self.proposed_opening_wdv
        if proposed is None:
            return self.has_impossible_values
        return proposed != self.current_opening_wdv or self.has_impossible_values

    @property
    def has_impossible_values(self) -> bool:
        return (
            self.current_opening_wdv < ZERO
            or self.current_closing_wdv < ZERO
            or self.current_depreciation < ZERO
        )


def _tb_lines(fy):
    from core.models import TrialBalanceLine
    return TrialBalanceLine.objects.filter(financial_year=fy)


def _closing_for(fy, code):
    """Net closing balance across every trial-balance row for an account code."""
    rows = _tb_lines(fy).filter(account_code=code)
    if not rows.exists():
        return None
    total = sum((r.closing_balance or ZERO) for r in rows)
    return Decimal(total).quantize(Decimal("0.01"))


# Sections of the chart that can never hold an asset's cost or its accumulated
# depreciation. Kinross Builders' 1740 "Hire/Rent of plant & equipment" matched
# the cost regex on both "plant" and "equipment" and, at a lower account code
# than the real 2860 and 2890, won the match outright.
_PROFIT_AND_LOSS_SECTIONS = ("expenses", "revenue", "pl_appropriation")


def _profit_and_loss_codes(fy):
    """Account codes the entity's own chart puts in the P&L.

    Empty when the chart says nothing, which leaves the name match exactly as
    it was for entities whose chart is unclassified.
    """
    from core.models import EntityChartOfAccount
    return set(
        EntityChartOfAccount.objects
        .filter(entity=fy.entity, section__in=_PROFIT_AND_LOSS_SECTIONS)
        .values_list("account_code", flat=True)
    )


def _find_account(fy, pattern, exclude=None, balance_sheet_only=True):
    """The one trial-balance account whose name matches, as (code, closing).

    Returns (None, None) unless exactly one account matches. A name that fits
    several accounts identifies none of them: Kinross Builders carries both
    2869 and 2895 as "Less: Accumulated depreciation", and taking the lower
    code paired one asset's cost with another asset's accumulated depreciation.
    Guessing here is what produced a written-down value of -15,390, and this
    module's whole purpose is to report what it cannot resolve rather than
    invent an answer for it.

    ``balance_sheet_only`` drops candidates the chart puts in the P&L. It is
    off for the depreciation EXPENSE lookup, which is a P&L account by
    definition -- excluding it there left every posted year looking unposted
    and understated the proposed opening by a full year's charge.
    """
    qs = _tb_lines(fy).filter(account_name__iregex=pattern)
    if exclude:
        qs = qs.exclude(account_name__iregex=exclude)
    barred = _profit_and_loss_codes(fy) if balance_sheet_only else set()
    codes = sorted({r.account_code for r in qs if r.account_code not in barred})
    if len(codes) != 1:
        return None, None
    return codes[0], _closing_for(fy, codes[0])


def reconcile_asset(asset) -> Reconciliation:
    """Compare one asset's schedule against the trial balance. Writes nothing."""
    from core.models import DepreciationAsset

    fy = asset.financial_year
    result = Reconciliation(
        asset=asset,
        current_opening_wdv=asset.opening_wdv or ZERO,
        current_depreciation=asset.depreciation_amount or ZERO,
        current_closing_wdv=asset.closing_wdv or ZERO,
    )

    # An account pair serving several assets cannot be apportioned by rule.
    siblings = DepreciationAsset.objects.filter(financial_year=fy).exclude(pk=asset.pk)
    if siblings.exists():
        result.reason = (
            f"Shares its accounts with {siblings.count()} other asset(s) in this "
            f"year — the trial balance cannot be apportioned between them by rule."
        )

    # Cost account: the asset's own code first, then by name.
    if asset.asset_account_code:
        code = asset.asset_account_code
        result.cost_account_code, result.tb_cost = code, _closing_for(fy, code)
    else:
        result.cost_account_code, result.tb_cost = _find_account(
            fy, r"cost|vehicle|motor|plant|equipment|furniture",
            exclude=r"accum|depreciation|expense",
        )

    # Accumulated depreciation, held as a credit; compare it as a positive.
    if asset.accum_dep_code:
        code = asset.accum_dep_code
        result.accum_account_code = code
        accum = _closing_for(fy, code)
    else:
        result.accum_account_code, accum = _find_account(fy, r"accum")
    result.tb_accumulated = None if accum is None else abs(accum)

    # This year's charge, to tell an opening position from a closing one.
    if asset.dep_expense_code:
        result.tb_depreciation_expense = _closing_for(fy, asset.dep_expense_code)
    else:
        _, expense = _find_account(
            fy, r"depreciation", exclude=r"accum", balance_sheet_only=False)
        result.tb_depreciation_expense = expense

    if result.tb_cost is None:
        result.reason = (
            "No asset cost account could be identified in the trial balance — "
            "the asset carries no cost account code, and its name matches no "
            "single balance-sheet account."
        ) if not asset.asset_account_code else (
            f"Account {asset.asset_account_code} is not in the trial balance."
        )
        result.is_reconcilable = False
        return result
    if result.tb_accumulated is None:
        result.reason = (
            "No accumulated depreciation account could be identified — the "
            "asset carries no accumulated depreciation code, and its name "
            "matches no single account."
        ) if not asset.accum_dep_code else (
            f"Account {asset.accum_dep_code} is not in the trial balance."
        )
        result.is_reconcilable = False
        return result

    result.is_reconcilable = not result.reason
    return result


def reconcile_all(only_impossible=True, entity=None):
    """Every asset worth a look, most broken first. Writes nothing."""
    from core.models import DepreciationAsset

    qs = DepreciationAsset.objects.select_related(
        "financial_year", "financial_year__entity")
    if only_impossible:
        qs = qs.filter(
            Q(opening_wdv__lt=0) | Q(closing_wdv__lt=0) | Q(depreciation_amount__lt=0))
    if entity:
        qs = qs.filter(financial_year__entity=entity)

    results = [reconcile_asset(a) for a in qs]
    results.sort(
        key=lambda r: (
            r.asset.financial_year.entity.entity_name,
            r.asset.financial_year.year_label,
            r.asset.asset_name or "",
        )
    )
    return results
