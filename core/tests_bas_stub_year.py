"""An entity that starts trading mid-year has the same quarters as everyone else.

A business established on 1 April 2026 has a financial year of 1 Apr - 30 Jun
2026. That is an ordinary stub period: it ends 30 June like every other
Australian financial year, and its BAS quarters are the statutory ones -- Q4 is
April to June 2026, the same three months it would be for a full-year entity.

get_period_dates derived the quarters from ``fy.start_date.year``, treating it
as "the July this year opened in". True for a full year, wrong for a stub. A
year starting 2026-04-01 produced fy_start_year=2026 and therefore Q4 =
April-June 2027, a year late. Every window fell outside the year's own span, so
a real partnership with 403 allocated transactions and a reconciled ledger saw
an empty BAS on every quarter.

The financial year is identified by the June it ENDS in, which holds for a full
year and a stub alike. That is what these tests pin.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.bas_utils import (
    calculate_gst_for_period, get_all_period_dates, get_period_dates,
)
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job, make_txn,
)

D = Decimal

FULL_YEAR_QUARTERS = [
    (1, date(2025, 7, 1), date(2025, 9, 30)),
    (2, date(2025, 10, 1), date(2025, 12, 31)),
    (3, date(2026, 1, 1), date(2026, 3, 31)),
    (4, date(2026, 4, 1), date(2026, 6, 30)),
]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class StubYearPeriodTests(TestCase):
    def setUp(self):
        self.entity = make_entity()

    def _full(self):
        return make_fy(self.entity, label="FY2026",
                       start=date(2025, 7, 1), end=date(2026, 6, 30))

    def _stub(self):
        return make_fy(self.entity, label="FY2026-stub",
                       start=date(2026, 4, 1), end=date(2026, 6, 30))

    def test_a_stub_year_derives_the_same_quarters_as_a_full_year(self):
        self.assertEqual(get_all_period_dates(self._stub(), "quarterly"),
                         FULL_YEAR_QUARTERS)

    def test_a_full_year_is_unchanged(self):
        """The regression that matters: 21 of the 22 years on the live book."""
        self.assertEqual(get_all_period_dates(self._full(), "quarterly"),
                         FULL_YEAR_QUARTERS)

    def test_the_two_shapes_agree_with_each_other(self):
        self.assertEqual(get_all_period_dates(self._stub(), "quarterly"),
                         get_all_period_dates(self._full(), "quarterly"))

    def test_q4_of_a_stub_year_is_april_to_june_of_the_year_it_ends_in(self):
        self.assertEqual(get_period_dates(self._stub(), "quarterly", 4),
                         (date(2026, 4, 1), date(2026, 6, 30)))

    def test_monthly_periods_follow_the_same_rule(self):
        stub, full = self._stub(), self._full()
        self.assertEqual(get_all_period_dates(stub, "monthly"),
                         get_all_period_dates(full, "monthly"))
        # Period 1 is July, period 12 is June — of the year it ends in.
        self.assertEqual(get_period_dates(stub, "monthly", 1),
                         (date(2025, 7, 1), date(2025, 7, 31)))
        self.assertEqual(get_period_dates(stub, "monthly", 12),
                         (date(2026, 6, 1), date(2026, 6, 30)))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class StubYearBasTests(TestCase):
    """The behaviour the defect actually cost: a BAS that reads zero."""

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity, label="FY2026-stub",
                          start=date(2026, 4, 1), end=date(2026, 6, 30))
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        make_txn(self.job, date_str="2026-05-14", amount="11000.00",
                 code="0510", tax_type="GST on Income", gst="1000.00")

    def _bas_for_quarter(self, number):
        start, end = get_period_dates(self.fy, "quarterly", number)
        return calculate_gst_for_period(self.fy, start, end)["bas_data"]

    def test_the_june_quarter_contains_the_entitys_transactions(self):
        self.assertEqual(D(str(self._bas_for_quarter(4)["G1"])), D("11000.00"))
        self.assertEqual(D(str(self._bas_for_quarter(4)["1A"])), D("1000.00"))

    def test_the_quarters_before_it_started_trading_are_empty(self):
        """Correct, not a defect — the entity did not exist in those quarters."""
        for number in (1, 2, 3):
            self.assertEqual(D(str(self._bas_for_quarter(number)["G1"])), D("0"),
                             f"Q{number} should be empty")
