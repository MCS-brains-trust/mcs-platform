"""Tests for the 0151 data migration that reclassifies Minli as trust_unit.

Exercises the migration's pure functions directly (imported from the
migration module by path -- a migration filename is not a valid Python
identifier, so it cannot be reached with a plain `import` statement) rather
than driving Django's MigrationExecutor. This asserts the exact same logic
the RunPython operation calls in production, without standing up a
separate migration-state test database.

Primary risk under test: four discretionary trusts run in production
alongside the one unit trust (Minli). The migration must never reclassify
a trust that only has beneficiaries, and must never touch their register.
"""
import importlib
from decimal import Decimal

from django.test import TestCase

from core.models import Entity, EntityOfficer

_migration = importlib.import_module("core.migrations.0151_minli_to_trust_unit")
reclassify_unit_trusts = _migration.reclassify_unit_trusts
unreclassify_unit_trusts = _migration.unreclassify_unit_trusts


class MinliMigrationTests(TestCase):
    """Only entities with unit holders become unit trusts."""

    def test_a_trust_with_unit_holders_is_reclassified(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust")
        EntityOfficer.objects.create(
            entity=entity, full_name="A", role="unit_holder", roles=["unit_holder"],
            distribution_percentage=Decimal("50.00"),
        )
        reclassify_unit_trusts(Entity.objects.all())
        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")

    def test_a_trust_with_beneficiaries_is_left_alone(self):
        entity = Entity.objects.create(entity_name="Vincent", entity_type="trust")
        EntityOfficer.objects.create(
            entity=entity, full_name="B", role="beneficiary", roles=["beneficiary"],
        )
        reclassify_unit_trusts(Entity.objects.all())
        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")

    def test_discretionary_trust_with_beneficiaries_is_untouched(self):
        """PRIMARY RISK: four discretionary trusts run in production
        alongside the one unit trust. Prove the migration cannot touch
        one -- entity_type, units_held and distribution_percentage must
        all be exactly as they were."""
        entity = Entity.objects.create(
            entity_name="Example Discretionary Trust", entity_type="trust",
        )
        b1 = EntityOfficer.objects.create(
            entity=entity, full_name="Beneficiary One", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("60.00"),
        )
        b2 = EntityOfficer.objects.create(
            entity=entity, full_name="Beneficiary Two", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("40.00"),
        )

        reclassify_unit_trusts(Entity.objects.all())

        entity.refresh_from_db()
        b1.refresh_from_db()
        b2.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")
        self.assertIsNone(b1.units_held)
        self.assertIsNone(b2.units_held)
        self.assertEqual(b1.distribution_percentage, Decimal("60.00"))
        self.assertEqual(b2.distribution_percentage, Decimal("40.00"))

    def test_units_seeded_and_percentages_still_read_50_50(self):
        """Ruling 1 + Ruling 2: units_held is reconstructed from the
        stored percentage, and recalculate_unit_percentages is called
        afterwards so the derived percentage still reads 50.00/50.00 --
        now genuinely DERIVED from a 50/100 unit split, not just typed."""
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust")
        h1 = EntityOfficer.objects.create(
            entity=entity, full_name="Double Water International Pty Ltd",
            role="unit_holder", roles=["unit_holder"],
            distribution_percentage=Decimal("50.00"),
        )
        h2 = EntityOfficer.objects.create(
            entity=entity, full_name="Penman Property Nominees Pty Ltd",
            role="unit_holder", roles=["unit_holder"],
            distribution_percentage=Decimal("50.00"),
        )

        reclassify_unit_trusts(Entity.objects.all())

        entity.refresh_from_db()
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")
        self.assertEqual(h1.units_held, 50)
        self.assertEqual(h2.units_held, 50)
        self.assertEqual(entity.total_units, 100)
        self.assertEqual(h1.distribution_percentage, Decimal("50.00"))
        self.assertEqual(h2.distribution_percentage, Decimal("50.00"))

    def test_reverse_restores_original_state(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust")
        h1 = EntityOfficer.objects.create(
            entity=entity, full_name="Holder One", role="unit_holder",
            roles=["unit_holder"], distribution_percentage=Decimal("50.00"),
        )
        h2 = EntityOfficer.objects.create(
            entity=entity, full_name="Holder Two", role="unit_holder",
            roles=["unit_holder"], distribution_percentage=Decimal("50.00"),
        )

        reclassify_unit_trusts(Entity.objects.all())
        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")

        unreclassify_unit_trusts(Entity.objects.all())
        entity.refresh_from_db()
        h1.refresh_from_db()
        h2.refresh_from_db()

        self.assertEqual(entity.entity_type, "trust")
        self.assertIsNone(h1.units_held)
        self.assertIsNone(h2.units_held)
        # distribution_percentage is left as-is by the reverse -- it
        # already reads the same 50.00/50.00 it held before the forward
        # migration ran.
        self.assertEqual(h1.distribution_percentage, Decimal("50.00"))
        self.assertEqual(h2.distribution_percentage, Decimal("50.00"))

    def test_idempotent_when_run_twice(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust")
        EntityOfficer.objects.create(
            entity=entity, full_name="Holder One", role="unit_holder",
            roles=["unit_holder"], distribution_percentage=Decimal("50.00"),
        )
        EntityOfficer.objects.create(
            entity=entity, full_name="Holder Two", role="unit_holder",
            roles=["unit_holder"], distribution_percentage=Decimal("50.00"),
        )

        reclassify_unit_trusts(Entity.objects.all())
        reclassify_unit_trusts(Entity.objects.all())  # must not raise or double-seed

        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")
        for holder in entity.officers.all():
            self.assertEqual(holder.units_held, 50)
            self.assertEqual(holder.distribution_percentage, Decimal("50.00"))
