"""
TPAR Obligations Cluster
=========================

Two-rule cluster for Taxable Payments Annual Report detection.
Replaces the old single TPAR rule (T2-40).

Rules:
    TPAR-01: Industry detection (ANZSIC code against reportable industries)
    TPAR-02: Contractor payment threshold (any contractor payments in
             TPAR industry → ADVISORY with amounts)

TPAR-reportable industries:
    Building & construction, cleaning, courier, IT, security, road freight

Reference:
    ATO TPAR reporting obligations
"""

import logging
from decimal import Decimal
from django.utils import timezone

from core.risk_modules.base import BaseDetectionModule, ZERO

logger = logging.getLogger(__name__)

# ATO Business Industry Codes (BIC) for TPAR-reportable industries
# These are the first 2-3 digits of the ATO NAT 1827 industry codes
TPAR_INDUSTRY_CODES = {
    # Building & Construction
    "301", "302", "303", "304", "305", "306", "307", "308", "309",
    "310", "311", "312", "313", "314", "315", "316", "317", "318",
    "319", "320", "321", "322", "323",
    # Cleaning
    "731",
    # Courier / Delivery
    "510", "511", "512",
    # IT
    "700", "701", "702",
    # Security
    "771",
    # Road Freight
    "461",
}

# Also match by industry description keywords
TPAR_INDUSTRY_KEYWORDS = {
    "building", "construction", "plumbing", "electrical", "carpentry",
    "painting", "roofing", "concreting", "landscaping", "demolition",
    "excavation", "tiling", "plastering", "bricklaying", "scaffolding",
    "cleaning", "courier", "delivery", "freight", "transport",
    "information technology", "it services", "software", "security",
    "guard", "surveillance",
}

_CONTRACTOR_KEYWORDS = {
    "contractor", "subcontractor", "sub-contractor", "subbie",
    "contract labour", "contract labor", "outsourced labour",
    "outsourced labor",
}


class TPARCluster(BaseDetectionModule):
    module_id = "cluster_tpar"
    display_name = "TPAR Obligations"
    entity_types = []  # All entity types
    assessment_model = None
    finding_category = "COMPLIANCE"

    def __init__(self, financial_year):
        super().__init__(financial_year)
        self.tb_data = None
        self.is_tpar_industry = False
        self.industry_code = ""
        self.industry_label = ""
        self.contractor_accounts = []
        self.total_contractors = ZERO

    def load_data(self):
        self.tb_data = self.load_trial_balance()
        self.industry_code = self.entity.industry or ""
        self._check_industry()
        self._scan_contractor_payments()

    def _check_industry(self):
        """Check if entity is in a TPAR-reportable industry."""
        code = self.industry_code.strip()

        if not code:
            return  # Will be handled by TPAR-01 as "unknown"

        # Check code prefix against TPAR codes
        for tpar_code in TPAR_INDUSTRY_CODES:
            if code.startswith(tpar_code):
                self.is_tpar_industry = True
                return

        # Also check entity industry description
        try:
            label = self.entity.get_industry_display() or ""
            self.industry_label = label
            label_lower = label.lower()
            if any(kw in label_lower for kw in TPAR_INDUSTRY_KEYWORDS):
                self.is_tpar_industry = True
        except Exception:
            pass

    def _scan_contractor_payments(self):
        """Scan TB for contractor payment accounts."""
        for line in self.tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            if any(kw in name_lower for kw in _CONTRACTOR_KEYWORDS):
                net = abs(line.effective_dr - line.effective_cr)
                if net > ZERO:
                    self.contractor_accounts.append({
                        "code": line.account_code,
                        "name": line.account_name,
                        "amount": net,
                    })
                    self.total_contractors += net

    def assess(self):
        if not self.tb_data or not self.tb_data["lines"]:
            return None

        self._rule_tpar_01()
        self._rule_tpar_02()

        if not self.rules_fired:
            self.overall_severity = "CLEAR"
        else:
            self.overall_severity = "ADVISORY"

        return self._build_assessment_dict()

    def _rule_tpar_01(self):
        """TPAR-01: Industry detection.

        Check entity's industry code against TPAR-reportable industries.
        If code not recorded, flag as ADVISORY.
        """
        if not self.industry_code:
            self.rules_fired.append("TPAR-01")
            self.finding_lines.append(
                "ANZSIC/BIC code not recorded on entity — unable to assess "
                "TPAR obligation. Record the industry code to enable "
                "automated TPAR detection."
            )
            return

        if self.is_tpar_industry:
            self.rules_fired.append("TPAR-01")
            label = self.industry_label or self.industry_code
            self.finding_lines.append(
                f"Entity is in a TPAR-reportable industry ({label}). "
                f"Taxable Payments Annual Report must be lodged by 28 August."
            )

    def _rule_tpar_02(self):
        """TPAR-02: Contractor payment threshold.

        If entity is in a TPAR industry and has any contractor payments,
        flag with total and account details.
        """
        if not self.is_tpar_industry:
            return

        if self.total_contractors <= ZERO:
            return

        self.rules_fired.append("TPAR-02")
        account_list = ", ".join(
            f"{a['name']} (${a['amount']:,.2f})"
            for a in sorted(self.contractor_accounts, key=lambda x: x["amount"], reverse=True)[:10]
        )
        self.finding_lines.append(
            f"Total contractor payments of ${self.total_contractors:,.2f} "
            f"across {len(self.contractor_accounts)} account(s) in a "
            f"TPAR-reportable industry. Ensure all payees are reported. "
            f"Accounts: {account_list}. "
            f"TPAR due date: 28 August."
        )

    def _build_assessment_dict(self):
        return {
            "industry_code": self.industry_code,
            "is_tpar_industry": self.is_tpar_industry,
            "total_contractors": str(self.total_contractors),
            "contractor_count": len(self.contractor_accounts),
            "rules_fired": self.rules_fired,
            "overall_severity": self.overall_severity,
        }

    def build_finding_card(self, assessment):
        entity_name = self.entity.entity_name
        year = self.fy.year_label

        description = f"**TPAR Obligations — {entity_name} {year}**\n\n"
        description += f"**Severity:** {self.overall_severity}\n\n"
        if self.is_tpar_industry:
            description += (
                f"**Industry:** {self.industry_label or self.industry_code} "
                f"(TPAR-reportable)\n\n"
            )
        description += "**Findings:**\n"
        for line in self.finding_lines:
            description += f"- {line}\n"

        return {
            "title": f"TPAR Obligations — {entity_name} {year}",
            "description": description,
            "recommended_action": (
                "1. Confirm entity is in a TPAR-reportable industry.\n"
                "2. Collate all contractor payment details (ABN, amounts, GST).\n"
                "3. Lodge TPAR by 28 August via Online Services for Business.\n"
                "4. Ensure all payees with ABN are included in the report."
            ),
            "legislation_ref": "TAA 1953 Sch 1 Div 396",
            "category": "COMPLIANCE",
            "calculated_values": assessment,
        }
