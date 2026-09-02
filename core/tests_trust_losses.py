"""The brought-forward 4199 position, as a single shared helper.

Two tabs need this figure and disagreed about it. ``_calculate_income_streams``
(Trust tab) recouped the carried-forward loss; ``calculate_section1_from_tb``
(Tax Planning tab) never looked at 4199 at all, so Minli Enterprise Unit Trust
FY2027 offered $216,101.66 for distribution on one tab while the other -- and
the post gate -- correctly said nil against $1,628,428.89 of losses.

The rule this helper carries, unchanged from where it grew up inside
``_calculate_income_streams``: 4199 is debit-positive, so a carried-forward
loss is a positive figure here and carried-forward undistributed income is
negative. Brought forward is *everything* in 4199 except the live
distribution's own appropriation -- not merely the ``source="rollover"`` row,
because a prior-period adjustment moves the recoupable balance too.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    AdjustingJournal, Client, Entity, FinancialYear, TrialBalanceLine,
)
from core.trust_losses import brought_forward_losses, recoup


class RecoupTests(TestCase):
    """The ladder arithmetic, in one place for both tabs.

    ``losses_carried_forward`` was computed as
    ``max(bf, 0) - recouped + max(-income, 0)``, which throws away a CREDIT
    brought-forward whenever the year is also a loss. With 20 of undistributed
    income brought forward and a 30 loss it reported 30 carried forward instead
    of 10; with 50 brought forward and a 30 loss it offered 20 to distribute
    *and* claimed 30 of losses carried forward in the same breath.

    The closing 4199 position is simply the opening position less what the
    year earned, floored at nil.
    """

    def _l(self, income, bf):
        return recoup(Decimal(income), Decimal(bf))

    def test_profit_partly_absorbed(self):
        r = self._l("100", "40")
        self.assertEqual(r["distributable"], Decimal("60"))
        self.assertEqual(r["losses_recouped"], Decimal("40"))
        self.assertEqual(r["losses_carried_forward"], Decimal("0"))

    def test_profit_fully_absorbed(self):
        r = self._l("100", "150")
        self.assertEqual(r["distributable"], Decimal("0"))
        self.assertEqual(r["losses_recouped"], Decimal("100"))
        self.assertEqual(r["losses_carried_forward"], Decimal("50"))

    def test_loss_year_against_a_carried_loss(self):
        r = self._l("-30", "40")
        self.assertEqual(r["distributable"], Decimal("0"))
        self.assertEqual(r["losses_recouped"], Decimal("0"))
        self.assertEqual(r["losses_carried_forward"], Decimal("70"))

    def test_profit_on_top_of_undistributed_income(self):
        r = self._l("100", "-20")
        self.assertEqual(r["distributable"], Decimal("120"))
        self.assertEqual(r["undistributed_brought_forward"], Decimal("20"))
        self.assertEqual(r["losses_carried_forward"], Decimal("0"))

    def test_a_loss_partly_eating_undistributed_income(self):
        """20 brought forward, 30 lost: 10 of losses carry forward, not 30."""
        r = self._l("-30", "-20")
        self.assertEqual(r["distributable"], Decimal("0"))
        self.assertEqual(r["losses_recouped"], Decimal("0"))
        self.assertEqual(
            r["losses_carried_forward"], Decimal("10"),
            "the credit brought forward was thrown away",
        )

    def test_a_loss_inside_undistributed_income_carries_no_loss(self):
        """50 brought forward, 30 lost: 20 still distributable, 0 carried."""
        r = self._l("-30", "-50")
        self.assertEqual(r["distributable"], Decimal("20"))
        self.assertEqual(
            r["losses_carried_forward"], Decimal("0"),
            "distributable income and carried-forward losses were reported "
            "at the same time, which cannot both be true",
        )

    def test_minli_fy2027(self):
        r = self._l("216101.66", "1628428.89")
        self.assertEqual(r["distributable"], Decimal("0"))
        self.assertEqual(r["losses_recouped"], Decimal("216101.66"))
        self.assertEqual(
            r["losses_carried_forward"], Decimal("1412327.23"))

    def test_minli_fy2025_loss_year(self):
        r = self._l("-568879.30", "1686352.10")
        self.assertEqual(
            r["losses_carried_forward"], Decimal("2255231.40"))

    def test_no_losses_at_all(self):
        r = self._l("100000", "0")
        self.assertEqual(r["distributable"], Decimal("100000"))
        self.assertEqual(r["losses_recouped"], Decimal("0"))
        self.assertEqual(r["losses_carried_forward"], Decimal("0"))


class BroughtForwardLossesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Losses Client")
        cls.trust = Entity.objects.create(
            entity_name="Losses Test Trust", entity_type="trust",
            client=cls.client_obj,
        )

    def setUp(self):
        self.fy = FinancialYear.objects.create(
            entity=self.trust, year_label="FY2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )

    def _line(self, closing, source, code="4199", journal=None):
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code,
            account_name="Undistributed income",
            closing_balance=Decimal(closing),
            debit=Decimal(closing) if Decimal(closing) > 0 else Decimal("0"),
            credit=-Decimal(closing) if Decimal(closing) < 0 else Decimal("0"),
            source=source, source_journal=journal,
        )

    def test_no_4199_rows_is_nil(self):
        self.assertEqual(brought_forward_losses(self.fy), Decimal("0"))

    def test_a_rollover_debit_is_a_carried_forward_loss(self):
        self._line("1628428.89", "rollover")
        self.assertEqual(
            brought_forward_losses(self.fy), Decimal("1628428.89"))

    def test_a_credit_balance_is_carried_forward_undistributed_income(self):
        self._line("-10000.00", "rollover")
        self.assertEqual(brought_forward_losses(self.fy), Decimal("-10000.00"))

    def test_a_prior_period_adjustment_moves_the_balance(self):
        """Not just the rollover row -- Dr Services FY2026's GST reclass."""
        self._line("29150.97", "rollover")
        self._line("-1099.23", "manual_journal")
        self.assertEqual(brought_forward_losses(self.fy), Decimal("28051.74"))

    def test_the_live_distributions_own_appropriation_is_excluded(self):
        """Posting a distribution must not shrink the balance that sized it."""
        self._line("28051.74", "rollover")
        journal = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-D01",
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=self.fy.end_date, description="Trust distribution",
            is_trust_distribution=True,
        )
        self._line("61848.01", "manual_journal", journal=journal)
        self.assertEqual(
            brought_forward_losses(self.fy), Decimal("28051.74"),
            "the year's own appropriation was counted as brought forward",
        )

    def test_other_accounts_are_ignored(self):
        self._line("500000.00", "rollover", code="4110")
        self.assertEqual(brought_forward_losses(self.fy), Decimal("0"))

    def test_a_reversed_distribution_leaves_no_half_behind(self):
        """Both sides of a reversed appropriation, or neither.

        Minli FY2026: JE-007 posted 626,802.51 to 4199 and JE-008 reversed it.
        ``live_trust_distribution`` returns JE-007, so excluding only the live
        distribution dropped the debit and left the reversing credit standing,
        counted as though it were a prior-period adjustment. The carried
        balance read 1,628,428.89 against a true 2,255,231.40.

        The reversal is identifiable: JE-008.reverses is JE-007. The helper
        simply never asked.
        """
        self._line("2255231.40", "rollover")
        distribution = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-007",
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=self.fy.end_date, description="Trust distribution",
            is_trust_distribution=True,
        )
        reversal = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-008",
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=self.fy.end_date,
            description="Reversal of JE-007", reverses=distribution,
        )
        self._line("626802.51", "manual_journal", journal=distribution)
        self._line("-626802.51", "manual_journal", journal=reversal)

        self.assertEqual(
            brought_forward_losses(self.fy), Decimal("2255231.40"),
            "the reversing credit was counted as brought forward",
        )

    def test_an_unposted_reversal_does_not_exclude_anything(self):
        """A draft reversal has not happened yet."""
        self._line("2255231.40", "rollover")
        distribution = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-007",
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=self.fy.end_date, description="Trust distribution",
            is_trust_distribution=True,
        )
        reversal = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-008",
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.DRAFT,
            journal_date=self.fy.end_date,
            description="Reversal of JE-007", reverses=distribution,
        )
        self._line("626802.51", "manual_journal", journal=distribution)
        self._line("-626802.51", "manual_journal", journal=reversal)

        # The live distribution's own row is excluded as always; the draft
        # reversal's row is not, because it has not been posted.
        self.assertEqual(
            brought_forward_losses(self.fy), Decimal("1628428.89"))
