"""The brought-forward trust loss position, shared by every caller that needs it.

Account 4199 holds the brought-forward position, debit-positive: a
carried-forward loss is a debit and reduces the distributable figure,
carried-forward undistributed income is a credit and increases it.

This lived inside ``_calculate_income_streams`` and was therefore invisible to
the Tax Planning tab, whose ``calculate_section1_from_tb`` never looked at 4199
at all. The two tabs disagreed by the whole carried-forward balance: Minli
Enterprise Unit Trust FY2027 offered $216,101.66 on one and nil on the other.
One function, two callers, one answer.
"""
from decimal import Decimal

ZERO = Decimal("0")


def brought_forward_losses(financial_year):
    """Return the brought-forward 4199 balance, debit-positive.

    A positive result is a loss to be recouped before income is distributable;
    a negative result is carried-forward undistributed income, itself
    distributable.

    The rule is "everything in 4199 EXCEPT this year's own appropriation".
    Keying off ``source="rollover"`` instead is wrong as soon as a prior year's
    correction is recognised in the current year: Dr Services FY2026 opens on
    the lodged 29,150.97 and carries a prior-period adjustment crediting
    1,099.23 (a GST reclass FY2025 could not take up, being already lodged), so
    the recoupable loss is 28,051.74 and not the 29,150.97 the rollover row
    alone reports. Excluding only the live distribution's own rows also keeps
    the figure idempotent -- posting a distribution must not shrink the balance
    that sized it.
    """
    from core.models import AdjustingJournal, TrialBalanceLine

    lines = TrialBalanceLine.objects.filter(
        financial_year=financial_year, account_code__startswith="4199",
    )
    live = AdjustingJournal.live_trust_distribution(financial_year)
    if live is not None:
        # Both sides of the appropriation, or neither. A reversal is still a
        # posted journal on the ledger, and excluding only the distribution
        # left the reversing credit standing where it read as a prior-period
        # adjustment: Minli FY2026's JE-007/JE-008 pair reported a carried
        # balance of 1,628,428.89 against a true 2,255,231.40.
        own = [live.pk] + list(
            live.reversed_by.filter(
                status=AdjustingJournal.JournalStatus.POSTED,
            ).values_list("pk", flat=True)
        )
        lines = lines.exclude(source_journal__in=own)
    # ZERO as the start value so an empty 4199 returns Decimal("0") rather
    # than int 0, which would poison Decimal arithmetic downstream.
    return sum((line.closing_balance or ZERO for line in lines), ZERO)
