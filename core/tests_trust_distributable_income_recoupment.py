"""Distributable income must recoup a brought-forward trust loss first.

``_calculate_income_streams`` set ``net_distributable_income`` to the raw
accounting profit -- its own comment conceded "Simplified; trust law
adjustments may apply". A trust carrying accumulated losses therefore had the
full profit offered for distribution, and Stage 2 allocated all of it.

Dr Services Family Trust FY2026: $89,899.75 profit against $28,051.74 of
losses carried forward in 4199. The workspace offered $89,899.75, the
"100% Ronnie" scenario allocated $89,899.75, and JE-006 posted it -- leaving
the trust with negative equity of exactly the $28,051.74 never recouped.

4199's rollover balance is the brought-forward position and is debit-positive,
so a carried-forward loss reduces the distributable figure while
carried-forward undistributed income increases it.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.eva_trust_planning import _calculate_income_streams
from core.models import (
    AccountMapping, Client, Entity, FinancialYear, TrialBalanceLine,
)

PROFIT_ROWS = [
    ("0630", "Sales", Decimal("-138676.98"), "tb_import"),
    ("1510", "Accountancy", Decimal("48777.23"), "tb_import"),
]


class TrustDistributableIncomeRecoupmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Recoupment Client")
        cls.trust = Entity.objects.create(
            entity_name="Recoupment Test Trust", entity_type="trust",
            client=cls.client_obj,
        )

    @staticmethod
    def _mapping(section):
        m, _ = AccountMapping.objects.get_or_create(
            standard_code=f"RCP-{section}",
            defaults={"line_item_label": section.title(),
                      "financial_statement": "income_statement",
                      "statement_section": section},
        )
        return m

    def _build(self, extra_rows):
        fy = FinancialYear.objects.create(
            entity=self.trust, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        for code, name, cb, source in PROFIT_ROWS + extra_rows:
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
    def _appropriate(fy, amount):
        """Post the year's appropriation the way trust_post_distribution does.

        DR 4199, on a journal flagged is_trust_distribution. The link matters:
        "brought forward" is everything in 4199 EXCEPT the live distribution's
        own rows, so an appropriation with no journal behind it -- which cannot
        occur in production -- is indistinguishable from a prior-period
        adjustment and would be counted as brought forward.
        """
        from core.models import AdjustingJournal
        j = AdjustingJournal.objects.create(
            financial_year=fy, reference_number="JE-D01",
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=fy.end_date, description="Trust distribution",
            is_trust_distribution=True,
        )
        TrialBalanceLine.objects.create(
            financial_year=fy, account_code="4199",
            account_name="Undistributed income",
            closing_balance=Decimal(amount), debit=Decimal(amount),
            source="manual_journal", source_journal=j,
        )
        return j

    def test_brought_forward_loss_reduces_distributable_income(self):
        """89,899.75 profit less 28,051.74 carried forward = 61,848.01."""
        fy = self._build([
            ("4199", "Undistributed income", Decimal("28051.74"), "rollover"),
        ])
        data = _calculate_income_streams(fy)
        self.assertEqual(Decimal(data["net_profit"]), Decimal("89899.75"))
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("61848.01"),
            "the brought-forward loss was not recouped",
        )
        self.assertEqual(
            Decimal(data["brought_forward_losses"]), Decimal("28051.74"))

    def test_brought_forward_undistributed_income_increases_it(self):
        """A credit 4199 b/fwd is itself distributable."""
        fy = self._build([
            ("4199", "Undistributed income", Decimal("-10000.00"), "rollover"),
        ])
        data = _calculate_income_streams(fy)
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("99899.75"))

    def test_losses_exceeding_profit_leave_nothing_distributable(self):
        """Never offer a negative figure for distribution."""
        fy = self._build([
            ("4199", "Undistributed income", Decimal("200000.00"), "rollover"),
        ])
        data = _calculate_income_streams(fy)
        self.assertEqual(Decimal(data["net_distributable_income"]), Decimal("0"))
        self.assertEqual(Decimal(data["net_profit"]), Decimal("89899.75"))

    def test_current_year_appropriation_is_not_treated_as_brought_forward(self):
        """Posting the distribution must not shrink the balance that sized it."""
        fy = self._build([
            ("4199", "Undistributed income", Decimal("28051.74"), "rollover"),
        ])
        self._appropriate(fy, "61848.01")
        data = _calculate_income_streams(fy)
        self.assertEqual(
            Decimal(data["brought_forward_losses"]), Decimal("28051.74"))
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("61848.01"))

    def test_no_prior_balance_leaves_profit_fully_distributable(self):
        fy = self._build([])
        data = _calculate_income_streams(fy)
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("89899.75"))
        self.assertEqual(Decimal(data["brought_forward_losses"]), Decimal("0"))


class PriorPeriodAdjustmentTests(TrustDistributableIncomeRecoupmentTests):
    """A prior-period adjustment to 4199 must move the recoupable loss.

    Keying brought_forward off source="rollover" alone breaks the moment a
    prior year's correction is recognised in the current year. Dr Services
    FY2026: openings carry the lodged 29,150.97, and a prior-period adjustment
    credits 4199 with the 1,099.23 GST reclass that FY25 could not take up
    because it was already lodged. The recoupable loss is 28,051.74, so
    distributable income is 61,848.01 -- not the 60,748.78 the rollover balance
    alone implies.

    The rule is "everything in 4199 except this year's own appropriation",
    which is identifiable by its source_journal being the live trust
    distribution.
    """

    def test_prior_period_adjustment_reduces_the_recoupable_loss(self):
        fy = self._build([
            ("4199", "Undistributed income", Decimal("29150.97"), "rollover"),
            ("4199", "Accumulated losses (prior period adj)",
             Decimal("-1099.23"), "manual_journal"),
        ])
        data = _calculate_income_streams(fy)
        self.assertEqual(
            Decimal(data["brought_forward_losses"]), Decimal("28051.74"),
            "the prior-period adjustment was ignored",
        )
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("61848.01"))

    def test_the_years_own_appropriation_is_excluded(self):
        """A posted distribution must not shrink the figure that sized it."""
        fy = self._build([
            ("4199", "Undistributed income", Decimal("29150.97"), "rollover"),
            ("4199", "Accumulated losses (prior period adj)",
             Decimal("-1099.23"), "manual_journal"),
        ])
        self._appropriate(fy, "61848.01")
        data = _calculate_income_streams(fy)
        self.assertEqual(
            Decimal(data["brought_forward_losses"]), Decimal("28051.74"),
            "the distribution's own appropriation was counted as brought forward",
        )
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("61848.01"),
            "the figure changed after posting, so it is not idempotent",
        )
