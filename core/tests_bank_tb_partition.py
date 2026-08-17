"""Bank postings must land in a row of their own.

_get_or_create_tb_line fell through to qs.first() when an account had no
non-adjustment row, so an account carrying only journal adjustments received its
bank postings inside one of them. Two live entities reached that state (Veronica
Cerratti 3565, Daniel Habteslassie 4080). The rebuild reads only bank_statement
rows and refuses to touch manual_journal, so it cannot see that money — and
would create a second row holding it all over again.
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.views import _post_txn_to_tb

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BankPostingPartitionTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def test_posting_beside_a_journal_adjustment_creates_its_own_row(self):
        """The Cerratti shape, reduced to a fixture."""
        journal = TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="manual_journal",
            is_adjustment=True, debit=D("62500.00"), credit=D("0.00"),
            closing_balance=D("62500.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-14", amount="-1100.00",
                       code="3565", gst="100.00")

        _post_txn_to_tb(txn, self.fy, has_gst=True)

        journal.refresh_from_db()
        self.assertEqual(journal.debit, D("62500.00"),
                         "the journal adjustment must not absorb a bank posting")
        line = bs_line(self.fy, "3565")
        self.assertIsNotNone(line, "a bank_statement row should have been created")
        self.assertEqual(line.debit, D("1000.00"))

    def test_posting_still_accumulates_onto_an_existing_bank_statement_row(self):
        for i in range(2):
            txn = make_txn(self.job, date_str=f"2025-08-1{i}", amount="-110.00",
                           code="0400", gst="10.00")
            _post_txn_to_tb(txn, self.fy, has_gst=True)

        rows = TrialBalanceLine.objects.filter(
            financial_year=self.fy, account_code="0400")
        self.assertEqual(rows.count(), 1, "must not create a row per posting")
        self.assertEqual(rows.first().debit, D("200.00"))

    def test_bank_contra_also_gets_its_own_row(self):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="1100",
            account_name="Business Cheque Account", source="tb_import",
            is_adjustment=False, debit=D("5000.00"), credit=D("0.00"),
            closing_balance=D("5000.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-14", amount="-110.00",
                       code="0400", gst="10.00")

        _post_txn_to_tb(txn, self.fy, has_gst=True)

        imported = TrialBalanceLine.objects.get(
            financial_year=self.fy, account_code="1100", source="tb_import")
        self.assertEqual(imported.debit, D("5000.00"),
                         "an imported balance must not absorb bank movement")
        self.assertEqual(bs_line(self.fy, "1100").credit, D("110.00"))

    def test_other_callers_of_the_helper_are_unchanged(self):
        """bank_statement_only defaults off; the old lookup still applies."""
        from core.views import _get_or_create_tb_line

        existing = TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="9999",
            account_name="Imported", source="tb_import", is_adjustment=False,
            debit=D("1.00"),
        )
        line, created = _get_or_create_tb_line(
            financial_year=self.fy, account_code="9999", defaults={})
        self.assertFalse(created)
        self.assertEqual(line.pk, existing.pk)
