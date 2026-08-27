"""The sweep must widen trust behaviour to unit trusts without altering trusts.

Guards Task 3 of the unit-trust plan: every "is this a trust?" behaviour branch
now tests membership in TRUST_LIKE_TYPES rather than equality with "trust", so
a unit trust gets the same behaviour a discretionary trust already had.

Two deliberate exceptions are pinned here rather than widened — see
test_section_100a_stays_discretionary_only and
test_eva_trust_planning_stays_dead_for_both_trust_kinds.
"""
from datetime import date

from django.test import TestCase, override_settings

from core.models import (
    Client, Entity, EntityOfficer, FinancialYear, TRUST_LIKE_TYPES,
)
from core.beneficiary_account_service import _profile_for
from core.eva_trust_planning import is_trust_planning_query
from core.risk_modules.section100a import Section100AModule


STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TrustSweepRegressionTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Trust Sweep Client")
        self.discretionary = Entity.objects.create(
            entity_name="Vincent Family Trust",
            entity_type="trust",
            client=self.client_obj,
        )
        self.unit = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust",
            entity_type="trust_unit",
            client=self.client_obj,
        )

    def test_trust_like_types_is_a_superset_of_trust(self):
        self.assertIn("trust", TRUST_LIKE_TYPES)
        self.assertIn("trust_unit", TRUST_LIKE_TYPES)

    def test_beneficiary_accounts_apply_to_both_trust_kinds(self):
        """core/beneficiary_account_service.py:_profile_for gates on entity
        type AND officer role together; a unit trust needs the same capital
        accounts a discretionary trust gets."""
        beneficiary = EntityOfficer.objects.create(
            entity=self.discretionary,
            full_name="Vincent Beneficiary",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
        )
        unit_holder = EntityOfficer.objects.create(
            entity=self.unit,
            full_name="Minli Unit Holder",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER,
        )
        self.assertIsNotNone(_profile_for(beneficiary))
        self.assertIsNotNone(_profile_for(unit_holder))

    def test_section_100a_stays_discretionary_only(self):
        """A fixed unit trust makes no discretionary distribution, so Section
        100A does not apply. This is a deliberate narrowing, not an omission."""
        fy_d = FinancialYear.objects.create(
            entity=self.discretionary,
            year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        fy_u = FinancialYear.objects.create(
            entity=self.unit,
            year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        self.assertTrue(Section100AModule(fy_d).should_run())
        self.assertFalse(Section100AModule(fy_u).should_run())
        self.assertEqual(Section100AModule.entity_types, ["trust"])

    def test_eva_trust_planning_stays_dead_for_both_trust_kinds(self):
        """Eva's chat trust-planning mode gates on three entity type values that
        have never existed in Entity.EntityType, so the feature has never run
        for any entity. "trust_unit" was deliberately removed from that gate:
        listing it would switch a never-run feature on for a live unit trust
        while the discretionary trusts still would not have it. Keeping it dead
        preserves the status quo for every entity. Not an oversight."""
        message = "who should we distribute the trust income to this year?"
        # The message itself must be one the gate would otherwise accept, or
        # this test would pass for the wrong reason.
        self.assertTrue(is_trust_planning_query(message, "trust_discretionary"))
        self.assertFalse(is_trust_planning_query(message, "trust_unit"))
        self.assertFalse(is_trust_planning_query(message, "trust"))
