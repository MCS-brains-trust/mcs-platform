"""Report depreciation schedules that disagree with the trial balance.

Writes nothing, and has no flag that would. Twenty-four assets hold a negative
closing written-down value, which cannot be true of an asset, and the years
involved are largely finalised. Repairing a finalised year is an accounting
decision — and where an account pair serves several assets, apportioning it is
a judgement no rule can make. This command lays out the evidence for that
decision and stops there.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.depreciation_audit import reconcile_all
from core.models import Entity


def _money(value):
    if value is None:
        return "—".rjust(14)
    return f"{value:,.2f}".rjust(14)


class Command(BaseCommand):
    help = (
        "Audit depreciation schedules against the trial balance. "
        "Reports only; nothing is written."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--entity", dest="entity", default=None,
            help="Limit the audit to one entity primary key.",
        )
        parser.add_argument(
            "--all", dest="all", action="store_true",
            help="Audit every asset, not only those holding impossible values.",
        )

    def handle(self, *args, **options):
        entity = None
        if options["entity"]:
            entity = Entity.objects.filter(pk=options["entity"]).first()
            if entity is None:
                self.stderr.write(f"No entity with pk {options['entity']}")
                return

        results = reconcile_all(only_impossible=not options["all"], entity=entity)
        if not results:
            self.stdout.write("Nothing to report — no schedule holds an impossible value.")
            return

        correctable, needs_judgement = [], []
        for r in results:
            (correctable if r.is_reconcilable and r.needs_correction
             else needs_judgement).append(r)

        self.stdout.write("")
        self.stdout.write("DEPRECIATION SCHEDULE vs TRIAL BALANCE — report only, nothing written")
        self.stdout.write("=" * 78)

        if correctable:
            self.stdout.write("")
            self.stdout.write(f"RECONCILABLE — {len(correctable)} asset(s)")
            self.stdout.write("-" * 78)
            for r in correctable:
                self._render(r, proposal=True)

        if needs_judgement:
            self.stdout.write("")
            self.stdout.write(f"NEEDS AN ACCOUNTANT — {len(needs_judgement)} asset(s)")
            self.stdout.write("-" * 78)
            for r in needs_judgement:
                self._render(r, proposal=False)

        impossible = sum(1 for r in results if r.has_impossible_values)
        self.stdout.write("")
        self.stdout.write("=" * 78)
        self.stdout.write(
            f"{len(results)} asset(s) examined · {impossible} holding impossible values · "
            f"{len(correctable)} with a proposed opening WDV · "
            f"{len(needs_judgement)} needing judgement"
        )
        self.stdout.write("Nothing was written. Apply corrections through the "
                          "depreciation screen so they carry an audit trail.")
        self.stdout.write("")

    def _render(self, r, proposal):
        a = r.asset
        fy = a.financial_year
        self.stdout.write("")
        self.stdout.write(
            f"  {fy.entity.entity_name} · FY{fy.year_label} · {a.asset_name} "
            f"[{fy.status}]"
        )
        flags = []
        if r.current_opening_wdv < Decimal("0"):
            flags.append("opening negative")
        if r.current_closing_wdv < Decimal("0"):
            flags.append("closing negative")
        if r.current_depreciation < Decimal("0"):
            flags.append("depreciation negative")
        if flags:
            self.stdout.write(f"      impossible: {', '.join(flags)}")

        self.stdout.write(
            f"      schedule   opening {_money(r.current_opening_wdv)}"
            f"  dep {_money(r.current_depreciation)}"
            f"  closing {_money(r.current_closing_wdv)}"
        )
        self.stdout.write(
            f"      TB         cost    {_money(r.tb_cost)}"
            f"  accum {_money(r.tb_accumulated)}"
            f"  = WDV {_money(r.tb_written_down_value)}"
            f"  [{r.cost_account_code or '?'}/{r.accum_account_code or '?'}]"
        )
        posted = "posted" if r.depreciation_is_posted else "not posted"
        self.stdout.write(
            f"      this year's depreciation in the TB: {posted}"
            f" ({_money(r.tb_depreciation_expense).strip()})"
        )

        if proposal:
            self.stdout.write(
                self.style.SUCCESS(
                    f"      PROPOSED   opening WDV {_money(r.proposed_opening_wdv).strip()}"
                    f"   (closing {_money(r.proposed_closing_wdv).strip()})"
                )
            )
        elif r.reason:
            self.stdout.write(self.style.WARNING(f"      why not: {r.reason}"))
