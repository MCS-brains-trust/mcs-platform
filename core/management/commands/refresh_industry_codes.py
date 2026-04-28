"""
refresh_industry_codes — diff a candidate ATO BIC fixture against the live
fixture, report Added/Removed/Renamed codes, and (without --dry-run) replace
the live fixture in place.

Usage
-----
    python manage.py refresh_industry_codes --new-fixture <path>
    python manage.py refresh_industry_codes --new-fixture <path> --dry-run
    python manage.py refresh_industry_codes --new-fixture <path> --force \\
        --mapping <mapping.json>

Behaviour
---------
- Added (in new, not in current): included wholesale.
- Renamed (in both, description differs): description updated, code unchanged,
  no entity migration.
- Removed (in current, not in new): NOT auto-migrated. Reports the count of
  affected entities per code and refuses to write unless --force AND a
  --mapping JSON file is supplied that maps every removed code to a
  replacement code present in the new fixture.
"""
import json
import os
import shutil
from collections import Counter

from django.core.management.base import BaseCommand, CommandError


LIVE_FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fixtures",
    "ato_industry_codes.json",
)


class Command(BaseCommand):
    help = "Diff a candidate ATO BIC fixture against the live fixture and refresh in place."

    def add_arguments(self, parser):
        parser.add_argument(
            "--new-fixture", required=True,
            help="Path to the candidate JSON fixture (flat {code: description} map).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report diffs only; write nothing.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Required when removed codes are in use by entities. "
                 "Must be paired with --mapping.",
        )
        parser.add_argument(
            "--mapping", default=None,
            help="Path to a JSON mapping of {removed_code: replacement_code} "
                 "for entities to be migrated when codes are removed.",
        )

    def handle(self, *args, **opts):
        from core.models import Entity

        new_path = opts["new_fixture"]
        dry_run = opts["dry_run"]
        force = opts["force"]
        mapping_path = opts["mapping"]

        if not os.path.exists(LIVE_FIXTURE):
            raise CommandError(f"Live fixture not found: {LIVE_FIXTURE}")
        if not os.path.exists(new_path):
            raise CommandError(f"New fixture not found: {new_path}")

        with open(LIVE_FIXTURE) as f:
            current = json.load(f)
        with open(new_path) as f:
            candidate = json.load(f)

        cur_codes = set(current)
        new_codes = set(candidate)

        added = sorted(new_codes - cur_codes)
        removed = sorted(cur_codes - new_codes)
        renamed = sorted(
            c for c in cur_codes & new_codes
            if current[c] != candidate[c]
        )

        self.stdout.write(self.style.MIGRATE_HEADING("=== ATO BIC fixture diff ==="))
        self.stdout.write(f"Current: {LIVE_FIXTURE} ({len(current)} codes)")
        self.stdout.write(f"Candidate: {new_path} ({len(candidate)} codes)")
        self.stdout.write(f"  Added:   {len(added)}")
        self.stdout.write(f"  Removed: {len(removed)}")
        self.stdout.write(f"  Renamed: {len(renamed)}")

        if added:
            self.stdout.write(self.style.SUCCESS("\n--- Added ---"))
            for c in added[:50]:
                self.stdout.write(f"  + {c}  {candidate[c]}")
            if len(added) > 50:
                self.stdout.write(f"  ... +{len(added) - 50} more")

        if renamed:
            self.stdout.write(self.style.SUCCESS("\n--- Renamed (description changed) ---"))
            for c in renamed[:50]:
                self.stdout.write(f"  ~ {c}  {current[c]!r}  ->  {candidate[c]!r}")
            if len(renamed) > 50:
                self.stdout.write(f"  ... +{len(renamed) - 50} more")

        affected = Counter()
        if removed:
            self.stdout.write(self.style.WARNING("\n--- Removed ---"))
            usage = (
                Entity.objects
                .filter(industry__in=removed)
                .values_list("industry", flat=True)
            )
            affected = Counter(usage)
            for c in removed:
                n = affected.get(c, 0)
                tag = f" [{n} entit{'y' if n == 1 else 'ies'} affected]" if n else ""
                self.stdout.write(f"  - {c}  {current[c]}{tag}")

        if dry_run:
            self.stdout.write(self.style.NOTICE("\n--dry-run: no files written."))
            return

        impacted = sum(affected.values())
        if impacted:
            if not (force and mapping_path):
                raise CommandError(
                    f"{impacted} entit{'y' if impacted == 1 else 'ies'} reference "
                    f"removed industry codes. Re-run with --force --mapping "
                    f"<path> after preparing a replacement mapping JSON."
                )
            with open(mapping_path) as f:
                mapping = json.load(f)
            unmapped = [c for c in affected if c not in mapping]
            if unmapped:
                raise CommandError(
                    f"Mapping is missing replacements for: {', '.join(unmapped)}"
                )
            bad_targets = [
                (src, tgt) for src, tgt in mapping.items()
                if src in affected and tgt not in candidate
            ]
            if bad_targets:
                raise CommandError(
                    "Mapping targets not present in candidate fixture: "
                    + ", ".join(f"{s}->{t}" for s, t in bad_targets)
                )

            self.stdout.write(self.style.WARNING("\n--- Migrating affected entities ---"))
            migrated = 0
            for src, tgt in mapping.items():
                n = Entity.objects.filter(industry=src).update(industry=tgt)
                if n:
                    migrated += n
                    self.stdout.write(f"  {n} entit{'y' if n == 1 else 'ies'}: {src} -> {tgt}")
            self.stdout.write(f"  Total migrated: {migrated}")

        backup = LIVE_FIXTURE + ".bak"
        shutil.copyfile(LIVE_FIXTURE, backup)
        with open(LIVE_FIXTURE, "w") as f:
            json.dump(candidate, f, indent=2, sort_keys=True)
        self.stdout.write(self.style.SUCCESS(
            f"\nWrote {len(candidate)} codes to {LIVE_FIXTURE} "
            f"(backup at {backup}). Update __last_checked__ in "
            f"core/industry_codes.py and commit."
        ))
