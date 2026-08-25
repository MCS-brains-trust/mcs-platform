"""A bank loan is not a Division 7A exposure.

Found 2026-08-25 on DJLH Properties FY2025. Eva raised a CRITICAL Div 7A
finding titled "Div 7A Exposure — Account 3625 Bank Loans Debit $12,656.77",
asserting the debit balance was "indicating funds lent to a shareholder or
associate". Account 3625 is Bank Loans, mapped to BS-CL-002 Borrowings
(current) — money the company owes a bank. The $12,656.77 debit is the year's
repayments, not an advance to anyone.

``_check_div7a_loans`` treats any account whose code starts with "3" and whose
name contains a loan keyword as a candidate, and "Bank Loans" matches on the
word "loan". Division 7A concerns loans *to* shareholders and associates, so an
account owed to an institutional lender cannot qualify however it is named.

The second fix here is to the flag-to-check bucketing in eva_engine. Any flag
whose title contained "loan" was swept into the div7a bucket, including
"Significant variance: Loan - ALIC" and two other T1-VAR flags. Once the
finding key was derived from the union of that bucket, the Div 7A finding
claimed four accounts (3545, 3565, 3566, 3625) where only one carried a Div 7A
flag at all — pointing the accountant at accounts the finding was not about.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    AccountMapping,
    Client as ClientModel,
    Entity,
    FinancialYear,
    TrialBalanceLine,
)


class Div7ASkipsBankBorrowingsTests(TestCase):
    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Div7A Bank Client")
        self.entity = Entity.objects.create(
            entity_name="Borrower Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)

    def _mapping(self, code, label, section="Liabilities"):
        return AccountMapping.objects.get_or_create(
            standard_code=code, defaults={
                "line_item_label": label,
                "financial_statement": "balance_sheet",
                "statement_section": section})[0]

    def _line(self, code, name, debit, mapping=None):
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            debit=Decimal(debit), credit=Decimal("0"),
            closing_balance=Decimal(debit), mapped_line_item=mapping)

    def _run(self):
        from core.risk_engine import _check_div7a_loans, _load_trial_balance
        return _check_div7a_loans(_load_trial_balance(self.fy), {})

    def _rule_ids(self, flags):
        return {f["rule_id"] for f in flags}

    def test_a_bank_loan_with_a_debit_balance_is_not_div7a(self):
        """The DJLH false positive, exactly as reported."""
        self._line("3625", "Bank Loans", "12656.77",
                   self._mapping("BS-CL-002", "Borrowings (current)"))

        self.assertNotIn("T1-DIV7A-3625", self._rule_ids(self._run()))

    def test_a_shareholder_loan_with_a_debit_balance_still_is(self):
        """The rule must keep catching what it exists to catch."""
        self._line("3565", "Loan - Li Penman Property Family Trust",
                   "2752809.00",
                   self._mapping("BS-NCL-001", "Loans from related parties"))

        self.assertIn("T1-DIV7A-3565", self._rule_ids(self._run()))

    def test_an_account_named_bank_is_skipped_even_without_a_mapping(self):
        """Unmapped accounts are common mid-job; the name still tells us."""
        self._line("3630", "Bank Loan - NAB Term", "5000.00")

        self.assertNotIn("T1-DIV7A-3630", self._rule_ids(self._run()))

    def test_a_director_loan_is_untouched_by_the_bank_exclusion(self):
        self._line("3545", "Loan - Director", "48000.00",
                   self._mapping("BS-NCL-002", "Loans from related parties"))

        self.assertIn("T1-DIV7A-3545", self._rule_ids(self._run()))


class Div7ABucketTakesOnlyDiv7AFlagsTests(TestCase):
    """Variance flags must not widen a Div 7A finding's account set."""

    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Bucket Client")
        self.entity = Entity.objects.create(
            entity_name="Bucketed Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)

    def _bucket(self, flags):
        from core.eva_engine import _bucket_flag_for_div7a
        return [f for f in flags if _bucket_flag_for_div7a(f)]

    class _Flag:
        def __init__(self, rule_id, title, accounts):
            self.rule_id = rule_id
            self.title = title
            self.description = ""
            self.affected_accounts = accounts

    def test_a_variance_flag_is_not_a_div7a_flag(self):
        """These three widened the DJLH key to four accounts."""
        variance = self._Flag("T1-VAR-3566", "Significant variance: Loan - ALIC",
                              ["3566"])

        self.assertEqual(self._bucket([variance]), [])

    def test_a_real_div7a_flag_is_kept(self):
        real = self._Flag("T1-DIV7A-3565",
                          "Div 7A: Loan - Li Penman has debit balance", ["3565"])

        self.assertEqual(self._bucket([real]), [real])

    def test_a_flag_naming_division_7a_in_its_title_is_kept(self):
        """Module and synthetic flags carry no T1-DIV7A rule id."""
        synthetic = self._Flag("MODULE:div7a",
                               "Division 7A exposure requires agreement", ["3565"])

        self.assertEqual(self._bucket([synthetic]), [synthetic])

    def test_the_djlh_bucket_narrows_to_the_one_real_flag(self):
        """Regression: the exact five flags open on DJLH FY2025."""
        flags = [
            self._Flag("T1-VAR-3565", "Significant variance: Loan - Penman Properties", ["3565"]),
            self._Flag("T1-VAR-3545", "Significant variance: Loan - Li Penman", ["3545"]),
            self._Flag("T1-VAR-3566", "Significant variance: Loan - ALIC", ["3566"]),
            self._Flag("T1-DIV7A-3625", "Div 7A: Bank Loans has debit balance", ["3625"]),
            self._Flag("T1-VAR-3625", "Significant variance: Bank Loans", ["3625"]),
        ]

        kept = self._bucket(flags)

        self.assertEqual([f.rule_id for f in kept], ["T1-DIV7A-3625"])
