"""TRU-01 must fire on a profitable trust that has not distributed.

Two defects meant it never did the job.

1. **Sign.** ``tb["totals"]["net_profit"]`` is credit-normal -- revenue is
   negative -- so a profit is a NEGATIVE number. Testing ``net_income > ZERO``
   therefore matched only LOSS years: the rule stayed silent on exactly the
   profitable trusts it exists to catch, and fired on loss-making ones where
   there is no income to distribute and s99A cannot bite.

2. **Evidence of a distribution.** It looked only for a ``TrustDistribution``
   record. That is a OneToOne planning row, not the structural record of a
   posted appropriation: Dr Services Family Trust FY2026 has a posted
   distribution journal (JE-001, 61,848.01) and no TrustDistribution row at
   all, so the rule would report "no distribution recorded" for a trust that
   has demonstrably distributed. ``AdjustingJournal.live_trust_distribution``
   is the source of truth the post gate, the un-post action and TRU-07 all use.

Consequence of the pair: on a profitable trust with no resolution -- the
s99A exposure at 47% -- the rule was silent.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (AccountMapping, AdjustingJournal, Client, Entity,
                         FinancialYear, RiskRule, TrialBalanceLine)
from core.risk_engine import _eval_trust_distribution, _load_trial_balance

PROFIT_ROWS = [
    ("0630", "Sales", Decimal("-138676.98"), "tb_import"),
    ("1510", "Accountancy", Decimal("48777.23"), "tb_import"),
]
LOSS_ROWS = [
    ("0630", "Sales", Decimal("-22937.03"), "tb_import"),
    ("1510", "Accountancy", Decimal("32179.00"), "tb_import"),
]


class TrustUndistributedRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Undistributed Client")
        cls.trust = Entity.objects.create(
            entity_name="Undistributed Test Trust", entity_type="trust",
            client=cls.client_obj,
        )
        cls.rule = RiskRule(
            rule_id="TRU-01", category="trust",
            title="Trust distribution resolution required",
            description=("{entity_name} has net income of {net_income} with no "
                         "distribution resolution recorded."),
            severity="HIGH", tier=2,
            recommended_action="Prepare a distribution resolution before 30 June.",
            legislation_ref="ITAA 1936 s97, s99, s99A",
            trigger_config={"type": "trust_distribution",
                            "check_type": "undistributed"},
        )

    @staticmethod
    def _mapping(section):
        m, _ = AccountMapping.objects.get_or_create(
            standard_code=f"UND-{section}",
            defaults={"line_item_label": section.title(),
                      "financial_statement": "income_statement",
                      "statement_section": section},
        )
        return m

    def _build(self, rows):
        fy = FinancialYear.objects.create(
            entity=self.trust, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        for code, name, cb, source in rows:
            head = code.split(".")[0]
            mapping = None
            if head.isdigit() and int(head) < 1000:
                mapping = self._mapping("revenue")
            elif head.isdigit() and 1000 <= int(head) < 2000:
                mapping = self._mapping("expenses")
            TrialBalanceLine.objects.create(
                financial_year=fy, account_code=code, account_name=name,
                closing_balance=cb,
                debit=cb if cb > 0 else Decimal("0"),
                credit=-cb if cb < 0 else Decimal("0"),
                source=source, mapped_line_item=mapping,
            )
        return fy

    @staticmethod
    def _post_distribution(fy):
        return AdjustingJournal.objects.create(
            financial_year=fy, reference_number="JE-D01",
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=fy.end_date, description="Trust distribution",
            is_trust_distribution=True,
        )

    def _evaluate(self, fy, entity_type="trust"):
        return _eval_trust_distribution(
            self.rule, fy, _load_trial_balance(fy), {},
            {"entity_type": entity_type, "entity_name": self.trust.entity_name},
            self.rule.trigger_config)

    def test_flags_a_profitable_trust_with_no_distribution(self):
        """The s99A exposure: 89,899.75 of income and no resolution."""
        flag = self._evaluate(self._build(PROFIT_ROWS))
        self.assertIsNotNone(flag, "a profitable undistributed trust was not flagged")
        self.assertEqual(flag["rule_id"], "TRU-01")
        self.assertEqual(
            Decimal(flag["calculated_values"]["net_income"]), Decimal("89899.75"))
        self.assertIn("89,899.75", flag["description"])

    def test_silent_when_a_distribution_journal_is_posted(self):
        """A posted appropriation is evidence, with or without a planning row."""
        fy = self._build(PROFIT_ROWS)
        self._post_distribution(fy)
        self.assertIsNone(self._evaluate(fy))

    def test_a_voided_distribution_does_not_count(self):
        fy = self._build(PROFIT_ROWS)
        j = self._post_distribution(fy)
        j.status = AdjustingJournal.JournalStatus.VOIDED
        j.save(update_fields=["status"])
        self.assertIsNotNone(
            self._evaluate(fy), "a voided distribution was treated as live")

    def test_silent_on_a_loss_year(self):
        """No income to distribute, so no resolution is owed."""
        self.assertIsNone(self._evaluate(self._build(LOSS_ROWS)))

    def test_silent_for_non_trust_entities(self):
        self.assertIsNone(
            self._evaluate(self._build(PROFIT_ROWS), entity_type="company"))
