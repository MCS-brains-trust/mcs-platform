"""
Regression test for Defect B Phase 2 — P&L section classification
==================================================================
Verifies the predicate-first section classifier in
``core/fs_template_service.py:_get_tb_sections`` routes all TBL rows for
a single account_code to the SAME section based on
``AccountMapping.standard_code`` (IS-REV-001 → trading_income, other
IS-REV-* → income, IS-COS-* → cogs, IS-EXP-* → expenses), with keyword
fallback on a single representative row's name for unmapped accounts.

Defect B Phase 1 documented the mechanism: per-row keyword classification
routed rollover ('Sales') and tb_import ('Asbestos Removal') rows for the
same account_code into different sections (trading_income vs income),
producing two P&L lines per code after per-section aggregation. This fix
collapses them to one line per code by deciding the section once per code.

NOTE on test surface: like Defect D's tests, these call ``_get_tb_sections``
directly rather than the full ``build_company_context`` orchestrator
because the orchestrator reaches a Linux-only ``strftime('%-d %B %Y')``
that raises on Windows. The section classifier under test is fully
exercised by the direct call.

Defect B Phase 2. See audit_defect_b_label_split.md and phase2_fix_defect_b.md.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from core.fs_template_service import _get_tb_sections
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


def _find_in_section(sections, section_name, account_code):
    """Return the merged entry for account_code in section_name, or None."""
    for entry in sections.get(section_name, []):
        if str(entry.get("account_code", "")).split(".")[0] == account_code:
            return entry
    return None


def _count_across_sections(sections, account_code):
    """Return total number of entries for account_code across all sections."""
    total = 0
    for items in sections.values():
        for entry in items:
            if str(entry.get("account_code", "")).split(".")[0] == account_code:
                total += 1
    return total


@override_settings(STORAGES=STORAGES_OVERRIDE)
class SectionClassificationTests(TestCase):
    """6 cases for the Defect B Phase 2 classifier."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Section Classifier Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Section Classifier Test Co Pty Ltd",
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
        prior_debit/credit split to give py_amount=prior in _get_tb_sections.
        Income accounts are credit-natural so callers typically pass negative
        ``current`` to represent positive revenue.
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
    # + tb_import for the same code merge into ONE entry with the
    # tb_import name winning display.
    # ------------------------------------------------------------------
    def test_isrev001_merges_rollover_and_tb_import_in_trading_income(self):
        # Hazaway 0630 pattern: rollover 'Sales' (PY only) + tb_import
        # 'Asbestos Removal' (CY only), both mapped to IS-REV-001.
        self._make_row("0630", "Sales", "0", "-708104.00",
                       mapping=self.rev_001, source="rollover")
        self._make_row("0630", "Asbestos Removal", "-782710.60", "0",
                       mapping=self.rev_001, source="tb_import")

        sections = _get_tb_sections(self.fy)

        # MUST appear in trading_income exactly once.
        ti_entry = _find_in_section(sections, "trading_income", "0630")
        self.assertIsNotNone(
            ti_entry,
            f"0630 must appear in trading_income; trading_income="
            f"{sections.get('trading_income')!r}",
        )
        # MUST NOT also appear in income (the Defect B symptom).
        self.assertEqual(
            _count_across_sections(sections, "0630"), 1,
            f"0630 must appear exactly once across all sections; "
            f"got entries in: "
            f"{[s for s, items in sections.items() if any(str(e.get('account_code','')).split('.')[0]=='0630' for e in items)]}",
        )
        # Display name must be the tb_import name (non-rollover wins via weight).
        self.assertEqual(
            ti_entry["account_name"], "Asbestos Removal",
            f"Display name must be tb_import 'Asbestos Removal' "
            f"(non-rollover, cy != 0 → weight 10 vs rollover weight 1); "
            f"got {ti_entry['account_name']!r}",
        )
        # CY and PY both populated from the merged entry.
        self.assertEqual(ti_entry["cy_amount"], Decimal("-782710.60"))
        self.assertEqual(ti_entry["py_amount"], Decimal("-708104.00"))

    # ------------------------------------------------------------------
    # Test 2 — IS-REV-002 routes to income (different std_code,
    # different section). Confirms the IS-REV-* fall-through fires
    # after the IS-REV-001 predicate misses.
    # ------------------------------------------------------------------
    def test_isrev002_routes_to_income_not_trading(self):
        self._make_row("0700", "Other non-operating revenue", "0", "-1200.00",
                       mapping=self.rev_002, source="rollover")
        self._make_row("0700", "Other Income", "-517.90", "0",
                       mapping=self.rev_002, source="tb_import")

        sections = _get_tb_sections(self.fy)

        # MUST appear in income, NOT trading_income.
        self.assertIsNone(
            _find_in_section(sections, "trading_income", "0700"),
            "0700 mapped to IS-REV-002 must NOT route to trading_income",
        )
        in_entry = _find_in_section(sections, "income", "0700")
        self.assertIsNotNone(
            in_entry, "0700 must appear in income (IS-REV-002 fall-through)",
        )
        # Display name is the tb_import name.
        self.assertEqual(in_entry["account_name"], "Other Income")

    # ------------------------------------------------------------------
    # Test 3 — Standard_code wins over keyword. Two rows for the SAME
    # code, mapped to IS-REV-001, with names that the pre-fix keyword
    # classifier would route to DIFFERENT sections. Confirms the
    # std_code predicate takes precedence.
    # ------------------------------------------------------------------
    def test_standard_code_wins_over_keyword_fallback(self):
        # Names chosen so the old per-row keyword classifier would split:
        # 'Dividend Income Account' → is_other_income ('dividend') → income
        # 'Trading Revenue Inc'     → is_trading ('trading'/'revenue') → trading_income
        # Both mapped to IS-REV-001 → both must land in trading_income.
        self._make_row("0800", "Dividend Income Account", "0", "-500.00",
                       mapping=self.rev_001, source="rollover")
        self._make_row("0800", "Trading Revenue Inc", "-1000.00", "0",
                       mapping=self.rev_001, source="tb_import")

        sections = _get_tb_sections(self.fy)

        # Exactly one entry, in trading_income.
        self.assertIsNotNone(
            _find_in_section(sections, "trading_income", "0800"),
            "0800 must appear in trading_income via IS-REV-001 predicate",
        )
        self.assertIsNone(
            _find_in_section(sections, "income", "0800"),
            "0800 must NOT also appear in income — std_code wins over keyword",
        )
        self.assertEqual(_count_across_sections(sections, "0800"), 1)

    # ------------------------------------------------------------------
    # Test 4 — Unmapped accounts (mapped_line_item=None) fall back to
    # keyword classification on a SINGLE representative row per code, not
    # per row. Rollover ('Sales') and tb_import ('Asbestos Removal') for
    # the same code must end up in the SAME section even when their names
    # would individually route to different sections.
    # ------------------------------------------------------------------
    def test_unmapped_keyword_fallback_uses_representative_row(self):
        # Both rows unmapped. tb_import 'Asbestos Removal' has larger
        # abs(closing_balance) and is non-rollover → wins representative
        # selection. 'asbestos removal' has no trading/other-income keyword
        # match → falls through to else → 'income' section.
        # Both rows therefore route to 'income'.
        self._make_row("0900", "Sales", "0", "-100.00",
                       mapping=None, source="rollover")
        self._make_row("0900", "Asbestos Removal", "-1000.00", "0",
                       mapping=None, source="tb_import")

        sections = _get_tb_sections(self.fy)

        # Exactly one entry across all sections.
        self.assertEqual(
            _count_across_sections(sections, "0900"), 1,
            f"0900 must appear exactly once after representative-row keyword "
            f"fallback merges rollover + tb_import. "
            f"Sections containing 0900: "
            f"{[s for s, items in sections.items() if any(str(e.get('account_code','')).split('.')[0]=='0900' for e in items)]}",
        )
        # The representative row's name decides the section — 'Asbestos
        # Removal' has no keyword match → falls through to income.
        in_entry = _find_in_section(sections, "income", "0900")
        self.assertIsNotNone(
            in_entry,
            "Representative row 'Asbestos Removal' has no keyword match → "
            "must route to income (else fall-through)",
        )
        # Display name uses the tb_import name (weight 10 vs rollover weight 1).
        self.assertEqual(in_entry["account_name"], "Asbestos Removal")

    # ------------------------------------------------------------------
    # Test 5 — Name tiebreaker: when two non-rollover rows have equal
    # weight (both cy != 0), the more recently created name wins display.
    # ------------------------------------------------------------------
    def test_name_selection_breaks_weight_ties_by_created_at(self):
        # Both tb_import (non-rollover), both non-zero CY → both score
        # weight=10 per row. The newer row's name must win.
        older = self._make_row("0950", "OlderName", "-100.00", "0",
                               mapping=self.rev_001, source="tb_import")
        # Force a later created_at by directly updating the auto_now_add
        # field (auto_now_add sets the timestamp on create; update bypasses
        # that to deterministically set a known later value).
        newer_ts = older.created_at + timedelta(seconds=10)
        newer = self._make_row("0950", "NewerName", "-200.00", "0",
                               mapping=self.rev_001, source="tb_import")
        TrialBalanceLine.objects.filter(pk=newer.pk).update(created_at=newer_ts)

        sections = _get_tb_sections(self.fy)

        ti_entry = _find_in_section(sections, "trading_income", "0950")
        self.assertIsNotNone(ti_entry, "0950 must appear in trading_income")
        self.assertEqual(
            ti_entry["account_name"], "NewerName",
            f"On weight tie (both rows weight=10), more recent created_at must "
            f"win display name; got {ti_entry['account_name']!r}",
        )

    # ------------------------------------------------------------------
    # Test 6 — Totals invariant: across the entire income side
    # (trading_income + income), CY and PY totals are conserved by the
    # classifier. The fix is a presentation change, not a totals change.
    # ------------------------------------------------------------------
    def test_totals_unchanged_by_classification(self):
        # Three codes, each with the canonical Model A rollover + tb_import
        # pair (rollover carries PY, tb_import carries CY). _get_tb_sections
        # at line ~142 hardcodes ``py = Decimal('0')`` for non-rollover
        # sources — PY only ever comes from rollover rows under Model A.
        self._make_row("0610", "Sales", "0", "-708104.00",
                       mapping=self.rev_001, source="rollover")
        self._make_row("0610", "Asbestos Removal", "-782710.60", "0",
                       mapping=self.rev_001, source="tb_import")
        self._make_row("0620", "Other non-operating revenue", "0", "-1200.00",
                       mapping=self.rev_002, source="rollover")
        self._make_row("0620", "Other Income", "-517.90", "0",
                       mapping=self.rev_002, source="tb_import")
        # 0640: rollover carries PY=-2500, tb_import carries CY=-3000.
        self._make_row("0640", "Misc revenue", "0", "-2500.00",
                       mapping=None, source="rollover")
        self._make_row("0640", "Misc revenue", "-3000.00", "0",
                       mapping=None, source="tb_import")

        sections = _get_tb_sections(self.fy)

        # Sum CY + PY across trading_income + income only (the P&L revenue
        # side; this test doesn't touch cogs/expenses).
        ti = sections.get("trading_income", [])
        inc = sections.get("income", [])
        total_cy = sum((e.get("cy_amount") or Decimal("0")) for e in ti + inc)
        total_py = sum((e.get("py_amount") or Decimal("0")) for e in ti + inc)

        # Expected totals:
        #   CY: -782710.60 + -517.90 + -3000.00 = -786228.50
        #   PY (from rollover rows only, per Model A):
        #       -708104.00 + -1200.00 + -2500.00 = -711804.00
        self.assertEqual(
            total_cy, Decimal("-786228.50"),
            f"Total CY across trading_income + income must equal sum of "
            f"tb_import closing_balances; got {total_cy}",
        )
        self.assertEqual(
            total_py, Decimal("-711804.00"),
            f"Total PY across trading_income + income must equal sum of "
            f"rollover prior_debit - prior_credit; got {total_py}",
        )
