"""The distribution workspace must seed from the ledger, not from nothing.

``trust_distribution`` auto-populated a freshly created ``TrustDistribution``
with::

    total_credit = tb_lines.aggregate(Sum("credit"))
    total_debit  = tb_lines.aggregate(Sum("debit"))
    dist.accounting_profit = total_credit - total_debit

summed over *every* trial balance line, balance sheet included. A trial balance
balances by definition, so that expression is structurally zero -- verified
$0.00 on all twelve live trust financial years. It then copied that zero into
``distributable_income`` and ``other_income``.

Zero is not merely wrong as a starting figure. ``distributable_income`` is what
``allocate_unit_trust_distribution`` splits across the unit register, and what
``risk_modules/section100a.py`` multiplies by each allocation percentage at
three sites to decide whether a distribution is large enough to assess.

The seed is now the same ledger figure every other trust surface uses, via
``core.trust_losses`` -- so carried-forward losses are recouped here too, and a
trust with nothing to distribute seeds nil for the right reason rather than by
arithmetic accident.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    AccountMapping, Entity, FinancialYear, TrialBalanceLine, TrustDistribution,
)


class TrustDistributionSeedTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="dist", email="dist@example.com", password="secret123",
            role="senior_accountant",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        # A unit trust: _entity_has_unitholders short-circuits on entity_type,
        # avoiding the JSONField roles__contains lookup sqlite cannot run.
        self.entity = Entity.objects.create(
            entity_name="Seed Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
        )

    @staticmethod
    def _mapping(section):
        m, _ = AccountMapping.objects.get_or_create(
            standard_code=f"SEED-{section}",
            defaults={"line_item_label": section.title(),
                      "financial_statement": "income_statement",
                      "statement_section": section},
        )
        return m

    def _line(self, code, name, closing, mapping=None, source="tb_import"):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            closing_balance=Decimal(closing),
            debit=Decimal(closing) if Decimal(closing) > 0 else Decimal("0"),
            credit=-Decimal(closing) if Decimal(closing) < 0 else Decimal("0"),
            source=source, mapped_line_item=mapping,
        )

    def _balanced_year(self, revenue, expenses, brought_forward=None):
        """A trial balance that genuinely balances, and asserts that it does.

        The balance is the whole point. The old formula netted every debit
        against every credit, so on any real trial balance it could only ever
        return zero -- a fixture that does not balance would let it produce a
        plausible answer by accident and prove nothing.
        """
        self._line("0630", "Sales", f"-{revenue}", self._mapping("revenue"))
        self._line("1510", "Accountancy", expenses, self._mapping("expenses"))
        # Cash holds what was earned less what was paid, so DR == CR.
        self._line("2010", "Cash at bank", str(Decimal(revenue) - Decimal(expenses)))
        if brought_forward is not None:
            # An accumulated loss is a debit in equity, carried against a
            # liability of the same size.
            bf = Decimal(brought_forward)
            self._line("4199", "Undistributed income", str(bf), source="rollover")
            self._line("3010", "Loan from unitholder", str(-bf))
        self._assert_tb_balances()

    def _assert_tb_balances(self):
        lines = TrialBalanceLine.objects.filter(financial_year=self.fy)
        dr = sum((l.debit or Decimal("0") for l in lines), Decimal("0"))
        cr = sum((l.credit or Decimal("0") for l in lines), Decimal("0"))
        self.assertEqual(
            dr, cr,
            "fixture guard: the trial balance must balance, or the old "
            "sum(credit) - sum(debit) formula could return a right answer "
            "by accident",
        )

    def _open_page(self):
        response = self.client.get(
            reverse("core:trust_distribution", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        return TrustDistribution.objects.get(financial_year=self.fy)

    def test_the_seed_is_the_years_profit_not_zero(self):
        self._balanced_year("300000.00", "120000.00")
        dist = self._open_page()
        self.assertEqual(
            dist.accounting_profit, Decimal("180000.00"),
            "a balanced trial balance still nets to zero under the old formula",
        )
        self.assertEqual(dist.distributable_income, Decimal("180000.00"))

    def test_carried_forward_losses_are_recouped_in_the_seed(self):
        """The fourth surface that ignored 4199."""
        self._balanced_year("216101.66", "0.00", "1628428.89")
        dist = self._open_page()
        self.assertEqual(dist.accounting_profit, Decimal("216101.66"))
        self.assertEqual(
            dist.distributable_income, Decimal("0.00"),
            "losses carried forward were not recouped",
        )

    def test_partial_recoupment_seeds_the_remainder(self):
        self._balanced_year("100000.00", "0.00", "30000.00")
        dist = self._open_page()
        self.assertEqual(dist.accounting_profit, Decimal("100000.00"))
        self.assertEqual(dist.distributable_income, Decimal("70000.00"))

    def test_the_streams_are_seeded_from_the_income_breakdown(self):
        """other_income was set to the whole profit, gains included."""
        self._balanced_year("100000.00", "0.00")
        self._line("0601", "Capital gains/Loss", "-20000.00",
                   self._mapping("revenue"))
        self._line("2012", "Proceeds receivable", "20000.00")
        self._assert_tb_balances()
        dist = self._open_page()
        self.assertEqual(dist.capital_gains, Decimal("20000.00"))
        self.assertEqual(
            dist.other_income, Decimal("100000.00"),
            "the capital gain was double-counted into other income",
        )

    def test_the_components_sum_to_distributable_income(self):
        """They are components OF distributable income, so they must add up.

        ``income_streams`` is a GROSS character breakdown of revenue, not a
        split of the distributable figure -- Dr Services FY2026 earns
        138,676.98 of ordinary income but only 61,848.01 is distributable once
        expenses and the carried-forward loss are taken. Seeding the component
        fields straight from the streams overstates them against the total
        they are supposed to divide up.
        """
        self._balanced_year("138676.98", "48777.23", "28051.74")
        dist = self._open_page()
        self.assertEqual(dist.distributable_income, Decimal("61848.01"))
        total = (dist.capital_gains + dist.franked_dividends
                 + dist.foreign_income + dist.other_income)
        self.assertEqual(
            total, dist.distributable_income,
            "the components do not sum to the figure they divide",
        )
        self.assertEqual(dist.other_income, Decimal("61848.01"))

    def test_components_are_nil_when_nothing_is_distributable(self):
        self._balanced_year("216101.66", "0.00", "1628428.89")
        dist = self._open_page()
        self.assertEqual(dist.distributable_income, Decimal("0.00"))
        self.assertEqual(dist.capital_gains, Decimal("0.00"))
        self.assertEqual(dist.other_income, Decimal("0.00"))

    def test_a_capital_gain_is_capped_at_what_is_distributable(self):
        """A gain cannot be streamed beyond the income that survives."""
        self._balanced_year("100000.00", "0.00", "90000.00")
        self._line("0601", "Capital gains/Loss", "-40000.00",
                   self._mapping("revenue"))
        self._line("2012", "Proceeds receivable", "40000.00")
        self._assert_tb_balances()
        dist = self._open_page()
        # 140,000 earned, 90,000 recouped, 50,000 distributable.
        self.assertEqual(dist.distributable_income, Decimal("50000.00"))
        self.assertEqual(dist.capital_gains, Decimal("40000.00"))
        self.assertEqual(dist.other_income, Decimal("10000.00"))

    def test_an_existing_record_is_never_reseeded(self):
        """These figures are user-editable; opening the page must not reset."""
        self._balanced_year("300000.00", "120000.00")
        self._open_page()
        TrustDistribution.objects.filter(financial_year=self.fy).update(
            distributable_income=Decimal("12345.67"),
        )
        dist = self._open_page()
        self.assertEqual(dist.distributable_income, Decimal("12345.67"))
