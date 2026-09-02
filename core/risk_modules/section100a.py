"""
Section 100A Risk Assessment Module
====================================

Detection module for Section 100A reimbursement agreement risk on
discretionary trusts. Distributions are read from the POSTED distribution
journal, not from a plan: see load_data.

Rules:
    S100A-01: Distribution to Low-Tax Beneficiary (ADVISORY)
    S100A-04: Distribution and resolution recorded (ADVISORY)
    S100A-05: Four-Factor Summary Assessment (ADVISORY → CRITICAL)

Retired 2026-09-02, and deliberately not replaced with a silent gap:
    S100A-02: Circular Money Flow
    S100A-03: UPE to Related Entity

Both resolved a beneficiary's entity as ``alloc.beneficiary.entity``, but
``EntityOfficer.entity`` is a foreign key to the TRUST -- so they compared the
trust against itself, and would have reported a UPE from a trust to itself had
their data ever arrived. There is no officer-to-beneficiary-entity link in the
schema and no beneficiary exists as an Entity, so the rules cannot be made
correct by rewriting them. assess() states plainly that these two exposures are
not assessed rather than leaving their absence to be read as "no risk found".
See docs/superpowers/specs/2026-09-02-section-100a-design.md.

Where a rule cannot be evaluated -- no marginal rates recorded, no four-factor
assessment completed -- it says so. Returning early in silence puts a clean
assessment on the finding card, which is worse than no rule at all.

Dependency: Trust Tab data (Stages 1–4).  If not completed, fires an
ADVISORY finding that the assessment is incomplete.

Legislative Foundation:
    ITAA 1936 s 100A, s 99A, TD 2022/11
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone

from core.risk_modules.base import BaseDetectionModule, ZERO

logger = logging.getLogger(__name__)

# Thresholds
LOW_TAX_DISTRIBUTION_MIN = Decimal("10000")   # $10K minimum to flag
MARGINAL_RATE_DIFF = Decimal("0.15")          # 15% rate difference


class Section100AModule(BaseDetectionModule):
    module_id = "section100a"
    display_name = "Section 100A Risk Assessment"
    # Deliberately NOT TRUST_LIKE_TYPES: Section 100A concerns discretionary
    # distributions. A fixed unit trust makes none, so it is excluded on
    # purpose — see should_run() below. Do not widen this to "trust_unit".
    entity_types = ["trust"]
    finding_category = "COMPLIANCE"

    def __init__(self, financial_year):
        super().__init__(financial_year)
        # No dedicated assessment model — uses the existing Section100AAssessment
        # per-beneficiary model and produces a consolidated EvaFinding.
        self.assessment_model = None

        # Data holders
        self.trust_workspace = None
        self.beneficiary_profiles = []
        self.allocations = []
        self.distribution_journal = None
        self.trust_tab_complete = False
        # Exposures this run could not evaluate. Severity reads this, so an
        # assessment that tested nothing cannot come back CLEAR.
        self.unassessed = []
        self._unassessed_summaries = []

    def should_run(self):
        """Only run for discretionary trusts.

        Deliberately equality with "trust", not membership in
        TRUST_LIKE_TYPES: a fixed unit trust distributes by the unit register
        and makes no discretionary distribution, so Section 100A cannot apply.
        core/views_trust.py already auto-skips the Section 100A stage for unit
        trusts. This narrowing is intentional — do not "fix" it in a sweep.
        """
        return self.entity.entity_type == "trust"

    def load_data(self):
        """Load Trust Tab data and what was actually distributed."""
        from core.models import (
            AdjustingJournal, EntityChartOfAccount, TrustWorkspace,
        )

        # Load trust workspace
        try:
            self.trust_workspace = TrustWorkspace.objects.get(
                financial_year=self.fy,
            )
        except TrustWorkspace.DoesNotExist:
            self.trust_workspace = None
            return

        # Check if stages 1-4 are completed
        if self.trust_workspace:
            self.trust_tab_complete = all([
                self.trust_workspace.stage_1_status == "completed",
                self.trust_workspace.stage_2_status == "completed",
                self.trust_workspace.stage_3_status == "completed",
                self.trust_workspace.stage_4_status == "completed",
            ])

            # Load beneficiary profiles
            self.beneficiary_profiles = list(
                self.trust_workspace.beneficiary_profiles.select_related(
                    "beneficiary"
                ).all()
            )

        # What was actually distributed, from the posted journal.
        #
        # This read BeneficiaryAllocation, of which there are zero rows
        # platform-wide: only the orphaned /years/<pk>/distribution/ page
        # creates them, and nothing links to it. So every rule that opened with
        # `if not self.allocations: return` was dead on arrival.
        #
        # The journal holds the same facts. live_trust_distribution excludes a
        # journal that has been reversed, so an un-posted distribution
        # correctly yields nothing, and the 4199 line is the debit side of the
        # appropriation rather than a recipient.
        self.distribution_journal = AdjustingJournal.live_trust_distribution(
            self.fy)
        self.allocations = []
        if self.distribution_journal is None:
            return

        officer_by_code = {
            coa.account_code: coa.beneficiary_officer
            for coa in EntityChartOfAccount.objects.filter(
                entity=self.entity, beneficiary_officer__isnull=False,
            ).select_related("beneficiary_officer")
        }
        for line in self.distribution_journal.lines.order_by("line_number", "id"):
            code = line.account_code or ""
            if code.split(".")[0] == "4199":
                continue
            amount = line.credit or ZERO
            if amount <= ZERO:
                continue
            self.allocations.append({
                "beneficiary": officer_by_code.get(code),
                "account_code": code,
                "account_name": line.account_name or "",
                "amount": amount,
            })

    def assess(self):
        """Run all Section 100A rules."""
        # If no trust workspace at all, fire advisory
        if self.trust_workspace is None:
            self.rules_fired.append("S100A-INCOMPLETE")
            self.finding_lines.append(
                "Section 100A assessment incomplete — Trust Tab not created. "
                "Complete Stages 1–4 for full risk analysis."
            )
            self.overall_severity = "ADVISORY"
            return self._build_assessment_dict()

        # If trust tab stages not complete, fire advisory
        if not self.trust_tab_complete:
            self.rules_fired.append("S100A-INCOMPLETE")
            self.finding_lines.append(
                "Section 100A assessment incomplete — Trust Tab Stages 1–4 "
                "not all completed. Complete remaining stages for full risk analysis."
            )

        # Run rules (even with partial data — report what we can)
        self._rule_s100a_01()
        self._rule_s100a_04()
        self._rule_s100a_05()
        self._note_retired_rules()
        self._report_unassessed()

        # Composite severity
        self.overall_severity = self._composite_severity()

        return self._build_assessment_dict()

    def _get_controller_marginal_rate(self):
        """Find the trust controller/principal's marginal tax rate."""
        from core.models import EntityRelationship

        # Find entities that are directors/trustees of this trust
        controller_rels = EntityRelationship.objects.filter(
            to_entity=self.entity,
            relationship_type__in=["trustee_of", "director_of"],
        ).select_related("from_entity")

        # Look for the controller's marginal rate in beneficiary profiles
        controller_rate = None
        for profile in self.beneficiary_profiles:
            if profile.marginal_rate and profile.marginal_rate > (controller_rate or ZERO):
                # Heuristic: the controller is typically the highest-rate individual
                # who is also a trustee/director
                for rel in controller_rels:
                    if (hasattr(profile.beneficiary, 'entity')
                            and profile.beneficiary.entity == rel.from_entity):
                        controller_rate = profile.marginal_rate
                        break

        # Fallback: use the highest marginal rate among profiles
        if controller_rate is None and self.beneficiary_profiles:
            rates = [p.marginal_rate for p in self.beneficiary_profiles if p.marginal_rate]
            if rates:
                controller_rate = max(rates)

        return controller_rate

    def _cannot_assess(self, exposure, summary):
        """Record an exposure this run could not evaluate.

        Returning early in silence is what made the module dangerous: the
        finding card reported a clean Section 100A assessment on trusts where
        nothing had been tested. Every early return that is a gap rather than a
        pass goes through here. ``_report_unassessed`` states them, and
        ``_composite_severity`` refuses to report CLEAR while any remain.

        *summary* is a short noun phrase naming the gap and why, not a
        sentence: they are joined into one line rather than listed separately,
        so that a trust which has simply distributed does not carry four
        near-identical findings.
        """
        self.unassessed.append(exposure)
        self._unassessed_summaries.append(summary)

    def _report_unassessed(self):
        """One line naming every gap, or nothing if there are none."""
        if not self._unassessed_summaries:
            return
        self.finding_lines.append(
            "Not assessed — " + "; ".join(self._unassessed_summaries) + ". "
            "These exposures were not tested and are not cleared; review them "
            "manually."
        )

    def _rule_s100a_01(self):
        """S100A-01: Distribution to Low-Tax Beneficiary.

        Comparing a beneficiary's marginal rate against the controller's needs
        both figures. Neither is recorded anywhere on the platform today --
        BeneficiaryProfile.marginal_rate is a manual field nobody fills in, and
        the controller is looked up through EntityRelationship, which has no
        rows. Rather than return in silence, say which input is missing: a rule
        that cannot run should not read as a rule that found nothing.
        """
        if not self.allocations:
            return

        profile_map = {
            str(p.beneficiary_id): p for p in self.beneficiary_profiles
        }
        material = [
            a for a in self.allocations
            if a["amount"] >= LOW_TAX_DISTRIBUTION_MIN
        ]
        if not material:
            return

        rated = [
            a for a in material
            if a["beneficiary"] is not None
            and (profile_map.get(str(a["beneficiary"].pk)) or None)
            and profile_map[str(a["beneficiary"].pk)].marginal_rate
        ]
        if not rated:
            self._cannot_assess(
                "low_tax_beneficiary",
                f"low-tax beneficiary, across {len(material)} material "
                f"distribution(s) (no beneficiary marginal rate is recorded)",
            )
            return

        controller_rate = self._get_controller_marginal_rate()
        if controller_rate is None:
            self._cannot_assess(
                "low_tax_beneficiary",
                "low-tax beneficiary (the trust controller could not be "
                "identified, so no marginal rate comparison is possible)",
            )
            return

        for alloc in material:
            officer = alloc["beneficiary"]
            if officer is None:
                continue
            profile = profile_map.get(str(officer.pk))
            if not profile or not profile.marginal_rate:
                continue

            rate_diff = controller_rate - profile.marginal_rate
            if rate_diff >= MARGINAL_RATE_DIFF:
                self.rules_fired.append("S100A-01")
                self.finding_lines.append(
                    f"Distribution of ${alloc['amount']:,.2f} to "
                    f"{officer.full_name} "
                    f"(marginal rate {profile.marginal_rate * 100:.1f}%) "
                    f"is {rate_diff * 100:.1f}% lower than the trust "
                    f"controller's rate ({controller_rate * 100:.1f}%). "
                    f"Pattern consistent with Section 100A risk."
                )
                break  # Only fire once — list all in finding card

    def _note_retired_rules(self):
        """State the two exposures this module does not assess.

        Circular money flow and unpaid present entitlements are the patterns
        Section 100A most often turns on. Dropping the rules without saying so
        would leave the finding card implying they were checked and found
        clean, which is the same fault the rules already had.
        """
        if not self.allocations:
            return
        self._cannot_assess(
            "circular_flow_and_upe",
            "circular money flow and unpaid present entitlement (beneficiaries "
            "are not linked to their own entities, which this platform does "
            "not yet record)",
        )

    def _rule_s100a_04(self):
        """S100A-04: is there a distribution, and can its timing be checked?

        This fired CRITICAL "resolution not confirmed" on every discretionary
        trust on the platform. Both its guards were broken: it required
        ``stage_6_status == "completed"``, but the Trust tab has five stages and
        nothing ever sets a sixth; and it fell back to
        ``workspace.confirmed_scenario``, which is None on every workspace
        because the live flow selects a TaxPlanningScenario and posts a journal.

        What can honestly be said is narrower. Whether a distribution was posted
        is knowable from the ledger. WHEN the trustee resolved is not: no
        resolution date is stored anywhere -- it exists only as transient wizard
        input at document-generation time, defaulted to year end. So this states
        the position and asks for the check to be made by a person, instead of
        asserting a breach it cannot evidence.
        """
        if self.distribution_journal is None:
            self.finding_lines.append(
                "No distribution has been posted for this year. Income not "
                "distributed by 30 June is assessed to the trustee at the top "
                "marginal rate under s 99A ITAA 1936."
            )
            return

        self.finding_lines.append(
            f"Distribution posted as {self.distribution_journal.reference_number}. "
            f"The resolution date is not recorded, so it cannot be verified "
            f"here — confirm the trustee resolved on or before "
            f"{self.fy.end_date:%d %B %Y}. A late resolution means the income is "
            f"assessed to the trustee under s 99A ITAA 1936."
        )

    def _rule_s100a_05(self):
        """S100A-05: Four-Factor Summary Assessment.

        Pulls data from S100A-01 through S100A-04 and presents the
        four-factor test.  This is a structured summary, not an
        automated detection rule.
        """
        from core.models import Section100AAssessment

        # Check existing per-beneficiary assessments
        if self.trust_workspace is None:
            return

        assessments = Section100AAssessment.objects.filter(
            trust_workspace=self.trust_workspace,
        )

        if not assessments.exists():
            # Stage 3 can be marked complete with no assessment recorded, and
            # is on every trust-year on the platform. Silence here reads as a
            # clean four-factor result on a test that was never performed.
            self._cannot_assess(
                "four_factor",
                "the four-factor test (the Stage 3 questionnaire has not been "
                "completed for any beneficiary)",
            )
            return

        red_count = assessments.filter(risk_rating="red").count()
        amber_count = assessments.filter(risk_rating="amber").count()

        if red_count > 0:
            self.rules_fired.append("S100A-05")
            self.finding_lines.append(
                f"Section 100A Four-Factor Assessment: {red_count} "
                f"beneficiary(ies) rated RED. The four-factor test "
                f"(arrangement, tax benefit, non-arm's length benefit, "
                f"purpose) indicates high risk of Section 100A application. "
                f"Manual review required."
            )
        elif amber_count > 0:
            self.rules_fired.append("S100A-05")
            self.finding_lines.append(
                f"Section 100A Four-Factor Assessment: {amber_count} "
                f"beneficiary(ies) rated AMBER. Some indicators of "
                f"Section 100A risk present. Review recommended."
            )

    # ------------------------------------------------------------------
    # Composite Severity
    # ------------------------------------------------------------------

    def _composite_severity(self):
        fired = set(self.rules_fired)

        if not fired:
            # CLEAR asserts "no Section 100A risk found", which is only true if
            # the exposures were actually tested. While any went unassessed the
            # honest answer is ADVISORY -- and today that means a trust which
            # distributed can never be cleared, because circular flow and UPE
            # cannot be tested at all. That is the correct reading of the
            # platform's position, not a defect.
            return "ADVISORY" if self.unassessed else "CLEAR"

        if fired == {"S100A-INCOMPLETE"}:
            return "ADVISORY"

        # S100A-05 with RED ratings → CRITICAL. This is the only CRITICAL
        # path left, and deliberately so: it rests on a judgement a person
        # actually recorded. S100A-02 (circular flow) is retired, and S100A-04
        # no longer asserts a late resolution it cannot evidence -- between
        # them they raised a CRITICAL on every discretionary trust regardless
        # of the facts.
        if "S100A-05" in fired:
            from core.models import Section100AAssessment
            if self.trust_workspace:
                red_count = Section100AAssessment.objects.filter(
                    trust_workspace=self.trust_workspace,
                    risk_rating="red",
                ).count()
                if red_count > 0:
                    return "CRITICAL"

        return "ADVISORY"

    # ------------------------------------------------------------------
    # Assessment dict & finding card
    # ------------------------------------------------------------------

    def _build_assessment_dict(self):
        return {
            "trust_tab_complete": self.trust_tab_complete,
            "beneficiary_count": len(self.beneficiary_profiles),
            "allocation_count": len(self.allocations),
            "unassessed": self.unassessed,
            "rules_fired": self.rules_fired,
            "overall_severity": self.overall_severity,
            "finding_lines": self.finding_lines,
        }

    def build_finding_card(self, assessment):
        entity_name = self.entity.entity_name
        year = self.fy.year_label

        description = f"**Section 100A Risk Assessment — {entity_name} {year}**\n\n"
        description += f"**Severity:** {self.overall_severity}\n\n"

        if not self.trust_tab_complete:
            description += (
                "**Note:** Trust Tab Stages 1–4 are not all completed. "
                "This assessment may be incomplete.\n\n"
            )

        description += "**Findings:**\n"
        for line in self.finding_lines:
            description += f"- {line}\n"

        description += (
            "\n**Four-Factor Test (s 100A ITAA 1936):**\n"
            "1. Does an agreement/arrangement exist?\n"
            "2. Did a beneficiary obtain a tax benefit?\n"
            "3. Did someone other than the beneficiary benefit?\n"
            "4. Was the arrangement entered into for the purpose of reducing tax?\n"
        )

        recommended_action = (
            "1. Review each flagged beneficiary distribution.\n"
            "2. Complete the Section 100A questionnaire for each beneficiary.\n"
            "3. Document the commercial rationale for the distribution pattern.\n"
            "4. Consider obtaining a private ruling if risk is assessed as high."
        )

        return {
            "title": f"Section 100A Risk Assessment — {entity_name} {year}",
            "description": description,
            "recommended_action": recommended_action,
            "legislation_ref": "ITAA 1936 s 100A, s 99A, TD 2022/11",
            "category": "COMPLIANCE",
            "calculated_values": {
                "trust_tab_complete": self.trust_tab_complete,
                "beneficiary_count": len(self.beneficiary_profiles),
                "rules_fired": self.rules_fired,
            },
        }
