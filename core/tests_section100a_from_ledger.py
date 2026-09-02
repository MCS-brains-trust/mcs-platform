"""Section 100A must read the ledger, and must not claim what it cannot know.

Scoped in docs/superpowers/specs/2026-09-02-section-100a-design.md. This is
Tier 1 ("honesty") plus option D ("retire what cannot work").

What was wrong:

* S100A-04 keyed off ``workspace.confirmed_scenario`` and ``stage_6_status``.
  confirmed_scenario is None on every workspace on the platform, and stage_6 is
  a phantom -- the Trust tab has five stages and nothing ever sets a sixth. Both
  guards therefore always passed, so a CRITICAL "resolution not confirmed" fired
  on every discretionary trust regardless of the facts.

* Rules 01-03 read ``BeneficiaryAllocation``, of which there are zero rows
  platform-wide: only the orphaned /years/<pk>/distribution/ page creates them.
  The posted distribution journal holds the same facts and joins to the officer
  through EntityChartOfAccount.beneficiary_officer.

* S100A-02 and S100A-03 were not merely idle, they were wrong. Both resolved the
  beneficiary's entity as ``alloc.beneficiary.entity``, but EntityOfficer.entity
  is a foreign key to the TRUST. They would have compared the trust against
  itself. There is no officer-to-beneficiary-entity link in the schema, so they
  are retired rather than carried.

* Where a rule cannot be evaluated -- no marginal rates recorded, no four-factor
  assessments completed -- it returned early and silently. Silence reads as "no
  risk found" on the finding card. It now says what it could not assess.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    AdjustingJournal, Client, Entity, EntityChartOfAccount, EntityOfficer,
    FinancialYear, JournalLine, Section100AAssessment, TrialBalanceLine,
    TrustWorkspace,
)
from core.risk_modules.section100a import Section100AModule


class Section100ABase(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Sec100A Family Trust", entity_type="trust",
            client=Client.objects.create(name="Sec100A Client"),
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        self.workspace = TrustWorkspace.objects.create(
            financial_year=self.fy,
            stage_1_status="completed", stage_2_status="completed",
            stage_3_status="completed", stage_4_status="completed",
        )

    def _beneficiary(self, name, code):
        officer = EntityOfficer.objects.create(
            entity=self.entity, full_name=name,
            role=EntityOfficer.OfficerRole.BENEFICIARY, roles=["beneficiary"],
        )
        EntityChartOfAccount.objects.update_or_create(
            entity=self.entity, account_code=code,
            defaults={"account_name": f"Distribution — {name}",
                      "section": "equity", "beneficiary_officer": officer},
        )
        return officer

    def _post_distribution(self, allocations, ref="JE-001"):
        total = sum(Decimal(a) for _, a in allocations)
        journal = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number=ref,
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=self.fy.end_date, description="Trust distribution",
            is_trust_distribution=True,
            total_debit=total, total_credit=total,
        )
        JournalLine.objects.create(
            journal=journal, line_number=1, account_code="4199",
            account_name="Undistributed income", description="Appropriation",
            debit=total, credit=Decimal("0"),
        )
        for i, (code, amount) in enumerate(allocations, start=2):
            coa = EntityChartOfAccount.objects.get(
                entity=self.entity, account_code=code)
            JournalLine.objects.create(
                journal=journal, line_number=i, account_code=code,
                account_name=coa.account_name, description="Distribution",
                debit=Decimal("0"), credit=Decimal(amount),
            )
        return journal

    def _run(self):
        module = Section100AModule(self.fy)
        module.load_data()
        return module, module.assess()

    def _text(self, module):
        return "\n".join(module.finding_lines).lower()


class AllocationsComeFromTheLedgerTests(Section100ABase):
    def test_a_posted_distribution_supplies_allocations(self):
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        module, result = self._run()
        self.assertEqual(
            result["allocation_count"], 1,
            "the posted distribution produced no allocations",
        )
        self.assertEqual(module.allocations[0]["amount"], Decimal("61848.01"))
        self.assertEqual(
            module.allocations[0]["beneficiary"].full_name, "Ronen Davidov")

    def test_the_appropriation_line_is_not_an_allocation(self):
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        module, _ = self._run()
        self.assertEqual([a["account_code"] for a in module.allocations],
                         ["4004.01"])

    def test_a_reversed_distribution_supplies_none(self):
        self._beneficiary("Ronen Davidov", "4004.01")
        journal = self._post_distribution([("4004.01", "61848.01")])
        AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-002",
            journal_type=AdjustingJournal.JournalType.YEAR_END,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=self.fy.end_date, reverses=journal,
            description="Reversal of JE-001",
        )
        module, result = self._run()
        self.assertEqual(result["allocation_count"], 0)


class ResolutionRuleTests(Section100ABase):
    """S100A-04 fired CRITICAL on every trust. It must stop."""

    def test_a_posted_distribution_does_not_fire_the_resolution_rule(self):
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        module, result = self._run()
        self.assertNotIn(
            "S100A-04", result["rules_fired"],
            "the resolution rule still fires on a trust that has distributed",
        )

    def test_it_says_the_resolution_date_is_not_recorded(self):
        """Honest: the platform stores no resolution date to check."""
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        module, _ = self._run()
        self.assertIn("resolution date", self._text(module))
        self.assertIn("not recorded", self._text(module))

    def test_no_distribution_posted_is_reported(self):
        module, result = self._run()
        self.assertIn("no distribution", self._text(module))


class RetiredRulesTests(Section100ABase):
    """S100A-02 and S100A-03 compared the trust against itself."""

    def test_the_circular_flow_rule_is_gone(self):
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        _, result = self._run()
        self.assertNotIn("S100A-02", result["rules_fired"])

    def test_the_upe_rule_is_gone(self):
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        _, result = self._run()
        self.assertNotIn("S100A-03", result["rules_fired"])

    def test_the_finding_says_they_are_not_assessed(self):
        """Removing them silently would be the same fault in a new form."""
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        module, result = self._run()
        text = self._text(module)
        self.assertIn("circular", text)
        self.assertIn("not assessed", text)
        self.assertIn("circular_flow_and_upe", result["unassessed"])


class CannotAssessIsStatedTests(Section100ABase):
    """Gaps are stated once, not one line per gap.

    Every unassessed exposure used to append its own finding line, so a trust
    that had simply distributed carried four of them. They are consolidated
    into a single line that still names each gap and why it could not be
    tested -- the point is that the gaps are visible, not that they are
    numerous.
    """

    def _unassessed_lines(self, module):
        return [l for l in module.finding_lines if l.lower().startswith("not assessed")]

    def test_the_gaps_are_reported_on_a_single_line(self):
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        module, _ = self._run()
        self.assertEqual(
            len(self._unassessed_lines(module)), 1,
            f"expected one consolidated line, got "
            f"{self._unassessed_lines(module)}",
        )

    def test_the_single_line_still_names_every_gap(self):
        """Condensing must not cost the reader the detail."""
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        module, result = self._run()
        line = self._unassessed_lines(module)[0].lower()
        self.assertIn("marginal rate", line)
        self.assertIn("circular", line)
        self.assertIn("four-factor", line)
        self.assertEqual(
            set(result["unassessed"]),
            {"low_tax_beneficiary", "circular_flow_and_upe", "four_factor"},
        )

    def test_a_completed_four_factor_assessment_drops_out_of_the_gaps(self):
        officer = self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        # risk_rating is recomputed from the questionnaire on save, so it is
        # driven through the answers rather than set directly. No risk
        # indicators -> GREEN.
        Section100AAssessment.objects.create(
            trust_workspace=self.workspace, beneficiary=officer,
            q1="no", q6="no",
        )
        module, result = self._run()
        self.assertNotIn("four_factor", result["unassessed"])
        self.assertNotIn("four-factor", self._unassessed_lines(module)[0].lower())


class SeverityTests(Section100ABase):
    def test_nothing_assertable_is_not_critical(self):
        """The module asserted CRITICAL on every trust via S100A-04."""
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        _, result = self._run()
        self.assertNotEqual(
            result["overall_severity"], "CRITICAL",
            "a CRITICAL was raised with nothing the module can actually assert",
        )

    def test_unassessed_exposures_are_not_reported_as_clear(self):
        """CLEAR means "no risk found", which is not what we know.

        Every rule now states what it could not assess, but severity was still
        derived purely from what fired -- so a trust with four unassessed
        exposures came back CLEAR. That is the same false assurance the rest of
        this change removes, reintroduced one level up.
        """
        self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        _, result = self._run()
        self.assertNotEqual(
            result["overall_severity"], "CLEAR",
            "four exposures went unassessed and the module reported CLEAR",
        )
        self.assertEqual(result["overall_severity"], "ADVISORY")

    def test_the_module_cannot_currently_clear_a_trust_that_distributed(self):
        """An honest consequence, asserted so it is not lost.

        Circular flow and UPE cannot be assessed by this platform at all, so
        any trust that distributed carries at least one unassessed exposure.
        CLEAR is therefore unreachable for such a trust today, and should be:
        the module is not in a position to clear anyone. It becomes reachable
        again when those exposures can actually be tested.
        """
        officer = self._beneficiary("Ronen Davidov", "4004.01")
        # Everything that CAN be assessed, is: below the materiality floor for
        # the low-tax test, and a completed green questionnaire.
        self._post_distribution([("4004.01", "500.00")])
        Section100AAssessment.objects.create(
            trust_workspace=self.workspace, beneficiary=officer,
            q1="no", q6="no",
        )
        _, result = self._run()
        self.assertEqual(result["overall_severity"], "ADVISORY")

    def test_a_red_four_factor_assessment_is_still_critical(self):
        """The one CRITICAL path that rests on real recorded judgement."""
        officer = self._beneficiary("Ronen Davidov", "4004.01")
        self._post_distribution([("4004.01", "61848.01")])
        # Q1 + Q6 both YES is RED by the model's own rule.
        Section100AAssessment.objects.create(
            trust_workspace=self.workspace, beneficiary=officer,
            q1="yes", q6="yes",
        )
        _, result = self._run()
        self.assertEqual(result["overall_severity"], "CRITICAL")


class UnitTrustsAreStillExcludedTests(TestCase):
    def test_a_unit_trust_does_not_run(self):
        entity = Entity.objects.create(
            entity_name="Unit Trust", entity_type="trust_unit",
            client=Client.objects.create(name="UT Client"),
        )
        fy = FinancialYear.objects.create(
            entity=entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        self.assertFalse(Section100AModule(fy).should_run())
