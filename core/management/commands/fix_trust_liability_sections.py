"""
Reclassify trust liability accounts from generic 'liabilities' to
'current_liabilities' (3000-3499) or 'noncurrent_liabilities' (3500-3999).

Applies to both the global ChartOfAccount template and all per-entity
EntityChartOfAccount records for trust entities.

Usage:
    python3 manage.py fix_trust_liability_sections --dry-run
    python3 manage.py fix_trust_liability_sections
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import ChartOfAccount, EntityChartOfAccount, Entity


def _classify(account_code):
    """Return 'current_liabilities' or 'noncurrent_liabilities' based on code range."""
    try:
        code_int = int(str(account_code).split(".")[0])
    except (ValueError, IndexError):
        return None
    if 3000 <= code_int <= 3499:
        return "current_liabilities"
    elif 3500 <= code_int <= 3999:
        return "noncurrent_liabilities"
    return None


class Command(BaseCommand):
    help = "Reclassify trust liability accounts from 'liabilities' to current/noncurrent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report changes without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verbosity = options["verbosity"]
        prefix = "[DRY RUN] " if dry_run else ""

        # --- Report non-trust entities with section='liabilities' ---
        other_count = EntityChartOfAccount.objects.filter(
            section="liabilities",
        ).exclude(entity__entity_type="trust").count()
        if other_count:
            self.stdout.write(
                f"NOTE: {other_count} non-trust EntityChartOfAccount records also "
                f"have section='liabilities'. Not touching those in this run."
            )

        global_coa_count = ChartOfAccount.objects.filter(
            section="liabilities",
        ).exclude(entity_type="trust").count()
        if global_coa_count:
            self.stdout.write(
                f"NOTE: {global_coa_count} non-trust ChartOfAccount records also "
                f"have section='liabilities'. Not touching those in this run."
            )

        # --- Fix 1: Global ChartOfAccount template ---
        global_current = 0
        global_noncurrent = 0
        global_skipped = 0

        coa_qs = ChartOfAccount.objects.filter(
            entity_type="trust", section="liabilities"
        )
        self.stdout.write(f"\n--- Global ChartOfAccount (trust, section=liabilities): {coa_qs.count()} ---")

        with transaction.atomic():
            for acct in coa_qs:
                new_section = _classify(acct.account_code)
                if not new_section:
                    global_skipped += 1
                    if verbosity >= 2:
                        self.stdout.write(f"  SKIP {acct.account_code} {acct.account_name} — code out of range")
                    continue
                if verbosity >= 2:
                    self.stdout.write(
                        f"  {prefix}{acct.account_code} {acct.account_name}: "
                        f"liabilities → {new_section}"
                    )
                if new_section == "current_liabilities":
                    global_current += 1
                else:
                    global_noncurrent += 1
                if not dry_run:
                    acct.section = new_section
                    acct.save(update_fields=["section"])

            # --- Fix 2: EntityChartOfAccount for trust entities ---
            trust_entities = Entity.objects.filter(entity_type="trust")
            entity_current = 0
            entity_noncurrent = 0
            entity_skipped = 0

            for entity in trust_entities:
                ecoa_qs = EntityChartOfAccount.objects.filter(
                    entity=entity, section="liabilities"
                )
                if not ecoa_qs.exists():
                    continue
                self.stdout.write(
                    f"\n--- {entity.entity_name} (EntityChartOfAccount, section=liabilities): "
                    f"{ecoa_qs.count()} ---"
                )
                for acct in ecoa_qs:
                    new_section = _classify(acct.account_code)
                    if not new_section:
                        entity_skipped += 1
                        if verbosity >= 2:
                            self.stdout.write(f"  SKIP {acct.account_code} {acct.account_name}")
                        continue
                    if verbosity >= 2:
                        self.stdout.write(
                            f"  {prefix}{acct.account_code} {acct.account_name}: "
                            f"liabilities → {new_section}"
                        )
                    if new_section == "current_liabilities":
                        entity_current += 1
                    else:
                        entity_noncurrent += 1
                    if not dry_run:
                        acct.section = new_section
                        acct.save(update_fields=["section"])

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(f"{prefix}Global ChartOfAccount: "
                          f"{global_current} → current_liabilities, "
                          f"{global_noncurrent} → noncurrent_liabilities, "
                          f"{global_skipped} skipped")
        self.stdout.write(f"{prefix}EntityChartOfAccount (trusts): "
                          f"{entity_current} → current_liabilities, "
                          f"{entity_noncurrent} → noncurrent_liabilities, "
                          f"{entity_skipped} skipped")
        total = global_current + global_noncurrent + entity_current + entity_noncurrent
        self.stdout.write(f"{prefix}Total updated: {total}")
