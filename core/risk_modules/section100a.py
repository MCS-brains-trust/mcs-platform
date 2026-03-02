"""
Section 100A Risk Assessment Module
====================================

5-rule detection module for Section 100A reimbursement agreement risk
on trust entities.  Reads data from the Trust Tab (TrustWorkspace,
BeneficiaryProfile, TrustDistribution, BeneficiaryAllocation) and
cross-references entity relationships.

Rules:
    S100A-01: Distribution to Low-Tax Beneficiary (ADVISORY)
    S100A-02: Circular Money Flow (CRITICAL)
    S100A-03: UPE to Related Entity (ADVISORY)
    S100A-04: Resolution Date Compliance (CRITICAL)
    S100A-05: Four-Factor Summary Assessment (ADVISORY → CRITICAL)

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
CIRCULAR_FLOW_PCT = Decimal("50")             # 50% return flow threshold


class Section100AModule(BaseDetectionModule):
    module_id = "section100a"
    display_name = "Section 100A Risk Assessment"
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
        self.distribution = None
        self.trust_tab_complete = False

    def should_run(self):
        """Only run for trust entities."""
        return self.entity.entity_type == "trust"

    def load_data(self):
        """Load Trust Tab data."""
        from core.models import TrustWorkspace, TrustDistribution

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

        # Load distribution data
        try:
            self.distribution = TrustDistribution.objects.get(
                financial_year=self.fy,
            )
            self.allocations = list(
                self.distribution.allocations.select_related("beneficiary").all()
            )
        except TrustDistribution.DoesNotExist:
            self.distribution = None

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
        self._rule_s100a_02()
        self._rule_s100a_03()
        self._rule_s100a_04()
        self._rule_s100a_05()

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

    def _rule_s100a_01(self):
        """S100A-01: Distribution to Low-Tax Beneficiary."""
        if not self.allocations or not self.beneficiary_profiles:
            return

        controller_rate = self._get_controller_marginal_rate()
        if controller_rate is None:
            return

        profile_map = {
            str(p.beneficiary_id): p for p in self.beneficiary_profiles
        }

        for alloc in self.allocations:
            # Calculate allocated amount
            if alloc.fixed_amount and alloc.fixed_amount > ZERO:
                amount = alloc.fixed_amount
            elif self.distribution and alloc.percentage > ZERO:
                amount = (
                    self.distribution.distributable_income
                    * alloc.percentage / Decimal("100")
                )
            else:
                continue

            if amount < LOW_TAX_DISTRIBUTION_MIN:
                continue

            # Find beneficiary's profile
            profile = profile_map.get(str(alloc.beneficiary_id))
            if not profile or not profile.marginal_rate:
                continue

            rate_diff = controller_rate - profile.marginal_rate
            if rate_diff >= MARGINAL_RATE_DIFF:
                self.rules_fired.append("S100A-01")
                ben_name = alloc.beneficiary.full_name if hasattr(alloc.beneficiary, 'full_name') else str(alloc.beneficiary)
                self.finding_lines.append(
                    f"Distribution of ${amount:,.2f} to {ben_name} "
                    f"(marginal rate {profile.marginal_rate * 100:.1f}%) "
                    f"is {rate_diff * 100:.1f}% lower than the trust "
                    f"controller's rate ({controller_rate * 100:.1f}%). "
                    f"Pattern consistent with Section 100A risk."
                )
                break  # Only fire once — list all in finding card

    def _rule_s100a_02(self):
        """S100A-02: Circular Money Flow — funds flow back to controller."""
        if not self.allocations:
            return

        from core.models import EntityRelationship

        # Get entities related to the trust controller
        controller_entities = set()
        controller_rels = EntityRelationship.objects.filter(
            to_entity=self.entity,
            relationship_type__in=["trustee_of", "director_of"],
        )
        for rel in controller_rels:
            controller_entities.add(rel.from_entity_id)
            # Also add entities the controller controls
            sub_rels = EntityRelationship.objects.filter(
                from_entity=rel.from_entity,
                relationship_type__in=["director_of", "shareholder_of"],
            )
            for sub_rel in sub_rels:
                controller_entities.add(sub_rel.to_entity_id)

        if not controller_entities:
            return

        # Check if any beneficiary is related to the controller AND
        # has payments flowing back (via TB inter-entity accounts)
        tb_data = self.load_trial_balance()
        for alloc in self.allocations:
            if not hasattr(alloc.beneficiary, 'entity'):
                continue

            ben_entity = getattr(alloc.beneficiary, 'entity', None)
            if ben_entity is None:
                continue

            # Calculate allocated amount
            if alloc.fixed_amount and alloc.fixed_amount > ZERO:
                amount = alloc.fixed_amount
            elif self.distribution and alloc.percentage > ZERO:
                amount = (
                    self.distribution.distributable_income
                    * alloc.percentage / Decimal("100")
                )
            else:
                continue

            if amount <= ZERO:
                continue

            # Check for return flows in TB (inter-entity payables/receivables)
            return_flow = ZERO
            for line in tb_data["lines"]:
                name_lower = (line.account_name or "").lower()
                # Look for accounts referencing the beneficiary entity
                ben_name_lower = (ben_entity.entity_name or "").lower()
                if ben_name_lower and ben_name_lower in name_lower:
                    net = line.effective_dr - line.effective_cr
                    if net < ZERO:  # Credit = money flowing back
                        return_flow += abs(net)

            if return_flow > ZERO and amount > ZERO:
                return_pct = (return_flow / amount * Decimal("100"))
                if return_pct >= CIRCULAR_FLOW_PCT:
                    self.rules_fired.append("S100A-02")
                    self.finding_lines.append(
                        f"Circular money flow detected: ${return_flow:,.2f} "
                        f"({return_pct:.0f}% of distribution) flowed back from "
                        f"{ben_entity.entity_name} to trust-related entities. "
                        f"This is the primary 'reimbursement agreement' pattern "
                        f"targeted by the ATO under Section 100A."
                    )
                    return  # Fire once

    def _rule_s100a_03(self):
        """S100A-03: UPE to Related Entity — distribution unpaid."""
        if not self.allocations:
            return

        from core.models import EntityRelationship

        for alloc in self.allocations:
            if not hasattr(alloc.beneficiary, 'entity'):
                continue

            ben_entity = getattr(alloc.beneficiary, 'entity', None)
            if ben_entity is None:
                continue

            # Calculate allocated amount
            if alloc.fixed_amount and alloc.fixed_amount > ZERO:
                amount = alloc.fixed_amount
            elif self.distribution and alloc.percentage > ZERO:
                amount = (
                    self.distribution.distributable_income
                    * alloc.percentage / Decimal("100")
                )
            else:
                continue

            if amount <= ZERO:
                continue

            # Check if the beneficiary entity has a receivable from the trust
            # (i.e. distribution recorded but not paid = UPE)
            tb_data = self.load_trial_balance()
            trust_name_lower = self.entity.entity_name.lower()
            has_receivable = False
            for line in tb_data["lines"]:
                name_lower = (line.account_name or "").lower()
                if trust_name_lower in name_lower:
                    net = line.effective_dr - line.effective_cr
                    if net > ZERO:  # Debit = receivable from trust
                        has_receivable = True
                        break

            if has_receivable:
                self.rules_fired.append("S100A-03")
                msg = (
                    f"Distribution to {ben_entity.entity_name} of "
                    f"${amount:,.2f} appears unpaid (UPE). "
                    f"The beneficiary has not received economic benefit."
                )
                # Cross-module link for companies
                if ben_entity.entity_type == "company":
                    msg += (
                        f" See Division 7A Assessment on "
                        f"{ben_entity.entity_name} for loan compliance."
                    )
                self.finding_lines.append(msg)
                return  # Fire once

    def _rule_s100a_04(self):
        """S100A-04: Resolution Date Compliance."""
        # Check if trust workspace has stage 6 (Documents) data
        # Resolution date would be in the trust workspace or distribution
        if self.trust_workspace is None:
            return

        # The trust workspace stage_6 is "Documents" — check if completed
        if self.trust_workspace.stage_6_status != "completed":
            # Check if we can determine resolution date from confirmed scenario
            if self.trust_workspace.confirmed_scenario is None:
                self.rules_fired.append("S100A-04")
                self.finding_lines.append(
                    "Trust distribution resolution not confirmed. "
                    "Ensure resolution was made on or before 30 June of the "
                    "income year. A late resolution means income may be "
                    "assessed to the trustee at the top marginal rate "
                    "under s 99A ITAA 1936."
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
            return "CLEAR"

        if fired == {"S100A-INCOMPLETE"}:
            return "ADVISORY"

        # S100A-02 (circular flow) → CRITICAL
        if "S100A-02" in fired:
            return "CRITICAL"

        # S100A-04 (late resolution) → CRITICAL
        if "S100A-04" in fired:
            return "CRITICAL"

        # S100A-05 with RED ratings → CRITICAL
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
