"""
Backfill per-beneficiary 4xxx children for trust entities that have officers
but no (or partial) materialised children.

Idempotent. Wraps each entity in transaction.atomic() so a failure on one
entity does not roll back the whole run.

Usage:
    python manage.py materialise_beneficiary_accounts --entity <UUID>
    python manage.py materialise_beneficiary_accounts --all
    python manage.py materialise_beneficiary_accounts --all --dry-run
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Backfill per-beneficiary 4xxx children for trust entities. "
        "See per_beneficiary_accounts_phase2.md."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--entity", type=str, default="",
            help="Single entity UUID. Mutually exclusive with --all.",
        )
        parser.add_argument(
            "--all", action="store_true",
            help="Process every trust entity that has at least one "
                 "distribution-role officer.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Roll back at end of each entity. Reports counts only.",
        )

    def handle(self, *args, **options):
        from core.models import Entity, EntityOfficer
        from core.beneficiary_account_service import (
            provision_beneficiary_accounts,
            _cleanup_slot_codes,
            _cleanup_ghost_rows,
            BENEFICIARY_PARENT_CODES,
        )

        if not options["all"] and not options["entity"]:
            raise CommandError("Specify --entity <UUID> or --all.")
        if options["all"] and options["entity"]:
            raise CommandError("--entity and --all are mutually exclusive.")

        if options["entity"]:
            try:
                entities = [Entity.objects.get(pk=options["entity"])]
            except Entity.DoesNotExist:
                raise CommandError(f"Entity {options['entity']} not found.")
        else:
            entities = list(
                Entity.objects.filter(
                    entity_type="trust",
                    officers__role__in=list(EntityOfficer.DISTRIBUTION_ROLES),
                ).distinct().order_by("entity_name")
            )

        dry = options["dry_run"]
        if dry:
            self.stdout.write(self.style.WARNING("DRY RUN — all changes rolled back."))
        self.stdout.write(
            f"Processing {len(entities)} entit{'y' if len(entities) == 1 else 'ies'}.\n"
            f"Canonical parent codes in scope: {len(BENEFICIARY_PARENT_CODES)}\n"
        )

        totals = {
            "entities": 0,
            "officers": 0,
            "children_created": 0,
            "ghosts_deleted": 0,
            "ghosts_escalated": 0,
            "slots_deleted": 0,
            "slots_escalated": 0,
            "slots_retained": 0,
        }

        for entity in entities:
            entity_summary = {
                "officers": 0, "children_created": 0,
                "ghosts_deleted": 0, "ghosts_escalated": 0,
                "slots_deleted": 0, "slots_escalated": 0, "slots_retained": 0,
            }
            try:
                with transaction.atomic():
                    # Slot + ghost cleanup runs first, before any officer
                    # provisioning, so we capture the counts.
                    slot_result = _cleanup_slot_codes(entity)
                    entity_summary["slots_deleted"] = slot_result["deleted"]
                    entity_summary["slots_escalated"] = len(slot_result["escalated"])
                    entity_summary["slots_retained"] = len(slot_result["retained_custom"])

                    ghost_result = _cleanup_ghost_rows(entity)
                    entity_summary["ghosts_deleted"] = ghost_result["deleted"]
                    entity_summary["ghosts_escalated"] = len(ghost_result["escalated"])

                    officers = EntityOfficer.objects.filter(
                        entity=entity,
                        role__in=list(EntityOfficer.DISTRIBUTION_ROLES),
                    ).order_by("display_order", "full_name")
                    for officer in officers:
                        created = provision_beneficiary_accounts(officer.pk)
                        entity_summary["officers"] += 1
                        entity_summary["children_created"] += (created or 0)

                    if dry:
                        transaction.set_rollback(True)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"  {entity.entity_name}: FAILED — {exc!r}"
                ))
                continue

            self.stdout.write(
                f"  {entity.entity_name}: "
                f"{entity_summary['officers']} officers, "
                f"{entity_summary['children_created']} children created, "
                f"{entity_summary['ghosts_deleted']} ghosts deleted "
                f"({entity_summary['ghosts_escalated']} escalated), "
                f"{entity_summary['slots_deleted']} slots deleted "
                f"({entity_summary['slots_escalated']} escalated, "
                f"{entity_summary['slots_retained']} retained custom)"
            )
            totals["entities"] += 1
            for k in entity_summary:
                totals[k] += entity_summary[k]

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done. Entities processed: {totals['entities']}. "
            f"Officers: {totals['officers']}. "
            f"Children created: {totals['children_created']}. "
            f"Ghosts deleted: {totals['ghosts_deleted']} "
            f"(escalated: {totals['ghosts_escalated']}). "
            f"Slot codes deleted: {totals['slots_deleted']} "
            f"(escalated: {totals['slots_escalated']}, "
            f"retained custom: {totals['slots_retained']})."
        ))
        if dry:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes committed."))
