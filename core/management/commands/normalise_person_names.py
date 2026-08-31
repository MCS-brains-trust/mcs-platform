"""Backfill casing for person names already stored in capitals.

normalise_person_name runs on save(), so a record nobody saves keeps whatever
casing it arrived with. Production had ELLIOTT JAQUES on both entity_name and
trading_as of a sole trader, imported from XPM where names are held in capitals.

    python3 manage.py normalise_person_names --dry-run
    python3 manage.py normalise_person_names

Company, trust and partnership entity names are not touched: title case damages
the acronyms they carry ("ABC PTY LTD" becomes "Abc Pty Ltd"). See
core/name_case.py.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Client, Entity, EntityOfficer
from core.name_case import normalise_person_name

# (model, field, queryset filter) — the person-name fields, and only those.
TARGETS = (
    (Client, "name", {}),
    (Entity, "entity_name", {"entity_type__in": sorted(Entity.PERSON_NAMED_TYPES)}),
    (Entity, "trading_as", {"entity_type__in": sorted(Entity.PERSON_NAMED_TYPES)}),
    (EntityOfficer, "full_name", {}),
)


class Command(BaseCommand):
    help = "Normalise the casing of person names already stored in capitals."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        changed = 0

        with transaction.atomic():
            for model, field, filters in TARGETS:
                for obj in model.objects.filter(**filters):
                    current = getattr(obj, field)
                    proposed = normalise_person_name(current)
                    if proposed == current:
                        continue
                    changed += 1
                    self.stdout.write(
                        f"  {model.__name__}.{field}: "
                        f"{current!r} -> {proposed!r}"
                    )
                    if not dry_run:
                        # update() rather than save(): the hook would do the
                        # same work again, and this skips unrelated save-time
                        # side effects on these models.
                        model.objects.filter(pk=obj.pk).update(**{field: proposed})

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(f"Dry run: {changed} value(s) would change.")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"Normalised {changed} value(s).")
                )
