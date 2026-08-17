"""An out-of-year transaction cannot reach the BAS, by any period selection.

The BAS selects candidate transactions by their JOB's financial year
(core/bas_utils.py:_confirmed_transactions) while posting selects by transaction
DATE. Those are different rules, and what stops them diverging is the date window
calculate_gst_for_period applies — period_start or fy.start_date through
period_end or fy.end_date, so even the full-year view is bounded by the year.

If this test ever fails, a July 2026 transaction sitting in an FY2026 job has
started appearing in FY2026's BAS while posting refuses to post it — a BAS that
disagrees with the ledger, which is the defect class the desync project closed.
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from core.bas_utils import calculate_gst_for_period
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job, make_txn,
)

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasDateWindowTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)          # 2025-07-01 .. 2026-06-30
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        # Inside the year: 1,100 gross of GST-bearing income.
        make_txn(self.job, date_str="2025-08-14", amount="1100.00",
                 code="0510", tax_type="GST on Income", gst="100.00")

    def _full_year_1a(self):
        return calculate_gst_for_period(self.fy)["bas_data"]["1A"]

    def _baseline_1a(self):
        """The in-year figure, asserted non-zero before anything is compared to it.

        Without this the whole module is vacuous: if the BAS calculation returned
        0 for every input — a broken fixture, a renamed tax type, a changed key —
        then `after == before` would hold trivially and both tests would pass
        while proving nothing about the date window.
        """
        before = self._full_year_1a()
        self.assertGreater(
            D(str(before)), 0,
            "the in-year transaction must actually reach the BAS, or comparing "
            "against this baseline proves nothing",
        )
        return before

    def test_the_full_year_view_excludes_a_transaction_dated_after_the_year(self):
        before = self._baseline_1a()
        # Same shape, dated one FY later, in the SAME FY2026 job.
        make_txn(self.job, date_str="2026-07-15", amount="2200.00",
                 code="0510", tax_type="GST on Income", gst="200.00")
        self.assertEqual(
            self._full_year_1a(), before,
            "a July 2026 transaction must not reach FY2026's BAS",
        )

    def test_the_full_year_view_excludes_a_transaction_dated_before_the_year(self):
        before = self._baseline_1a()
        make_txn(self.job, date_str="2024-09-10", amount="3300.00",
                 code="0510", tax_type="GST on Income", gst="300.00")
        self.assertEqual(self._full_year_1a(), before)
