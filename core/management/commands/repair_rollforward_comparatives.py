"""
Management command: repair_rollforward_comparatives

Repairs the comparative column (prior_debit / prior_credit) on rolled-forward
financial years where the roll-forward wrote the prior year's period MOVEMENT
instead of its CLOSING BALANCE.

The invariant
-------------
At most ONE trial balance line per account code carries a non-zero
comparative, and the per-code total equals the prior year's closing balance
for that code.

Deciding per line instead of per code is what made this command dangerous.
It summed the prior year's closing across every line for a code -- correct --
then wrote that whole figure onto each rollover line without looking at what
the code's other lines already held. A year holding both a tb_import line and
a rollover line for one code (the ordinary shape after a trial balance import
into a rolled-forward year) ended up carrying the comparative twice. On Minli
FY2027 account 2000 the import line already held 134,996.41 and the rollover
line correctly held nil; the command wanted to write 134,996.41 onto the
rollover line as well.

The financial statements would have survived it: fs_template_service's Pass 1b
skips rows whose comparative nets to nil and picks a single survivor. Every
aggregating reader would not -- views.py:2179 sums prior_debit across the
group and :2270 into the column total, eva_service.py:1028 accumulates it.
The old Dr-vs-Cr imbalance gate could not see any of it, because doubling both
sides leaves the columns equal.

Usage:
  python manage.py repair_rollforward_comparatives <uuid> ...    # dry-run
  python manage.py repair_rollforward_comparatives <uuid> --commit
  python manage.py repair_rollforward_comparatives --known-targets
"""
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import ChartOfAccount, FinancialYear, TrialBalanceLine, template_entity_type
from core.views import _comparative_for_line, _is_balance_sheet_account

# The three FYs the Phase 1 audit identified. Reachable only via
# --known-targets: a stale list firing on a bare invocation is its own hazard.
DEFAULT_TARGET_IDS = [
    "bddf499d-3f9a-4ede-8203-802d492c3f0d",  # Hazaway Operations Pty Ltd FY2025
    "37415162-08dc-46d0-a600-11868c76de4c",  # Makhmalbaf Pty Ltd FY2025
    "359c99a0-cfbb-41ce-aaec-abc0f183699d",  # Vincent Family Trust FY2025
]

RECONCILIATION_THRESHOLD = Decimal("0.01")
IMBALANCE_THRESHOLD = Decimal("0.50")
ZERO = Decimal("0")


class _InvariantViolation(Exception):
    """Raised inside the transaction so a bad write is rolled back."""


def _net(line):
    return (line.prior_debit or ZERO) - (line.prior_credit or ZERO)


def _clear_losing_carriers(lines):
    """Zero the comparative on every line that is not the code's carrier.

    Returns the lines actually changed. A seam of its own so the post-write
    invariant check has something to catch when it goes wrong.
    """
    cleared = []
    for line in lines:
        if _net(line) == ZERO:
            continue
        line.prior_debit = ZERO
        line.prior_credit = ZERO
        line.prior_closing_balance = ZERO
        line.save(update_fields=["prior_debit", "prior_credit", "prior_closing_balance"])
        cleared.append(line)
    return cleared


class Command(BaseCommand):
    help = (
        "Repair prior_debit/prior_credit comparative values on rolled-forward FYs "
        "that were incorrectly populated with period movements instead of closing balances."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "fy_ids",
            nargs="*",
            help="UUIDs of target financial years.",
        )
        parser.add_argument(
            "--known-targets",
            action="store_true",
            default=False,
            help=(
                "Use the 3 FYs from the Phase 1 audit instead of passing ids. "
                "Verify they are still the years you mean before using this."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Write changes to the database. Default is dry-run (no writes).",
        )

    def handle(self, *args, **options):
        fy_ids = options["fy_ids"]
        if not fy_ids:
            if not options["known_targets"]:
                raise CommandError(
                    "No financial years given. Pass one or more FY UUIDs, or "
                    "--known-targets to use the Phase 1 audit list "
                    f"({len(DEFAULT_TARGET_IDS)} FYs)."
                )
            fy_ids = DEFAULT_TARGET_IDS

        commit = options["commit"]
        if not commit:
            self.stdout.write(
                self.style.WARNING("DRY-RUN mode (no writes). Pass --commit to apply.\n")
            )

        overall_ok = True
        self.needs_investigation = []
        for fy_id in fy_ids:
            self.stdout.write(f"\n{'='*70}")
            self.stdout.write(f"FY: {fy_id}")
            if not self._process_fy(fy_id.strip(), commit):
                overall_ok = False

        self.stdout.write(f"\n{'='*70}")
        if not overall_ok:
            self.stdout.write(self.style.ERROR("One or more FYs had errors. See above."))
        elif self.needs_investigation:
            # Never sign off clean over a STOP. The old command printed
            # "No issues found" directly beneath Vincent FY2025's red STOP.
            self.stdout.write(self.style.ERROR(
                f"{len(self.needs_investigation)} FY(s) need investigation before "
                f"they can be trusted: {', '.join(self.needs_investigation)}. "
                f"See the STOP line(s) above."
            ))
        elif commit:
            self.stdout.write(self.style.SUCCESS("All FYs processed successfully."))
        else:
            self.stdout.write(self.style.SUCCESS(
                "Dry-run complete. No issues found. Run with --commit to apply."
            ))

    def _process_fy(self, fy_id, commit):
        try:
            fy = FinancialYear.objects.select_related("entity", "prior_year").get(id=fy_id)
        except (FinancialYear.DoesNotExist, ValueError, TypeError):
            self.stdout.write(self.style.ERROR(f"  NOT FOUND: {fy_id}"))
            return False

        entity = fy.entity
        self.stdout.write(f"  Entity : {entity.entity_name}")
        self.stdout.write(f"  FY     : {fy.year_label}  status={fy.status}")

        if fy.is_locked:
            self.stdout.write(self.style.ERROR(
                "  ABORT: FY is finalised/locked. Cannot modify comparatives."
            ))
            return False

        prior_fy = fy.prior_year
        if not prior_fy:
            self.stdout.write(self.style.ERROR("  ABORT: FY has no prior_year FK set."))
            return False

        self.stdout.write(f"  Prior  : {prior_fy.year_label}  status={prior_fy.status}")

        coa_sections = dict(
            ChartOfAccount.objects.filter(
                entity_type=template_entity_type(entity.entity_type), is_active=True
            ).values_list("account_code", "section")
        )

        # Prior-FY closing balance per account code.
        prior_map = defaultdict(Decimal)
        for pline in prior_fy.trial_balance_lines.all():
            prior_map[pline.account_code or ""] += pline.closing_balance or ZERO

        # Every current-FY line, grouped by code -- not just the rollover ones.
        # The whole defect was reading a rollover line in isolation.
        lines_by_code = defaultdict(list)
        for line in fy.trial_balance_lines.select_related("mapped_line_item").order_by(
            "account_code", "created_at", "id"
        ):
            lines_by_code[line.account_code or ""].append(line)

        plans = []    # (code, carrier, losers, pd, pc, prior_net)
        skipped = []  # codes with no prior-FY counterpart
        guarded = []  # (code, reason)
        fails = []    # (code, prior_net, recomputed_net)

        for code, group in sorted(lines_by_code.items()):
            carriers = [l for l in group if l.source == "rollover"]
            if not carriers:
                continue
            carrier = carriers[0]
            if not _is_balance_sheet_account(
                carrier.account_code, carrier.mapped_line_item, coa_sections
            ):
                continue

            if code not in prior_map:
                skipped.append(code)
                continue
            prior_net = prior_map[code]

            locked = [l for l in group if getattr(l, "comparatives_locked", False)]
            overridden = [l for l in group if getattr(l, "prior_balance_override", False)]
            if locked or overridden:
                reason = "comparatives_locked" if locked else "prior_balance_override"
                guarded.append((code, reason))
                continue

            # The invariant, both halves: the code's total must be right AND
            # only one line may carry it.
            current_total = sum((_net(l) for l in group), ZERO)
            existing_carriers = [l for l in group if _net(l) != ZERO]
            if (abs(current_total - prior_net) <= RECONCILIATION_THRESHOLD
                    and len(existing_carriers) <= 1):
                continue

            proxy = TrialBalanceLine(closing_balance=prior_net)
            _pd, _pc = _comparative_for_line(proxy)
            if abs((_pd - _pc) - prior_net) > RECONCILIATION_THRESHOLD:
                fails.append((code, prior_net, _pd - _pc))
                continue

            losers = [l for l in group if l.pk != carrier.pk]
            plans.append((code, carrier, losers, _pd, _pc, prior_net))

        if skipped:
            self.stdout.write(f"  Skipped (no prior-FY counterpart): {', '.join(skipped)}")
        for code, reason in guarded:
            self.stdout.write(self.style.WARNING(
                f"  Skipped {code}: a line is marked {reason} — a person set this "
                f"comparative, so the command will not overwrite it."
            ))
        if fails:
            self.stdout.write(self.style.ERROR(
                "  ABORT (reconciliation failures — no writes made for this FY):"
            ))
            for code, prior_net, recomputed_net in fails:
                self.stdout.write(f"    {code}: prior_net={prior_net}, recomputed={recomputed_net}")
            return False

        if not plans:
            self.stdout.write("  Already correct — no changes needed (idempotent).")
            self._report_comparative_totals(fy, "  ")
            return True

        self.stdout.write(f"  {'WRITING' if commit else 'DRY-RUN'}: {len(plans)} code(s) to repair")
        for code, carrier, losers, _pd, _pc, prior_net in plans:
            self.stdout.write(
                f"    {code} {(carrier.account_name or '')[:35]}: "
                f"carrier({carrier.source}) prior_debit {carrier.prior_debit} -> {_pd}  |  "
                f"prior_credit {carrier.prior_credit} -> {_pc}  "
                f"(prior_fy closing net: {prior_net})"
            )
            for loser in losers:
                if _net(loser) != ZERO:
                    self.stdout.write(
                        f"      clear {loser.source} line carrying {_net(loser)} "
                        f"— one carrier per code"
                    )

        if commit:
            try:
                with transaction.atomic():
                    for code, carrier, losers, _pd, _pc, prior_net in plans:
                        carrier.prior_debit = _pd
                        carrier.prior_credit = _pc
                        carrier.prior_closing_balance = _pd - _pc
                        carrier.save(update_fields=[
                            "prior_debit", "prior_credit", "prior_closing_balance",
                        ])
                        _clear_losing_carriers(losers)
                    self._assert_invariant(fy, plans)
                self.stdout.write(self.style.SUCCESS("  Written OK."))
            except _InvariantViolation as exc:
                self.stdout.write(self.style.ERROR(
                    f"  ROLLED BACK — the write would have broken the one-carrier "
                    f"invariant: {exc}"
                ))
                return False
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  DB write FAILED: {exc}"))
                return False

        self._report_comparative_totals(fy, "  ", post_commit=commit)
        return True

    def _assert_invariant(self, fy, plans):
        """Re-read every repaired code and prove both halves of the invariant."""
        codes = [code for code, *_ in plans]
        expected = {code: prior_net for code, _c, _l, _pd, _pc, prior_net in plans}
        fresh = defaultdict(list)
        for line in TrialBalanceLine.objects.filter(
            financial_year=fy, account_code__in=codes
        ):
            fresh[line.account_code].append(line)

        for code in codes:
            group = fresh.get(code, [])
            carriers = [l for l in group if _net(l) != ZERO]
            total = sum((_net(l) for l in group), ZERO)
            if len(carriers) > 1:
                raise _InvariantViolation(
                    f"{code} left {len(carriers)} lines carrying a comparative "
                    f"({', '.join(str(_net(l)) for l in carriers)})"
                )
            if abs(total - expected[code]) > RECONCILIATION_THRESHOLD:
                raise _InvariantViolation(
                    f"{code} totals {total}, expected {expected[code]}"
                )

    def _report_comparative_totals(self, fy, indent="", post_commit=False):
        """Dr/Cr totals, plus the duplicate-carrier count the old gate missed."""
        from django.db.models import Sum as _Sum
        totals = fy.trial_balance_lines.aggregate(
            total_pd=_Sum("prior_debit"), total_pc=_Sum("prior_credit"),
        )
        total_pd = totals["total_pd"] or ZERO
        total_pc = totals["total_pc"] or ZERO
        imbalance = total_pd - total_pc
        label = "After writes" if post_commit else "Current"
        self.stdout.write(
            f"{indent}Comparative column totals ({label}): "
            f"Dr={total_pd:,.2f}  Cr={total_pc:,.2f}  Imbalance={imbalance:,.2f}"
        )
        # Every entity gets this, not the three the old command matched by name.
        # A comparative column that does not balance is how a rollover line that
        # was never written shows up at all: Vincent Family Trust FY2025 is out
        # by its missing 4199 of 17,834.03, and nothing else in this report
        # mentions it -- the command skips codes that have no rollover line.
        if abs(imbalance) > IMBALANCE_THRESHOLD:
            label = f"{fy.entity.entity_name} {fy.year_label}"
            if label not in getattr(self, "needs_investigation", []):
                self.needs_investigation.append(label)
            self.stdout.write(self.style.ERROR(
                f"{indent}STOP: comparative column is out of balance by "
                f"{imbalance:,.2f}, beyond the {IMBALANCE_THRESHOLD} tolerance. "
                f"Something is wrong with this year's comparatives that repairing "
                f"rollover lines will not fix. Investigate before proceeding."
            ))

        by_code = defaultdict(list)
        for line in fy.trial_balance_lines.all():
            if _net(line) != ZERO:
                by_code[line.account_code].append(line)
        doubled = {code: rows for code, rows in by_code.items() if len(rows) > 1}
        if doubled:
            self.stdout.write(self.style.ERROR(
                f"{indent}{len(doubled)} account code(s) carry a comparative on more "
                f"than one line — every aggregating reader doubles these: "
                f"{', '.join(sorted(doubled))}"
            ))
        else:
            self.stdout.write(f"{indent}One carrier per account code — OK.")
