"""Balance sheet sub-groups and their wording follow HandiLedger.

DJLH Properties Pty Ltd FY2024, HandiLedger, nests every section::

    Current Assets
      Cash Assets                                    817       622
      Receivables                                     --     1,500
      Other                                           --  3,415,000
    Non-Current Assets
      Receivables                              3,331,159        --
      Property, Plant and Equipment            1,006,628  1,006,628
    Current Liabilities
      Payables / Unsecured:                        1,291    12,353
      Current Tax Liabilities                    316,467   281,168
    Non-Current Liabilities
      Financial Liabilities / Unsecured: ... Secured:  3,827,292

StatementHub sub-grouped only the two CURRENT sections, so non-current assets
and non-current liabilities were flat lists with no heading and no subtotal.
Its wording also differed: "Cash and Cash Equivalents" for "Cash Assets",
"Tax Liabilities" for "Current Tax Liabilities", "Other Current Assets" for
"Other".
"""
from decimal import Decimal

from django.test import SimpleTestCase

from core.fs_template_service import (
    NONCURRENT_ASSET_GROUP_ORDER,
    NONCURRENT_LIABILITY_GROUP_ORDER,
    _build_subgrouped_items,
    _classify_current_asset,
    _classify_current_liability,
    _classify_noncurrent_asset,
    _classify_noncurrent_liability,
)

D = Decimal


def _row(name, cy="0", py="0", code="", standard_code=None):
    return {"account_code": code, "account_name": name,
            "cy_amount": D(cy), "py_amount": D(py),
            "standard_code": standard_code}


def _headings(rendered):
    return [r["account_name"] for r in rendered if r.get("is_heading")]


class HandiLedgerWordingTests(SimpleTestCase):
    def test_cash_is_headed_cash_assets(self):
        self.assertEqual(
            _classify_current_asset(_row("Cash at bank", standard_code="BS-CA-001")),
            "Cash Assets",
        )

    def test_the_residual_current_asset_group_is_headed_other(self):
        self.assertEqual(
            _classify_current_asset(_row("Accrued settlements")), "Other")

    def test_current_tax_liabilities_carries_the_word_current(self):
        self.assertEqual(
            _classify_current_liability(_row("GST payable control account")),
            "Current Tax Liabilities",
        )


class NonCurrentAssetGroupingTests(SimpleTestCase):
    def test_a_loan_is_a_receivable_and_property_is_ppe(self):
        items = [
            _row("7 Tinarra Court Kilsyth", "1006627.56", "1006627.56", "2800"),
            _row("Loan - Li Penman Property Family Trust",
                 "3331158.98", "0", "3565"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_asset,
            group_order=NONCURRENT_ASSET_GROUP_ORDER)
        self.assertEqual(
            _headings(rendered),
            ["Receivables", "Property, Plant and Equipment"],
            "HandiLedger lists Receivables before Property, Plant and Equipment; "
            "trial-balance order must not decide it",
        )

    def test_each_group_gets_a_subtotal(self):
        items = [
            _row("7 Tinarra Court Kilsyth", "1006627.56", "1006627.56", "2800"),
            _row("Loan - Li Penman Property Family Trust",
                 "3331158.98", "0", "3565"),
        ]
        rendered = _build_subgrouped_items(items, _classify_noncurrent_asset)
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(
            len(subtotals), 2,
            "non-current assets were a flat list with no subtotal per group",
        )


class NonCurrentLiabilityGroupingTests(SimpleTestCase):
    def test_loans_group_under_financial_liabilities(self):
        self.assertEqual(
            _classify_noncurrent_liability(_row("Loan - Jim's Group")),
            "Financial Liabilities",
        )

    def test_a_bank_loan_is_secured(self):
        """HandiLedger splits Unsecured: from Secured: within the group."""
        self.assertEqual(
            _classify_noncurrent_liability(_row("Bank loans")),
            "Financial Liabilities — Secured",
        )

    def test_djlh_non_current_liabilities_group_as_handiledger_does(self):
        items = [
            _row("Loan - Director", "-752512.34", "-617466.24", "3545"),
            _row("Loan - Jim Penman", "-166600.36", "-166600.36", "3546"),
            _row("Loan - Jim's Group", "-1467400.00", "-1467400.00", "3548"),
            _row("Loan - ALIC", "-893676.50", "-876256.82", "3566"),
            _row("Loan - Jim's Properties", "-10000.00", "-10000.00", "3567"),
            _row("Bank loans", "-676911.78", "-689568.55", "3625"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_liability, credit_normal=True,
            group_order=NONCURRENT_LIABILITY_GROUP_ORDER)
        self.assertEqual(
            _headings(rendered),
            ["Financial Liabilities", "Financial Liabilities — Secured"],
        )
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(len(subtotals), 2)
