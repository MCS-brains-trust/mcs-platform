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
