"""
Management command: repair_rollforward_comparatives

Repairs the comparative column (prior_debit / prior_credit) on rolled-forward
financial years where the roll-forward wrote the prior year's period MOVEMENT
instead of its CLOSING BALANCE.

Targets (3 CORRUPT FYs identified in Phase 1 audit):
  Hazaway Operations Pty Ltd  FY2025  bddf499d-3f9a-4ede-8203-802d492c3f0d
  Makhmalbaf Pty Ltd          FY2025  37415162-08dc-46d0-a600-11868c76de4c
  Vincent Family Trust        FY2025  359c99a0-cfbb-41ce-aaec-abc0f183699d

Usage:
  python manage.py repair_rollforward_comparatives              # dry-run (default)
  python manage.py repair_rollforward_comparatives --commit     # write changes
  python manage.py repair_rollforward_comparatives <uuid> ...   # subset of FYs
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import ChartOfAccount, FinancialYear, TrialBalanceLine
from core.views import _comparative_for_line, _is_balance_sheet_account

DEFAULT_TARGET_IDS = [
    "bddf499d-3f9a-4ede-8203-802d492c3f0d",  # Hazaway Operations Pty Ltd FY2025
    "37415162-08dc-46d0-a600-11868c76de4c",  # Makhmalbaf Pty Ltd FY2025
    "359c99a0-cfbb-41ce-aaec-abc0f183699d",  # Vincent Family Trust FY2025
]

RECONCILIATION_THRESHOLD = Decimal("0.01")
IMBALANCE_THRESHOLD = Decimal("0.50")


class Command(BaseCommand):
    help = (
        "Repair prior_debit/prior_credit comparative values on rolled-forward FYs "
        "that were incorrectly populated with period movements instead of closing balances."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "fy_ids",
            nargs="*",
            help=(
                "UUIDs of target financial years. "
                "Defaults to the 3 known CORRUPT FYs from the Phase 1 audit."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Write changes to the database. Default is dry-run (no writes).",
        )

    def handle(self, *args, **options):
        fy_ids = options["fy_ids"] or DEFAULT_TARGET_IDS
        commit = options["commit"]

        if not commit:
            self.stdout.write(
                self.style.WARNING("DRY-RUN mode (no writes). Pass --commit to apply.\n")
            )

        overall_ok = True

        for fy_id in fy_ids:
            self.stdout.write(f"\n{'='*70}")
            self.stdout.write(f"FY: {fy_id}")
            ok = self._process_fy(fy_id.strip(), commit)
            if not ok:
                overall_ok = False

        self.stdout.write(f"\n{'='*70}")
        if overall_ok:
            if commit:
                self.stdout.write(self.style.SUCCESS("All FYs processed successfully."))
            else:
                self.stdout.write(self.style.SUCCESS("Dry-run complete. No issues found. Run with --commit to apply."))
        else:
            self.stdout.write(self.style.ERROR("One or more FYs had errors. See above."))

    def _process_fy(self, fy_id, commit):
        try:
            fy = FinancialYear.objects.select_related("entity", "prior_year").get(id=fy_id)
        except FinancialYear.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"  NOT FOUND: {fy_id}"))
            return False

        entity = fy.entity
        self.stdout.write(f"  Entity : {entity.entity_name}")
        self.stdout.write(f"  FY     : {fy.year_label}  status={fy.status}")

        # Pre-check 1: abort if finalised/locked
        if fy.is_locked:
            self.stdout.write(
                self.style.ERROR(
                    f"  ABORT: FY is finalised/locked. Cannot modify comparatives."
                )
            )
            return False

        # Pre-check 2: must have a prior year
        prior_fy = fy.prior_year
        if not prior_fy:
            self.stdout.write(
                self.style.ERROR(f"  ABORT: FY has no prior_year FK set.")
            )
            return False

        self.stdout.write(f"  Prior  : {prior_fy.year_label}  status={prior_fy.status}")

        # Vincent-specific caution: inspect unusual state indicators
        if "vincent" in entity.entity_name.lower():
            self.stdout.write(self.style.WARNING(
                "  NOTE: Vincent Family Trust — checking for unusual state ..."
            ))
            locked_lines = fy.trial_balance_lines.filter(comparatives_locked=True).count()
            if locked_lines > 0:
                self.stdout.write(
                    self.style.ERROR(
                        f"  STOP: {locked_lines} TB line(s) have comparatives_locked=True "
                        f"even though FY is not finalised. Stopping for safety — "
                        f"investigate before writing."
                    )
                )
                return False
            self.stdout.write(f"  Vincent state OK: no locked comparative lines.")

        # Build CoA section lookup for BS/PL classification
        coa_sections = dict(
            ChartOfAccount.objects.filter(
                entity_type=entity.entity_type, is_active=True
            ).values_list("account_code", "section")
        )

        # Build prior-FY closing balance map: account_code -> net sum(closing_balance)
        prior_map = {}
        for pline in prior_fy.trial_balance_lines.all():
            code = pline.account_code or ""
            if code not in prior_map:
                prior_map[code] = Decimal("0")
            prior_map[code] += pline.closing_balance or Decimal("0")

        # Inspect rollover BS lines in current FY
        rollover_lines = fy.trial_balance_lines.filter(source="rollover").select_related(
            "mapped_line_item"
        )

        changes = []   # (line, new_pd, new_pc, prior_net)
        skipped = []   # account codes with no prior-FY counterpart
        fails = []     # (account_code, prior_net, recomputed_net) reconciliation failures

        for line in rollover_lines:
            if not _is_balance_sheet_account(
                line.account_code, line.mapped_line_item, coa_sections
            ):
                continue

            prior_net = prior_map.get(line.account_code or "")
            if prior_net is None:
                skipped.append(line.account_code)
                continue

            # Create a proxy object so _comparative_for_line can read .closing_balance
            class _CBProxy:
                pass
            proxy = _CBProxy()
            proxy.closing_balance = prior_net
            _pd, _pc = _comparative_for_line(proxy)

            # Reconciliation gate: (_pd - _pc) must equal prior_net
            recomputed_net = _pd - _pc
            if abs(recomputed_net - prior_net) > RECONCILIATION_THRESHOLD:
                fails.append((line.account_code, prior_net, recomputed_net))
                continue

            # Idempotency: skip lines already at the correct values
            if line.prior_debit == _pd and line.prior_credit == _pc:
                continue

            changes.append((line, _pd, _pc, prior_net))

        # Report skipped lines
        if skipped:
            self.stdout.write(
                f"  Skipped (no prior-FY counterpart): {', '.join(skipped)}"
            )

        # Abort FY if any reconciliation failure
        if fails:
            self.stdout.write(self.style.ERROR(
                f"  ABORT (reconciliation failures — no writes made for this FY):"
            ))
            for code, prior_net, recomputed_net in fails:
                self.stdout.write(
                    f"    {code}: prior_net={prior_net}, recomputed={recomputed_net}"
                )
            return False

        if not changes:
            self.stdout.write("  Already correct — no changes needed (idempotent).")
            self._report_comparative_totals(fy, "  ")
            return True

        # Report proposed changes
        self.stdout.write(f"  {'WRITING' if commit else 'DRY-RUN'}: {len(changes)} line(s) to update")
        for line, _pd, _pc, prior_net in changes:
            self.stdout.write(
                f"    {line.account_code} {line.account_name[:35]}: "
                f"prior_debit  {line.prior_debit} -> {_pd}  |  "
                f"prior_credit {line.prior_credit} -> {_pc}  "
                f"(prior_fy closing net: {prior_net})"
            )

        if commit:
            try:
                with transaction.atomic():
                    for line, _pd, _pc, _ in changes:
                        line.prior_debit = _pd
                        line.prior_credit = _pc
                        line.save(update_fields=["prior_debit", "prior_credit"])
                self.stdout.write(self.style.SUCCESS(f"  Written OK."))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  DB write FAILED: {exc}"))
                return False

        # Secondary diagnostic: comparative column totals
        self._report_comparative_totals(fy, "  ", post_commit=commit)
        return True

    def _report_comparative_totals(self, fy, indent="", post_commit=False):
        """Report Dr/Cr totals of the prior-year comparative column for this FY."""
        from django.db.models import Sum as _Sum
        totals = fy.trial_balance_lines.aggregate(
            total_pd=_Sum("prior_debit"),
            total_pc=_Sum("prior_credit"),
        )
        total_pd = totals["total_pd"] or Decimal("0")
        total_pc = totals["total_pc"] or Decimal("0")
        imbalance = total_pd - total_pc
        label = "After writes" if post_commit else "Current"
        self.stdout.write(
            f"{indent}Comparative column totals ({label}): "
            f"Dr={total_pd:,.2f}  Cr={total_pc:,.2f}  "
            f"Imbalance={imbalance:,.2f}"
        )
        entity_name = fy.entity.entity_name
        if "hazaway" in entity_name.lower() or "vincent" in entity_name.lower():
            if abs(imbalance) > IMBALANCE_THRESHOLD:
                self.stdout.write(
                    self.style.ERROR(
                        f"{indent}STOP: {entity_name} shows comparative imbalance of "
                        f"{imbalance:,.2f} — this exceeds {IMBALANCE_THRESHOLD} and "
                        f"signals something beyond the known bug. Investigate before proceeding."
                    )
                )
        elif "makhmalbaf" in entity_name.lower():
            if abs(imbalance) > Decimal("1.00"):
                self.stdout.write(
                    self.style.WARNING(
                        f"{indent}Note: Makhmalbaf imbalance {imbalance:,.2f} is larger "
                        f"than the expected ~0.18 pre-existing source imbalance."
                    )
                )
            else:
                self.stdout.write(
                    f"{indent}Makhmalbaf: imbalance {imbalance:,.2f} "
                    f"(expected ~0.18 from pre-existing 2023/2024 source data — OK)."
                )
