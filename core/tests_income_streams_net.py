"""Income streams are the net position per account, not the gross journals.

_calculate_income_streams accumulated ``abs(line.debit - line.credit)`` for
every trial balance line, so two journals on the same account added together
instead of offsetting.

Minli Enterprise Unit Trust FY2027 sold a property with two journals on account
601 "Capital gains/Loss - Sale of Assets": 744,189.00 credit on settlement and
527,945.52 debit for the CGT cost base. The gain is the net, 216,243.48. The
distribution tab reported 1,272,134.52 -- the two added -- against a trial
balance whose whole P&L netted to 216,101.66.

Minli FY2026 is the same defect with a starker result: a disposal booked and
then journalled back out, a true capital gain of nil, reported as 1,011,691.04.

"GROSS" in the function's own closing comment means before recoupment of
brought-forward losses -- the character breakdown used for streaming. It does
not mean before offsetting the journals within one account.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.eva_trust_planning import _calculate_income_streams
from core.models import (
    AccountMapping, Client, Entity, FinancialYear, TrialBalanceLine,
)

D = Decimal


class IncomeStreamsNetPerAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.entity = Entity.objects.create(
            entity_name="Streams Unit Trust", entity_type="trust_unit",
            client=Client.objects.create(name="Streams Client"),
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
        )
        cls.revenue = AccountMapping.objects.create(
            standard_code="IS-REV-901", line_item_label="Rents received",
            financial_statement="Income Statement", statement_section="Revenue",
        )
        cls.expense = AccountMapping.objects.create(
            standard_code="IS-EXP-901", line_item_label="Rates",
            financial_statement="Income Statement", statement_section="Expenses",
        )

    def _line(self, code, name, debit=D("0"), credit=D("0"), mapping=None):
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            debit=debit, credit=credit, closing_balance=debit - credit,
            mapped_line_item=mapping, source="manual_journal",
        )

    def _streams(self):
        return {
            k: D(v)
            for k, v in _calculate_income_streams(self.fy)["income_streams"].items()
        }

    def test_cost_base_offsets_proceeds(self):
        """The defect this test exists for: 744,189.00 + 527,945.52."""
        self._line("601", "Capital gains/Loss - Sale of Assets", credit=D("744189.00"))
        self._line("601", "Capital gains/Loss - Sale of Assets", debit=D("527945.52"))

        self.assertEqual(self._streams()["cgt_non_discount"], D("216243.48"))

    def test_a_disposal_journalled_back_out_leaves_nothing(self):
        """Minli FY2026: booked in twice, reversed once, net nil."""
        self._line("601", "Capital gains/Loss - Sale of Assets", debit=D("374313.25"))
        self._line("601", "Capital gains/Loss - Sale of Assets", debit=D("131532.27"))
        self._line("601", "Capital gains/Loss - Sale of Assets", credit=D("505845.52"))

        self.assertEqual(self._streams()["cgt_non_discount"], D("0"))

    def test_a_net_capital_loss_is_not_reported_as_a_gain(self):
        """abs() turned a loss into a gain -- the account is named gains/Loss."""
        self._line("601", "Capital gains/Loss - Sale of Assets", credit=D("100000.00"))
        self._line("601", "Capital gains/Loss - Sale of Assets", debit=D("250000.00"))

        self.assertEqual(self._streams()["cgt_non_discount"], D("-150000.00"))

    def test_ordinary_revenue_is_unchanged(self):
        self._line("620", "Rents received", credit=D("100000.00"), mapping=self.revenue)

        data = _calculate_income_streams(self.fy)
        self.assertEqual(D(data["income_streams"]["ordinary_income"]), D("100000.00"))
        self.assertEqual(D(data["total_revenue"]), D("100000.00"))

    def test_a_credit_note_against_revenue_reduces_it(self):
        self._line("620", "Rents received", credit=D("100000.00"), mapping=self.revenue)
        self._line("620", "Rents received", debit=D("15000.00"), mapping=self.revenue)

        self.assertEqual(D(_calculate_income_streams(self.fy)["total_revenue"]),
                         D("85000.00"))

    def test_expenses_net_within_an_account(self):
        self._line("1850", "Rates & land taxes", debit=D("9000.00"), mapping=self.expense)
        self._line("1850", "Rates & land taxes", credit=D("1000.00"), mapping=self.expense)

        self.assertEqual(D(_calculate_income_streams(self.fy)["total_expenses"]),
                         D("8000.00"))

    def test_net_profit_is_revenue_less_expenses(self):
        self._line("620", "Rents received", credit=D("100000.00"), mapping=self.revenue)
        self._line("1850", "Rates & land taxes", debit=D("9000.00"), mapping=self.expense)

        self.assertEqual(D(_calculate_income_streams(self.fy)["net_profit"]),
                         D("91000.00"))
