"""The rebuild primitive: recompute bank-statement TB rows from the transactions.

Everything in this project depends on this function being right, because once
wired it runs on every edit of every book.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.txn_periods import resolve_fy_for_txn
from core.views import _post_txn_to_tb, _recalculate_bank_tb_lines

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RebuildPrimitiveTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _post(self, date_str, amount, code, gst="0"):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code=code,
                       gst=gst, tax_type="GST on Expenses" if gst != "0" else "")
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=(gst != "0"))
        return txn

    def test_equivalence_with_incremental_posting_on_a_clean_book(self):
        self._post("2025-08-01", "-1100.00", "0400", gst="100.00")
        self._post("2025-08-02", "-550.00", "0400", gst="50.00")
        self._post("2025-08-03", "2200.00", "0510", gst="200.00")
        before = {
            l.account_code: (l.debit, l.credit)
            for l in TrialBalanceLine.objects.filter(
                financial_year=self.fy, source="bank_statement")
        }

        _recalculate_bank_tb_lines(self.fy)

        after = {
            l.account_code: (l.debit, l.credit)
            for l in TrialBalanceLine.objects.filter(
                financial_year=self.fy, source="bank_statement")
        }
        self.assertEqual(before, after)

    def test_creates_a_line_for_an_account_that_has_none(self):
        txn = self._post("2025-08-01", "-110.00", "0400", gst="10.00")
        # Reallocate to an account with no TB row at all.
        txn.confirmed_code = "0450"
        txn.confirmed_name = "Repairs"
        txn.save(update_fields=["confirmed_code", "confirmed_name"])

        _recalculate_bank_tb_lines(self.fy)

        line = bs_line(self.fy, "0450")
        self.assertIsNotNone(line, "the rebuild must create the vacated-to line")
        self.assertEqual(line.debit, D("100.00"))
        self.assertEqual(line.account_name, "Repairs")

    def test_zeroes_the_line_the_transactions_left(self):
        txn = self._post("2025-08-01", "-110.00", "0400", gst="10.00")
        txn.confirmed_code = "0450"
        txn.save(update_fields=["confirmed_code"])

        _recalculate_bank_tb_lines(self.fy)

        self.assertEqual(bs_line(self.fy, "0400").debit, D("0.00"))
        self.assertEqual(bs_line(self.fy, "0400").closing_balance, D("0.00"))

    def test_is_idempotent(self):
        self._post("2025-08-01", "-1100.00", "0400", gst="100.00")
        _recalculate_bank_tb_lines(self.fy)
        once = bs_line(self.fy, "0400").debit
        _recalculate_bank_tb_lines(self.fy)
        _recalculate_bank_tb_lines(self.fy)
        self.assertEqual(bs_line(self.fy, "0400").debit, once)

    def test_manual_journal_lines_are_untouched(self):
        journal = TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="0400",
            account_name="Office costs", source="manual_journal",
            is_adjustment=True, debit=D("777.00"), closing_balance=D("777.00"),
        )
        self._post("2025-08-01", "-110.00", "0400", gst="10.00")

        _recalculate_bank_tb_lines(self.fy)

        journal.refresh_from_db()
        self.assertEqual(journal.debit, D("777.00"))

    def test_opening_balance_is_preserved(self):
        self._post("2025-08-01", "-110.00", "0400", gst="10.00")
        line = bs_line(self.fy, "0400")
        line.opening_balance = D("500.00")
        line.save(update_fields=["opening_balance"])

        _recalculate_bank_tb_lines(self.fy)

        line.refresh_from_db()
        self.assertEqual(line.opening_balance, D("500.00"))
        self.assertEqual(line.closing_balance, D("600.00"))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RebuildYearIsolationTests(TestCase):
    """The rebuild had no year filter at all — it summed every year onto one."""

    def setUp(self):
        self.entity = make_entity()
        self.fy25 = make_fy(self.entity, "FY2025", date(2024, 7, 1), date(2025, 6, 30))
        self.fy26 = make_fy(self.entity, "FY2026", date(2025, 7, 1), date(2026, 6, 30))
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy25)

    def _post(self, date_str, amount, code="0400"):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code=code)
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=False)
        return txn

    def test_rebuilding_one_year_does_not_absorb_the_other(self):
        self._post("2025-06-20", "-100.00")
        self._post("2025-07-03", "-250.00")

        _recalculate_bank_tb_lines(self.fy25)
        _recalculate_bank_tb_lines(self.fy26)

        self.assertEqual(bs_line(self.fy25, "0400").debit, D("100.00"))
        self.assertEqual(bs_line(self.fy26, "0400").debit, D("250.00"))

    def test_rebuilding_one_year_leaves_the_other_untouched(self):
        self._post("2025-06-20", "-100.00")
        self._post("2025-07-03", "-250.00")
        _recalculate_bank_tb_lines(self.fy25)
        _recalculate_bank_tb_lines(self.fy26)
        before = bs_line(self.fy26, "0400").debit

        _recalculate_bank_tb_lines(self.fy25)

        self.assertEqual(bs_line(self.fy26, "0400").debit, before)

    def test_an_unparseable_date_stays_in_the_year_posting_put_it_in(self):
        txn = make_txn(self.job, date_str="n/a", amount="-90.00", code="0400")
        posted_to = resolve_fy_for_txn(txn)
        self.assertEqual(posted_to, self.fy26, "fallback is the most recent year")
        _post_txn_to_tb(txn, posted_to, has_gst=False)

        _recalculate_bank_tb_lines(self.fy26)

        self.assertEqual(bs_line(self.fy26, "0400").debit, D("90.00"),
                         "filtering on the date range would have zeroed this")


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RebuildEntanglementGuardTests(TestCase):
    """A book whose bank postings sit inside journal rows must not be rebuilt."""

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def test_declines_and_writes_nothing_when_entangled(self):
        # The Cerratti shape: a journal row holding bank money, no bank row.
        journal = TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="manual_journal",
            is_adjustment=True, debit=D("62500.00"), closing_balance=D("62500.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-01", amount="-500.00",
                       code="3565")
        txn.posted_to_tb = True
        txn.save(update_fields=["posted_to_tb"])

        result = _recalculate_bank_tb_lines(self.fy)

        self.assertEqual(result["status"], "entangled")
        self.assertIn("3565", result["codes"])
        journal.refresh_from_db()
        self.assertEqual(journal.debit, D("62500.00"))
        self.assertIsNone(bs_line(self.fy, "3565"),
                          "declining means writing nothing, not writing a duplicate")

    def test_runs_normally_once_the_book_is_repaired(self):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="manual_journal",
            is_adjustment=True, debit=D("62000.00"), closing_balance=D("62000.00"),
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="bank_statement",
            is_adjustment=False, debit=D("500.00"), closing_balance=D("500.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-01", amount="-500.00",
                       code="3565")
        txn.posted_to_tb = True
        txn.save(update_fields=["posted_to_tb"])

        result = _recalculate_bank_tb_lines(self.fy)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(bs_line(self.fy, "3565").debit, D("500.00"))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RebuildYearNotPostableGuardTests(TestCase):
    """entity_financial_years() excludes 'reopened' years (see core/txn_periods.py).

    On such a year, resolve_fy_for_txn(txn, fys) can never return fy, so
    _bank_tb_totals would report every transaction as vacated and the rebuild
    would zero every source='bank_statement' row for that year — worse than
    the Task 3 defect, which only reached contra rows. The rebuild must decline
    before touching any row.
    """

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _post(self, date_str, amount, code, gst="0"):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code=code,
                       gst=gst, tax_type="GST on Expenses" if gst != "0" else "")
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=(gst != "0"))
        return txn

    def test_a_reopened_year_is_left_untouched_not_zeroed(self):
        self._post("2025-08-01", "-1100.00", "0400", gst="100.00")
        _recalculate_bank_tb_lines(self.fy)
        line_before = bs_line(self.fy, "0400")
        self.assertEqual(line_before.debit, D("1000.00"))

        self.fy.status = "reopened"
        self.fy.save(update_fields=["status"])

        result = _recalculate_bank_tb_lines(self.fy)

        self.assertEqual(result["status"], "year_not_postable")
        line_after = bs_line(self.fy, "0400")
        self.assertEqual(line_after.debit, D("1000.00"),
                          "an unresolvable year must not be zeroed")
