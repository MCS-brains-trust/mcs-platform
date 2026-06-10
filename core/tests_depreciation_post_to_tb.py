"""
Regression tests for the depreciation Post to Trial Balance reconciliation.

Covers the four cases specified in Phase 4:
  1. First reconcile with pre-existing imported depreciation lands on schedule
     total, not stacked.
  2. Idempotent repeat press — net zero change, accounts still equal schedule.
  3. Accumulated-depreciation rollover opening preserved across a reconcile.
  4. _post_journal_to_tb source param defaults to 'manual_journal' (siblings
     unaffected).
"""

from datetime import date
from decimal import Decimal

from django.test import Client, TestCase, override_settings

from accounts.models import User
from core.models import (
    AdjustingJournal,
    Client as ClientModel,
    DepreciationAsset,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

POST_URL = "core:depreciation_post_to_tb"


def _post_url(fy_pk):
    from django.urls import reverse
    return reverse(POST_URL, args=[fy_pk])


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DepreciationPostToTBTests(TestCase):
    """
    End-to-end view tests hitting depreciation_post_to_tb with confirmed=1.

    All tests reopen the FY before posting (the handler blocks on FINALISED).
    TB balance assertions read TrialBalanceLine aggregates excluding rollover,
    matching what the accounts actually show after posting.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="dep_post_test_admin",
            password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-dep-post",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Dep Post Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Dep Post Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
            primary_accountant=cls.user,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        # COA entries so the tier-3 fallback resolves accounts by name
        cls.exp_acct = EntityChartOfAccount.objects.create(
            entity=cls.entity,
            account_code="1617",
            account_name="Depreciation – Other",
            section="expenses",
            is_active=True,
        )
        cls.accum_acct = EntityChartOfAccount.objects.create(
            entity=cls.entity,
            account_code="2895",
            account_name="Accumulated Depreciation",
            section="assets",
            is_active=True,
        )

    def setUp(self):
        self.http = Client()
        self.http.force_login(self.user)
        # Each test starts with a clean slate of assets and TB lines
        DepreciationAsset.objects.filter(financial_year=self.fy).delete()
        TrialBalanceLine.objects.filter(financial_year=self.fy).delete()
        AdjustingJournal.objects.filter(financial_year=self.fy).delete()

    def _make_asset(self, dep_amount, private_dep=Decimal("0"), **kwargs):
        """Create a minimal DepreciationAsset with explicit account codes."""
        return DepreciationAsset.objects.create(
            financial_year=self.fy,
            category="Motor Vehicles",
            asset_name=kwargs.pop("name", "Test Asset"),
            total_cost=kwargs.pop("total_cost", dep_amount * 2),
            opening_wdv=dep_amount + Decimal("10"),
            depreciation_amount=dep_amount,
            private_depreciation=private_dep,
            closing_wdv=Decimal("10"),
            rate=Decimal("25.00"),
            dep_expense_code="1617",
            dep_expense_name="Depreciation – Other",
            accum_dep_code="2895",
            accum_dep_name="Accumulated Depreciation",
            **kwargs,
        )

    def _tb_net(self, account_code):
        """Net current-year movement (dr - cr) for an account, excluding rollover."""
        from django.db.models import Sum
        mv = TrialBalanceLine.objects.filter(
            financial_year=self.fy,
            account_code=account_code,
        ).exclude(source="rollover").aggregate(dr=Sum("debit"), cr=Sum("credit"))
        return (mv["dr"] or Decimal("0")) - (mv["cr"] or Decimal("0"))

    def _post(self):
        """POST to the handler with confirmed=1; returns the raw (non-followed) response."""
        return self.http.post(
            _post_url(self.fy.pk),
            data={"confirmed": "1"},
            secure=True,
        )

    # ------------------------------------------------------------------
    # Test 1: first reconcile with imported depreciation → schedule total
    # ------------------------------------------------------------------
    def test_first_post_with_imported_depreciation_lands_on_schedule_total(self):
        """
        Imported TB holds $42,713.20 of depreciation in both accounts.
        Schedule is $16,868.94.  After posting, both accounts net to
        $16,868.94 — no stacking.
        """
        imported = Decimal("42713.20")
        schedule = Decimal("16868.94")

        self._make_asset(schedule, name="Hilux DV")

        # Simulate a prior TB import
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="1617",
            account_name="Depreciation – Other",
            debit=imported, credit=Decimal("0"),
            source="tb_import",
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="2895",
            account_name="Accumulated Depreciation",
            debit=Decimal("0"), credit=imported,
            source="tb_import",
        )

        resp = self._post()
        self.assertEqual(resp.status_code, 302)

        # Expense account: net Dr should equal schedule total
        self.assertEqual(self._tb_net("1617"), schedule)
        # Accum dep account: net Cr (as negative net Dr) should equal schedule
        self.assertEqual(self._tb_net("2895"), -schedule)

        # Reversal journal was created
        reversal = AdjustingJournal.objects.filter(
            financial_year=self.fy,
            journal_type=AdjustingJournal.JournalType.DEPRECIATION_REVERSAL,
        ).first()
        self.assertIsNotNone(reversal)

        # Fresh depreciation journal was created
        dep_journal = AdjustingJournal.objects.filter(
            financial_year=self.fy,
            journal_type=AdjustingJournal.JournalType.DEPRECIATION,
            status=AdjustingJournal.JournalStatus.POSTED,
        ).first()
        self.assertIsNotNone(dep_journal)
        self.assertEqual(dep_journal.total_debit, schedule)

        # TB lines are source-tagged
        self.assertTrue(
            TrialBalanceLine.objects.filter(
                financial_year=self.fy, source="depreciation_schedule"
            ).exists()
        )
        self.assertTrue(
            TrialBalanceLine.objects.filter(
                financial_year=self.fy, source="depreciation_reversal"
            ).exists()
        )

    # ------------------------------------------------------------------
    # Test 2: idempotent repeat press → net zero change
    # ------------------------------------------------------------------
    def test_idempotent_repeat_press_net_zero(self):
        """
        Press Post twice with an unchanged schedule. After the second press
        both accounts still hold exactly the schedule total.
        """
        schedule = Decimal("16868.94")
        self._make_asset(schedule)

        # First post
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        after_first = self._tb_net("1617")
        self.assertEqual(after_first, schedule)

        # Second post (no change to schedule)
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        after_second = self._tb_net("1617")
        self.assertEqual(after_second, schedule)
        self.assertEqual(self._tb_net("2895"), -schedule)

        # Two DEPRECIATION journals and two DEPRECIATION_REVERSAL journals exist
        self.assertEqual(
            AdjustingJournal.objects.filter(
                financial_year=self.fy,
                journal_type=AdjustingJournal.JournalType.DEPRECIATION,
                status=AdjustingJournal.JournalStatus.POSTED,
            ).count(),
            2,
        )

    # ------------------------------------------------------------------
    # Test 3: rollover opening balance on accum-dep is untouched
    # ------------------------------------------------------------------
    def test_rollover_opening_balance_preserved(self):
        """
        Accumulated-depreciation account carries a rollover opening balance
        of $25,000 from a prior year. After reconciliation the rollover line
        is untouched; only current-year movement is reversed and re-posted.
        """
        schedule = Decimal("8000.00")
        rollover_amount = Decimal("25000.00")

        self._make_asset(schedule)

        # Rollover opening balance — must not be reversed
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="2895",
            account_name="Accumulated Depreciation",
            debit=Decimal("0"), credit=rollover_amount,
            source="rollover",
        )
        # Current-year import (should be reversed)
        imported_current = Decimal("5000.00")
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="1617",
            account_name="Depreciation – Other",
            debit=imported_current, credit=Decimal("0"),
            source="tb_import",
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="2895",
            account_name="Accumulated Depreciation",
            debit=Decimal("0"), credit=imported_current,
            source="tb_import",
        )

        resp = self._post()
        self.assertEqual(resp.status_code, 302)

        # Rollover line must still exist, unchanged
        rollover_line = TrialBalanceLine.objects.get(
            financial_year=self.fy,
            account_code="2895",
            source="rollover",
        )
        self.assertEqual(rollover_line.credit, rollover_amount)

        # Current-year net on accum dep (excluding rollover) = schedule total
        self.assertEqual(self._tb_net("2895"), -schedule)
        # The reversal only reversed the tb_import amount, not the rollover
        reversal = AdjustingJournal.objects.filter(
            financial_year=self.fy,
            journal_type=AdjustingJournal.JournalType.DEPRECIATION_REVERSAL,
        ).first()
        self.assertIsNotNone(reversal)
        # Reversal total_debit = imported_current (reversed the accum dep credit)
        self.assertEqual(reversal.total_debit, imported_current)

    # ------------------------------------------------------------------
    # Test 4: _post_journal_to_tb source param defaults to manual_journal
    # ------------------------------------------------------------------
    def test_post_journal_to_tb_default_source_is_manual_journal(self):
        """
        _post_journal_to_tb called without a source arg (all 9 existing
        callers) writes TB lines with source='manual_journal', unchanged.
        """
        from core.views import _post_journal_to_tb

        journal = AdjustingJournal.objects.create(
            financial_year=self.fy,
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=date(2025, 6, 30),
            description="Sibling journal test",
            total_debit=Decimal("100"),
            total_credit=Decimal("100"),
            created_by=self.user,
        )
        from core.models import JournalLine
        JournalLine.objects.create(
            journal=journal, line_number=1,
            account_code="1617", account_name="Depreciation – Other",
            debit=Decimal("100"), credit=Decimal("0"),
        )
        JournalLine.objects.create(
            journal=journal, line_number=2,
            account_code="2895", account_name="Accumulated Depreciation",
            debit=Decimal("0"), credit=Decimal("100"),
        )

        _post_journal_to_tb(journal, self.fy)  # no source arg

        tb_lines = TrialBalanceLine.objects.filter(
            financial_year=self.fy,
            source_journal=journal,
        )
        self.assertTrue(tb_lines.exists())
        for line in tb_lines:
            self.assertEqual(line.source, "manual_journal")

    # ------------------------------------------------------------------
    # Guard: unconfirmed POST is rejected
    # ------------------------------------------------------------------
    def test_unconfirmed_post_is_rejected(self):
        """POST without confirmed=1 is rejected without writing anything."""
        self._make_asset(Decimal("1000.00"))
        resp = self.http.post(_post_url(self.fy.pk), data={}, secure=True)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            AdjustingJournal.objects.filter(financial_year=self.fy).exists()
        )

    # ------------------------------------------------------------------
    # Guard: locked year is rejected
    # ------------------------------------------------------------------
    def test_locked_year_is_rejected(self):
        """POST on a FINALISED year is rejected without writing anything."""
        self.fy.status = FinancialYear.Status.FINALISED
        self.fy.save(update_fields=["status"])

        self._make_asset(Decimal("1000.00"))
        resp = self.http.post(_post_url(self.fy.pk), data={"confirmed": "1"}, secure=True)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            AdjustingJournal.objects.filter(financial_year=self.fy).exists()
        )

        # restore
        self.fy.status = FinancialYear.Status.DRAFT
        self.fy.save(update_fields=["status"])

    # ------------------------------------------------------------------
    # Fresh year: no prior movement, no reversal journal created
    # ------------------------------------------------------------------
    def test_fresh_year_no_reversal_created(self):
        """An entity with no existing depreciation posts cleanly (no reversal)."""
        schedule = Decimal("5000.00")
        self._make_asset(schedule)

        resp = self._post()
        self.assertEqual(resp.status_code, 302)

        self.assertFalse(
            AdjustingJournal.objects.filter(
                financial_year=self.fy,
                journal_type=AdjustingJournal.JournalType.DEPRECIATION_REVERSAL,
            ).exists()
        )
        dep_journal = AdjustingJournal.objects.filter(
            financial_year=self.fy,
            journal_type=AdjustingJournal.JournalType.DEPRECIATION,
            status=AdjustingJournal.JournalStatus.POSTED,
        ).first()
        self.assertIsNotNone(dep_journal)
        self.assertEqual(self._tb_net("1617"), schedule)
