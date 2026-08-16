"""The bank contra is grouped by the same year rule as the posting it mirrors.

_recalc_bank_contra scoped on job__financial_year while posting resolved the
year from the transaction's own date, so a statement spanning a year end posted
to one year and had its contra counted into the other.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.views import _post_txn_to_tb, _recalc_bank_contra

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BankContraYearScopeTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy25 = make_fy(self.entity, "FY2025", date(2024, 7, 1), date(2025, 6, 30))
        self.fy26 = make_fy(self.entity, "FY2026", date(2025, 7, 1), date(2026, 6, 30))
        make_bank_mapping(self.entity)
        # One job, attached to FY2025, holding a statement that crosses 30 June.
        self.job = make_job(self.entity, self.fy25)

    def _post(self, date_str, amount):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code="0400")
        from core.txn_periods import resolve_fy_for_txn
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=False)
        return txn

    def test_a_year_spanning_statement_splits_its_contra(self):
        self._post("2025-06-20", "-100.00")   # FY2025
        self._post("2025-07-03", "-250.00")   # FY2026

        _recalc_bank_contra(self.fy25)
        _recalc_bank_contra(self.fy26)

        self.assertEqual(bs_line(self.fy25, "1100").credit, D("100.00"))
        self.assertEqual(bs_line(self.fy26, "1100").credit, D("250.00"),
                         "the July transaction belongs to FY2026, not the job's year")

    def test_a_vacated_bank_row_is_zeroed_not_left_standing(self):
        txn = self._post("2025-06-20", "-100.00")
        _recalc_bank_contra(self.fy25)
        self.assertEqual(bs_line(self.fy25, "1100").credit, D("100.00"))

        # The transaction is re-dated into the next year — the FY2025 contra
        # must shed it rather than keep it forever.
        txn.date = "2025-07-03"
        txn.save(update_fields=["date"])

        _recalc_bank_contra(self.fy25)

        self.assertEqual(bs_line(self.fy25, "1100").credit, D("0.00"))
        self.assertEqual(bs_line(self.fy25, "1100").closing_balance, D("0.00"))

    def test_receipts_debit_and_payments_credit(self):
        self._post("2025-08-01", "440.00")
        self._post("2025-08-02", "-110.00")

        _recalc_bank_contra(self.fy26)

        line = bs_line(self.fy26, "1100")
        self.assertEqual(line.debit, D("440.00"))
        self.assertEqual(line.credit, D("110.00"))

    def test_calling_it_twice_changes_nothing(self):
        self._post("2025-08-01", "-330.00")
        _recalc_bank_contra(self.fy26)
        first = bs_line(self.fy26, "1100").credit
        _recalc_bank_contra(self.fy26)
        self.assertEqual(bs_line(self.fy26, "1100").credit, first)
