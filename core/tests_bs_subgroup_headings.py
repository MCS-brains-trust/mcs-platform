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


class ContraAccountsStayWithTheirParentTests(SimpleTestCase):
    """A "Less:" line belongs directly beneath the account it offsets.

    Dr Services Family Trust FY2026 carries a hire-purchase pair::

        3523  Hire purchase                     (20,280.50)
        3524  Less: Unexpired interest charges    6,565.19

    Classified on their own names, the parent is Secured and the contra is not,
    so sub-grouping put them under different headings -- leaving "Less:
    Unexpired interest charges" alone under Financial Liabilities, reading as a
    standalone positive liability instead of a deduction from the hire purchase
    above it.
    """

    def test_a_less_line_inherits_the_group_above_it(self):
        items = [
            _row("Hire purchase", "-20280.50", "-28392.74", "3523"),
            _row("Less: Unexpired interest charges", "6565.19", "8757.00", "3524"),
            _row("Loan - Jewish Care", "-22382.24", "-13289.52", "3565"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_liability, credit_normal=True,
            group_order=NONCURRENT_LIABILITY_GROUP_ORDER)

        names = [r["account_name"] for r in rendered]
        hp = names.index("Hire purchase")
        contra = names.index("Less: Unexpired interest charges")
        self.assertEqual(
            contra, hp + 1,
            f"the contra was separated from its parent: {names}",
        )
        # And no heading or subtotal may come between them.
        self.assertFalse(rendered[hp + 1].get("is_heading"))
        self.assertFalse(rendered[hp + 1].get("is_subtotal"))

    def test_the_pair_is_never_split_across_two_groups(self):
        """Net hire purchase is 20,280.50 - 6,565.19 = 13,715.31.

        With the contra inheriting its parent's group there is only one group
        here, and _build_subgrouped_items returns a flat list in that case --
        long-standing behaviour, unrelated to this fix. What matters is that
        the pair is never split into two groups each carrying its own
        subtotal, which is what made the hire purchase read as 20,280 gross
        with a stray 6,565 liability elsewhere.
        """
        items = [
            _row("Hire purchase", "-20280.50", "-28392.74", "3523"),
            _row("Less: Unexpired interest charges", "6565.19", "8757.00", "3524"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_liability, credit_normal=True,
            group_order=NONCURRENT_LIABILITY_GROUP_ORDER)

        self.assertLessEqual(
            len([r for r in rendered if r.get("is_heading")]), 1,
            "the pair was split across two groups",
        )
        amounts = [r["cy_amount"] for r in rendered if "cy_amount" in r]
        self.assertEqual(
            sum(amounts), D("-13715.31"),
            "the pair no longer nets to the hire purchase carrying value",
        )

    def test_accumulated_depreciation_stays_with_its_asset(self):
        items = [
            _row("Motor vehicles (cost)", "38354.00", "38354.00", "2890"),
            _row("Less: Accumulated depreciation", "-19160.75", "-12763.00", "2895"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_asset,
            group_order=NONCURRENT_ASSET_GROUP_ORDER)
        names = [r["account_name"] for r in rendered]
        self.assertEqual(
            names.index("Less: Accumulated depreciation"),
            names.index("Motor vehicles (cost)") + 1,
        )
