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
    CURRENT_LIABILITY_GROUP_ORDER,
    NONCURRENT_ASSET_GROUP_ORDER,
    NONCURRENT_LIABILITY_GROUP_ORDER,
    _build_subgrouped_items,
    _classify_current_asset,
    _classify_current_liability,
    _classify_noncurrent_asset,
    _classify_noncurrent_liability,
    _classify_security_tier,
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
        """HandiLedger splits Unsecured: from Secured: WITHIN the group.

        The security status is a tier inside "Financial Liabilities", not a
        group of its own -- carrying it in the group name produced two
        subtotals where HandiLedger prints one.
        """
        self.assertEqual(_classify_security_tier(_row("Bank loans")), "Secured:")
        self.assertEqual(
            _classify_noncurrent_liability(_row("Bank loans")),
            "Financial Liabilities",
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
            ["Financial Liabilities", "Unsecured:", "Secured:"],
        )
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(len(subtotals), 1)


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
        here. What matters is that the pair is never split into two groups
        each carrying its own subtotal, which is what made the hire purchase
        read as 20,280 gross with a stray 6,565 liability elsewhere.
        """
        items = [
            _row("Hire purchase", "-20280.50", "-28392.74", "3523"),
            _row("Less: Unexpired interest charges", "6565.19", "8757.00", "3524"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_liability, credit_normal=True,
            group_order=NONCURRENT_LIABILITY_GROUP_ORDER)

        group_headings = [r for r in rendered
                          if r.get("is_heading") and not r.get("is_nested")]
        self.assertLessEqual(
            len(group_headings), 1,
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


class SecurityTierNestingTests(SimpleTestCase):
    """HandiLedger nests two levels, and prints ONE subtotal for the group.

    DJLH Properties Pty Ltd FY2024, Non-Current Liabilities::

        Financial Liabilities
        Unsecured:
        Loan - Director                                617,466    511,697
        Loan - Jim Penman                              166,600    166,600
        Loan - Jim's Group                           1,467,400  1,467,400
        Loan - Li Penman Property Family Trust               -     66,421
        Loan - ALIC                                    876,257    914,107
        Loan - Jim's Properties                         10,000     10,000
        Secured:
        Bank loans                                     689,569    702,717
                                                     3,827,292  3,838,942

    StatementHub carried the security status in the heading itself --
    "Financial Liabilities" and "Financial Liabilities — Secured" -- which
    produced two groups and therefore two subtotals where HandiLedger prints
    one. "Unsecured:"/"Secured:" are a second tier inside the group.

    Current Liabilities nests the same way: HandiLedger prints "Payables"
    then "Unsecured:" above the trade creditors.
    """

    def _djlh_ncl(self):
        return [
            _row("Loan - Director", "-617466.24", "-511697.00", "3545"),
            _row("Loan - Jim Penman", "-166600.36", "-166600.36", "3546"),
            _row("Loan - Jim's Group", "-1467400.00", "-1467400.00", "3548"),
            _row("Loan - Li Penman Property Family Trust", "0", "-66421.00",
                 "3565"),
            _row("Loan - ALIC", "-876256.82", "-914107.00", "3566"),
            _row("Loan - Jim's Properties", "-10000.00", "-10000.00", "3567"),
            _row("Bank loans", "-689568.55", "-702717.00", "3625"),
        ]

    def _render(self, items):
        return _build_subgrouped_items(
            items, _classify_noncurrent_liability, credit_normal=True,
            group_order=NONCURRENT_LIABILITY_GROUP_ORDER)

    def test_a_bank_loan_keeps_the_financial_liabilities_group(self):
        """Security is a tier inside the group, not a group of its own."""
        self.assertEqual(
            _classify_noncurrent_liability(_row("Bank loans")),
            "Financial Liabilities",
        )

    def test_djlh_nests_unsecured_then_secured_under_one_heading(self):
        rendered = self._render(self._djlh_ncl())
        self.assertEqual(
            _headings(rendered),
            ["Financial Liabilities", "Unsecured:", "Secured:"],
        )

    def test_the_group_prints_exactly_one_subtotal(self):
        rendered = self._render(self._djlh_ncl())
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(
            len(subtotals), 1,
            "HandiLedger prints one subtotal for Financial Liabilities, "
            "not one per security tier",
        )
        self.assertEqual(subtotals[0]["cy_formatted"], "3,827,292")
        self.assertEqual(subtotals[0]["py_formatted"], "3,838,942")

    def test_the_subtotal_comes_after_both_tiers(self):
        rendered = self._render(self._djlh_ncl())
        kinds = [
            "subtotal" if r.get("is_subtotal")
            else r["account_name"] if r.get("is_heading")
            else "line"
            for r in rendered
        ]
        self.assertEqual(kinds[-1], "subtotal")
        self.assertLess(kinds.index("Secured:"), kinds.index("subtotal"))

    def test_only_the_tier_rows_are_marked_nested(self):
        rendered = self._render(self._djlh_ncl())
        nested = [r["account_name"] for r in rendered if r.get("is_nested")]
        self.assertEqual(nested, ["Unsecured:", "Secured:"])

    def test_a_tier_heading_carries_no_amounts(self):
        rendered = self._render(self._djlh_ncl())
        tier = next(r for r in rendered if r["account_name"] == "Unsecured:")
        self.assertEqual(tier["cy_formatted"], "")
        self.assertEqual(tier["py_formatted"], "")

    def test_payables_names_its_single_tier(self):
        """HandiLedger prints "Unsecured:" even when it is the only tier."""
        items = [_row("Trade creditors", "-1291.00", "-12353.00", "3000")]
        rendered = _build_subgrouped_items(
            items, _classify_current_liability, credit_normal=True,
            group_order=CURRENT_LIABILITY_GROUP_ORDER)
        self.assertEqual(_headings(rendered), ["Payables", "Unsecured:"])
        self.assertEqual(len([r for r in rendered if r.get("is_subtotal")]), 1)

    def test_a_lone_tiered_group_keeps_its_heading_and_subtotal(self):
        """The single-group flat shortcut must not swallow a nested group.

        Collapsing "Financial Liabilities — Secured" back into its parent
        leaves DJLH's non-current liabilities as ONE group. Returning a flat
        list there would drop the heading and the subtotal that already ship.
        """
        rendered = self._render(self._djlh_ncl())
        self.assertTrue(any(r.get("is_heading") for r in rendered))
        self.assertTrue(any(r.get("is_subtotal") for r in rendered))

    def test_an_untiered_group_gets_no_tier_heading(self):
        """DJLH FY2024's Current Tax Liabilities carry no security tier."""
        items = [
            _row("Trade creditors", "-1291.00", "-12353.00", "3000"),
            _row("GST payable control account", "-316467.00", "-182975.00",
                 "3200"),
            _row("Taxation", "0", "-98193.00", "3300"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_current_liability, credit_normal=True,
            group_order=CURRENT_LIABILITY_GROUP_ORDER)
        self.assertEqual(
            _headings(rendered),
            ["Payables", "Unsecured:", "Current Tax Liabilities"],
        )

    def test_a_contra_line_stays_inside_its_parents_tier(self):
        """Dr Services FY2026's hire purchase pair must not straddle a tier.

        The contra already inherits its parent's GROUP; it must inherit the
        parent's TIER too, or "Less: Unexpired interest charges" lands under
        "Unsecured:" while the hire purchase it reduces sits under "Secured:".
        """
        items = [
            _row("Hire purchase", "-20280.50", "-28392.74", "3523"),
            _row("Less: Unexpired interest charges", "6565.19", "8757.00",
                 "3524"),
            _row("Loan - Jewish Care", "-22382.24", "-13289.52", "3565"),
        ]
        rendered = self._render(items)
        names = [r["account_name"] for r in rendered]
        hp = names.index("Hire purchase")
        self.assertEqual(names[hp + 1], "Less: Unexpired interest charges")
        self.assertEqual(
            _headings(rendered),
            ["Financial Liabilities", "Unsecured:", "Secured:"],
        )
        self.assertLess(names.index("Secured:"), hp)

    def test_the_hire_purchase_pair_nets_inside_one_subtotal(self):
        items = [
            _row("Hire purchase", "-20280.50", "-28392.74", "3523"),
            _row("Less: Unexpired interest charges", "6565.19", "8757.00",
                 "3524"),
        ]
        rendered = self._render(items)
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(len(subtotals), 1)
        self.assertEqual(subtotals[0]["cy_formatted"], "13,715")


class CurrentLiabilityGroupOrderTests(SimpleTestCase):
    """The declared order must use the names the classifier actually returns.

    ``CURRENT_LIABILITY_GROUP_ORDER`` said "Bank Overdraft" and "Other" while
    ``_classify_current_liability`` returns "Bank Overdrafts" and "Other
    Current Liabilities", so neither matched and both groups silently fell to
    the end in first-seen order instead of the overdraft printing first.
    """

    def test_every_group_the_classifier_returns_is_ordered(self):
        returned = {
            _classify_current_liability(_row("Shift Overdraft *9989")),
            _classify_current_liability(_row("GST payable control account")),
            _classify_current_liability(_row("Trade creditors")),
            _classify_current_liability(_row("Accrued settlements")),
        }
        missing = returned - set(CURRENT_LIABILITY_GROUP_ORDER)
        self.assertEqual(missing, set(), f"unordered groups: {missing}")

    def test_a_bank_overdraft_prints_before_payables(self):
        items = [
            _row("Trade creditors", "-1291.00", "-12353.00", "3000"),
            _row("Shift Overdraft *9989", "-5000.00", "-4000.00", "2100"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_current_liability, credit_normal=True,
            group_order=CURRENT_LIABILITY_GROUP_ORDER)
        groups = [h for h in _headings(rendered) if not h.endswith(":")]
        self.assertEqual(groups, ["Bank Overdrafts", "Payables"])


class ASingleGroupStillGetsItsHeadingTests(SimpleTestCase):
    """HandiLedger prints the heading and subtotal even for a lone group.

    DJLH Properties Pty Ltd FY2024's Non-Current Liabilities hold one group,
    and HandiLedger prints it in full -- the group subtotal repeats the
    section total immediately below it::

        Non-Current Liabilities

        Financial Liabilities
        Unsecured: ... Secured: ...
                                                 3,827,292   3,838,942
        Total Non-Current Liabilities             3,827,292   3,838,942

    StatementHub returned a flat list whenever a section resolved to a single
    group, dropping both rows -- so a one-group section read as a bare list of
    accounts with no heading at all.
    """

    def test_a_lone_group_prints_its_heading(self):
        items = [
            _row("Motor vehicles (cost)", "38354.00", "38354.00", "2890"),
            _row("Less: Accumulated depreciation", "-19160.75", "-12763.00",
                 "2895"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_asset,
            group_order=NONCURRENT_ASSET_GROUP_ORDER)
        self.assertEqual(_headings(rendered),
                         ["Property, Plant and Equipment"])

    def test_a_lone_group_prints_its_subtotal(self):
        """Dr Services FY2026: 38,354 less 19,161 accumulated = 19,193."""
        items = [
            _row("Motor vehicles (cost)", "38354.00", "38354.00", "2890"),
            _row("Less: Accumulated depreciation", "-19160.75", "-12763.00",
                 "2895"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_asset,
            group_order=NONCURRENT_ASSET_GROUP_ORDER)
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(len(subtotals), 1)
        self.assertEqual(subtotals[0]["cy_formatted"], "19,193")
        self.assertEqual(subtotals[0]["py_formatted"], "25,591")

    def test_a_lone_untiered_liability_group_prints_both(self):
        """Elliott Jaques FY2025 carries GST alone in current liabilities."""
        items = [
            _row("GST payable control account", "-1689.00", "0", "3200"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_current_liability, credit_normal=True,
            group_order=CURRENT_LIABILITY_GROUP_ORDER)
        self.assertEqual(_headings(rendered), ["Current Tax Liabilities"])
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(len(subtotals), 1)
        self.assertEqual(subtotals[0]["cy_formatted"], "1,689")

    def test_the_contra_still_sits_under_its_parent(self):
        """The heading must not come between an asset and its contra."""
        items = [
            _row("Motor vehicles (cost)", "38354.00", "38354.00", "2890"),
            _row("Less: Accumulated depreciation", "-19160.75", "-12763.00",
                 "2895"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_noncurrent_asset,
            group_order=NONCURRENT_ASSET_GROUP_ORDER)
        names = [r["account_name"] for r in rendered]
        self.assertEqual(
            names.index("Less: Accumulated depreciation"),
            names.index("Motor vehicles (cost)") + 1,
        )

    def test_an_empty_section_renders_nothing(self):
        self.assertEqual(
            _build_subgrouped_items([], _classify_noncurrent_asset), [])
