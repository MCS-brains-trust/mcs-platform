"""The Contents page must list the documents the pack actually contains.

Two defects, both visible in the Dr Services Family Trust FY2026 pack:

  * The Depreciation Report is page 8 of the pack and is absent from Contents.
  * Contents said "Beneficiaries Distribution Summary" where the document's own
    pages are headed "Beneficiaries Profit Distribution Summary".

The Depreciation Report is skipped at render time when the year has no
DepreciationAsset rows (_generate_depreciation_report returns None), so its
Contents line is a conditional row driven by has_depreciation_report rather
than a static entry that would promise a document that isn't there.
"""
from datetime import date

from django.test import SimpleTestCase, TestCase

from core.management.commands.generate_fs_templates import _build_cover
from core.fs_template_service import build_company_context
from core.models import Client, DepreciationAsset, Entity, FinancialYear


def _contents_rows(entity_type):
    doc = _build_cover(entity_type)
    return [
        [c.text.strip() for c in row.cells]
        for table in doc.tables
        for row in table.rows
    ]


def _labels(entity_type):
    return [r[0] for r in _contents_rows(entity_type) if r]


class ContentsPageTests(SimpleTestCase):
    def test_depreciation_report_is_listed(self):
        self.assertIn("Depreciation Report", _labels("trust"))

    def test_depreciation_report_line_is_conditional(self):
        labels = _labels("trust")
        idx = labels.index("Depreciation Report")
        self.assertEqual(labels[idx - 1], "{%tr if has_depreciation_report %}")
        self.assertEqual(labels[idx + 1], "{%tr endif %}")

    def test_distribution_summary_uses_the_documents_own_title(self):
        labels = _labels("trust")
        self.assertIn("Beneficiaries Profit Distribution Summary", labels)
        self.assertNotIn("Beneficiaries Distribution Summary", labels)

    def test_depreciation_report_sits_between_notes_and_the_declaration(self):
        labels = [l for l in _labels("trust") if not l.startswith("{%tr")]
        self.assertLess(
            labels.index("Notes to the Financial Statements"),
            labels.index("Depreciation Report"),
        )
        self.assertLess(
            labels.index("Depreciation Report"),
            labels.index("Trustee's Declaration"),
        )

    def test_every_entity_type_lists_the_depreciation_report(self):
        for entity_type in ("company", "trust", "partnership", "sole_trader"):
            with self.subTest(entity_type=entity_type):
                self.assertIn("Depreciation Report", _labels(entity_type))


class DepreciationFlagTests(TestCase):
    """has_depreciation_report must mirror _generate_depreciation_report's own
    condition: DepreciationAsset rows exist for the year."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Contents Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Contents Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )

    def test_false_when_the_year_has_no_assets(self):
        ctx = build_company_context(self.fy)
        self.assertFalse(ctx["has_depreciation_report"])

    def test_true_once_the_year_has_an_asset(self):
        DepreciationAsset.objects.create(
            financial_year=self.fy, category="General Pool",
            asset_name="Vehicle", total_cost=1000, opening_wdv=1000,
            depreciation_amount=250, closing_wdv=750, rate=25,
        )
        ctx = build_company_context(self.fy)
        self.assertTrue(ctx["has_depreciation_report"])
