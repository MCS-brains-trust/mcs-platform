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
    _is_contra_line,
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


class AContraSitsWithTheAccountItReducesTests(SimpleTestCase):
    """Contra lines are paired to their parent by account code.

    ``_interleave_subaccounts`` pairs dotted sub-accounts on their SUFFIX, and
    Berwick Mechanical Services' hire-purchase codes do not carry matching
    suffixes: FY2022 holds

        3523.02  Hire Purchase - Toyota Landcruiser
        3523.03  Hire Purchase - Kluger
        3524     Less: Unexpired interest charges     (Toyota's)
        3524.01  Unexpired HP - Kluger

    Plain codes sort ahead of every dotted one and ".01" ahead of ".03", so
    both contras were stranded above both parents -- and once the security
    tier landed, the contras printed under "Unsecured:" while the hire
    purchases they reduce printed under "Secured:".

    A contra now attaches to the parent whose base code is nearest below its
    own (3524 -> 3523), preferring a parent with the same suffix, and
    otherwise claiming them in suffix order. Hazaway Operations' five pairs
    carry matching suffixes and are already adjacent, so nothing moves there.
    """

    def _render(self, items):
        return _build_subgrouped_items(
            items, _classify_noncurrent_liability, credit_normal=True,
            group_order=NONCURRENT_LIABILITY_GROUP_ORDER)

    def _pairs(self, rendered):
        """Each line and the line directly above it, headings excluded."""
        names = [r["account_name"] for r in rendered
                 if not r.get("is_heading") and not r.get("is_subtotal")]
        return names

    def test_unexpired_interest_is_recognised_as_a_contra(self):
        """Berwick FY2022 names it "Unexpired HP - Kluger", not "Less: ..."."""
        self.assertTrue(_is_contra_line(_row("Unexpired HP - Kluger")))
        self.assertTrue(
            _is_contra_line(_row("Unexpired Hire Purchase Liability - Porsche")))
        self.assertTrue(_is_contra_line(_row("Less: Unexpired interest charges")))
        self.assertFalse(_is_contra_line(_row("Hire Purchase - Kluger")))

    def test_a_plain_contra_follows_its_dotted_parent(self):
        """Berwick FY2021: 3524 belongs under 3523.02."""
        items = [
            _row("Hire Purchase - Toyota Landcruiser", "-38696.25", "0",
                 "3523.02"),
            _row("Less: Unexpired interest charges", "7275.75", "0", "3524"),
            _row("Loans from Greg Smart", "0", "-7000.00", "3565"),
            _row("Loan - Directors", "-14570.00", "-26475.00", "3566"),
        ]
        names = self._pairs(self._render(items))
        self.assertEqual(
            names.index("Less: Unexpired interest charges"),
            names.index("Hire Purchase - Toyota Landcruiser") + 1,
            f"the contra was stranded away from its parent: {names}",
        )

    def test_the_pair_lands_in_the_secured_tier_together(self):
        items = [
            _row("Hire Purchase - Toyota Landcruiser", "-38696.25", "0",
                 "3523.02"),
            _row("Less: Unexpired interest charges", "7275.75", "0", "3524"),
            _row("Loan - Directors", "-14570.00", "-26475.00", "3566"),
        ]
        rendered = self._render(items)
        names = [r["account_name"] for r in rendered]
        secured = names.index("Secured:")
        self.assertLess(secured, names.index("Hire Purchase - Toyota Landcruiser"))
        self.assertLess(secured, names.index("Less: Unexpired interest charges"))

    def test_two_mismatched_pairs_each_find_their_own_parent(self):
        """Berwick FY2022: 3524 -> 3523.02 (Toyota), 3524.01 -> 3523.03."""
        items = [
            _row("Hire Purchase - Toyota Landcruiser", "0", "-38696.25",
                 "3523.02"),
            _row("Hire Purchase - Kluger", "-63265.66", "0", "3523.03"),
            _row("Less: Unexpired interest charges", "0", "7275.75", "3524"),
            _row("Unexpired HP - Kluger", "4537.46", "0", "3524.01"),
            _row("Loan - Directors", "-59988.00", "-14570.00", "3566"),
        ]
        names = self._pairs(self._render(items))
        self.assertEqual(
            names.index("Less: Unexpired interest charges"),
            names.index("Hire Purchase - Toyota Landcruiser") + 1,
        )
        self.assertEqual(
            names.index("Unexpired HP - Kluger"),
            names.index("Hire Purchase - Kluger") + 1,
        )

    def test_all_four_hire_purchase_lines_are_secured(self):
        items = [
            _row("Hire Purchase - Toyota Landcruiser", "0", "-38696.25",
                 "3523.02"),
            _row("Hire Purchase - Kluger", "-63265.66", "0", "3523.03"),
            _row("Less: Unexpired interest charges", "0", "7275.75", "3524"),
            _row("Unexpired HP - Kluger", "4537.46", "0", "3524.01"),
            _row("Loan - Directors", "-59988.00", "-14570.00", "3566"),
        ]
        rendered = self._render(items)
        self.assertEqual(
            _headings(rendered),
            ["Financial Liabilities", "Unsecured:", "Secured:"],
        )
        names = [r["account_name"] for r in rendered]
        self.assertEqual(names.index("Loan - Directors"),
                         names.index("Unsecured:") + 1)
        # Group subtotal: 63,266 - 4,537 + 59,988 = 118,716 (Berwick FY2022)
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(len(subtotals), 1)
        self.assertEqual(subtotals[0]["cy_formatted"], "118,716")

    def test_matched_suffixes_are_left_as_they_are(self):
        """Hazaway Operations FY2024: 3245.0N already pairs with 3244.0N."""
        items = [
            _row("HP - Porsche Boxster CL", "-122028.00", "0", "3244.01"),
            _row("HP - 2022 Fuso Fighter XW86HQ", "-181156.00", "0", "3244.02"),
            _row("Unexpired HP - Porsche Boxster CL", "17572.00", "0",
                 "3245.01"),
            _row("Unexpired HP - 2022 Fuso Fighter XW86HQ", "35734.00", "0",
                 "3245.02"),
        ]
        names = self._pairs(self._render(items))
        self.assertEqual(names, [
            "HP - Porsche Boxster CL",
            "Unexpired HP - Porsche Boxster CL",
            "HP - 2022 Fuso Fighter XW86HQ",
            "Unexpired HP - 2022 Fuso Fighter XW86HQ",
        ])

    def test_an_already_adjacent_plain_pair_does_not_move(self):
        """Berwick FY2017/18 and Dr Services carry both codes undotted."""
        items = [
            _row("Bank loans - CBA", "-100000.00", "0", "3500"),
            _row("Hire purchase", "-28392.74", "0", "3523"),
            _row("Less: Unexpired interest charges", "8757.00", "0", "3524"),
            _row("Loans from Greg Smart", "-7000.00", "0", "3565"),
        ]
        names = self._pairs(self._render(items))
        self.assertEqual(names, [
            "Loans from Greg Smart",
            "Bank loans - CBA",
            "Hire purchase",
            "Less: Unexpired interest charges",
        ])

    def test_a_contra_with_no_parent_below_it_stays_put(self):
        items = [
            _row("Less: Unexpired interest charges", "8757.00", "0", "3524"),
            _row("Loan - Directors", "-14570.00", "0", "3566"),
        ]
        names = self._pairs(self._render(items))
        self.assertEqual(names[0], "Less: Unexpired interest charges")

    def test_a_contra_with_no_account_code_stays_put(self):
        items = [
            _row("Loan - Directors", "-14570.00", "0", "3566"),
            _row("Less: something unnumbered", "500.00", "0", ""),
        ]
        names = self._pairs(self._render(items))
        self.assertEqual(names, ["Loan - Directors",
                                 "Less: something unnumbered"])


class CurrentFinancialLiabilitiesTests(SimpleTestCase):
    """Current-liability loans and hire purchases head "Financial Liabilities".

    HandiLedger, Scarton Family Trust FY2024 (``handiledger_reference/
    Financial Statements SCARFT.pdf``), Current Liabilities::

        Financial Liabilities
        Unsecured:
        Hire purchase - BMW                        33,671    52,912
        Less: Unexpired interest charges          (2,064)   (4,902)
        Beneficiary loan: Elio Scarton            113,647    94,407
        Beneficiary loan: Jess Scarton            269,621   291,240
                                                  414,875   433,657

        Current Tax Liabilities
        GST payable control account                   584       584

    ``_classify_current_liability`` had no Financial Liabilities group at all,
    so every one of those lines fell into the residual "Other Current
    Liabilities" -- untiered, and named nothing like HandiLedger. It is live on
    Dr Services Family Trust FY2026, whose beneficiary loan is the only
    client-facing instance.

    The master chart is the evidence for what belongs here: HandiLedger's
    current-liability block runs Bank loans, Trade creditors, Bills of
    exchange, Other loans, Hire purchase and its contra, the cards, Debentures
    and Lease liabilities (3000-3155, mirrored at 3156-3299). Trade creditors
    head "Payables" in HandiLedger's own DJLH report, so they stay there; the
    rest are financial liabilities.
    """

    def test_a_beneficiary_loan_is_a_financial_liability(self):
        """Dr Services FY2026. Both namings: the trial balance carries
        "Funds loaned to trust", and beneficiary netting renames the row."""
        self.assertEqual(
            _classify_current_liability(
                _row("Funds loaned to trust — Ronen Davidov", code="4110.01")),
            "Financial Liabilities",
        )
        self.assertEqual(
            _classify_current_liability(
                _row("Beneficiary loan: Ronen Davidov", code="BEN_4110")),
            "Financial Liabilities",
        )

    def test_hire_purchase_and_its_abbreviation_agree(self):
        """Hazaway writes both "HP - Porsche Boxster CL" and "Hire Purchase - "."""
        for name in ("Hire purchase - BMW", "HP - Porsche Boxster CL",
                     "Chattel Mortgage - Fuso Truck 2020",
                     "Loan Current - 2022 Fuso Fighter XW86HQ"):
            self.assertEqual(
                _classify_current_liability(_row(name)),
                "Financial Liabilities", name,
            )

    def test_the_chart_block_all_lands_in_one_group(self):
        for name in ("Bank loans", "Bills of exchange", "Other loans",
                     "Debentures", "Lease liabilities", "Fleet Card",
                     "Altitude Black Card", "Credit card - Amex"):
            self.assertEqual(
                _classify_current_liability(_row(name)),
                "Financial Liabilities", name,
            )

    def test_the_groups_handiledger_already_prints_are_untouched(self):
        """DJLH's Payables and Current Tax Liabilities must not move."""
        self.assertEqual(
            _classify_current_liability(_row("Trade creditors")), "Payables")
        self.assertEqual(
            _classify_current_liability(_row("Superannuation payable")),
            "Payables")
        self.assertEqual(
            _classify_current_liability(_row("GST payable control account")),
            "Current Tax Liabilities")
        self.assertEqual(
            _classify_current_liability(_row("Creditors - ATO")),
            "Current Tax Liabilities")
        self.assertEqual(
            _classify_current_liability(
                _row("Cash at bank - Overdraft *9989",
                     standard_code="BS-CA-001")),
            "Bank Overdrafts")
        self.assertEqual(
            _classify_current_liability(
                _row("Amounts withheld from salary & wages")),
            "Other Current Liabilities")

    def test_financial_liabilities_prints_before_payables(self):
        """Chart order: Bank loans 3000 comes before Trade creditors 3048."""
        order = list(CURRENT_LIABILITY_GROUP_ORDER)
        self.assertIn("Financial Liabilities", order)
        self.assertLess(order.index("Financial Liabilities"),
                        order.index("Payables"))
        self.assertLess(order.index("Payables"),
                        order.index("Current Tax Liabilities"))

    def test_the_scarton_current_liabilities_render(self):
        """Its group subtotal is 414,875 / 433,657 and there is one of them.

        The tier split differs from that report on purpose: HandiLedger shows
        the BMW hire purchase as unsecured, and Elio ruled a hire purchase is
        secured over the asset by law, so it prints under "Secured:" here.
        The grouping and the group total are what must match.
        """
        items = [
            _row("Hire purchase - BMW", "-33671.00", "-52912.00", "3100"),
            _row("Less: Unexpired interest charges", "2064.00", "4902.00",
                 "3101"),
            _row("Beneficiary loan: Elio Scarton", "-113647.00", "-94407.00",
                 "BEN_1"),
            _row("Beneficiary loan: Jess Scarton", "-269621.00", "-291240.00",
                 "BEN_2"),
            _row("GST payable control account", "-584.00", "-584.00", "3380"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_current_liability, credit_normal=True,
            group_order=CURRENT_LIABILITY_GROUP_ORDER)
        self.assertEqual(
            _headings(rendered),
            ["Financial Liabilities", "Unsecured:", "Secured:",
             "Current Tax Liabilities"],
        )
        subtotals = [r for r in rendered if r.get("is_subtotal")]
        self.assertEqual(len(subtotals), 2)
        self.assertEqual(subtotals[0]["cy_formatted"], "414,875")
        self.assertEqual(subtotals[0]["py_formatted"], "433,657")

    def test_the_hire_purchase_pair_stays_together_in_the_tier(self):
        items = [
            _row("Hire purchase - BMW", "-33671.00", "-52912.00", "3100"),
            _row("Less: Unexpired interest charges", "2064.00", "4902.00",
                 "3101"),
            _row("Beneficiary loan: Elio Scarton", "-113647.00", "-94407.00",
                 "BEN_1"),
        ]
        rendered = _build_subgrouped_items(
            items, _classify_current_liability, credit_normal=True,
            group_order=CURRENT_LIABILITY_GROUP_ORDER)
        names = [r["account_name"] for r in rendered]
        self.assertEqual(
            names.index("Less: Unexpired interest charges"),
            names.index("Hire purchase - BMW") + 1,
        )

    def test_a_card_is_grouped_however_it_is_named(self):
        """Hazaway's account 3142 is named two ways across two years.

        FY2024 calls it "Credit card - Amex" and FY2025 "American Express
        Platinum Busi". Grouping on the name alone put one year's spelling in
        Financial Liabilities and the other's in Other Current Liabilities --
        the same account in two different groups depending on the year.
        """
        for name in ("Credit card - Amex", "American Express Platinum Busi",
                     "Hazaway Credit Card", "Visa card", "Mastercard"):
            self.assertEqual(
                _classify_current_liability(_row(name, code="3142")),
                "Financial Liabilities", name,
            )
