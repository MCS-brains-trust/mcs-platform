"""A trust must not distribute more than profit less brought-forward losses.

Dr Services Family Trust FY2026 carried $28,051.74 of accumulated losses in
4199 and earned $89,899.75. The trust workspace set
``net_distributable_income`` to the full $89,899.75 and the distribution
journal posted all of it to the beneficiary loan, so the trust distributed
$28,051.74 more than it had available and finished with negative equity of
exactly that amount.

The balance-sheet integrity check cannot see this: both legs of the journal
are posted, so the trial balance and the balance sheet still balance. The
only observable symptom is that a fully-distributing trust ends the year with
accumulated losses instead of nil.

Available income = net profit − brought-forward 4199 balance (debit-positive,
so a carried-forward loss reduces what can be distributed and carried-forward
undistributed income increases it).
"""
from datetime import date
from decimal import Decimal

from core.models import (
    AccountMapping, AdjustingJournal, Client, Entity, EntityChartOfAccount,
    FinancialYear, RiskRule, TrialBalanceLine,
)
from django.test import TestCase

from core.risk_engine import _eval_trust_distribution, _load_trial_balance

# Dr Services FY2026 shape, reduced to what the rule reads.
TB_ROWS = [
    # (code, name, closing_balance, source)
    ("0630", "Sales",                 Decimal("-138676.98"), "tb_import"),
    ("1510", "Accountancy",           Decimal("48777.23"),   "tb_import"),
    ("2000", "Cash at bank",          Decimal("134421.17"),  "tb_import"),
    ("4199", "Undistributed income",  Decimal("28051.74"),   "rollover"),
]

# Posted separately so it carries the source_journal link that marks it as this
# year's distribution rather than a brought-forward or prior-period balance.
APPROPRIATION = Decimal("89899.75")


class TrustOverDistributionRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Trust Rule Client")
        cls.trust = Entity.objects.create(
            entity_name="Dr Services Test Trust",
            entity_type="trust",
            client=cls.client_obj,
        )
        cls.rule = RiskRule(
            rule_id="TRU-07",
            category="trust",
            title="Distribution exceeds available income",
            description=(
                "{entity_name} distributed {distributed} against {available} "
                "available ({net_profit} profit less {brought_forward} "
                "brought-forward losses) — over-distributed by {excess}."
            ),
            severity="HIGH",
            tier=2,
            recommended_action="Reduce the distribution to available income.",
            legislation_ref="ITAA 1936 Sch 2F; ITAA 1936 s97",
            trigger_config={"type": "trust_distribution",
                            "check_type": "over_distribution"},
        )

    @classmethod
    def _mapping(cls, section):
        m, _ = AccountMapping.objects.get_or_create(
            standard_code=f"TEST-{section}",
            defaults={
                "line_item_label": section.title(),
                "financial_statement": "income_statement",
                "statement_section": section,
            },
        )
        return m

    def _build(self, rows):
        fy = FinancialYear.objects.create(
            entity=self.trust, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        for code, name, cb, source in rows:
            EntityChartOfAccount.objects.get_or_create(
                entity=self.trust, account_code=code,
                defaults={"account_name": name, "is_active": True},
            )
            mapping = None
            head = code.split(".")[0]
            if head.isdigit() and int(head) < 1000:
                mapping = self._mapping("revenue")
            elif head.isdigit() and 1000 <= int(head) < 2000:
                mapping = self._mapping("expenses")
            TrialBalanceLine.objects.create(
                financial_year=fy, account_code=code, account_name=name,
                closing_balance=cb,
                debit=cb if cb > 0 else Decimal("0"),
                credit=-cb if cb < 0 else Decimal("0"),
                source=source,
                mapped_line_item=mapping,
            )
        return fy

    @staticmethod
    def _appropriate(fy, amount):
        """DR 4199 / CR beneficiary loan on a live is_trust_distribution journal."""
        j = AdjustingJournal.objects.create(
            financial_year=fy, reference_number="JE-D01",
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=fy.end_date, description="Trust distribution",
            is_trust_distribution=True,
        )
        for code, name, cb in (
            ("4199", "Undistributed income", Decimal(amount)),
            ("4004.01", "Funds loaned to trust", -Decimal(amount)),
        ):
            TrialBalanceLine.objects.create(
                financial_year=fy, account_code=code, account_name=name,
                closing_balance=cb,
                debit=cb if cb > 0 else Decimal("0"),
                credit=-cb if cb < 0 else Decimal("0"),
                source="manual_journal", source_journal=j,
            )
        return j

    def _evaluate(self, fy):
        tb = _load_trial_balance(fy)
        ctx = {"entity_type": "trust", "entity_name": self.trust.entity_name}
        return _eval_trust_distribution(
            self.rule, fy, tb, {}, ctx, self.rule.trigger_config)

    def test_flags_distribution_that_exceeds_available_income(self):
        """89,899.75 distributed against 61,848.01 available → flagged."""
        fy = self._build(TB_ROWS)
        self._appropriate(fy, APPROPRIATION)
        flag = self._evaluate(fy)
        self.assertIsNotNone(
            flag, "over-distribution of 28,051.74 was not flagged")
        self.assertEqual(flag["rule_id"], "TRU-07")
        cv = flag["calculated_values"]
        self.assertEqual(Decimal(cv["net_profit"]), Decimal("89899.75"))
        self.assertEqual(Decimal(cv["brought_forward"]), Decimal("28051.74"))
        self.assertEqual(Decimal(cv["available"]), Decimal("61848.01"))
        self.assertEqual(Decimal(cv["distributed"]), Decimal("89899.75"))
        self.assertEqual(Decimal(cv["excess"]), Decimal("28051.74"))

    def test_silent_when_distribution_equals_available_income(self):
        """The corrected 61,848.01 distribution must not flag."""
        fy = self._build(TB_ROWS)
        self._appropriate(fy, Decimal("61848.01"))
        self.assertIsNone(self._evaluate(fy))

    def test_brought_forward_undistributed_income_increases_headroom(self):
        """A credit 4199 b/fwd is distributable, so a larger distribution is fine."""
        rows = [r for r in TB_ROWS if r[0] != "4199"]
        rows += [("4199", "Undistributed income", Decimal("-10000.00"), "rollover")]
        fy = self._build(rows)
        self._appropriate(fy, Decimal("99899.75"))
        self.assertIsNone(self._evaluate(fy))

    def test_prior_period_adjustment_is_not_counted_as_distributed(self):
        """A PPA credit to 4199 raises headroom instead of looking distributed.

        Dr Services FY2026 shape: openings carry the lodged 29,150.97 and a
        prior-period adjustment credits the 1,099.23 GST reclass FY2025 could
        not take up, being already lodged. Recoupable loss 28,051.74, so
        61,848.01 is in order and must not flag.
        """
        rows = [r for r in TB_ROWS if r[0] != "4199"]
        rows += [
            ("4199", "Undistributed income", Decimal("29150.97"), "rollover"),
            ("4199", "Prior period adjustment", Decimal("-1099.23"), "manual_journal"),
        ]
        fy = self._build(rows)
        self._appropriate(fy, Decimal("61848.01"))
        self.assertIsNone(self._evaluate(fy))

    def test_silent_for_non_trust_entities(self):
        fy = self._build(TB_ROWS)
        self._appropriate(fy, APPROPRIATION)
        tb = _load_trial_balance(fy)
        ctx = {"entity_type": "company", "entity_name": "Not A Trust"}
        self.assertIsNone(_eval_trust_distribution(
            self.rule, fy, tb, {}, ctx, self.rule.trigger_config))
