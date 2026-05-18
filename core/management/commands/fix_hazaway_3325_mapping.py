"""
Django management command: fix_hazaway_3325_mapping

One-shot data fix for the single mistagged TrialBalanceLine for Hazaway
Operations Pty Ltd, account 3325 (Income Tax Payable). The line's
mapped_line_item.standard_code is BS-EQ-011 (Income tax provision / Equity),
which the rollover income-tax handler correctly absorbs into Retained
Profits. The account's master ClientAccountMapping is BS-CL-005 (Other
current liabilities). The line-level tag is wrong; this command corrects
it to match the master CAM.

The FY is finalised + locked. This command explicitly:
  - snapshots the FY's locked state,
  - sets status to REOPENED to unlock,
  - updates the single TB line's mapped_line_item,
  - restores the FY's prior status and locked metadata verbatim,
  - all inside one transaction.atomic() block.

It does NOT exploit the model-layer lock gap with a bare .save().

Defaults to --dry-run (prints what it would do, writes nothing). Pass
--apply to actually write.

Usage (on the server):
  cd /opt/statementhub
  source venv/bin/activate
  python manage.py fix_hazaway_3325_mapping            # dry-run (default)
  python manage.py fix_hazaway_3325_mapping --apply    # actually apply
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import (
    AccountMapping,
    ClientAccountMapping,
    FinancialYear,
    TrialBalanceLine,
)


TARGET_TB_PK = "558b87f1-e53a-46d7-9b31-ac64d0f7448b"
TARGET_ENTITY_PK = "1be5d8ac-6ac5-4461-86ae-e3b08762172d"
TARGET_ACCOUNT_CODE = "3325"
EXPECTED_OLD_STD = "BS-EQ-011"
EXPECTED_NEW_STD = "BS-CL-005"


class Command(BaseCommand):
    help = (
        "One-shot fix for Hazaway Operations 3325 mistagged TB line "
        "(BS-EQ-011 → BS-CL-005). Default is dry-run; pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually apply the fix. Without this flag, runs in dry-run mode.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        mode = "APPLY" if apply_changes else "DRY-RUN"

        self.stdout.write("=" * 70)
        self.stdout.write(f"  fix_hazaway_3325_mapping  [{mode}]")
        self.stdout.write("=" * 70)

        # ── 1. Load the target TB line ──────────────────────────────────
        try:
            tb_line = (
                TrialBalanceLine.objects
                .select_related("mapped_line_item", "financial_year", "financial_year__entity")
                .get(pk=TARGET_TB_PK)
            )
        except TrialBalanceLine.DoesNotExist:
            raise CommandError(
                f"Aborting: TrialBalanceLine pk={TARGET_TB_PK} does not exist."
            )

        # ── 2. Sanity assertions on the target row ──────────────────────
        if tb_line.account_code != TARGET_ACCOUNT_CODE:
            raise CommandError(
                f"Aborting: TB line account_code={tb_line.account_code!r}, "
                f"expected {TARGET_ACCOUNT_CODE!r}."
            )

        fy = tb_line.financial_year
        entity = fy.entity
        if str(entity.pk) != TARGET_ENTITY_PK:
            raise CommandError(
                f"Aborting: TB line entity pk={entity.pk}, "
                f"expected {TARGET_ENTITY_PK}."
            )

        current_mapping = tb_line.mapped_line_item
        if current_mapping is None:
            raise CommandError(
                "Aborting: TB line has no mapped_line_item; expected "
                f"standard_code={EXPECTED_OLD_STD}."
            )
        current_std = current_mapping.standard_code or ""
        if current_std != EXPECTED_OLD_STD:
            raise CommandError(
                f"Aborting: TB line mapped_line_item.standard_code="
                f"{current_std!r}, expected {EXPECTED_OLD_STD!r}. "
                f"It may have already been fixed."
            )

        # ── 3. Resolve target AccountMapping (BS-CL-005) ────────────────
        try:
            new_mapping = AccountMapping.objects.get(standard_code=EXPECTED_NEW_STD)
        except AccountMapping.DoesNotExist:
            raise CommandError(
                f"Aborting: no AccountMapping with standard_code="
                f"{EXPECTED_NEW_STD!r}. Cannot resolve target line item."
            )

        # ── 4. Verify CAM is BS-CL-005 (must match the master) ──────────
        cam = (
            ClientAccountMapping.objects
            .select_related("mapped_line_item")
            .filter(entity=entity, client_account_code=TARGET_ACCOUNT_CODE)
            .first()
        )
        if cam is None or cam.mapped_line_item is None:
            raise CommandError(
                "Aborting: no ClientAccountMapping with mapped_line_item for "
                f"entity={entity.pk} account_code={TARGET_ACCOUNT_CODE}."
            )
        cam_std = cam.mapped_line_item.standard_code or ""
        if cam_std != EXPECTED_NEW_STD:
            raise CommandError(
                f"Aborting: CAM mapped_line_item.standard_code={cam_std!r}, "
                f"expected {EXPECTED_NEW_STD!r}. Fix the CAM first."
            )

        # ── 5. Show the plan ────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(f"  Entity         : {entity.entity_name}  ({entity.pk})")
        self.stdout.write(f"  Financial year : {fy.year_label}  pk={fy.pk}")
        self.stdout.write(f"    status       : {fy.status}")
        self.stdout.write(f"    is_locked    : {fy.is_locked}")
        self.stdout.write(f"  TB line pk     : {tb_line.pk}")
        self.stdout.write(f"    account_code : {tb_line.account_code}")
        self.stdout.write(f"    account_name : {tb_line.account_name}")
        self.stdout.write(f"    debit        : {tb_line.debit}")
        self.stdout.write(f"    credit       : {tb_line.credit}")
        self.stdout.write(f"  Mapping change : {current_std} → {EXPECTED_NEW_STD}")
        self.stdout.write(
            f"    old line item: {current_mapping.line_item_label!r}"
        )
        self.stdout.write(
            f"    new line item: {new_mapping.line_item_label!r}"
        )

        if not apply_changes:
            self.stdout.write("")
            self.stdout.write(
                "  ** DRY-RUN: no changes written. Re-run with --apply to write. **"
            )
            return

        # ── 6. Apply: unlock → update → restore lock, in one transaction ─
        prior_status = fy.status
        prior_finalised_at = fy.finalised_at
        prior_locked_at = fy.locked_at
        prior_locked_by_id = fy.locked_by_id
        prior_reopened_at = fy.reopened_at
        prior_reopened_by_id = fy.reopened_by_id
        prior_reopen_reason = fy.reopen_reason

        with transaction.atomic():
            # Unlock by transitioning to REOPENED. We do NOT touch
            # comparatives_locked or generated_documents — this is a
            # metadata-only fix on one TB line, not a full reopen workflow.
            if fy.is_locked:
                fy.status = FinancialYear.Status.REOPENED
                fy.save(update_fields=["status"])

            # Apply the mapping correction
            tb_line.mapped_line_item = new_mapping
            tb_line.save(update_fields=["mapped_line_item"])

            # Restore the FY's prior locked state verbatim
            FinancialYear.objects.filter(pk=fy.pk).update(
                status=prior_status,
                finalised_at=prior_finalised_at,
                locked_at=prior_locked_at,
                locked_by_id=prior_locked_by_id,
                reopened_at=prior_reopened_at,
                reopened_by_id=prior_reopened_by_id,
                reopen_reason=prior_reopen_reason,
            )

        # ── 7. Audit line ──────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write("  ** APPLIED **")
        self.stdout.write(
            f"  TB line {tb_line.pk}: mapped_line_item "
            f"{EXPECTED_OLD_STD} → {EXPECTED_NEW_STD}"
        )
        self.stdout.write(
            f"  FY {fy.pk} relocked: status={prior_status}, "
            f"is_locked={prior_status == FinancialYear.Status.FINALISED}"
        )
        self.stdout.write(
            f"  Source: fix_hazaway_3325_mapping mgmt cmd @ "
            f"{timezone.now().isoformat()}"
        )
