"""Tests for the 0151 data migration that reclassifies Minli as trust_unit.

Two layers, deliberately:

1. ``MinliMigrationTests`` calls the migration's pure functions directly
   (imported by path -- a migration filename is not a valid Python
   identifier, so it cannot be reached with a plain `import`) with the REAL
   current ``core.models.Entity``. Fast, and enough for the register logic
   and the safety guards.

2. ``MigrationForwardsTests`` and ``MigrationExecutorTests`` drive
   ``forwards(apps, schema_editor)`` itself -- the RunPython entry point --
   with HISTORICAL ``apps.get_model`` classes, one through a project state
   built by the migration loader and one through the real
   ``MigrationExecutor``, the same machinery ``manage.py migrate`` uses.

Layer 2 exists because layer 1, on its own, once shipped a deploy-breaking
bug behind a green suite. An earlier revision of this module claimed layer 1
"asserts the exact same logic the RunPython operation calls in production".
It did not: passing the real Entity class meant the historical model never
appeared, and the historical model was the whole problem. The migration
handed that historical instance to
``EntityOfficer.recalculate_unit_percentages``, whose ``cls.objects.filter(
entity=entity, ...)`` is rejected by Django's ``check_query_object_type``
with ``ValueError: Cannot query "Entity object (...)": Must be "Entity"
instance.`` -- deterministically, on exactly the one entity the migration
exists to convert, aborting the deploy's ``manage.py migrate``. Any test
that never builds a historical model class cannot see that class of bug, so
one that does now runs on every commit.

Primary risk under test: four discretionary trusts run in production
alongside the one unit trust (Minli). The migration must never reclassify
a trust that only has beneficiaries, and must never touch their register.
"""
import importlib
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from core.models import Entity, EntityOfficer, OfficerDistributionHistory

_migration = importlib.import_module("core.migrations.0151_minli_to_trust_unit")
reclassify_unit_trusts = _migration.reclassify_unit_trusts
unreclassify_unit_trusts = _migration.unreclassify_unit_trusts

BEFORE = ("core", "0150_entityofficer_units_held")
AFTER = ("core", "0151_minli_to_trust_unit")


def _make_holder(entity, name, pct, units=None, ceased=None, role="unit_holder"):
    return EntityOfficer.objects.create(
        entity=entity, full_name=name, role=role, roles=[role],
        distribution_percentage=pct, units_held=units, date_ceased=ceased,
    )


def _make_minli(name="Minli Enterprise Unit Trust"):
    entity = Entity.objects.create(entity_name=name, entity_type="trust")
    h1 = _make_holder(entity, "Double Water International Pty Ltd", Decimal("50.00"))
    h2 = _make_holder(entity, "Penman Property Nominees Pty Ltd", Decimal("50.00"))
    return entity, h1, h2


class MinliMigrationTests(TestCase):
    """Only entities with a whole unit register become unit trusts."""

    def test_a_trust_with_unit_holders_is_reclassified(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust")
        _make_holder(entity, "A", Decimal("100.00"))
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
        entity, h1, h2 = _make_minli()

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
        h1 = _make_holder(entity, "Holder One", Decimal("50.00"))
        h2 = _make_holder(entity, "Holder Two", Decimal("50.00"))

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
        entity, _, _ = _make_minli()

        reclassify_unit_trusts(Entity.objects.all())
        reclassify_unit_trusts(Entity.objects.all())  # must not raise or double-seed

        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")
        for holder in entity.officers.all():
            self.assertEqual(holder.units_held, 50)
            self.assertEqual(holder.distribution_percentage, Decimal("50.00"))


class MigrationSafetyGuardTests(TestCase):
    """The selector is "any 'trust' with any 'unit_holder'", which is far
    broader than the single entity this migration was written for. Each
    adversarial register below must be SKIPPED WHOLE -- entity_type not
    flipped, units_held not seeded, percentages not rewritten -- rather
    than half-converted into a register that no longer sums to 100%.
    """

    def test_unit_holder_alongside_a_beneficiary_is_skipped(self):
        """Without the guard: the holder's 50.00 is recomputed to 100.00
        (it is the only row with units), the beneficiary keeps 50.00, and
        the register reads 150%."""
        entity = Entity.objects.create(entity_name="Mixed", entity_type="trust")
        holder = _make_holder(entity, "Holder Half", Decimal("50.00"))
        ben = _make_holder(
            entity, "Beneficiary Half", Decimal("50.00"), role="beneficiary",
        )

        reclassify_unit_trusts(Entity.objects.all())

        entity.refresh_from_db()
        holder.refresh_from_db()
        ben.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")
        self.assertIsNone(holder.units_held)
        self.assertEqual(holder.distribution_percentage, Decimal("50.00"))
        self.assertEqual(ben.distribution_percentage, Decimal("50.00"))

    def test_an_already_ceased_holder_is_not_seeded_units(self):
        """A ceased holder is in no denominator (active_register_q), so
        seeding them units would put units on issue that
        Entity.total_units never counts. Here the active side sums to
        50%, not 100%, so the whole entity is skipped."""
        today = timezone.now().date()
        entity = Entity.objects.create(entity_name="Half Ceased", entity_type="trust")
        gone = _make_holder(
            entity, "Gone Holder", Decimal("50.00"),
            ceased=today - timedelta(days=1),
        )
        left = _make_holder(entity, "Left Holder", Decimal("50.00"))

        reclassify_unit_trusts(Entity.objects.all())

        entity.refresh_from_db()
        gone.refresh_from_db()
        left.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")
        self.assertIsNone(gone.units_held)
        self.assertIsNone(left.units_held)
        self.assertEqual(entity.total_units, 0)

    def test_every_holder_ceased_is_skipped(self):
        today = timezone.now().date()
        entity = Entity.objects.create(entity_name="All Ceased", entity_type="trust")
        _make_holder(
            entity, "Gone One", Decimal("50.00"), ceased=today - timedelta(days=2),
        )
        _make_holder(
            entity, "Gone Two", Decimal("50.00"), ceased=today - timedelta(days=1),
        )

        reclassify_unit_trusts(Entity.objects.all())

        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")
        self.assertFalse(
            entity.officers.filter(units_held__isnull=False).exists()
        )

    def test_a_future_ceased_holder_still_counts_as_active(self):
        """active_register_q's rule, not date_ceased__isnull: a holder
        ceasing next month is still on the register today, so this entity
        DOES convert."""
        today = timezone.now().date()
        entity = Entity.objects.create(entity_name="Future Ceased", entity_type="trust")
        soon = _make_holder(
            entity, "Leaving Soon", Decimal("50.00"),
            ceased=today + timedelta(days=30),
        )
        staying = _make_holder(entity, "Staying", Decimal("50.00"))

        reclassify_unit_trusts(Entity.objects.all())

        entity.refresh_from_db()
        soon.refresh_from_db()
        staying.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")
        self.assertEqual(soon.units_held, 50)
        self.assertEqual(staying.units_held, 50)

    def test_null_percentages_cannot_create_a_zero_unit_unit_trust(self):
        """NULL/0.00 percentages sum to 0, so they fail the 100% guard --
        and even if they did not, a register with zero units on issue
        must never be flipped to trust_unit: every downstream allocation
        (allocate_by_units) raises ValueError on one."""
        entity = Entity.objects.create(entity_name="No Percentages", entity_type="trust")
        h1 = _make_holder(entity, "No Pct One", None)
        h2 = _make_holder(entity, "No Pct Two", Decimal("0.00"))

        reclassify_unit_trusts(Entity.objects.all())

        entity.refresh_from_db()
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")
        self.assertIsNone(h1.units_held)
        self.assertIsNone(h2.units_held)
        self.assertEqual(entity.total_units, 0)

    def test_zero_units_already_stored_is_skipped_before_the_flip(self):
        """The second, independent fence: percentages sum to 100.00 but
        the units already stored come to zero. entity_type must not be
        written."""
        entity = Entity.objects.create(entity_name="Zero Units", entity_type="trust")
        _make_holder(entity, "Zero One", Decimal("50.00"), units=0)
        _make_holder(entity, "Zero Two", Decimal("50.00"), units=0)

        reclassify_unit_trusts(Entity.objects.all())

        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")

    def test_a_skipped_entity_does_not_stop_minli_converting(self):
        """Skipping prints a warning; it must not raise, and it must not
        abort the rest of the pass -- a deploy that converts Minli cannot
        be blocked by some other entity's odd register."""
        bad = Entity.objects.create(entity_name="Mixed", entity_type="trust")
        _make_holder(bad, "Holder Half", Decimal("50.00"))
        _make_holder(bad, "Ben Half", Decimal("50.00"), role="beneficiary")
        minli, h1, h2 = _make_minli()

        reclassify_unit_trusts(Entity.objects.all())

        bad.refresh_from_db()
        minli.refresh_from_db()
        self.assertEqual(bad.entity_type, "trust")
        self.assertEqual(minli.entity_type, "trust_unit")
        self.assertEqual(minli.total_units, 100)


class MigrationForwardsTests(TestCase):
    """``forwards()`` itself, with HISTORICAL apps.get_model classes.

    This is the layer that was missing. ``forwards`` is the only place the
    historical model ever appears, and it is what RunPython calls. The
    project state is built by the migration loader at the migration BEFORE
    0151, so ``state.apps`` hands out exactly the historical classes
    ``manage.py migrate`` would.
    """

    def _historical_apps(self):
        return MigrationLoader(connection).project_state(BEFORE).apps

    def test_forwards_converts_minli_with_historical_models(self):
        entity, h1, h2 = _make_minli()

        apps = self._historical_apps()
        # Sanity: this really is a different class from core.models.Entity --
        # otherwise the test is not exercising what it claims to.
        self.assertIsNot(apps.get_model("core", "Entity"), Entity)

        _migration.forwards(apps, connection.schema_editor(atomic=False))

        entity.refresh_from_db()
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")
        self.assertEqual((h1.units_held, h2.units_held), (50, 50))
        self.assertEqual(h1.distribution_percentage, Decimal("50.00"))
        self.assertEqual(h2.distribution_percentage, Decimal("50.00"))
        # The percentage recompute also wrote its audit trail -- proof it
        # actually ran, rather than being skipped or swallowed.
        self.assertEqual(
            OfficerDistributionHistory.objects.filter(officer=h1).count(), 1,
        )

    def test_backwards_reverses_with_historical_models(self):
        entity, h1, h2 = _make_minli()
        apps = self._historical_apps()
        editor = connection.schema_editor(atomic=False)

        _migration.forwards(apps, editor)
        _migration.backwards(apps, editor)

        entity.refresh_from_db()
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")
        self.assertIsNone(h1.units_held)
        self.assertIsNone(h2.units_held)

    def test_forwards_skips_a_partial_register_with_historical_models(self):
        entity = Entity.objects.create(entity_name="Mixed", entity_type="trust")
        _make_holder(entity, "Holder Half", Decimal("50.00"))
        _make_holder(entity, "Ben Half", Decimal("50.00"), role="beneficiary")

        _migration.forwards(
            self._historical_apps(), connection.schema_editor(atomic=False),
        )

        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")


class MigrationExecutorTests(TransactionTestCase):
    """End to end through ``MigrationExecutor`` -- what ``manage.py migrate``
    runs on deploy. Unapplies 0151 (data-only, so no schema changes), seeds
    a Minli-shaped register, then re-applies it.

    TransactionTestCase rather than TestCase: the executor manages its own
    transactions, so it cannot run inside the test's outer atomic block.
    """

    def test_migrate_forward_and_backward_over_real_data(self):
        executor = MigrationExecutor(connection)
        executor.migrate([BEFORE])

        entity, h1, h2 = _make_minli("Minli Enterprise Unit Trust")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([AFTER])

        entity.refresh_from_db()
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")
        self.assertEqual((h1.units_held, h2.units_held), (50, 50))
        self.assertEqual(h1.distribution_percentage, Decimal("50.00"))
        self.assertEqual(h2.distribution_percentage, Decimal("50.00"))

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([BEFORE])

        entity.refresh_from_db()
        h1.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")
        self.assertIsNone(h1.units_held)

        # Leave the test database as the rest of the suite expects it.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([AFTER])
