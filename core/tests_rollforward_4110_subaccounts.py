"""A 4110.NN unitholder loan is not the income tax provision.

4110 means two things in this codebase:

  * core/trust_coa_seed.py:412 -- "Income tax, operating profit", a P&L
    appropriation the roll forward clears at year end.
  * core/beneficiary_account_service.py:44 -- "Funds loaned to trust",
    section "liabilities", materialised per officer as 4110.NN.

_is_income_tax_account matched on ``account_code.split(".")[0] == "4110"``,
so every unitholder's loan account answered yes. In _populate_rolled_forward_fy
``income_tax_line`` is a plain assignment with no ranking, so the last match
won and was written into the new year with a nil closing balance -- while any
earlier 4110.NN escaped and carried normally.

Minli Enterprise Unit Trust FY2027 rolled with unitholder .01 correct at
1,168,294.01 and unitholder .02 at 2,014,410.20 instead of 1,686,273.09. The
same 328,137.11 also landed in 4199, because retained profits opens at
``closing + net_pl_result + tax_amount`` and tax_amount came from that line.
The two errors offset, so the trial balance still summed to zero.
"""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from core.models import (
    Client, Entity, EntityChartOfAccount, EntityOfficer, FinancialYear,
    TrialBalanceLine,
)
from core.views import _is_income_tax_account, _populate_rolled_forward_fy


class IncomeTaxAccountPredicateTests(SimpleTestCase):
    def test_the_bare_4110_parent_is_still_the_income_tax_provision(self):
        self.assertTrue(
            _is_income_tax_account("4110", "Income tax, operating profit", None)
        )

    def test_a_4110_subaccount_is_a_unitholder_loan_not_income_tax(self):
        """The defect this test exists for."""
        for code in ("4110.01", "4110.02", "4110.10"):
            with self.subTest(code=code):
                self.assertFalse(
                    _is_income_tax_account(code, "Funds loaned to trust", None)
                )

    def test_the_name_check_still_catches_a_differently_coded_tax_account(self):
        self.assertTrue(
            _is_income_tax_account("4900", "Income tax expense", None)
        )

    def test_other_beneficiary_subaccounts_are_unaffected(self):
        for code in ("4000.01", "4004.01", "4053.01"):
            with self.subTest(code=code):
                self.assertFalse(
                    _is_income_tax_account(code, "Funds loaned to trust", None)
                )


class UnitTrustRollForwardTests(TestCase):
    """Minli's FY2026 -> FY2027 shape, reduced to the accounts that matter."""

    LOANS = {"01": Decimal("301473.02"), "02": Decimal("328137.11")}
    OPENING = {"01": Decimal("-1156365.77"), "02": Decimal("-1701008.95")}
    INTRODUCED = {"01": Decimal("-313401.26"), "02": Decimal("-313401.25")}

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Roll Unit Trust", entity_type="trust_unit",
            client=Client.objects.create(name="Roll Client"),
        )
        self.fy26 = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        self.fy27 = FinancialYear.objects.create(
            entity=self.entity, year_label="2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
            prior_year=self.fy26,
        )
        for suffix in ("01", "02"):
            officer = EntityOfficer.objects.create(
                entity=self.entity, full_name=f"Holder {suffix}",
                role="beneficiary", display_order=int(suffix),
            )
            for code, section, name in (
                (f"4000.{suffix}", "capital_accounts", "Opening balance - Unit Holder"),
                (f"4004.{suffix}", "capital_accounts", "Unitholders' funds introduced"),
                (f"4110.{suffix}", "liabilities", "Funds loaned to trust"),
            ):
                # update_or_create: creating an officer auto-provisions the
                # beneficiary account rows, so these already exist.
                EntityChartOfAccount.objects.update_or_create(
                    entity=self.entity, account_code=code,
                    defaults={
                        "account_name": name, "section": section,
                        "is_active": True, "beneficiary_officer": officer,
                    },
                )
            for code, balance in (
                (f"4000.{suffix}", self.OPENING[suffix]),
                (f"4004.{suffix}", self.INTRODUCED[suffix]),
                (f"4110.{suffix}", self.LOANS[suffix]),
            ):
                TrialBalanceLine.objects.create(
                    financial_year=self.fy26, account_code=code,
                    account_name="Funds loaned to trust",
                    closing_balance=balance, source="tb_import",
                )
        TrialBalanceLine.objects.create(
            financial_year=self.fy26, account_code="4199",
            account_name="Undistributed income",
            closing_balance=Decimal("2882033.91"), source="tb_import",
        )

    def _rolled(self, code):
        return sum(
            (line.closing_balance or Decimal("0"))
            for line in TrialBalanceLine.objects.filter(
                financial_year=self.fy27, account_code=code
            )
        )

    def test_every_unitholder_loan_carries_forward(self):
        _populate_rolled_forward_fy(self.fy26, self.fy27)

        for suffix, expected in self.LOANS.items():
            with self.subTest(unitholder=suffix):
                self.assertEqual(self._rolled(f"4110.{suffix}"), expected)

    def test_each_unitholders_total_position_is_preserved(self):
        _populate_rolled_forward_fy(self.fy26, self.fy27)

        for suffix in ("01", "02"):
            closing_26 = (
                self.OPENING[suffix] + self.INTRODUCED[suffix] + self.LOANS[suffix]
            )
            opening_27 = sum(
                self._rolled(f"{parent}.{suffix}")
                for parent in ("4000", "4004", "4053", "4110")
            )
            with self.subTest(unitholder=suffix):
                self.assertEqual(opening_27, closing_26)

    def test_undistributed_income_is_not_inflated_by_a_loan_balance(self):
        """4199 opens at closing + net P&L; there is no tax provision here."""
        _populate_rolled_forward_fy(self.fy26, self.fy27)

        self.assertEqual(self._rolled("4199"), Decimal("2882033.91"))
