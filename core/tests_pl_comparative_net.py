"""A P&L comparative is the prior year's net result, not its gross movements.

Pass 3 stored ``prior_debit=line.debit, prior_credit=line.credit`` -- the gross
period movements summed across every journal on the account. An account whose
debits and credits offset therefore carried a large figure on both sides.

Minli Enterprise Unit Trust FY2027 showed account 601 "Capital gains/Loss -
Sale of Assets" with a 505,845.52 prior debit AND a 505,845.52 prior credit,
because FY2026 booked a property disposal (374,313.25 + 131,532.27 into 601)
and then journalled it back out when the sale slipped to FY2027. The net was
nil, and the financial statements correctly showed nothing -- but the trial
balance screen renders prior_debit and prior_credit raw, so it read as half a
million of capital gain that never existed, and contradicted the P&L for the
same account and year.

_comparative_for_line already derives a single-sided comparative from the net
closing balance for balance sheet lines, and says in its own docstring that
period movements must not be used. The same reasoning applies to the P&L.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Client, Entity, FinancialYear, TrialBalanceLine
from core.views import _populate_rolled_forward_fy

D = Decimal


class PLComparativeIsNetTests(TestCase):
    """Minli's FY2026 property journals, reduced to the accounts involved."""

    @classmethod
    def setUpTestData(cls):
        cls.entity = Entity.objects.create(
            entity_name="Comparative Co Pty Ltd", entity_type="company",
            client=Client.objects.create(name="Comparative Client"),
        )
        cls.fy26 = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        cls.fy27 = FinancialYear.objects.create(
            entity=cls.entity, year_label="2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
            prior_year=cls.fy26,
        )

        def line(code, name, debit=D("0"), credit=D("0")):
            TrialBalanceLine.objects.create(
                financial_year=cls.fy26, account_code=code, account_name=name,
                debit=debit, credit=credit, closing_balance=debit - credit,
                source="manual_journal",
            )

        # 601: booked in, then journalled straight back out. Net nil.
        line("601", "Capital gains/Loss - Sale of Assets", debit=D("374313.25"))
        line("601", "Capital gains/Loss - Sale of Assets", debit=D("131532.27"))
        line("601", "Capital gains/Loss - Sale of Assets", credit=D("505845.52"))
        # 1792: written off, partly reversed. Net 239,315.03 debit.
        line("1792", "Write of Building", debit=D("745160.55"))
        line("1792", "Write of Building", credit=D("374313.25"))
        line("1792", "Write of Building", credit=D("131532.27"))
        # A plain revenue account, net credit.
        line("620", "Sales", credit=D("100000.00"))
        # A balance sheet account so the roll has something to carry.
        line("2000", "Cash at bank", debit=D("50000.00"))

    def _comparative(self, code):
        rows = TrialBalanceLine.objects.filter(
            financial_year=self.fy27, account_code=code
        )
        return [(r.prior_debit or D("0"), r.prior_credit or D("0")) for r in rows]

    def test_an_account_that_nets_to_nil_carries_no_comparative(self):
        """The defect this test exists for: 505,845.52 on both sides."""
        _populate_rolled_forward_fy(self.fy26, self.fy27)

        for prior_debit, prior_credit in self._comparative("601"):
            self.assertEqual(prior_debit, D("0"))
            self.assertEqual(prior_credit, D("0"))

    def test_a_net_debit_carries_only_a_prior_debit(self):
        _populate_rolled_forward_fy(self.fy26, self.fy27)

        self.assertEqual(self._comparative("1792"), [(D("239315.03"), D("0"))])

    def test_a_net_credit_carries_only_a_prior_credit(self):
        _populate_rolled_forward_fy(self.fy26, self.fy27)

        self.assertEqual(self._comparative("620"), [(D("0"), D("100000.00"))])

    def test_no_pl_line_carries_a_figure_on_both_sides(self):
        """A trial balance line belongs in one column."""
        _populate_rolled_forward_fy(self.fy26, self.fy27)

        both = [
            (r.account_code, r.prior_debit, r.prior_credit)
            for r in TrialBalanceLine.objects.filter(financial_year=self.fy27)
            if (r.prior_debit or D("0")) and (r.prior_credit or D("0"))
        ]
        self.assertEqual(both, [])

    def test_the_balance_sheet_comparative_is_unchanged(self):
        _populate_rolled_forward_fy(self.fy26, self.fy27)

        self.assertEqual(self._comparative("2000"), [(D("50000.00"), D("0"))])
