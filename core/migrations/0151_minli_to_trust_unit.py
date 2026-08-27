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

SELECTOR AND SAFETY GUARDS -- the query below ("any 'trust' entity with any
'unit_holder' officer") is broader than the one entity this was written
for, so it is fenced rather than trusted. An entity is SKIPPED, with a
printed warning and no writes at all, unless:

  * it has at least one ACTIVE unit holder (date_ceased null or in the
    future -- the same rule as EntityOfficer.active_register_q and
    Entity.total_units), and
  * those active unit holders' stored distribution_percentage values sum
    to exactly 100.00, and
  * the units this migration would put on issue for them come to more
    than zero.

The percentage-sum guard is what makes the reconstruction sound: units are
derived FROM the percentages, so a register that does not already describe
a whole 100% is not a unit register this migration can reconstruct. It
covers, in one rule, a trust carrying a unit holder alongside
beneficiaries (the holder at 50.00 would otherwise be rewritten to 100.00,
leaving the register reading 150%), an already-ceased holder whose seeded
units no denominator would ever count, and NULL or 0.00 percentages (which
sum to 0, and would otherwise produce a 'trust_unit' with zero units on
issue and no error at all). The zero-unit guard is a second, independent
fence: it runs BEFORE entity_type is written, so a 'trust_unit' whose
every downstream allocation would raise ValueError cannot be created here.
Skipping prints a warning rather than raising: an unexpected register shape
on some other entity must not abort the deploy that reclassifies Minli,
and the survivors are visible in the migrate output for follow-up by hand.

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

The entity handed to that classmethod is RE-FETCHED through the current
Entity model (`CurrentEntity.objects.get(pk=entity.pk)`); it is NOT the
historical `apps.get_model` instance this migration iterates. Django's ORM
does not merely read `.pk` off the object it is given: Query.build_filter
runs check_query_object_type, which compares the value's class against the
field's related model and raises

    ValueError: Cannot query "Entity object (...)": Must be "Entity" instance.

for a historical Entity, before `.pk` is ever touched. An earlier revision
of this migration passed the historical instance straight through and
asserted here that "the ORM only needs its `.pk`" -- that claim was FALSE,
and it would have aborted `manage.py migrate` on exactly the one entity
this migration exists to convert. The officers the recompute then loads
and saves are its own `cls.objects` rows, so everything it writes is a
fully-featured current-model instance.

Both reclassify_unit_trusts() and unreclassify_unit_trusts() are written
to be idempotent: each only acts on entities matching its *starting*
entity_type ('trust' forward, 'trust_unit' in reverse), so re-running
either after it has already applied is a no-op.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

HUNDRED = Decimal("100.00")


def _active_holders(holders, today):
    """Unit holders still on the register -- active_register_q's rule."""
    return [
        holder for holder in holders
        if holder.date_ceased is None or holder.date_ceased > today
    ]


def _seed_units(pct):
    """Reconstruct a unit count from a stored percentage (see docstring)."""
    return int((pct or Decimal("0")).to_integral_value(rounding=ROUND_HALF_UP))


def reclassify_unit_trusts(entities):
    """Reclassify every 'trust' entity with unit_holder officers to 'trust_unit'.

    Accepts any Entity-like queryset/manager -- the historical model from
    apps.get_model (as used by the RunPython forwards() below) or the real
    current model (as used directly in tests) both work: only `.filter()`,
    the `officers` reverse relation, and `.save()` are used on it, and the
    one call that genuinely needs a current-model instance re-fetches by
    pk (see the module docstring).

    A trust with only beneficiaries (every discretionary trust on the
    platform) is left completely alone: the `unit_holder` filter never
    matches them, so entity_type, distribution_percentage and units_held
    are untouched. So is any trust whose unit register fails one of the
    guards described in the module docstring -- it is skipped whole, with
    a printed warning, before a single field is written.
    """
    from django.utils import timezone
    today = timezone.now().date()

    for entity in entities.filter(entity_type="trust"):
        holders = list(entity.officers.filter(role="unit_holder"))
        if not holders:
            continue  # discretionary trust -- beneficiaries only, skip

        label = f"{entity.entity_name} ({entity.pk})"
        active = _active_holders(holders, today)
        if not active:
            print(
                f"  SKIPPED {label}: every unit_holder is already ceased, so "
                f"there is no active register to reconstruct. Left as "
                f"entity_type='trust'."
            )
            continue

        pct_total = sum(
            (holder.distribution_percentage or Decimal("0")) for holder in active
        )
        if pct_total != HUNDRED:
            print(
                f"  SKIPPED {label}: its {len(active)} active unit holder(s) "
                f"carry distribution percentages summing to {pct_total}%, not "
                f"100.00%. Units are reconstructed FROM those percentages, so "
                f"this register cannot be converted safely (a partial "
                f"register, unit holders mixed with beneficiaries, or "
                f"NULL/0.00 percentages). Left as entity_type='trust' -- set "
                f"units_held by hand and flip entity_type once the register "
                f"is whole."
            )
            continue

        # Work out the units BEFORE touching entity_type, so a register
        # that would put zero units on issue never becomes a 'trust_unit'
        # at all (every downstream allocation would raise ValueError on it).
        planned = {}
        for holder in active:
            if holder.units_held is not None:
                continue  # already seeded (defensive against a partial re-run)
            # See module docstring: RECONSTRUCTED from the stored
            # percentage, not the (nonexistent) deed.
            planned[holder.pk] = _seed_units(holder.distribution_percentage)
        units_on_issue = sum(
            planned.get(holder.pk, holder.units_held or 0) for holder in active
        )
        if units_on_issue <= 0:
            print(
                f"  SKIPPED {label}: the register would carry zero units on "
                f"issue, which is not a unit trust. Left as "
                f"entity_type='trust'."
            )
            continue

        if entity.entity_type != "trust_unit":
            entity.entity_type = "trust_unit"
            entity.save(update_fields=["entity_type"])

        for holder in active:
            if holder.pk not in planned:
                continue
            holder.units_held = planned[holder.pk]
            holder.save(update_fields=["units_held"])

        if planned:
            # Ruling 2 / Task 5: this MUST be the last write against this
            # entity's register, and MUST run after units_held is seeded --
            # it is the sole writer of distribution_percentage now. Import
            # the real, current models and re-fetch the entity through
            # them: the ORM rejects a foreign (historical) model instance
            # in an `entity=` filter outright. See module docstring.
            from core.models import Entity as CurrentEntity
            from core.models import EntityOfficer as CurrentEntityOfficer
            CurrentEntityOfficer.recalculate_unit_percentages(
                CurrentEntity.objects.get(pk=entity.pk)
            )


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
