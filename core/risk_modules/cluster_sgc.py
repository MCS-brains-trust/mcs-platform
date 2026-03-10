"""
Superannuation Guarantee Compliance Cluster
=============================================

Three-rule calculation-based cluster for SGC compliance.
Replaces the old single SGC shortfall rule (T2-20).

Rules:
    SGC-01: SG rate shortfall (super < wages × SG rate × 0.95)
    SGC-02: Contractor SG exposure (>$20K regular payments)
    SGC-03: SG charge risk (shortfall > $5K, calculate charge exposure)

Reference Data:
    sg_rate — current SG rate from RiskReferenceData (12% for FY2025-26)
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone

from core.risk_modules.base import BaseDetectionModule, ZERO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ATO Super Guarantee rate schedule — source:
# https://www.ato.gov.au/tax-rates-and-codes/key-superannuation-rates-and-thresholds/super-guarantee
# Key = calendar year of 30 June end date (i.e. FY ending 30 Jun YYYY)
# ---------------------------------------------------------------------------
_SG_RATE_BY_YEAR = {
    2013: Decimal("0.0900"),  # 1 Jul 2012 – 30 Jun 2013
    2014: Decimal("0.0925"),  # 1 Jul 2013 – 30 Jun 2014
    2015: Decimal("0.0950"),  # 1 Jul 2014 – 30 Jun 2015
    2016: Decimal("0.0950"),
    2017: Decimal("0.0950"),
    2018: Decimal("0.0950"),
    2019: Decimal("0.0950"),
    2020: Decimal("0.0950"),
    2021: Decimal("0.0950"),
    2022: Decimal("0.1000"),  # 1 Jul 2021 – 30 Jun 2022
    2023: Decimal("0.1050"),  # 1 Jul 2022 – 30 Jun 2023
    2024: Decimal("0.1100"),  # 1 Jul 2023 – 30 Jun 2024
    2025: Decimal("0.1150"),  # 1 Jul 2024 – 30 Jun 2025
    2026: Decimal("0.1200"),  # 1 Jul 2025 – 30 Jun 2026
    2027: Decimal("0.1200"),  # 1 Jul 2026 onwards
}
DEFAULT_SG_RATE = Decimal("0.12")  # Fallback for future years not yet in table


def _get_sg_rate_for_fy(financial_year):
    """Return the correct ATO SG rate for the given FinancialYear.

    Uses the end_date year to determine which rate applies.
    Falls back to DEFAULT_SG_RATE if the year is not in the table.
    """
    if financial_year.end_date:
        end_year = financial_year.end_date.year
        return _SG_RATE_BY_YEAR.get(end_year, DEFAULT_SG_RATE)
    # Try to parse from year_label e.g. "FY2024"
    label = financial_year.year_label or ""
    for part in label.split():
        digits = "".join(c for c in part if c.isdigit())
        if len(digits) == 4:
            return _SG_RATE_BY_YEAR.get(int(digits), DEFAULT_SG_RATE)
    return DEFAULT_SG_RATE


SG_TOLERANCE = Decimal("0.95")     # 5% tolerance for timing
CONTRACTOR_THRESHOLD = Decimal("20000")
SG_CHARGE_THRESHOLD = Decimal("5000")
SG_CHARGE_MULTIPLIER = Decimal("1.25")  # Nominal interest component

_WAGES_KEYWORDS = {
    "wages", "salary", "salaries", "gross pay", "payroll",
    "employee costs", "staff costs", "labour", "labor",
}
_SUPER_KEYWORDS = {
    "superannuation", "super guarantee", "super contribution",
    "sgc", "employee super", "super expense",
}
_CONTRACTOR_KEYWORDS = {
    "contractor", "subcontractor", "sub-contractor", "subbie",
    "contract labour", "contract labor", "outsourced",
}


class SGCCluster(BaseDetectionModule):
    module_id = "cluster_sgc"
    display_name = "Superannuation Guarantee Compliance"
    entity_types = []  # All entity types
    assessment_model = None
    finding_category = "COMPLIANCE"

    def __init__(self, financial_year):
        super().__init__(financial_year)
        self.tb_data = None
        self.ref_data = None
        self.total_wages = ZERO
        self.total_super = ZERO
        self.total_contractors = ZERO
        self.sg_rate = DEFAULT_SG_RATE
        self.expected_super = ZERO
        self.shortfall = ZERO
        self.contractor_accounts = []

    def load_data(self):
        self.tb_data = self.load_trial_balance()
        self.ref_data = self.load_reference_data()

        # Determine the correct SG rate from the ATO schedule for this FY.
        # RiskReferenceData can still override if an explicit "sg_rate" key
        # is present (e.g. for a non-standard year), but the ATO table is
        # the primary source of truth.
        self.sg_rate = _get_sg_rate_for_fy(self.fy)

        # Allow RiskReferenceData override (explicit "sg_rate" key only)
        sg_rate_str = self.ref_data.get("sg_rate")
        if sg_rate_str:
            try:
                rate = Decimal(str(sg_rate_str))
                if rate > Decimal("1"):
                    rate = rate / Decimal("100")
                self.sg_rate = rate
            except Exception:
                pass

        self._classify_accounts()

    def _classify_accounts(self):
        """Scan TB for wages, super, and contractor accounts."""
        for line in self.tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            net = abs(line.effective_dr - line.effective_cr)

            if any(kw in name_lower for kw in _WAGES_KEYWORDS):
                self.total_wages += net
            elif any(kw in name_lower for kw in _SUPER_KEYWORDS):
                self.total_super += net
            elif any(kw in name_lower for kw in _CONTRACTOR_KEYWORDS):
                self.total_contractors += net
                self.contractor_accounts.append({
                    "code": line.account_code,
                    "name": line.account_name,
                    "amount": net,
                })

        self.expected_super = (
            self.total_wages * self.sg_rate
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def assess(self):
        if not self.tb_data or not self.tb_data["lines"]:
            return None

        # Only assess if there are wages (otherwise no SG obligation)
        if self.total_wages <= ZERO:
            self.overall_severity = "CLEAR"
            return self._build_assessment_dict()

        self._rule_sgc_01()
        self._rule_sgc_02()
        self._rule_sgc_03()

        if not self.rules_fired:
            self.overall_severity = "CLEAR"
        elif "SGC-03" in self.rules_fired:
            self.overall_severity = "CRITICAL"
        else:
            self.overall_severity = "ADVISORY"

        return self._build_assessment_dict()

    def _rule_sgc_01(self):
        """SGC-01: SG rate shortfall.

        Super < wages × SG rate × 0.95 (5% tolerance for timing).
        """
        minimum_expected = (
            self.expected_super * SG_TOLERANCE
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if self.total_super < minimum_expected:
            self.shortfall = self.expected_super - self.total_super
            self.rules_fired.append("SGC-01")
            self.finding_lines.append(
                f"Superannuation shortfall detected. "
                f"Total wages: ${self.total_wages:,.2f}. "
                f"Expected super at {self.sg_rate * 100:.1f}%: "
                f"${self.expected_super:,.2f}. "
                f"Recorded super: ${self.total_super:,.2f}. "
                f"Shortfall: ${self.shortfall:,.2f} "
                f"(after 5% timing tolerance)."
            )

    def _rule_sgc_02(self):
        """SGC-02: Contractor SG exposure.

        Contractor payments > $20,000 where payment pattern suggests
        employment-like arrangement.
        """
        material_contractors = [
            a for a in self.contractor_accounts
            if a["amount"] > CONTRACTOR_THRESHOLD
        ]

        if material_contractors:
            self.rules_fired.append("SGC-02")
            for c in material_contractors:
                self.finding_lines.append(
                    f"Contractor payment of ${c['amount']:,.2f} to "
                    f"'{c['name']}' exceeds $20,000. If payment pattern "
                    f"suggests employment-like arrangement (regular, recurring), "
                    f"SG obligations may apply."
                )

    def _rule_sgc_03(self):
        """SGC-03: SG charge risk.

        If SGC-01 fired and shortfall > $5,000, calculate SG charge exposure.
        """
        if "SGC-01" not in self.rules_fired:
            return

        if self.shortfall > SG_CHARGE_THRESHOLD:
            charge_exposure = (
                self.shortfall * SG_CHARGE_MULTIPLIER
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            self.rules_fired.append("SGC-03")
            self.finding_lines.append(
                f"SG charge exposure: shortfall of ${self.shortfall:,.2f} "
                f"exceeds $5,000 threshold. Estimated SG charge (including "
                f"nominal interest component): ${charge_exposure:,.2f}. "
                f"Lodge SG charge statement to avoid additional penalties."
            )

    def _build_assessment_dict(self):
        return {
            "total_wages": str(self.total_wages),
            "total_super": str(self.total_super),
            "expected_super": str(self.expected_super),
            "shortfall": str(self.shortfall),
            "sg_rate": str(self.sg_rate),
            "total_contractors": str(self.total_contractors),
            "contractor_count": len(self.contractor_accounts),
            "rules_fired": self.rules_fired,
            "overall_severity": self.overall_severity,
        }

    def build_finding_card(self, assessment):
        entity_name = self.entity.entity_name
        year = self.fy.year_label

        description = f"**Superannuation Guarantee Compliance — {entity_name} {year}**\n\n"
        description += f"**Severity:** {self.overall_severity}\n\n"
        description += (
            f"**SG Rate:** {self.sg_rate * 100:.1f}% | "
            f"**Total Wages:** ${self.total_wages:,.2f} | "
            f"**Expected Super:** ${self.expected_super:,.2f} | "
            f"**Recorded Super:** ${self.total_super:,.2f}\n\n"
        )
        description += "**Findings:**\n"
        for line in self.finding_lines:
            description += f"- {line}\n"

        return {
            "title": f"Superannuation Guarantee — {entity_name} {year}",
            "description": description,
            "recommended_action": (
                "1. Reconcile superannuation payments against payroll records.\n"
                "2. Verify all eligible employees received correct SG contributions.\n"
                "3. If shortfall confirmed, lodge SG charge statement by due date.\n"
                "4. Review contractor arrangements for employment-like characteristics."
            ),
            "legislation_ref": "SG Act 1992, SG (Administration) Act 1992",
            "category": "COMPLIANCE",
            "calculated_values": assessment,
        }
