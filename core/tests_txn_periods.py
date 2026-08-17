"""The one rule for which financial year a transaction belongs to.

Three functions used to answer this differently — see the spec's "Three
functions disagree about which year a transaction belongs to". These tests pin
the rule, including its fallback, because the rebuild reproduces posting only if
it reproduces the fallback too.
"""
from datetime import date

from django.test import TestCase, override_settings

from core.models import FinancialYear
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_entity, make_fy, make_job, make_txn,
)
from core.txn_periods import (
    entity_financial_years, parse_txn_date, resolve_fy_for_txn,
)


class ParseTxnDateTests(TestCase):
    def test_parses_every_format_the_parsers_emit(self):
        self.assertEqual(parse_txn_date("2025-08-14"), date(2025, 8, 14))
        self.assertEqual(parse_txn_date("14/08/2025"), date(2025, 8, 14))
        self.assertEqual(parse_txn_date("14-08-2025"), date(2025, 8, 14))
        self.assertEqual(parse_txn_date("14 Aug 2025"), date(2025, 8, 14))

    def test_tolerates_surrounding_whitespace(self):
        self.assertEqual(parse_txn_date("  2025-08-14 "), date(2025, 8, 14))

    def test_returns_none_rather_than_raising(self):
        for raw in ("", None, "   ", "not a date", "31/02/2025"):
            self.assertIsNone(parse_txn_date(raw), raw)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class ResolveFyForTxnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.entity = make_entity()
        cls.fy25 = make_fy(cls.entity, "FY2025", date(2024, 7, 1), date(2025, 6, 30))
        cls.fy26 = make_fy(cls.entity, "FY2026", date(2025, 7, 1), date(2026, 6, 30))
        # The job is attached to FY2025 deliberately: a statement spanning the
        # year end is exactly the case job__financial_year got wrong.
        cls.job = make_job(cls.entity, cls.fy25)

    def test_resolves_to_the_year_covering_the_date(self):
        txn = make_txn(self.job, date_str="2025-05-20", amount="-110.00", code="0400")
        self.assertEqual(resolve_fy_for_txn(txn), self.fy25)

    def test_ignores_the_jobs_own_year(self):
        # Job is FY2025; the transaction is July, so it belongs to FY2026.
        txn = make_txn(self.job, date_str="2025-07-03", amount="-110.00", code="0400")
        self.assertEqual(resolve_fy_for_txn(txn), self.fy26)

    def test_unparseable_date_falls_back_to_the_most_recent_year(self):
        txn = make_txn(self.job, date_str="n/a", amount="-110.00", code="0400")
        self.assertEqual(resolve_fy_for_txn(txn), self.fy26)

    def test_date_outside_every_year_falls_back_to_the_most_recent_year(self):
        txn = make_txn(self.job, date_str="1999-01-01", amount="-110.00", code="0400")
        self.assertEqual(resolve_fy_for_txn(txn), self.fy26)

    def test_returns_none_when_the_entity_has_no_years(self):
        # fy.delete() would cascade (ReviewJob.financial_year is CASCADE) and
        # take the job and this transaction down with it. Instead, archive
        # every financial year so none remains postable — POSTABLE_FY_STATUSES
        # is draft/in_review/finished — and exercise the real
        # `if not fys: return None` branch without deleting the row under test.
        other = make_entity("Yearless Pty Ltd")
        fy = make_fy(other, "FY2026")
        job = make_job(other, fy)
        txn = make_txn(job, date_str="2025-08-01", amount="-110.00", code="0400")
        FinancialYear.objects.filter(entity=other).update(status="finalised")
        txn.refresh_from_db()
        self.assertIsNone(resolve_fy_for_txn(txn))

    def test_prefetched_years_give_the_same_answer(self):
        txn = make_txn(self.job, date_str="2025-07-03", amount="-110.00", code="0400")
        fys = entity_financial_years(self.entity)
        with self.assertNumQueries(0):
            self.assertEqual(resolve_fy_for_txn(txn, fys=fys), self.fy26)
