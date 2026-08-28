"""FIX 1 (round-1 review of Task 8): the save_allocations POST branch in
core/views_upgrades.trust_distribution is the real distribution save path
used by the UI. Before this fix it called
``BeneficiaryAllocation.calculate_allocation`` for every entity type,
including unit trusts -- so core.views_trust.allocate_unit_trust_distribution
(Task 8) was never reached in production, and Task 7's exact-sum guarantee
never took effect for a unit trust saved through the UI.

This module proves the fork: a unit trust distribution saved through that
view routes to allocate_unit_trust_distribution and never touches
calculate_allocation; a discretionary trust's save is byte-for-byte
unchanged and still goes through calculate_allocation.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from core.models import (
    BeneficiaryAllocation, Entity, EntityOfficer, FinancialYear, TrustDistribution,
)
from core.views_upgrades import trust_distribution

User = get_user_model()


def _sqlite_json_contains_as_sql(self, compiler, connection):
    """Constant-false stand-in for JSONField ``contains`` under SQLite.

    See core/tests_trust_4199_carried_forward.py's identical helper: the
    view's own `beneficiaries` queryset ORs in a ``roles__contains``
    lookup that Django's SQLite backend cannot compile at all (regardless
    of whether any row would actually match it).
    """
    return "0", ()


def _prepare_request(request, user):
    """Same pattern as core/tests_unit_register.py: a view that calls
    django.contrib.messages needs a request with a real session and
    message storage attached, or messages.success()/error() raise
    MessageFailure."""
    request.user = user
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


class TrustDistributionSaveAllocationsWiringTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="wiring-admin", password="x", role=User.Role.ADMIN,
        )

    def _post(self, fy):
        request = self.factory.post(
            f"/years/{fy.pk}/distribution/", data={"action": "save_allocations"},
        )
        _prepare_request(request, self.user)
        return trust_distribution(request, pk=fy.pk)

    def test_unit_trust_save_routes_to_allocate_unit_trust_distribution(self):
        entity = Entity.objects.create(
            entity_name="Wired Unit Trust", entity_type="trust_unit",
        )
        fy = FinancialYear.objects.create(
            entity=entity, start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        for name, units in [("A", 75), ("B", 25)]:
            EntityOfficer.objects.create(
                entity=entity, full_name=name,
                role="unit_holder", roles=["unit_holder"], units_held=units,
            )
        EntityOfficer.recalculate_unit_percentages(entity)
        dist = TrustDistribution.objects.create(
            financial_year=fy,
            distributable_income=Decimal("100000.00"),
            capital_gains=Decimal("20000.00"),
        )

        with patch.object(
            BeneficiaryAllocation, "calculate_allocation",
        ) as mocked_calculate_allocation:
            response = self._post(fy)
            mocked_calculate_allocation.assert_not_called()

        self.assertEqual(response.status_code, 302)
        rows = BeneficiaryAllocation.objects.filter(distribution=dist)
        self.assertEqual(rows.count(), 2)
        a = rows.get(beneficiary__full_name="A")
        self.assertEqual(a.percentage, Decimal("75.00"))
        self.assertEqual(a.total_distribution, Decimal("75000.00"))
        self.assertEqual(a.allocated_capital_gains, Decimal("15000.00"))
        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("100000.00"),
        )
        dist.refresh_from_db()
        self.assertTrue(dist.is_fully_allocated)

    def test_discretionary_trust_save_still_uses_calculate_allocation(self):
        entity = Entity.objects.create(
            entity_name="Wired Discretionary Trust", entity_type="trust",
        )
        fy = FinancialYear.objects.create(
            entity=entity, start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        officer = EntityOfficer.objects.create(
            entity=entity, full_name="Sole Beneficiary",
            role="beneficiary", roles=["beneficiary"],
        )
        dist = TrustDistribution.objects.create(
            financial_year=fy,
            distributable_income=Decimal("100000.00"),
            capital_gains=Decimal("20000.00"),
        )

        # The view's own `beneficiaries` queryset ORs in a roles__contains
        # lookup that Django's SQLite backend cannot compile (see
        # core/tests_trust_4199_carried_forward.py's identical
        # workaround); this officer already matches via role="beneficiary"
        # alone, so a constant-false stand-in for that half is safe here.
        with patch(
            "django.db.models.fields.json.DataContains.as_sql",
            _sqlite_json_contains_as_sql,
        ), patch(
            "core.views_trust.allocate_unit_trust_distribution",
        ) as mocked_unit_alloc, patch.object(
            BeneficiaryAllocation, "calculate_allocation",
            autospec=True,
        ) as mocked_calculate_allocation:
            request = self.factory.post(
                f"/years/{fy.pk}/distribution/",
                data={"action": "save_allocations", f"pct_{officer.pk}": "100"},
            )
            _prepare_request(request, self.user)
            response = trust_distribution(request, pk=fy.pk)
            mocked_calculate_allocation.assert_called_once()
            mocked_unit_alloc.assert_not_called()

        self.assertEqual(response.status_code, 302)
        row = BeneficiaryAllocation.objects.get(distribution=dist, beneficiary=officer)
        self.assertEqual(row.percentage, Decimal("100"))
