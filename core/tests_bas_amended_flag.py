"""A correction inside a lodged period is allowed, and flagged."""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from core.bas_utils import ensure_bas_periods
from core.models import BASPeriod
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job,
    make_txn,
)
from core.txn_periods import flag_period_amended, resolve_bas_period_for_txn

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class AmendedFlagTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        ensure_bas_periods(self.fy, "quarterly")
        self.q1 = BASPeriod.objects.get(
            financial_year=self.fy, period_type="quarterly", period_number=1)
        User = get_user_model()
        self.user = User.objects.create_user(
            username="amender", password="pw", email="a@example.com")

    def _txn(self, date_str):
        return make_txn(self.job, date_str=date_str, amount="-110.00", code="0400")

    def test_resolves_a_transaction_to_its_period(self):
        txn = self._txn("2025-08-14")   # Q1 is Jul-Sep
        self.assertEqual(resolve_bas_period_for_txn(txn), self.q1)

    def test_flag_sets_inside_a_lodged_period(self):
        self.q1.status = "lodged"
        self.q1.lodged_at = timezone.now()
        self.q1.save()

        period = flag_period_amended(self._txn("2025-08-14"), self.user)

        self.assertEqual(period, self.q1)
        self.q1.refresh_from_db()
        self.assertTrue(self.q1.amended_since_lodgement)
        self.assertEqual(self.q1.amended_by, self.user)
        self.assertIsNotNone(self.q1.amended_at)

    def test_flag_stays_clear_outside_a_lodged_period(self):
        self.assertIsNone(flag_period_amended(self._txn("2025-08-14"), self.user))
        self.q1.refresh_from_db()
        self.assertFalse(self.q1.amended_since_lodgement)

    def test_the_lodged_snapshot_is_never_written(self):
        self.q1.status = "lodged"
        self.q1.snapshot_1a = D("1234.00")
        self.q1.save()

        flag_period_amended(self._txn("2025-08-14"), self.user)

        self.q1.refresh_from_db()
        self.assertEqual(self.q1.snapshot_1a, D("1234.00"))

    def test_an_unparseable_date_flags_nothing(self):
        self.q1.status = "lodged"
        self.q1.save()
        self.assertIsNone(flag_period_amended(self._txn("n/a"), self.user))

    def test_no_period_row_means_nothing_to_flag(self):
        BASPeriod.objects.all().delete()
        self.assertIsNone(flag_period_amended(self._txn("2025-08-14"), self.user))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class PeriodTypeResolutionTests(TestCase):
    """A year can hold quarterly AND monthly rows covering the same dates.

    Veronica Cerratti's live FY2026 holds 16 BASPeriod rows — 12 monthly and 4
    quarterly — while her bas_frequency is quarterly. So the overlap is real,
    not hypothetical, and resolve_bas_period_for_txn has to choose.

    Why the failure is asymmetric. Meta.ordering is ["period_number"], and for
    any given date the quarterly number is always <= the monthly number
    (Q = ceil(M/3)). So an unfiltered .first() picks the QUARTERLY row almost
    always — which is accidentally right for a quarterly entity and always
    wrong for a monthly one. The exception is July, where Q1 and Jul are both
    period_number 1 and the tie is broken by nothing at all.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        # Both period types, covering the same dates.
        ensure_bas_periods(self.fy, "quarterly")
        ensure_bas_periods(self.fy, "monthly")
        User = get_user_model()
        self.user = User.objects.create_user(
            username="periodtyper", password="pw", email="p@example.com")

    def _txn(self, date_str):
        return make_txn(self.job, date_str=date_str, amount="-110.00", code="0400")

    def _period(self, period_type, number):
        return BASPeriod.objects.get(
            financial_year=self.fy, period_type=period_type, period_number=number)

    def _set_frequency(self, freq):
        self.entity.bas_frequency = freq
        self.entity.save(update_fields=["bas_frequency"])

    def test_a_quarterly_entity_resolves_to_its_quarterly_period(self):
        self._set_frequency("quarterly")
        period = resolve_bas_period_for_txn(self._txn("2025-08-14"))
        self.assertEqual(period.period_type, "quarterly")
        self.assertEqual(period, self._period("quarterly", 1))

    def test_a_monthly_entity_resolves_to_its_monthly_period(self):
        """Deterministically wrong before the fix: Q1 is period_number 1, Aug is 2."""
        self._set_frequency("monthly")
        period = resolve_bas_period_for_txn(self._txn("2025-08-14"))
        self.assertEqual(period.period_type, "monthly")
        self.assertEqual(period, self._period("monthly", 2))

    def test_a_july_date_is_not_decided_by_a_period_number_tie(self):
        """July is the one date where Q1 and Jul share period_number 1."""
        self._set_frequency("monthly")
        period = resolve_bas_period_for_txn(self._txn("2025-07-15"))
        self.assertEqual(period.period_type, "monthly")
        self.assertEqual(period, self._period("monthly", 1))

    def test_the_flag_lands_on_the_entitys_own_period_type(self):
        """The badge only renders periods of the entity's frequency.

        Flagging the overlapping row means the flag is set and the badge never
        appears — a silent no-op of the whole feature.
        """
        self._set_frequency("monthly")
        aug = self._period("monthly", 2)
        q1 = self._period("quarterly", 1)
        for p in (aug, q1):
            p.status = "lodged"
            p.save(update_fields=["status"])

        flagged = flag_period_amended(self._txn("2025-08-14"), self.user)

        self.assertEqual(flagged, aug)
        aug.refresh_from_db()
        q1.refresh_from_db()
        self.assertTrue(aug.amended_since_lodgement)
        self.assertFalse(q1.amended_since_lodgement)
