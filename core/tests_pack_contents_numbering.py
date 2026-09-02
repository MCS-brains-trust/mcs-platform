"""Every document the Contents lists gets a page number, in the pack too.

Two defects, both visible on a company's client package.

1. ``CONTENTS_LABEL_TO_DOC_TYPE`` had no entry for "Solvency Resolution" or
   "Management Representation Letter", so those Contents rows were never
   numbered. Its own comment conceded it: "Labels absent from this map ... get
   no number."

2. ``package_pdf_renderer`` called ``_stamp_page_numbers(raw_bytes)`` with no
   ``doc_start_pages``, so the pack's Contents was left alone entirely. It got
   away with it for a trust because ``generate_combined_pdf`` had already
   stamped the Contents while building the FS bundle -- but a COMPANY pack
   excludes DECLARATION from that bundle and appends it afterwards as a legal
   document, so "Directors' Declaration" was never in any offsets map and
   never got a number either. That is exactly the set that showed as blank:
   the declaration, the resolution, and the representation letter.

A label can therefore point at an FS document type in one entity's pack and at
a LegalDocument type in another's, which is why the map holds candidates
rather than a single key.
"""
import io

from django.test import SimpleTestCase

from core.fs_template_service import (
    CONTENTS_LABEL_TO_DOC_TYPE, _stamp_page_numbers, _resolve_contents_start,
)


def _make_pdf(pages):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for lines in pages:
        y = 700
        for line in lines:
            c.setFont("Helvetica", 11)
            c.drawString(72, y, line)
            y -= 20
        c.showPage()
    c.save()
    return buf.getvalue()


def _page_text(pdf_bytes, index):
    from pypdf import PdfReader

    return PdfReader(io.BytesIO(pdf_bytes)).pages[index].extract_text()


COMPANY_CONTENTS = [
    "Contents",
    "Detailed Profit and Loss Statement",
    "Detailed Balance Sheet",
    "Notes to the Financial Statements",
    "Directors' Declaration",
    "Solvency Resolution",
    "Compilation Report",
    "Management Representation Letter",
]


class CompanyPackContentsTests(SimpleTestCase):
    """The three labels that came back blank on a real company pack."""

    def _stamped(self):
        return _stamp_page_numbers(
            _make_pdf([["Cover"], COMPANY_CONTENTS] + [[f"p{i}"] for i in range(9)]),
            doc_start_pages={
                "DETAILED_PL": 3,
                "BALANCE_SHEET": 4,
                "NOTES": 5,
                # Appended by package assembly, not in the FS bundle.
                "directors_declaration": 7,
                "solvency_resolution": 8,
                "COMPILATION": 9,
                "management_rep_letter": 10,
            },
        )

    def test_the_directors_declaration_is_numbered(self):
        self.assertIn("7", _page_text(self._stamped(), 1))

    def test_the_solvency_resolution_is_numbered(self):
        self.assertIn("8", _page_text(self._stamped(), 1))

    def test_the_management_representation_letter_is_numbered(self):
        self.assertIn("10", _page_text(self._stamped(), 1))


class LabelResolutionTests(SimpleTestCase):
    """A label resolves across both namespaces, whichever is present."""

    def test_a_declaration_resolves_to_the_fs_document_in_a_trust_pack(self):
        self.assertEqual(
            _resolve_contents_start("Trustee's Declaration", {"DECLARATION": 7}), 7)

    def test_a_declaration_resolves_to_the_legal_doc_in_a_company_pack(self):
        self.assertEqual(
            _resolve_contents_start(
                "Directors' Declaration", {"directors_declaration": 9}), 9)

    def test_the_representation_letter_resolves_whatever_its_variant(self):
        for key in ("management_rep_letter", "management_rep_letter_trust",
                    "management_rep_letter_partnership"):
            with self.subTest(key=key):
                self.assertEqual(
                    _resolve_contents_start(
                        "Management Representation Letter", {key: 12}), 12)

    def test_an_absent_document_resolves_to_nothing(self):
        self.assertIsNone(
            _resolve_contents_start("Solvency Resolution", {"DETAILED_PL": 3}))

    def test_every_contents_label_the_builder_writes_is_mapped(self):
        """The Contents builder and this map must not drift apart.

        These are the exact strings generate_fs_templates._build_cover
        appends, across every entity type.
        """
        for label in (
            "Detailed Profit and Loss Statement",
            "Detailed Balance Sheet",
            "Summary Profit and Loss Statement",
            "Notes to the Financial Statements",
            "Depreciation Report",
            "Directors' Declaration",
            "Solvency Resolution",
            "Trustee's Declaration",
            "Beneficiaries Profit Distribution Summary",
            "Proprietor Declaration",
            "Partners' Declaration",
            "Compilation Report",
            "Management Representation Letter",
        ):
            with self.subTest(label=label):
                self.assertIn(label, CONTENTS_LABEL_TO_DOC_TYPE)
