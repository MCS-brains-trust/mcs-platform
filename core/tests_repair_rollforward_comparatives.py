"""repair_rollforward_comparatives must decide per account code, not per line.

The command summed the prior year's closing balance per account code, then
wrote that whole figure onto every rollover line for the code without looking
at what the code's other lines already carried. Where a year holds both a
tb_import line and a rollover line for one code -- the ordinary shape after a
trial balance import into a rolled-forward year -- the comparative ended up on
both.

The financial statements survive that: core/fs_template_service.py's Pass 1b
skips rows whose comparative nets to nil and picks a single survivor, so the
PDF still prints one figure. Everything that aggregates does not. The trial
balance screen sums prior_debit across the group (core/views.py:2179) and into
the column total (:2270), and Eva review accumulates it (core/eva_service.py:1028).
Each of those doubles, silently, and the command's own gate cannot see it
because doubling both sides leaves Dr and Cr still equal.

The invariant these tests hold the command to: at most one line per account
code carries a non-zero comparative, and the per-code total equals the prior
year's closing balance.
"""

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from core.models import Entity, FinancialYear, TrialBalanceLine

PRIOR_2000 = Decimal("134996.41")


class RepairRollforwardComparativesTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(entity_name="Minli Enterprise Unit Trust")
        self.prior = self._fy(2026)
        self.fy = self._fy(2027, prior_year=self.prior)

    def _fy(self, year, prior_year=None, status="draft"):
        return FinancialYear.objects.create(
            entity=self.entity,
            year_label=str(year),
            start_date=date(year - 1, 7, 1),
            end_date=date(year, 6, 30),
            status=status,
            prior_year=prior_year,
        )

    def _line(self, fy, code, source, closing="0", prior_debit="0", prior_credit="0", **kw):
        return TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code=code,
            account_name=kw.pop("name", "Cash at bank"),
            source=source,
            closing_balance=Decimal(closing),
            prior_debit=Decimal(prior_debit),
            prior_credit=Decimal(prior_credit),
            **kw,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("repair_rollforward_comparatives", str(self.fy.id), *args, stdout=out, stderr=out)
        return out.getvalue()

    def _comparative_total(self, code="2000"):
        return sum(
            (l.prior_debit or Decimal("0")) - (l.prior_credit or Decimal("0"))
            for l in TrialBalanceLine.objects.filter(financial_year=self.fy, account_code=code)
        )

    def _carriers(self, code="2000"):
        return [
            l for l in TrialBalanceLine.objects.filter(financial_year=self.fy, account_code=code)
            if (l.prior_debit or Decimal("0")) - (l.prior_credit or Decimal("0")) != Decimal("0")
        ]

    # --- 1. the Minli FY2027 regression -------------------------------------

    def test_leaves_a_code_alone_when_another_line_already_carries_the_comparative(self):
        """The import line holds the figure and the rollover line holds nil.

        That is a correct year. Writing the figure onto the rollover line as
        well doubles it everywhere the comparative is aggregated.
        """
        self._line(self.prior, "2000", "tb_import", closing=str(PRIOR_2000))
        rollover = self._line(self.fy, "2000", "rollover", closing=str(PRIOR_2000))
        self._line(self.fy, "2000", "tb_import", prior_debit=str(PRIOR_2000))

        self._run("--commit")

        rollover.refresh_from_db()
        self.assertEqual(rollover.prior_debit, Decimal("0"))
        self.assertEqual(self._comparative_total(), PRIOR_2000)
        self.assertEqual(len(self._carriers()), 1)

    # --- 2. a real repair converges on one carrier ---------------------------

    def test_repair_puts_the_figure_on_one_line_and_clears_the_others(self):
        self._line(self.prior, "2000", "tb_import", closing=str(PRIOR_2000))
        rollover = self._line(self.fy, "2000", "rollover", closing=str(PRIOR_2000))
        stale = self._line(self.fy, "2000", "tb_import", prior_debit="500.00")

        self._run("--commit")

        rollover.refresh_from_db()
        stale.refresh_from_db()
        self.assertEqual(rollover.prior_debit, PRIOR_2000)
        self.assertEqual(stale.prior_debit, Decimal("0"))
        self.assertEqual(stale.prior_credit, Decimal("0"))
        self.assertEqual(self._comparative_total(), PRIOR_2000)
        self.assertEqual(len(self._carriers()), 1)

    def test_repair_keeps_prior_closing_balance_in_step(self):
        """core/ai_service.py:304 reads prior_closing_balance, not the Dr/Cr pair."""
        self._line(self.prior, "2000", "tb_import", closing=str(PRIOR_2000))
        rollover = self._line(self.fy, "2000", "rollover", closing=str(PRIOR_2000))

        self._run("--commit")

        rollover.refresh_from_db()
        self.assertEqual(rollover.prior_closing_balance, PRIOR_2000)

    # --- 3. the single-line case still behaves as it always did --------------

    def test_single_rollover_line_with_a_wrong_comparative_is_repaired(self):
        self._line(self.prior, "2000", "tb_import", closing=str(PRIOR_2000))
        rollover = self._line(self.fy, "2000", "rollover", closing=str(PRIOR_2000), prior_debit="1.00")

        self._run("--commit")

        rollover.refresh_from_db()
        self.assertEqual(rollover.prior_debit, PRIOR_2000)
        self.assertEqual(rollover.prior_credit, Decimal("0"))

    # --- 4. locks and hand-set overrides are honoured ------------------------

    def test_a_locked_line_stops_the_code_being_touched(self):
        self._line(self.prior, "2000", "tb_import", closing=str(PRIOR_2000))
        rollover = self._line(self.fy, "2000", "rollover", closing=str(PRIOR_2000))
        self._line(self.fy, "2000", "tb_import", prior_debit="500.00", comparatives_locked=True)

        output = self._run("--commit")

        rollover.refresh_from_db()
        self.assertEqual(rollover.prior_debit, Decimal("0"))
        self.assertIn("2000", output)

    def test_a_hand_set_comparative_stops_the_code_being_touched(self):
        self._line(self.prior, "2000", "tb_import", closing=str(PRIOR_2000))
        rollover = self._line(self.fy, "2000", "rollover", closing=str(PRIOR_2000))
        self._line(self.fy, "2000", "tb_import", prior_debit="500.00", prior_balance_override=True)

        output = self._run("--commit")

        rollover.refresh_from_db()
        self.assertEqual(rollover.prior_debit, Decimal("0"))
        self.assertIn("2000", output)

    # --- 5. the post-write assertion rolls the transaction back --------------

    def test_a_write_that_breaks_the_invariant_is_rolled_back(self):
        """Fault-injected at the seam that clears the losing lines.

        The pre-write reconciliation gate cannot catch this: every figure it
        checks reconciles to the prior year. What goes wrong is that the code
        ends up with two carriers, which is precisely what the old command did
        and what its Dr-vs-Cr gate could not see. Neutering the clearing step
        must leave the year untouched, not half-written.
        """
        self._line(self.prior, "2000", "tb_import", closing=str(PRIOR_2000))
        rollover = self._line(self.fy, "2000", "rollover", closing=str(PRIOR_2000))
        other = self._line(self.fy, "2000", "tb_import", prior_debit="500.00")

        target = "core.management.commands.repair_rollforward_comparatives._clear_losing_carriers"
        with patch(target, return_value=[]):
            self._run("--commit")

        rollover.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(rollover.prior_debit, Decimal("0"))
        self.assertEqual(other.prior_debit, Decimal("500.00"))

    # --- 6. no more firing at a stale hardcoded target list ------------------

    def test_a_bare_run_will_not_fall_back_to_the_hardcoded_targets(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("repair_rollforward_comparatives", stdout=out, stderr=out)

    # --- 7. an out-of-balance comparative column is escalated, for anyone ----

    def test_an_out_of_balance_comparative_column_is_escalated(self):
        """The old command escalated this only for three entities by name.

        Vincent Family Trust FY2025 is out by 17,834.03 -- a 4199 rollover that
        was never written -- and that imbalance is the only thing in the report
        that shows it. Every entity gets the check, not three.
        """
        self._line(self.prior, "2000", "tb_import", closing="100.00")
        self._line(self.fy, "2000", "rollover", closing="100.00", prior_debit="100.00")

        output = self._run()

        self.assertIn("Imbalance", output)
        self.assertIn("100.00", output)
        self.assertIn("STOP", output)

    def test_the_closing_summary_does_not_claim_all_clear_after_a_stop(self):
        """"No issues found" under a STOP is how the old command misled.

        Vincent FY2025 printed a red STOP and then "Dry-run complete. No issues
        found. Run with --commit to apply." in the same output.
        """
        self._line(self.prior, "2000", "tb_import", closing="100.00")
        self._line(self.fy, "2000", "rollover", closing="100.00", prior_debit="100.00")

        output = self._run()

        self.assertIn("STOP", output)
        self.assertNotIn("No issues found", output)
