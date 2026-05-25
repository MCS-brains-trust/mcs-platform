"""
Regression test for Defect D — Cash & Cash Equivalents BS classification
========================================================================
Ensures the BS sub-classifier ``_classify_current_asset`` and the
sign-aware reclassifier ``_reclassify_sign_flips`` read the structured
``AccountMapping.standard_code`` first (BS-CA-001 = Cash and cash
equivalents) before falling back to the legacy account-name keyword
list (``_BANK_KEYWORDS``).

Defect D. See audit_hazaway_cash_classification_defect_d.md and
phase2_fix_cash_classification_defect_d.md.

NOTE on test surface: these tests call the lower-level helpers
(``_get_tb_sections``, ``_reclassify_sign_flips``, ``_build_subgrouped_items``,
``_classify_current_asset``, ``_classify_current_liability``,
``_sum_section``) directly rather than the orchestrator
``build_company_context``. Reason: ``build_company_context`` reaches an
unrelated ``strftime('%-d …')`` call (line ~1273) which is Linux-only;
on Windows local-test runs this raises ``ValueError: Invalid format
string`` before the BS classification code under test is touched. The
fix lives entirely inside the helpers exercised here.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.fs_template_service import (
    _build_subgrouped_items,
    _classify_current_asset,
    _classify_current_liability,
    _format_lines,
    _get_tb_sections,
    _reclassify_sign_flips,
    _sum_section,
)
from core.models import (
    AccountMapping,
    Client,
    Entity,
    FinancialYear,
    TrialBalanceLine,
)


STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _walk_subgrouped(section_list):
    """Walk a current_assets / current_liabilities list and yield
    ``(sub_heading, item)`` tuples for every body line item.

    ``_build_subgrouped_items`` interleaves sub-heading rows
    (``{"is_heading": True, "account_name": <label>}``), line item rows
    (with ``cy_amount`` + ``cy_formatted``), and subtotal rows
    (``{"is_subtotal": True}``).
    """
    current_sub = None
    for entry in section_list:
        if entry.get("is_heading"):
            current_sub = entry.get("account_name")
            continue
        if entry.get("is_subtotal"):
            continue
        yield current_sub, entry


def _render_ca_cl(fy):
    """Run the BS classification pipeline end-to-end (minus the orchestrator).

    Returns ``(current_assets_list, current_liabilities_list, sections)``
    where the two lists are the post-``_build_subgrouped_items`` shape
    used by the docxtpl template.
    """
    sections = _get_tb_sections(fy)
    _reclassify_sign_flips(sections)
    ca = _build_subgrouped_items(
        sections["current_assets"], _classify_current_asset,
    )
    cl = _build_subgrouped_items(
        sections["current_liabilities"], _classify_current_liability,
        credit_normal=True,
    )
    return ca, cl, sections


@override_settings(STORAGES=STORAGES_OVERRIDE)
class CashClassificationTests(TestCase):
    """5 cases: std_code predicate, keyword fallback, sign-aware
    reclassification, non-cash std_code, Net Assets invariant."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Cash Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Cash Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        # AccountMappings — the structured target of TrialBalanceLine.mapped_line_item
        cls.cash_mapping = AccountMapping.objects.create(
            standard_code="BS-CA-001",
            line_item_label="Cash and cash equivalents",
            financial_statement="balance_sheet",
            statement_section="Current Assets",
        )
        cls.other_ca_mapping = AccountMapping.objects.create(
            standard_code="BS-CA-005",
            line_item_label="Other current assets",
            financial_statement="balance_sheet",
            statement_section="Current Assets",
        )

    def _make_row(self, code, name, closing, mapping=None):
        """Create a tb_import-style TrialBalanceLine."""
        c = Decimal(closing)
        return TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code=code,
            account_name=name,
            opening_balance=Decimal("0"),
            debit=c if c > 0 else Decimal("0"),
            credit=abs(c) if c < 0 else Decimal("0"),
            closing_balance=c,
            source="tb_import",
            mapped_line_item=mapping,
        )

    # ------------------------------------------------------------------
    # Test 1 — structured standard_code routes to Cash and Cash Equivalents
    # even when the account name contains no cash/bank keyword.
    # ------------------------------------------------------------------
    def test_standard_code_predicate_routes_to_cash(self):
        # Hazaway 2437 pattern: bank account named after the entity, mapped to BS-CA-001,
        # positive balance — keyword list would miss it.
        self._make_row("2437", "HAZAWAY OPERATIONS PTY LTD", "9816.00",
                       mapping=self.cash_mapping)
        # Add a debtor so we get two sub-groups; _build_subgrouped_items only
        # emits sub-heading rows when there's more than one group (line ~744
        # early-returns a flat list otherwise).
        self._make_row("2199", "Trade debtors", "1000.00", mapping=None)

        ca, _, _ = _render_ca_cl(self.fy)
        matches = [
            (sub, item)
            for sub, item in _walk_subgrouped(ca)
            if "HAZAWAY OPERATIONS" in (item.get("account_name") or "")
        ]
        self.assertEqual(len(matches), 1,
                         f"Expected exactly one HAZAWAY row in current_assets; got {matches!r}")
        sub, _ = matches[0]
        self.assertEqual(
            sub, "Cash and Cash Equivalents",
            f"HAZAWAY row must classify under 'Cash and Cash Equivalents' "
            f"(mapped to BS-CA-001); got sub={sub!r}",
        )

    # ------------------------------------------------------------------
    # Test 2 — unmapped account with bank keyword still classifies as
    # Cash and Cash Equivalents via the name fallback.
    # ------------------------------------------------------------------
    def test_keyword_fallback_for_unmapped_accounts(self):
        self._make_row("2440", "ANZ Operating Account", "5000.00", mapping=None)
        # Add a debtor so we get two sub-groups and _build_subgrouped_items
        # emits sub-heading rows (single-group path returns a flat list).
        self._make_row("2199", "Trade debtors", "1000.00", mapping=None)

        ca, _, _ = _render_ca_cl(self.fy)
        matches = [
            (sub, item)
            for sub, item in _walk_subgrouped(ca)
            if "ANZ Operating" in (item.get("account_name") or "")
        ]
        self.assertEqual(len(matches), 1,
                         f"Expected exactly one ANZ row; got {matches!r}")
        sub, _ = matches[0]
        self.assertEqual(
            sub, "Cash and Cash Equivalents",
            f"Unmapped ANZ account must fall through to 'Cash and Cash Equivalents' "
            f"via keyword fallback; got sub={sub!r}",
        )

    # ------------------------------------------------------------------
    # Test 3 — negative-balance cash account is sign-flipped to
    # current_liabilities under 'Bank Overdrafts' via standard_code,
    # despite no bank keyword in the name.
    # ------------------------------------------------------------------
    def test_sign_aware_reclassification_via_standard_code(self):
        # Hazaway 2436 pattern: "Shift Overdraft *9989" — no _BANK_KEYWORDS hit
        # (no "bank"/"cash"/known-brand) but mapped to BS-CA-001, negative balance.
        self._make_row("2436", "Shift Overdraft *9989", "-56060.70",
                       mapping=self.cash_mapping)
        # Add a non-cash liability so _build_subgrouped_items emits sub-headings
        # (single-group path returns a flat list without headings).
        self._make_row("3000", "Trade creditors", "-1000.00", mapping=None)

        ca, cl, _ = _render_ca_cl(self.fy)

        # Must NOT appear anywhere in current_assets.
        ca_matches = [
            (sub, item)
            for sub, item in _walk_subgrouped(ca)
            if "Shift Overdraft" in (item.get("account_name") or "")
        ]
        self.assertEqual(
            ca_matches, [],
            f"Shift Overdraft must not appear in current_assets after sign-aware "
            f"reclassification; got {ca_matches!r}",
        )

        # MUST appear in current_liabilities under 'Bank Overdrafts'.
        cl_matches = [
            (sub, item)
            for sub, item in _walk_subgrouped(cl)
            if "Shift Overdraft" in (item.get("account_name") or "")
        ]
        self.assertEqual(len(cl_matches), 1,
                         f"Expected exactly one Shift Overdraft row in current_liabilities; "
                         f"got {cl_matches!r}")
        sub, item = cl_matches[0]
        self.assertEqual(
            sub, "Bank Overdrafts",
            f"Shift Overdraft must classify under 'Bank Overdrafts'; got sub={sub!r}",
        )
        # Sign-flipped display: -56,060.70 stored, _format_lines(credit_normal=True)
        # negates for display → positive 56,060.70, format_amount rounds → "56,061".
        self.assertEqual(
            item.get("cy_formatted"), "56,061",
            f"Sign-flipped display amount must be positive '56,061'; "
            f"got {item.get('cy_formatted')!r}",
        )

    # ------------------------------------------------------------------
    # Test 4 — non-cash standard_code stays in Other Current Assets
    # (predicate does not over-promote).
    # ------------------------------------------------------------------
    def test_non_cash_standard_code_stays_in_other_current_assets(self):
        # BS-CA-005 = Other current assets. Random name with no cash keyword.
        self._make_row("2495", "Bond Deposit", "1500.00",
                       mapping=self.other_ca_mapping)
        # Add a debtor so we get two sub-groups and headings are emitted.
        self._make_row("2199", "Trade debtors", "1000.00", mapping=None)

        ca, _, _ = _render_ca_cl(self.fy)
        matches = [
            (sub, item)
            for sub, item in _walk_subgrouped(ca)
            if "Bond Deposit" in (item.get("account_name") or "")
        ]
        self.assertEqual(len(matches), 1, f"Expected exactly one Bond Deposit row; got {matches!r}")
        sub, _ = matches[0]
        self.assertEqual(
            sub, "Other Current Assets",
            f"BS-CA-005-mapped row with no cash keyword must classify under "
            f"'Other Current Assets'; got sub={sub!r}",
        )

    # ------------------------------------------------------------------
    # Test 5 — Net Assets invariant across the sign-aware move.
    # A cash-mapped negative row reclassified to liabilities must NOT
    # change Net Assets (within-balance-sheet move, not a P&L change).
    # ------------------------------------------------------------------
    def test_net_assets_invariant_across_reclassification(self):
        # Cash positive (stays in CA) + Cash negative (moves to CL).
        # Raw asset-side algebra:
        #   sum(current_assets raw) = 1000 + (-500) = 500  (before reclassification)
        # After reclassification:
        #   current_assets raw =  1000
        #   current_liabilities raw (credit-normal) = -500
        #   Net Assets = total_assets - (-_sum(liabilities)) = 1000 - 500 = 500
        # Either way, Net Assets = 500.
        self._make_row("2440", "ANZ Operating Account", "1000.00",
                       mapping=self.cash_mapping)
        self._make_row("2441", "ANZ Overdraft Sub-Account", "-500.00",
                       mapping=self.cash_mapping)

        sections = _get_tb_sections(self.fy)
        # Compute Net Assets BEFORE reclassification (treating CA raw sum as-is).
        net_before = (
            _sum_section(sections["current_assets"])
            + _sum_section(sections["noncurrent_assets"])
            - (-_sum_section(sections["current_liabilities"]))
            - (-_sum_section(sections["noncurrent_liabilities"]))
        )
        # Apply the same pipeline build_company_context applies.
        _reclassify_sign_flips(sections)
        net_after = (
            _sum_section(sections["current_assets"])
            + _sum_section(sections["noncurrent_assets"])
            - (-_sum_section(sections["current_liabilities"]))
            - (-_sum_section(sections["noncurrent_liabilities"]))
        )
        self.assertEqual(
            net_before, Decimal("500"),
            f"Setup error: expected net_before = 500; got {net_before}",
        )
        self.assertEqual(
            net_after, net_before,
            f"Net Assets must be unchanged by sign-aware reclassification; "
            f"got before={net_before}, after={net_after}",
        )
