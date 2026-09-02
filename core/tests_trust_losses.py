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
from core.trust_losses import brought_forward_losses


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
