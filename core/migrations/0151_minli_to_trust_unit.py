"""Data migration: reclassify Minli Enterprise Unit Trust as trust_unit.

Minli Enterprise Unit Trust is currently stored as entity_type='trust' with
two `unit_holder` officers (Double Water International Pty Ltd and Penman
Property Nominees Pty Ltd), each carrying a typed distribution_percentage
of 50.00 and no units_held. Verified against production before writing this
migration: Minli is the ONLY trust on the platform with unit_holder
officers -- the platform's four discretionary trusts hold only
beneficiaries and are untouched by the query below (see
tests_unit_trust_migration.py for a regression test proving this).

UNIT COUNT PROVENANCE -- READ BEFORE CORRECTING: there is no trust deed on
file for Minli (no GoverningDocument, no LegalDocument), so the real unit
counts on issue are unrecoverable from this system. This migration seeds
50 units to each holder purely because that reproduces the existing
50.00% / 50.00% split exactly (50 / 100 = 50.00%). It is a RECONSTRUCTION
from the stored percentages, not a transcription of the deed. If the deed
later surfaces and says 100 units each, or 1,000 units each, correct
EntityOfficer.units_held by hand for the two holders -- the PERCENTAGE
already recorded is correct either way; only the unit COUNT is a guess.

Chart of accounts: verified by reading the seeding path rather than by
querying production (this migration must not touch production data before
review). EntityChartOfAccount has no entity_type field of its own -- it is
a plain FK to Entity, populated once by handle_trust_entity_created
(core/signals.py), which only fires on Entity creation (`created=True`),
gated on membership in TRUST_LIKE_TYPES = ("trust", "trust_unit"). Minli
was created as entity_type='trust' (itself in TRUST_LIKE_TYPES), so it
already received a seeded chart at creation time; nothing in this
migration, or in EntityChartOfAccount's own schema, re-filters or deletes
those rows when entity_type later changes to 'trust_unit'. Confirm
`EntityChartOfAccount.objects.filter(entity=<Minli>).exists()` is True as
part of the pre-deploy read-only check (see task-12 brief, Step 5)
before merging, as a belt-and-braces check on top of this code reading.

Percentage recompute: EntityOfficer.recalculate_unit_percentages(entity)
(Task 5) is the SOLE writer of distribution_percentage and its
OfficerDistributionHistory audit trail for unit holders -- individual
save() calls no longer derive anything. This migration imports the REAL
current EntityOfficer class (core.models.EntityOfficer) to call that
classmethod, rather than replicating its largest-remainder allocation and
audit-history logic here. This is a deliberate one-off exception to the
"always use the historical model via apps.get_model" migration rule:
recalculate_unit_percentages is business logic, not a schema shape, and
duplicating its rounding/history rules in a migration would itself be a
drift risk. The accepted risk is that if a later migration changes
EntityOfficer's schema in a way recalculate_unit_percentages depends on,
importing the CURRENT class here means this migration's behaviour could
change retroactively if ever replayed from scratch on an old database --
it is not pinned to the schema shape of 0150. Bringing the migration chain
fully up to date (as `migrate` always does before running unapplied
migrations) makes this safe in practice, and it only executes once
against real data, so the tradeoff is accepted rather than papered over.
The `entity`/`officer` instances actually saved by this recompute are
always fetched through `cls.objects` (the current model's own manager),
so they are fully-featured Django instances even though the entity object
passed in comes from the historical model -- Django's ORM only needs its
`.pk` to build the `entity=entity` filter.

Both reclassify_unit_trusts() and unreclassify_unit_trusts() are written
to be idempotent: each only acts on entities matching its *starting*
entity_type ('trust' forward, 'trust_unit' in reverse), so re-running
either after it has already applied is a no-op.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations


def reclassify_unit_trusts(entities):
    """Reclassify every 'trust' entity with unit_holder officers to 'trust_unit'.

    Accepts any Entity-like queryset/manager -- the historical model from
    apps.get_model (as used by the RunPython forwards() below) or the real
    current model (as used directly in tests) both work, since only
    `.filter()`, the `officers` reverse relation, and `.save()` are used.

    A trust with only beneficiaries (every discretionary trust on the
    platform) is left completely alone: the `unit_holder` filter never
    matches them, so entity_type, distribution_percentage and units_held
    are untouched.
    """
    for entity in entities.filter(entity_type="trust"):
        holders = list(entity.officers.filter(role="unit_holder"))
        if not holders:
            continue  # discretionary trust -- beneficiaries only, skip

        if entity.entity_type != "trust_unit":
            entity.entity_type = "trust_unit"
            entity.save(update_fields=["entity_type"])

        seeded_any = False
        for holder in holders:
            if holder.units_held is not None:
                continue  # already seeded (defensive against a partial re-run)
            pct = holder.distribution_percentage or Decimal("0")
            # See module docstring: RECONSTRUCTED from the stored
            # percentage, not the (nonexistent) deed.
            holder.units_held = int(
                pct.to_integral_value(rounding=ROUND_HALF_UP)
            )
            holder.save(update_fields=["units_held"])
            seeded_any = True

        if seeded_any:
            # Ruling 2 / Task 5: this MUST be the last write against this
            # entity's register, and MUST run after units_held is seeded --
            # it is the sole writer of distribution_percentage now. Import
            # the real, current model (see module docstring for why).
            from core.models import EntityOfficer as CurrentEntityOfficer
            CurrentEntityOfficer.recalculate_unit_percentages(entity)


def unreclassify_unit_trusts(entities):
    """Reverse of reclassify_unit_trusts(): trust_unit -> trust, units cleared.

    distribution_percentage is deliberately left as-is: recalculate_unit_
    percentages only ever re-derived the SAME 50.00/50.00 split that was
    already stored before the forward migration ran, so there is nothing
    to restore. Nulling units_held is enough to put the register back to
    "no units on issue", matching the pre-migration state.
    """
    for entity in entities.filter(entity_type="trust_unit"):
        holders = list(entity.officers.filter(role="unit_holder"))
        if not holders:
            continue

        entity.entity_type = "trust"
        entity.save(update_fields=["entity_type"])

        for holder in holders:
            if holder.units_held is None:
                continue
            holder.units_held = None
            holder.save(update_fields=["units_held"])


def forwards(apps, schema_editor):
    Entity = apps.get_model("core", "Entity")
    reclassify_unit_trusts(Entity.objects.all())


def backwards(apps, schema_editor):
    Entity = apps.get_model("core", "Entity")
    unreclassify_unit_trusts(Entity.objects.all())


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0150_entityofficer_units_held"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
