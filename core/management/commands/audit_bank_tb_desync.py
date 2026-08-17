"""Report where a book's bank-statement TB rows disagree with its transactions.

Writes nothing. Some entities may have been compensated by hand by an
accountant, and no automated sweep can tell a defect-induced variance from a
deliberate correction — which entities to repair is an accounting decision.
"""
import sys
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import Entity, FinancialYear, TrialBalanceLine
from core.views import _bank_tb_totals

D = Decimal


class Command(BaseCommand):
    help = "Audit bank-statement trial-balance rows against their transactions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--entity", dest="entity", default=None,
            help="Limit the audit to one entity primary key.",
        )

    def handle(self, *args, **options):
        entities = Entity.objects.all()
        if options["entity"]:
            entities = entities.filter(pk=options["entity"])

        entanglements = []
        variances = []
        unaudited_years = []

        for entity in entities.order_by("entity_name"):
            for fy in FinancialYear.objects.filter(entity=entity).order_by("start_date"):
                totals = _bank_tb_totals(fy)

                # Report against the BROAD map (`unbacked`), not the narrow
                # `entangled` one _recalculate_bank_tb_lines uses to decide
                # whether to decline a rebuild. `entangled` only names the
                # shape known to hold bank money hiding from the rebuild
                # (every non-bank_statement row on the code is a manual
                # adjustment, or duplicate bank_statement rows exist);
                # `unbacked` is every code with posted transactions and no
                # bank_statement row at all, whatever its other rows' source.
                # The audit's job is to cast a wide net for a human to judge,
                # not to pre-filter to only what would block a rebuild — so
                # this section is labelled ENTANGLED but is deliberately
                # sourced from the wider map. See _bank_tb_totals's docstring
                # in core/views.py for the full distinction; the naming is
                # otherwise confusing.
                for code, sources in sorted(totals["unbacked"].items()):
                    entanglements.append((entity, fy, code, sources))

                if not totals["fy_resolvable"]:
                    # A year outside entity_financial_years() (e.g.
                    # "reopened") can never have a transaction resolve back to
                    # it, so _bank_tb_totals reports empty accounts/gst — not
                    # because the transactions vacated it, but because the
                    # year itself is unresolvable. Comparing that against the
                    # stored rows would report "every posted transaction is
                    # missing from the trial balance", which is a lie. Skip
                    # the comparison and report the year as unaudited
                    # instead, with the reason.
                    unaudited_years.append((entity, fy))
                    continue

                # `bank_codes` is excluded from this comparison for the same
                # reason _recalculate_bank_tb_lines excludes it from its own
                # write loop: a bank-mapped code's row can legitimately hold a
                # contra movement (computed by _recalc_bank_contra's own
                # gross-amount, opposite-direction grouping — not exposed by
                # _bank_tb_totals) as well as, or instead of, an account-side
                # posting. Comparing `accounts`/`gst` — the account-side
                # figures this function *does* compute — against such a row
                # would report a false variance on every ordinary bank
                # account. Excluding these codes here mirrors exactly which
                # rows the rebuild itself would touch through this path.
                bank_codes = totals["bank_codes"]

                wanted = {
                    code: (t["debit"], t["credit"])
                    for code, t in totals["accounts"].items()
                    if code not in bank_codes
                }
                if (totals["gst"]["debit"] or totals["gst"]["credit"]) and (
                    "3380" not in bank_codes
                ):
                    wanted["3380"] = (totals["gst"]["debit"], totals["gst"]["credit"])

                stored = {
                    line.account_code: (line.debit, line.credit)
                    for line in TrialBalanceLine.objects.filter(
                        financial_year=fy, source="bank_statement",
                        is_adjustment=False,
                    ).exclude(account_code__in=bank_codes)
                }

                for code in sorted(set(wanted) | set(stored)):
                    if code in totals["unbacked"]:
                        continue  # already reported above, and not comparable
                    want = wanted.get(code, (D("0"), D("0")))
                    have = stored.get(code, (D("0"), D("0")))
                    if want != have:
                        variances.append((entity, fy, code, want, have))

        if entanglements:
            self.stdout.write(self.style.ERROR("\nENTANGLED — repair by hand before rebuilding:"))
            for entity, fy, code, sources in entanglements:
                self.stdout.write(
                    f"  {entity.entity_name} [{entity.pk}] {fy.year_label} "
                    f"account {code}: bank postings have no bank_statement row; "
                    f"rows present are {', '.join(sources)}"
                )

        if variances:
            self.stdout.write(self.style.WARNING("\nVARIANCE — trial balance disagrees with transactions:"))
            for entity, fy, code, want, have in variances:
                self.stdout.write(
                    f"  {entity.entity_name} [{entity.pk}] {fy.year_label} "
                    f"account {code}: transactions say Dr {want[0]} / Cr {want[1]}, "
                    f"trial balance holds Dr {have[0]} / Cr {have[1]}"
                )

        if unaudited_years:
            self.stdout.write(self.style.WARNING("\nNOT AUDITED — year is not currently postable, skipped:"))
            for entity, fy in unaudited_years:
                self.stdout.write(
                    f"  {entity.entity_name} [{entity.pk}] {fy.year_label}: "
                    f"status {fy.status!r} is outside this entity's postable "
                    f"year set, so no transaction can resolve back to it and "
                    f"the transaction-vs-trial-balance comparison would be "
                    f"meaningless"
                )

        if not entanglements and not variances:
            self.stdout.write(self.style.SUCCESS("no variance and no entanglement found"))
            return

        self.stdout.write(
            f"\n{len(entanglements)} entangled account(s), {len(variances)} variance(s). "
            f"Nothing was written."
        )
        sys.exit(1)
