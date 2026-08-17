"""The one rule for which financial year a transaction belongs to.

Three functions used to answer this differently — see the spec's "Three
functions disagree about which year a transaction belongs to". These tests pin
the rule, including its fallback, because the rebuild reproduces posting only if
it reproduces the fallback too.

That fallback is now unparseable-dates-only: a parseable date outside every
postable year resolves to None and posts nowhere. See StrictYearResolutionTests.
"""
from datetime import date

from django.test import TestCase, override_settings

from core.models import FinancialYear
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job,
    make_txn,
)
from core.txn_periods import (
    entity_financial_years, parse_txn_date, resolve_fy_for_txn,
    unpostable_reason,
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

    def test_date_outside_every_year_resolves_to_nothing(self):
        # Strict resolution: a parseable date outside every postable year is
        # not posted anywhere, rather than falling back to the most recent
        # year. See StrictYearResolutionTests for the full behaviour.
        txn = make_txn(self.job, date_str="1999-01-01", amount="-110.00", code="0400")
        self.assertIsNone(resolve_fy_for_txn(txn))

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


@override_settings(STORAGES=STORAGES_OVERRIDE)
class StrictYearResolutionTests(TestCase):
    """A transaction posts to the year its date falls in, or not at all.

    resolve_fy_for_txn used to fall back to the most recent postable year when
    nothing covered the date, so a 15 July 2026 transaction posted into FY2026 —
    overstating the year and corrupting its BAS. The fallback survives for dates
    that cannot be parsed, deliberately: there is nothing to reason from.
    """

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)        # FY2026: 2025-07-01 .. 2026-06-30
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _txn(self, date_str):
        return make_txn(self.job, date_str=date_str, amount="-110.00", code="0400")

    def test_a_date_inside_an_open_year_resolves_to_it(self):
        self.assertEqual(resolve_fy_for_txn(self._txn("2025-08-14")), self.fy)

    def test_a_date_beyond_every_year_resolves_to_nothing(self):
        """The reported defect: 15 July 2026 with no FY2027 in existence."""
        self.assertIsNone(resolve_fy_for_txn(self._txn("2026-07-15")))

    def test_a_date_inside_a_finalised_year_resolves_to_nothing(self):
        """A finalised year is not a posting target, so this must not fall back."""
        old = make_fy(self.entity, label="FY2023",
                      start=date(2022, 7, 1), end=date(2023, 6, 30))
        old.status = "finalised"
        old.save(update_fields=["status"])
        self.assertIsNone(resolve_fy_for_txn(self._txn("2023-01-15")))

    def test_a_date_inside_a_reopened_year_resolves_to_nothing(self):
        """'reopened' is also outside POSTABLE_FY_STATUSES — same treatment."""
        old = make_fy(self.entity, label="FY2024",
                      start=date(2023, 7, 1), end=date(2024, 6, 30))
        old.status = "reopened"
        old.save(update_fields=["status"])
        self.assertIsNone(resolve_fy_for_txn(self._txn("2024-01-15")))

    def test_an_unparseable_date_still_falls_back(self):
        """Unchanged on purpose: with unreadable text there is nothing to use."""
        self.assertEqual(resolve_fy_for_txn(self._txn("n/a")), self.fy)

    def test_the_fallback_picks_the_most_recent_year_as_before(self):
        later = make_fy(self.entity, label="FY2027",
                        start=date(2026, 7, 1), end=date(2027, 6, 30))
        later.status = "draft"
        later.save(update_fields=["status"])
        self.assertEqual(resolve_fy_for_txn(self._txn("garbage")), later)

    def test_a_date_beyond_every_year_resolves_once_that_year_exists(self):
        """The workflow this exists to support: allocate now, post when the year opens."""
        txn = self._txn("2026-07-15")
        self.assertIsNone(resolve_fy_for_txn(txn))
        later = make_fy(self.entity, label="FY2027",
                        start=date(2026, 7, 1), end=date(2027, 6, 30))
        later.status = "draft"
        later.save(update_fields=["status"])
        self.assertEqual(resolve_fy_for_txn(txn), later)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class UnpostableReasonTests(TestCase):
    """The reason is derived from the date and the entity's years, never stored.

    is_confirmed=True with posted_to_tb=False already represents "confirmed but
    not posted", so no model field is added. Only the explanation is new.
    """

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _txn(self, date_str):
        return make_txn(self.job, date_str=date_str, amount="-110.00", code="0400")

    def test_a_postable_transaction_has_no_reason(self):
        self.assertIsNone(unpostable_reason(self._txn("2025-08-14")))

    def test_no_year_covers_the_date(self):
        reason = unpostable_reason(self._txn("2026-07-15"))
        self.assertIn("No financial year", reason)
        self.assertIn("15 Jul 2026", reason)

    def test_the_covering_year_is_finalised(self):
        old = make_fy(self.entity, label="FY2023",
                      start=date(2022, 7, 1), end=date(2023, 6, 30))
        old.status = "finalised"
        old.save(update_fields=["status"])
        reason = unpostable_reason(self._txn("2023-01-15"))
        self.assertIn("FY2023", reason)
        self.assertIn("finalised", reason)

    def test_the_covering_year_is_reopened(self):
        """The message names the actual status, so 'reopened' is not mislabelled."""
        old = make_fy(self.entity, label="FY2024",
                      start=date(2023, 7, 1), end=date(2024, 6, 30))
        old.status = "reopened"
        old.save(update_fields=["status"])
        reason = unpostable_reason(self._txn("2024-01-15"))
        self.assertIn("FY2024", reason)
        self.assertIn("reopened", reason)

    def test_an_unparseable_date_has_no_reason_because_it_still_posts(self):
        self.assertIsNone(unpostable_reason(self._txn("n/a")))
