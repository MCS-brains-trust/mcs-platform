"""A distribution that has been reversed is no longer standing on the ledger.

``trust_unpost_distribution`` has two branches. On an editable year it VOIDs
the journal, and ``live_trust_distribution`` -- which filters status='posted'
-- correctly stops returning it. On a finalised year it REVERSES instead: the
original stays posted for the audit trail and a reversing journal is created
beside it. Nothing told the query the distribution had been undone.

Minli Enterprise Unit Trust FY2026 is in exactly that state: JE-007 posted,
JE-008 reversing it. Three things follow, and all three are wrong.

  * Un-posting again finds JE-007 still "live", takes the reverse branch a
    second time, and credits 4199 by another 626,802.51.
  * The post gate refuses a corrected distribution -- "already posted, un-post
    it first" -- on a year whose distribution has already been un-posted.
  * TRU-01 reads the year as having distributed.

``live_trust_distribution`` means the distribution *currently standing*. A
reversed one does not stand.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import (
    AdjustingJournal, Entity, FinancialYear, JournalLine,
)


class ReversedDistributionIsNotLiveTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Reversal Trust", entity_type="trust_unit",
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            status=FinancialYear.Status.FINALISED,
        )
        self.user = get_user_model().objects.create_user(
            username="rev", password="pw", email="rev@example.com",
        )

    def _distribution(self, ref="JE-007"):
        j = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number=ref,
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=self.fy.end_date,
            description="Trust distribution — FY2026",
            is_trust_distribution=True,
            total_debit=Decimal("626802.51"), total_credit=Decimal("626802.51"),
        )
        JournalLine.objects.create(
            journal=j, line_number=1, account_code="4199",
            account_name="Undistributed income",
            description="Appropriation", debit=Decimal("626802.51"),
            credit=Decimal("0"),
        )
        return j

    def _reversal(self, original, status=AdjustingJournal.JournalStatus.POSTED):
        return AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-008",
            journal_type=AdjustingJournal.JournalType.YEAR_END,
            status=status, journal_date=self.fy.end_date,
            is_trust_distribution=False, reverses=original,
            description=f"Reversal of {original.reference_number}",
            total_debit=Decimal("0"), total_credit=Decimal("626802.51"),
        )

    def test_an_unreversed_distribution_is_live(self):
        """Fixture guard, and the behaviour that must not regress."""
        journal = self._distribution()
        self.assertEqual(
            AdjustingJournal.live_trust_distribution(self.fy), journal)

    def test_a_reversed_distribution_is_not_live(self):
        journal = self._distribution()
        self._reversal(journal)
        self.assertIsNone(
            AdjustingJournal.live_trust_distribution(self.fy),
            "a reversed distribution still reads as standing on the ledger",
        )

    def test_a_draft_reversal_does_not_unseat_the_distribution(self):
        """An unposted reversal has not happened yet."""
        journal = self._distribution()
        self._reversal(journal, status=AdjustingJournal.JournalStatus.DRAFT)
        self.assertEqual(
            AdjustingJournal.live_trust_distribution(self.fy), journal)

    def test_a_voided_distribution_is_still_not_live(self):
        """The pre-existing void path must keep working."""
        journal = self._distribution()
        journal.status = AdjustingJournal.JournalStatus.VOIDED
        journal.save(update_fields=["status"])
        self.assertIsNone(AdjustingJournal.live_trust_distribution(self.fy))

    def test_a_redistribution_after_reversal_becomes_the_live_one(self):
        """Once a corrected distribution is posted, it is what stands."""
        original = self._distribution()
        self._reversal(original)
        corrected = self._distribution(ref="JE-009")
        self.assertEqual(
            AdjustingJournal.live_trust_distribution(self.fy), corrected)
