"""Section 1 must recoup carried-forward losses before offering income.

``calculate_section1_from_tb`` never looked at 4199. The Tax Planning tab
therefore invited a distribution the Trust tab's post gate would refuse:
Minli Enterprise Unit Trust FY2027 printed "Distributable Income
$216,101.66" against $1,628,428.89 of losses carried forward, while the Trust
tab -- correctly -- offered nil.

Section 1 keeps its own ladder. The non-deductible add-back and the
non-assessable deduction are a tax concept the Trust tab has no equivalent
for, so they stay; recoupment is inserted *after* them, reading the same 4199
source through ``core.trust_losses``.

4199 is debit-positive, so the brought-forward position is signed: a positive
balance is a loss to recoup, a negative balance is undistributed income
brought forward, which is itself distributable.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    Client, Entity, EntityChartOfAccount, FinancialYear, TrialBalanceLine,
)
from core.tax_engine import calculate_section1_from_tb


class Section1RecoupmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Section 1 Client")
        cls.trust = Entity.objects.create(
            entity_name="Section 1 Test Trust", entity_type="trust_unit",
            client=cls.client_obj,
        )
        # Creating a trust seeds a chart, so these codes may already exist --
        # pin the sections the calculator reads rather than assume the seed.
        for code, name, section in [
            ("0630", "Sales", "revenue"),
            ("1510", "Accountancy", "expenses"),
            ("4199", "Undistributed income", "equity"),
        ]:
            EntityChartOfAccount.objects.update_or_create(
                entity=cls.trust, account_code=code,
                defaults={"account_name": name, "classification": section,
                          "section": section},
            )

    def _year(self, revenue, expenses, brought_forward=None):
        """Build a year earning ``revenue`` less ``expenses``.

        Revenue is credit-normal and expenses debit-normal, which is how
        ``calculate_section1_from_tb`` reads net profit (credit - debit).
        """
        fy = FinancialYear.objects.create(
            entity=self.trust, year_label="FY2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        TrialBalanceLine.objects.create(
            financial_year=fy, account_code="0630", account_name="Sales",
            closing_balance=-Decimal(revenue), debit=Decimal("0"),
            credit=Decimal(revenue), source="tb_import",
        )
        TrialBalanceLine.objects.create(
            financial_year=fy, account_code="1510",
            account_name="Accountancy", closing_balance=Decimal(expenses),
            debit=Decimal(expenses), credit=Decimal("0"), source="tb_import",
        )
        if brought_forward is not None:
            bf = Decimal(brought_forward)
            TrialBalanceLine.objects.create(
                financial_year=fy, account_code="4199",
                account_name="Undistributed income", closing_balance=bf,
                debit=bf if bf > 0 else Decimal("0"),
                credit=-bf if bf < 0 else Decimal("0"), source="rollover",
            )
        return fy

    def test_losses_exceeding_the_years_income_leave_nothing_distributable(self):
        """Minli FY2027: 216,101.66 earned against 1,628,428.89 carried."""
        fy = self._year("216101.66", "0.00", "1628428.89")
        s = calculate_section1_from_tb(fy)
        self.assertEqual(s["income_before_recoupment"], Decimal("216101.66"))
        self.assertEqual(s["losses_recouped"], Decimal("216101.66"))
        self.assertEqual(
            s["distributable_income"], Decimal("0.00"),
            "Section 1 offered income that the post gate would refuse",
        )
        self.assertEqual(
            s["losses_carried_forward"], Decimal("1412327.23"))

    def test_partial_recoupment_leaves_the_excess_distributable(self):
        fy = self._year("100000.00", "0.00", "30000.00")
        s = calculate_section1_from_tb(fy)
        self.assertEqual(s["losses_recouped"], Decimal("30000.00"))
        self.assertEqual(s["distributable_income"], Decimal("70000.00"))
        self.assertEqual(s["losses_carried_forward"], Decimal("0.00"))

    def test_a_loss_year_recoups_nothing_and_adds_to_the_carried_balance(self):
        """Minli FY2025 lost 568,879.30 on top of 1,686,352.10 carried."""
        fy = self._year("0.00", "568879.30", "1686352.10")
        s = calculate_section1_from_tb(fy)
        self.assertEqual(
            s["income_before_recoupment"], Decimal("-568879.30"))
        self.assertEqual(
            s["losses_recouped"], Decimal("0.00"),
            "a loss year cannot recoup anything",
        )
        self.assertEqual(s["distributable_income"], Decimal("0.00"))
        self.assertEqual(
            s["losses_carried_forward"], Decimal("2255231.40"),
            "the year's own loss was not added to the carried balance",
        )

    def test_a_trust_with_no_losses_is_unaffected(self):
        """Regression guard for every trust already on the platform."""
        fy = self._year("100000.00", "40000.00")
        s = calculate_section1_from_tb(fy)
        self.assertEqual(s["distributable_income"], Decimal("60000.00"))
        self.assertEqual(s["losses_recouped"], Decimal("0.00"))
        self.assertEqual(s["losses_carried_forward"], Decimal("0.00"))

    def test_undistributed_income_brought_forward_is_itself_distributable(self):
        """A credit 4199 b/fwd increases the figure, matching the Trust tab."""
        fy = self._year("100000.00", "0.00", "-10000.00")
        s = calculate_section1_from_tb(fy)
        self.assertEqual(
            s["undistributed_brought_forward"], Decimal("10000.00"))
        self.assertEqual(s["losses_recouped"], Decimal("0.00"))
        self.assertEqual(s["distributable_income"], Decimal("110000.00"))
        self.assertEqual(s["losses_carried_forward"], Decimal("0.00"))

    def test_recoupment_applies_after_the_tax_adjustments(self):
        """Non-deductible add-backs are recouped against, not bypassed."""
        EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="1510",
        ).update(is_non_deductible=True)
        fy = self._year("100000.00", "40000.00", "80000.00")
        s = calculate_section1_from_tb(fy)
        # 60,000 profit + 40,000 added back = 100,000 before recoupment.
        self.assertEqual(s["income_before_recoupment"], Decimal("100000.00"))
        self.assertEqual(s["losses_recouped"], Decimal("80000.00"))
        self.assertEqual(s["distributable_income"], Decimal("20000.00"))
