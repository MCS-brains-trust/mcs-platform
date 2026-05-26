"""
Regression test for Defect B.2 — docgen section classifier sibling
===================================================================
Mirrors core/tests_fs_template_service_section_classification.py
(Defect B Phase 2 / commit aac5e1a) for the parallel classifier in
``core/docgen.py:_get_tb_sections`` that drives Management Accounts.

Defect B Phase 1 documented the mechanism — per-row account_name keyword
classification routes rollover ('Sales') and tb_import ('Asbestos Removal')
rows for the same account_code into different sections (trading_income vs
income), producing two P&L lines per code. This file verifies the
Defect B.2 fix in docgen (predicate-first standard_code classifier, with
keyword fallback on a single representative row per code).

NOTE on test surface: docgen's ``_get_tb_sections`` returns 4-tuples
``(account_code, account_name, current_amount, prior_amount)``, NOT the
dicts that fs_template_service uses. The tests parse tuple positions
directly. Test 5 is a cross-renderer PARITY test asserting both
implementations produce equivalent section assignment for the same
fixture (allowing for the dict-vs-tuple shape difference).

NOTE on tiebreaker: docgen does not implement the ``created_at``
weight-tie tiebreaker that fs_template_service got in aac5e1a. The
asymmetry is intentional and documented in the docgen Defect B.2 commit
— adding the tiebreaker would require either tuple-shape changes (out
of scope per spec) or invasive per-name sidecar plumbing. The
renderer-dedup refactor is the right place to unify behaviour.

Defect B.2. See phase_b2_docgen_section_classifier.md.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core import docgen
from core import fs_template_service
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


def _find_tuple_in_section(sections, section_name, account_code):
    """Return the merged 4-tuple for account_code in section_name, or None.

    docgen tuples: ``(account_code, account_name, current_amount, prior_amount)``.
    """
    for entry in sections.get(section_name, []):
        if str(entry[0]).split(".")[0] == account_code:
            return entry
    return None


def _count_tuple_across_sections(sections, account_code):
    """Return total number of tuples for account_code across all sections."""
    total = 0
    for items in sections.values():
        for entry in items:
            if str(entry[0]).split(".")[0] == account_code:
                total += 1
    return total


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DocgenSectionClassificationTests(TestCase):
    """5 cases for the Defect B.2 classifier in docgen."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Docgen Section Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Docgen Section Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        cls.rev_001 = AccountMapping.objects.create(
            standard_code="IS-REV-001",
            line_item_label="Revenue",
            financial_statement="income_statement",
            statement_section="Revenue",
        )
        cls.rev_002 = AccountMapping.objects.create(
            standard_code="IS-REV-002",
            line_item_label="Other revenue",
            financial_statement="income_statement",
            statement_section="Revenue",
        )

    def _make_row(self, code, name, current, prior, mapping=None, source="tb_import"):
        """Create a TrialBalanceLine with closing_balance=current and
        prior_debit/credit split to give prior_amount=prior in docgen.

        docgen's ``_get_tb_sections`` at line ~553-554:
            current_amount = line.closing_balance
            prior_amount = line.prior_debit - line.prior_credit
        Unlike fs_template_service, docgen does NOT branch by source for
        these — both rollover and tb_import use the same expressions.
        """
        c = Decimal(str(current))
        p = Decimal(str(prior))
        return TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code=code,
            account_name=name,
            opening_balance=Decimal("0"),
            debit=c if c > 0 else Decimal("0"),
            credit=abs(c) if c < 0 else Decimal("0"),
            closing_balance=c,
            prior_debit=p if p > 0 else Decimal("0"),
            prior_credit=abs(p) if p < 0 else Decimal("0"),
            source=source,
            mapped_line_item=mapping,
        )

    # ------------------------------------------------------------------
    # Test 1 — central fix: IS-REV-001 routes to trading_income; rollover
    # + tb_import for the same code merge into ONE tuple with the
    # tb_import name winning display (via existing weight-10/1 logic).
    # ------------------------------------------------------------------
    def test_isrev001_merges_rollover_and_tb_import_in_trading_income(self):
        # Hazaway 0630 pattern: rollover 'Sales' (no CY) + tb_import
        # 'Asbestos Removal' (CY only), both mapped to IS-REV-001.
        # docgen rollover P&L rows have closing_balance=0 per core/views.py
        # Pass 3, so current=0 → weight=1; tb_import current=non-zero → weight=10.
        self._make_row("0630", "Sales", "0", "-708104.00",
                       mapping=self.rev_001, source="rollover")
        self._make_row("0630", "Asbestos Removal", "-782710.60", "0",
                       mapping=self.rev_001, source="tb_import")

        sections = docgen._get_tb_sections(self.fy)

        # MUST appear in trading_income exactly once across all sections.
        ti_tuple = _find_tuple_in_section(sections, "trading_income", "0630")
        self.assertIsNotNone(
            ti_tuple,
            f"0630 must appear in trading_income; trading_income="
            f"{sections.get('trading_income')!r}",
        )
        self.assertEqual(
            _count_tuple_across_sections(sections, "0630"), 1,
            f"0630 must appear exactly once across all sections; "
            f"got entries in: "
            f"{[s for s, items in sections.items() if any(str(e[0]).split('.')[0]=='0630' for e in items)]}",
        )
        # Tuple shape: (code, name, current, prior). Display name is the
        # tb_import name (weight 10 vs rollover weight 1 — existing
        # aggregation name-selection picks the higher-weighted name).
        self.assertEqual(
            ti_tuple[1], "Asbestos Removal",
            f"Display name must be tb_import 'Asbestos Removal'; "
            f"got {ti_tuple[1]!r}",
        )
        self.assertEqual(ti_tuple[2], Decimal("-782710.60"))
        self.assertEqual(ti_tuple[3], Decimal("-708104.00"))

    # ------------------------------------------------------------------
    # Test 2 — IS-REV-002 routes to income (different std_code, different
    # section). Confirms the IS-REV-* fall-through fires after the
    # IS-REV-001 predicate misses.
    # ------------------------------------------------------------------
    def test_isrev002_routes_to_income_not_trading(self):
        self._make_row("0700", "Other non-operating revenue", "0", "-1200.00",
                       mapping=self.rev_002, source="rollover")
        self._make_row("0700", "Other Income", "-517.90", "0",
                       mapping=self.rev_002, source="tb_import")

        sections = docgen._get_tb_sections(self.fy)

        self.assertIsNone(
            _find_tuple_in_section(sections, "trading_income", "0700"),
            "0700 mapped to IS-REV-002 must NOT route to trading_income",
        )
        in_tuple = _find_tuple_in_section(sections, "income", "0700")
        self.assertIsNotNone(
            in_tuple, "0700 must appear in income (IS-REV-002 fall-through)",
        )
        self.assertEqual(in_tuple[1], "Other Income")

    # ------------------------------------------------------------------
    # Test 3 — Standard_code wins over keyword. Two rows for the SAME
    # code, mapped to IS-REV-001, with names the pre-fix keyword
    # classifier would route to DIFFERENT sections.
    # ------------------------------------------------------------------
    def test_standard_code_wins_over_keyword_fallback(self):
        # 'Dividend Income Account' → pre-fix is_other_income ('dividend')
        # 'Trading Revenue Inc'     → pre-fix is_trading ('trading','revenue')
        # Both mapped to IS-REV-001 → both must land in trading_income.
        self._make_row("0800", "Dividend Income Account", "0", "-500.00",
                       mapping=self.rev_001, source="rollover")
        self._make_row("0800", "Trading Revenue Inc", "-1000.00", "0",
                       mapping=self.rev_001, source="tb_import")

        sections = docgen._get_tb_sections(self.fy)

        self.assertIsNotNone(
            _find_tuple_in_section(sections, "trading_income", "0800"),
            "0800 must appear in trading_income via IS-REV-001 predicate",
        )
        self.assertIsNone(
            _find_tuple_in_section(sections, "income", "0800"),
            "0800 must NOT also appear in income — std_code wins over keyword",
        )
        self.assertEqual(_count_tuple_across_sections(sections, "0800"), 1)

    # ------------------------------------------------------------------
    # Test 4 — Unmapped accounts (mapped_line_item=None) fall back to
    # keyword classification on a SINGLE representative row per code.
    # ------------------------------------------------------------------
    def test_unmapped_keyword_fallback_uses_representative_row(self):
        # Both unmapped. tb_import 'Asbestos Removal' has larger
        # abs(closing_balance) and is non-rollover → wins representative
        # selection. 'asbestos removal' has no trading/other-income keyword
        # match → falls through to else → 'income'.
        self._make_row("0900", "Sales", "0", "-100.00",
                       mapping=None, source="rollover")
        self._make_row("0900", "Asbestos Removal", "-1000.00", "0",
                       mapping=None, source="tb_import")

        sections = docgen._get_tb_sections(self.fy)

        self.assertEqual(
            _count_tuple_across_sections(sections, "0900"), 1,
            f"0900 must appear exactly once after representative-row keyword "
            f"fallback merges rollover + tb_import. Sections containing 0900: "
            f"{[s for s, items in sections.items() if any(str(e[0]).split('.')[0]=='0900' for e in items)]}",
        )
        in_tuple = _find_tuple_in_section(sections, "income", "0900")
        self.assertIsNotNone(
            in_tuple,
            "Representative 'Asbestos Removal' lacks keyword match → income",
        )
        self.assertEqual(in_tuple[1], "Asbestos Removal")

    # ------------------------------------------------------------------
    # Test 5 — Cross-renderer parity. Same fixture, both renderers,
    # equivalent section assignment for the four target codes (allowing
    # for the dict-vs-tuple shape difference). This is the test that
    # asserts the two implementations agree on the central Defect B
    # mechanism — not byte-for-byte (the tiebreaker asymmetry is real)
    # but on which section each code lands in and which name wins display.
    # ------------------------------------------------------------------
    def test_cross_renderer_parity_for_section_assignment(self):
        # Mixed fixture exercising all four classifier branches:
        # IS-REV-001 → trading_income, IS-REV-002 → income, unmapped
        # keyword fallback, and a Hazaway-style sales rename case.
        self._make_row("0630", "Sales", "0", "-708104.00",
                       mapping=self.rev_001, source="rollover")
        self._make_row("0630", "Asbestos Removal", "-782710.60", "0",
                       mapping=self.rev_001, source="tb_import")
        self._make_row("0700", "Other non-operating revenue", "0", "-1200.00",
                       mapping=self.rev_002, source="rollover")
        self._make_row("0700", "Other Income", "-517.90", "0",
                       mapping=self.rev_002, source="tb_import")
        self._make_row("0900", "Sales", "0", "-100.00",
                       mapping=None, source="rollover")
        self._make_row("0900", "Asbestos Removal", "-1000.00", "0",
                       mapping=None, source="tb_import")

        docgen_sections = docgen._get_tb_sections(self.fy)
        fs_sections = fs_template_service._get_tb_sections(self.fy)

        # docgen returns 4-tuples (code, name, cy, py); fs returns dicts
        # {account_code, account_name, cy_amount, py_amount, ...}.
        # Normalise both to (section_key, code) -> name for comparison.
        def docgen_index(sections):
            idx = {}
            for sec_key, items in sections.items():
                for entry in items:
                    if isinstance(entry, (list, tuple)):
                        code = str(entry[0]).split(".")[0]
                        idx[(sec_key, code)] = entry[1]
            return idx

        def fs_index(sections):
            idx = {}
            for sec_key, items in sections.items():
                for entry in items:
                    if isinstance(entry, dict):
                        code = str(entry.get("account_code", "")).split(".")[0]
                        idx[(sec_key, code)] = entry.get("account_name")
            return idx

        d_idx = docgen_index(docgen_sections)
        f_idx = fs_index(fs_sections)

        # The target codes must land in the SAME section in both renderers
        # and resolve to the SAME display name.
        for code in ("0630", "0700", "0900"):
            d_locations = {(s, c): n for (s, c), n in d_idx.items() if c == code}
            f_locations = {(s, c): n for (s, c), n in f_idx.items() if c == code}
            self.assertEqual(
                set(d_locations.keys()), set(f_locations.keys()),
                f"Code {code}: docgen and fs_template_service must agree on "
                f"section assignment. docgen={d_locations}, fs={f_locations}",
            )
            for key in d_locations:
                self.assertEqual(
                    d_locations[key], f_locations[key],
                    f"Code {code} in section {key[0]}: display names disagree. "
                    f"docgen={d_locations[key]!r}, fs={f_locations[key]!r}",
                )
