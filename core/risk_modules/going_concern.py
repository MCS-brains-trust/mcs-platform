"""
Going Concern Assessment Module
================================

6-rule detection module that correlates multiple financial health indicators
to reach a single going concern conclusion.

Rules:
    GC-01: Net Liability Position (CRITICAL)
    GC-02: Cash Position Assessment (CRITICAL/ADVISORY)
    GC-03: Revenue Decline Trajectory (ADVISORY, escalates)
    GC-04: Consecutive Losses (ADVISORY, escalates)
    GC-05: Working Capital Ratio (ADVISORY)
    GC-06: Director Loan Extraction Relative to Operations (ADVISORY, Company only)

Composite severity is determined by rule combinations, not individual rules.

Legislative Foundation:
    AASB 101.25–26, AASB 1060.14–15, APES 205, Corporations Act s 588G
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone

from core.risk_modules.base import BaseDetectionModule, ZERO

logger = logging.getLogger(__name__)

# Thresholds
REVENUE_DECLINE_THRESHOLD = Decimal("30")    # 30% decline triggers GC-03
DIRECTOR_EXTRACTION_PCT = Decimal("50")       # 50% of revenue triggers GC-06
CASH_OPERATING_PCT = Decimal("5")             # Cash < 5% of opex triggers GC-02
WORKING_CAPITAL_THRESHOLD = Decimal("1.0")    # Current ratio < 1.0

# Account keywords for classification
_CASH_KEYWORDS = {"cash", "bank", "cash at bank", "cash on hand", "cheque account",
                  "savings account", "operating account", "business account"}
_OVERDRAFT_KEYWORDS = {"overdraft", "line of credit", "credit facility"}
_LOAN_KEYWORDS = {"director loan", "shareholder loan", "loan to director",
                  "loan - director", "loan – director", "advance to director",
                  "loan receivable", "related party loan"}
_REVENUE_SECTIONS = {"revenue", "income", "sales", "turnover"}
_EXPENSE_SECTIONS = {"expenses", "cost_of_sales"}


class GoingConcernModule(BaseDetectionModule):
    module_id = "going_concern"
    display_name = "Going Concern Assessment"
    entity_types = []  # Applies to ALL entity types
    finding_category = "GOING_CONCERN"

    def __init__(self, financial_year):
        super().__init__(financial_year)
        from core.models import GoingConcernAssessment
        self.assessment_model = GoingConcernAssessment

        # Data holders
        self.tb_data = None
        self.ref_data = None

        # Computed values
        self.total_assets = ZERO
        self.total_liabilities = ZERO
        self.net_assets = ZERO
        self.current_assets = ZERO
        self.current_liabilities = ZERO
        self.cash_position = ZERO
        self.cy_revenue = ZERO
        self.py_revenue = ZERO
        self.revenue_decline_pct = None
        self.cy_net_result = ZERO
        self.py_net_result = ZERO
        self.working_capital_ratio = None
        self.director_loan_balance = ZERO
        self.director_extraction_pct = None
        self.is_reliant_on_director = False
        self.is_startup = False
        self.director_loan_credit = ZERO  # Director funding the entity

    def load_data(self):
        self.tb_data = self.load_trial_balance()
        self.ref_data = self.load_reference_data()
        self._classify_balances()

    def _derive_section(self, mapped_line_item):
        """Derive section from mapped line item."""
        if mapped_line_item is None:
            return ""
        fs = getattr(mapped_line_item, 'financial_statement', '')
        ss = (getattr(mapped_line_item, 'statement_section', '') or '').lower()
        if fs == 'balance_sheet':
            if 'asset' in ss:
                return 'assets'
            elif 'liabilit' in ss:
                return 'liabilities'
            return 'assets'
        if fs == 'equity':
            return 'equity'
        if fs == 'income_statement':
            if any(kw in ss for kw in _REVENUE_SECTIONS):
                return 'revenue'
            elif any(kw in ss for kw in _EXPENSE_SECTIONS):
                return 'expenses'
            return 'expenses'
        return ''

    def _classify_balances(self):
        """Scan TB and classify into assets, liabilities, revenue, expenses."""
        cy_expenses = ZERO

        for line in self.tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            net = line.effective_dr - line.effective_cr
            prior_net = (getattr(line, 'prior_debit', None) or ZERO) - (getattr(line, 'prior_credit', None) or ZERO)

            section = self._derive_section(line.mapped_line_item)

            # --- Balance Sheet ---
            if section == "assets":
                self.total_assets += net
                if any(kw in name_lower for kw in {"cash", "bank", "receivable",
                       "inventory", "stock", "prepaid", "current", "trade debtor"}):
                    self.current_assets += net

                # Cash accounts
                if any(kw in name_lower for kw in _CASH_KEYWORDS):
                    self.cash_position += net

                # Director loan (debit = extracted, credit = funding)
                if any(kw in name_lower for kw in _LOAN_KEYWORDS):
                    if net > ZERO:
                        self.director_loan_balance += net
                    else:
                        self.director_loan_credit += abs(net)

            elif section == "liabilities":
                abs_net = abs(net)
                self.total_liabilities += abs_net
                if any(kw in name_lower for kw in {"payable", "creditor", "gst",
                       "payg", "provision", "accrued", "current", "overdraft"}):
                    self.current_liabilities += abs_net

                # Overdraft reduces cash position
                if any(kw in name_lower for kw in _OVERDRAFT_KEYWORDS):
                    self.cash_position -= abs_net

            # --- Income Statement ---
            elif section == "revenue":
                self.cy_revenue += abs(net)
                self.py_revenue += abs(prior_net)

            elif section == "expenses":
                cy_expenses += abs(net)
                # Prior year expenses for PY net result
                pass

        # Net assets
        self.net_assets = self.total_assets - self.total_liabilities

        # Net result (simplified: revenue - expenses from TB totals)
        self.cy_net_result = self.cy_revenue - cy_expenses

        # PY net result from TB totals if available
        py_expenses = ZERO
        for line in self.tb_data["lines"]:
            section = self._derive_section(line.mapped_line_item)
            if section == "expenses":
                prior_net = (getattr(line, 'prior_debit', None) or ZERO) - (getattr(line, 'prior_credit', None) or ZERO)
                py_expenses += abs(prior_net)
        self.py_net_result = self.py_revenue - py_expenses

        # Revenue decline
        if self.py_revenue > ZERO:
            self.revenue_decline_pct = (
                (self.py_revenue - self.cy_revenue) / self.py_revenue * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

        # Working capital ratio
        if self.current_liabilities > ZERO:
            self.working_capital_ratio = (
                self.current_assets / self.current_liabilities
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Director extraction percentage
        if self.cy_revenue > ZERO:
            self.director_extraction_pct = (
                self.director_loan_balance / self.cy_revenue * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

        # Director reliance: cash negative but director loan credit is funding
        if self.cash_position <= ZERO and self.director_loan_credit > ZERO:
            self.is_reliant_on_director = True

        # Startup detection: check if entity has < 2 years of FY data
        self._check_startup()

    def _check_startup(self):
        """Check if entity is a startup (< 2 financial years of data)."""
        from core.models import FinancialYear
        fy_count = FinancialYear.objects.filter(entity=self.entity).count()
        self.is_startup = fy_count <= 2

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def assess(self):
        if not self.tb_data or not self.tb_data["lines"]:
            return None

        # Execute rules
        self._rule_gc_01()
        self._rule_gc_02()
        self._rule_gc_03()
        self._rule_gc_04()
        self._rule_gc_05()
        self._rule_gc_06()

        # Composite severity
        self.overall_severity = self._composite_severity()

        return self._build_assessment_dict()

    def _rule_gc_01(self):
        """GC-01: Net Liability Position — total liabilities exceed total assets."""
        if self.net_assets < ZERO:
            self.rules_fired.append("GC-01")
            self.finding_lines.append(
                f"Net liability position of ${abs(self.net_assets):,.2f}. "
                f"Total assets ${self.total_assets:,.2f} are exceeded by "
                f"total liabilities ${self.total_liabilities:,.2f}. "
                f"Going concern disclosure required under AASB 101.25."
            )

    def _rule_gc_02(self):
        """GC-02: Cash Position Assessment."""
        if self.cash_position < ZERO:
            self.rules_fired.append("GC-02")
            msg = (
                f"Negative cash position of ${self.cash_position:,.2f} "
                f"(including overdraft facilities)."
            )
            if self.is_reliant_on_director:
                msg += (
                    f" Entity appears reliant on director funding. "
                    f"Cash position excluding director support is "
                    f"${self.cash_position:,.2f}."
                )
            self.finding_lines.append(msg)
        elif self.cy_revenue > ZERO:
            # Low cash: < 5% of operating expenses
            operating_expenses = self.cy_revenue - self.cy_net_result
            if operating_expenses > ZERO:
                cash_pct = (self.cash_position / operating_expenses * Decimal("100"))
                if cash_pct < CASH_OPERATING_PCT:
                    self.rules_fired.append("GC-02")
                    self.finding_lines.append(
                        f"Low cash position: ${self.cash_position:,.2f} represents "
                        f"only {cash_pct:.1f}% of annual operating expenses."
                    )

    def _rule_gc_03(self):
        """GC-03: Revenue Decline Trajectory — >30% decline from PY."""
        if self.revenue_decline_pct is not None and self.revenue_decline_pct > REVENUE_DECLINE_THRESHOLD:
            self.rules_fired.append("GC-03")
            msg = (
                f"Revenue declined {self.revenue_decline_pct}% year-on-year "
                f"(PY: ${self.py_revenue:,.2f} → CY: ${self.cy_revenue:,.2f})."
            )
            # Additional check: revenue decline + director loan debit
            if self.director_loan_balance > ZERO:
                msg += (
                    f" Director loan debit balance of ${self.director_loan_balance:,.2f} "
                    f"indicates funds being extracted despite declining income."
                )
            self.finding_lines.append(msg)

    def _rule_gc_04(self):
        """GC-04: Consecutive Losses — net loss in both CY and PY."""
        if self.cy_net_result < ZERO and self.py_net_result < ZERO:
            self.rules_fired.append("GC-04")
            qualifier = ""
            if self.is_startup:
                qualifier = " (Note: entity appears to be in startup phase — early losses may be expected.)"
            self.finding_lines.append(
                f"Consecutive losses: CY loss ${abs(self.cy_net_result):,.2f}, "
                f"PY loss ${abs(self.py_net_result):,.2f}.{qualifier}"
            )

    def _rule_gc_05(self):
        """GC-05: Working Capital Ratio < 1.0."""
        if self.current_liabilities == ZERO:
            return  # Ratio undefined, not a concern

        if self.working_capital_ratio is not None and self.working_capital_ratio < WORKING_CAPITAL_THRESHOLD:
            self.rules_fired.append("GC-05")
            self.finding_lines.append(
                f"Working capital ratio of {self.working_capital_ratio} "
                f"(current assets ${self.current_assets:,.2f} / "
                f"current liabilities ${self.current_liabilities:,.2f}). "
                f"Entity may be unable to meet short-term obligations."
            )
        elif self.working_capital_ratio is None:
            # Could not compute — note for Eva
            self.finding_lines.append(
                "Working capital ratio could not be computed — "
                "current/non-current classification required."
            )

    def _rule_gc_06(self):
        """GC-06: Director Loan Extraction > 50% of Revenue (Company only)."""
        if self.entity.entity_type != "company":
            return

        if self.director_loan_balance <= ZERO:
            return

        triggered = False
        if (self.director_extraction_pct is not None
                and self.director_extraction_pct > DIRECTOR_EXTRACTION_PCT):
            triggered = True

        if self.cy_net_result > ZERO and self.director_loan_balance > self.cy_net_result:
            triggered = True

        if triggered:
            self.rules_fired.append("GC-06")
            msg = (
                f"Director loan debit balance of ${self.director_loan_balance:,.2f} "
            )
            if self.director_extraction_pct is not None:
                msg += f"represents {self.director_extraction_pct}% of revenue. "
            msg += (
                "The trajectory of director extractions relative to operations "
                "is unsustainable. See also Division 7A Assessment for compliance "
                "implications of this loan balance."
            )
            self.finding_lines.append(msg)

    # ------------------------------------------------------------------
    # Composite Severity Logic (Section 4.3 of spec)
    # ------------------------------------------------------------------

    def _composite_severity(self):
        """Determine overall severity from rule combinations."""
        fired = set(self.rules_fired)

        if not fired:
            return "CLEAR"

        # GC-01 alone → CRITICAL
        if "GC-01" in fired:
            return "CRITICAL"

        # GC-02 + GC-03 → CRITICAL
        if "GC-02" in fired and "GC-03" in fired:
            return "CRITICAL"

        # GC-04 + GC-05 → CRITICAL
        if "GC-04" in fired and "GC-05" in fired:
            return "CRITICAL"

        # GC-02 + GC-06 → CRITICAL
        if "GC-02" in fired and "GC-06" in fired:
            return "CRITICAL"

        # Any 3+ rules → CRITICAL
        if len(fired) >= 3:
            return "CRITICAL"

        # Any single ADVISORY rule
        return "ADVISORY"

    # ------------------------------------------------------------------
    # Assessment dict & model persistence
    # ------------------------------------------------------------------

    def _build_assessment_dict(self):
        return {
            "net_assets": self.net_assets,
            "cash_position": self.cash_position,
            "cy_revenue": self.cy_revenue,
            "py_revenue": self.py_revenue,
            "revenue_decline_pct": self.revenue_decline_pct,
            "cy_net_result": self.cy_net_result,
            "py_net_result": self.py_net_result,
            "working_capital_ratio": self.working_capital_ratio,
            "director_loan_balance": self.director_loan_balance,
            "director_extraction_pct": self.director_extraction_pct,
            "is_reliant_on_director": self.is_reliant_on_director,
            "is_startup": self.is_startup,
            "rules_fired": self.rules_fired,
            "overall_severity": self.overall_severity,
        }

    def _build_model_kwargs(self, assessment):
        return {
            "assessed_at": timezone.now(),
            "net_assets": assessment["net_assets"],
            "cash_position": assessment["cash_position"],
            "cy_revenue": assessment["cy_revenue"],
            "py_revenue": assessment["py_revenue"],
            "revenue_decline_pct": assessment.get("revenue_decline_pct"),
            "cy_net_result": assessment["cy_net_result"],
            "py_net_result": assessment["py_net_result"],
            "working_capital_ratio": assessment.get("working_capital_ratio"),
            "director_loan_balance": assessment["director_loan_balance"],
            "director_extraction_pct": assessment.get("director_extraction_pct"),
            "is_reliant_on_director": assessment["is_reliant_on_director"],
            "is_startup": assessment["is_startup"],
            "rules_fired": assessment["rules_fired"],
            "overall_severity": assessment["overall_severity"],
        }

    def build_finding_card(self, assessment):
        """Build consolidated going concern finding card."""
        entity_name = self.entity.entity_name
        year = self.fy.year_label
        severity = self.overall_severity

        # Summary
        summary_parts = []
        if "GC-01" in self.rules_fired:
            summary_parts.append(
                f"a net liability position of ${abs(self.net_assets):,.2f}"
            )
        if "GC-02" in self.rules_fired:
            summary_parts.append(
                f"{'negative' if self.cash_position < ZERO else 'low'} cash of "
                f"${self.cash_position:,.2f}"
            )
        if "GC-03" in self.rules_fired:
            summary_parts.append(
                f"revenue declined {self.revenue_decline_pct}% YoY"
            )
        if "GC-04" in self.rules_fired:
            summary_parts.append("consecutive losses in CY and PY")
        if "GC-05" in self.rules_fired:
            summary_parts.append(
                f"working capital ratio of {self.working_capital_ratio}"
            )
        if "GC-06" in self.rules_fired:
            summary_parts.append(
                f"director extractions of ${self.director_loan_balance:,.2f}"
            )

        summary = (
            f"{entity_name} has {', '.join(summary_parts)}. "
            f"{'Multiple indicators suggest the going concern basis requires assessment.'}"
            if len(summary_parts) > 1
            else f"{entity_name} has {summary_parts[0]}."
        )

        # Description: combine all finding lines
        description = f"**Going Concern Assessment — {entity_name} {year}**\n\n"
        description += f"**Severity:** {severity}\n\n"
        description += f"**Summary:** {summary}\n\n"
        description += "**Indicators:**\n"
        for line in self.finding_lines:
            description += f"- {line}\n"

        if self.is_reliant_on_director:
            description += (
                "\n**Director Reliance:** This entity appears dependent on director "
                "funding. Without continued director support, the entity cannot "
                "meet its obligations.\n"
            )

        if severity == "CRITICAL":
            description += (
                "\n**Disclosure Requirement:** AASB 101.25 requires disclosure of "
                "material uncertainties relating to going concern. The compilation "
                "report must reference this assessment.\n"
            )

        # Recommended action
        recommended_action = (
            "1. Discuss going concern position with the director.\n"
            "2. Obtain written confirmation of director's intention to support.\n"
            "3. Include going concern note in financial statements.\n"
            "4. Consider whether director declaration wording needs qualification."
        )

        return {
            "title": f"Going Concern Assessment — {entity_name} {year}",
            "description": description,
            "recommended_action": recommended_action,
            "legislation_ref": "AASB 101.25-26, AASB 1060.14-15, APES 205, Corporations Act s 588G",
            "category": "GOING_CONCERN",
            "calculated_values": {
                "net_assets": str(self.net_assets),
                "cash_position": str(self.cash_position),
                "cy_revenue": str(self.cy_revenue),
                "py_revenue": str(self.py_revenue),
                "revenue_decline_pct": str(self.revenue_decline_pct) if self.revenue_decline_pct else None,
                "cy_net_result": str(self.cy_net_result),
                "py_net_result": str(self.py_net_result),
                "working_capital_ratio": str(self.working_capital_ratio) if self.working_capital_ratio else None,
                "director_loan_balance": str(self.director_loan_balance),
                "director_extraction_pct": str(self.director_extraction_pct) if self.director_extraction_pct else None,
                "is_reliant_on_director": self.is_reliant_on_director,
                "is_startup": self.is_startup,
                "rules_fired": self.rules_fired,
            },
        }
