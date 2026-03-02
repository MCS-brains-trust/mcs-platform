"""
Related Party Transactions Cluster (AASB 124)
==============================================

Three-rule cluster that shares a single related-party data scan.
Replaces the old single related party rule (T2-30).

Rules:
    RP-01: Inter-entity balance detection
    RP-02: KMP transaction detection (>$5,000 per KMP)
    RP-03: Arm's length assessment (material transactions >$50,000)

Legislative Foundation:
    AASB 124 Related Party Disclosures
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone

from core.risk_modules.base import BaseDetectionModule, ZERO

logger = logging.getLogger(__name__)

KMP_THRESHOLD = Decimal("5000")
ARMS_LENGTH_THRESHOLD = Decimal("50000")

# Keywords for related party account detection
_RP_KEYWORDS = {
    "director loan", "shareholder loan", "related party", "loan to director",
    "loan - director", "loan – director", "intercompany", "inter-company",
    "inter company", "management fee", "director fee", "consulting fee",
    "rent - director", "rent – director", "director rent",
}

_KMP_KEYWORDS = {
    "director fee", "director salary", "director remuneration",
    "management fee", "consulting fee", "rent - director", "rent – director",
    "director rent", "director superannuation", "director bonus",
    "key management", "kmp",
}


class RelatedPartyCluster(BaseDetectionModule):
    module_id = "cluster_rp"
    display_name = "Related Party Transactions (AASB 124)"
    entity_types = []  # All entity types
    assessment_model = None  # No dedicated model — produces EvaFinding only
    finding_category = "COMPLIANCE"

    def __init__(self, financial_year):
        super().__init__(financial_year)
        self.tb_data = None
        self.related_entities = []
        self.rp_accounts = []
        self.kmp_accounts = []

    def load_data(self):
        self.tb_data = self.load_trial_balance()
        self._scan_related_party_accounts()

    def _scan_related_party_accounts(self):
        """Single scan of TB for all related-party and KMP accounts."""
        from core.models import EntityRelationship

        # Get related entity names for matching
        rels = EntityRelationship.objects.filter(
            from_entity=self.entity,
        ).select_related("to_entity")
        rels_reverse = EntityRelationship.objects.filter(
            to_entity=self.entity,
        ).select_related("from_entity")

        related_names = set()
        for rel in rels:
            related_names.add(rel.to_entity.entity_name.lower())
        for rel in rels_reverse:
            related_names.add(rel.from_entity.entity_name.lower())
        self.related_entities = related_names

        for line in self.tb_data["lines"]:
            name_lower = (line.account_name or "").lower()
            net = line.effective_dr - line.effective_cr

            # Check for related party accounts
            is_rp = False
            if any(kw in name_lower for kw in _RP_KEYWORDS):
                is_rp = True
            elif any(rn in name_lower for rn in related_names if len(rn) > 3):
                is_rp = True

            if is_rp:
                self.rp_accounts.append({
                    "code": line.account_code,
                    "name": line.account_name,
                    "net": net,
                    "abs_net": abs(net),
                })

            # Check for KMP accounts
            if any(kw in name_lower for kw in _KMP_KEYWORDS):
                self.kmp_accounts.append({
                    "code": line.account_code,
                    "name": line.account_name,
                    "net": net,
                    "abs_net": abs(net),
                })

    def assess(self):
        if not self.tb_data or not self.tb_data["lines"]:
            return None

        self._rule_rp_01()
        self._rule_rp_02()
        self._rule_rp_03()

        if not self.rules_fired:
            self.overall_severity = "CLEAR"
        elif any(r == "RP-03" for r in self.rules_fired):
            self.overall_severity = "ADVISORY"
        else:
            self.overall_severity = "ADVISORY"

        return self._build_assessment_dict()

    def _rule_rp_01(self):
        """RP-01: Inter-entity balance detection.

        Flag inter-entity balances requiring AASB 124 disclosure.
        """
        if not self.rp_accounts:
            return

        material_balances = [
            a for a in self.rp_accounts if a["abs_net"] > ZERO
        ]

        if material_balances:
            self.rules_fired.append("RP-01")
            total = sum(a["abs_net"] for a in material_balances)
            account_list = ", ".join(
                f"{a['name']} (${a['abs_net']:,.2f})"
                for a in sorted(material_balances, key=lambda x: x["abs_net"], reverse=True)[:5]
            )
            self.finding_lines.append(
                f"Inter-entity balances totalling ${total:,.2f} detected "
                f"across {len(material_balances)} account(s). "
                f"AASB 124 disclosure required. Key accounts: {account_list}."
            )

    def _rule_rp_02(self):
        """RP-02: KMP transaction detection — aggregate > $5,000 per KMP."""
        if not self.kmp_accounts:
            return

        total_kmp = sum(a["abs_net"] for a in self.kmp_accounts)
        if total_kmp > KMP_THRESHOLD:
            self.rules_fired.append("RP-02")
            account_list = ", ".join(
                f"{a['name']} (${a['abs_net']:,.2f})"
                for a in sorted(self.kmp_accounts, key=lambda x: x["abs_net"], reverse=True)[:5]
            )
            self.finding_lines.append(
                f"Key management personnel transactions totalling "
                f"${total_kmp:,.2f} detected ({len(self.kmp_accounts)} accounts). "
                f"Exceeds $5,000 threshold for AASB 124 disclosure. "
                f"Accounts: {account_list}."
            )

    def _rule_rp_03(self):
        """RP-03: Arm's length assessment for material transactions > $50,000."""
        material = [
            a for a in self.rp_accounts if a["abs_net"] > ARMS_LENGTH_THRESHOLD
        ]

        if material:
            self.rules_fired.append("RP-03")
            for a in material:
                self.finding_lines.append(
                    f"Material related party transaction: {a['name']} "
                    f"(${a['abs_net']:,.2f}). Arm's length confirmation "
                    f"and documentation required."
                )

    def _build_assessment_dict(self):
        return {
            "rp_account_count": len(self.rp_accounts),
            "kmp_account_count": len(self.kmp_accounts),
            "related_entity_count": len(self.related_entities),
            "rules_fired": self.rules_fired,
            "overall_severity": self.overall_severity,
        }

    def build_finding_card(self, assessment):
        entity_name = self.entity.entity_name
        year = self.fy.year_label

        description = f"**Related Party Transactions — {entity_name} {year}**\n\n"
        description += f"**Severity:** {self.overall_severity}\n\n"
        description += "**Findings:**\n"
        for line in self.finding_lines:
            description += f"- {line}\n"

        return {
            "title": f"Related Party Transactions — {entity_name} {year}",
            "description": description,
            "recommended_action": (
                "1. Verify all related party balances are disclosed in the notes.\n"
                "2. Confirm arm's length terms for material transactions.\n"
                "3. Document KMP compensation disclosures per AASB 124."
            ),
            "legislation_ref": "AASB 124 Related Party Disclosures",
            "category": "COMPLIANCE",
            "calculated_values": assessment,
        }
