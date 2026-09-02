"""Page numbers on the pack, and matching numbers on the Contents page.

_stamp_page_numbers was a no-op ("Removed per client requirement: no page
numbers anywhere"). That requirement is reversed: the pack is numbered again,
and the Contents lists the page each document starts on.

Numbering is absolute from the cover, and the cover and Contents are not
themselves stamped -- so the first numbered page is 3, which is what the
Contents reports for the first document.

The Contents cannot know these numbers when it is rendered: it lives in the
COVER template, built by docxtpl before any other document exists, and the
pagination only settles once LibreOffice has laid every document out and they
have been merged. So the numbers are stamped onto the merged PDF, driven by
the page offsets generate_combined_pdf records while merging.
"""
import io

from django.test import SimpleTestCase

from core.fs_template_service import (
    CONTENTS_LABEL_TO_DOC_TYPE, _stamp_page_numbers,
)


def _make_pdf(pages):
    """A PDF where each page carries the given lines of text."""
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


CONTENTS = [
    "Contents",
    "Detailed Profit and Loss Statement",
    "Detailed Balance Sheet",
    "Notes to the Financial Statements",
    "Trustee's Declaration",
]


class FooterPageNumberTests(SimpleTestCase):
    def test_body_pages_are_numbered_absolutely(self):
        pdf = _stamp_page_numbers(_make_pdf([["Cover"], CONTENTS, ["P&L"], ["BS"]]))

        self.assertIn("3", _page_text(pdf, 2))
        self.assertIn("4", _page_text(pdf, 3))

    def test_the_cover_and_contents_are_not_numbered(self):
        pdf = _stamp_page_numbers(_make_pdf([["Cover"], CONTENTS, ["P&L"]]))

        self.assertNotIn("1", _page_text(pdf, 0))
        self.assertNotIn("2", _page_text(pdf, 1))

    def test_it_still_returns_a_readable_pdf(self):
        pdf = _stamp_page_numbers(_make_pdf([["Cover"], CONTENTS, ["P&L"]]))
        from pypdf import PdfReader

        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 3)


class ContentsPageNumberTests(SimpleTestCase):
    def test_each_listed_document_gets_the_page_it_starts_on(self):
        pdf = _stamp_page_numbers(
            _make_pdf([["Cover"], CONTENTS, ["P&L"], ["BS"], ["BS 2"], ["Notes"]]),
            doc_start_pages={
                "DETAILED_PL": 3, "BALANCE_SHEET": 4, "NOTES": 6, "DECLARATION": 7,
            },
        )
        text = _page_text(pdf, 1)

        for label, page in (
            ("Detailed Profit and Loss Statement", "3"),
            ("Detailed Balance Sheet", "4"),
            ("Notes to the Financial Statements", "6"),
            ("Trustee's Declaration", "7"),
        ):
            with self.subTest(label=label):
                self.assertIn(page, text)

    def test_a_listed_document_that_was_not_produced_gets_no_number(self):
        """SUMMARY_PL and DEPRECIATION_REPORT are skipped for some years, and
        a document that was not produced must be left unnumbered rather than
        pointed somewhere wrong. The Management Representation Letter is
        mapped now, but package assembly is what puts it in doc_start_pages --
        absent from the map passed here, it stays blank."""
        pdf = _stamp_page_numbers(
            _make_pdf([["Cover"],
                       CONTENTS + ["Management Representation Letter"],
                       ["P&L"]]),
            doc_start_pages={"DETAILED_PL": 3},
        )
        text = _page_text(pdf, 1)

        self.assertIn("Management Representation Letter", text)
        self.assertNotIn("4", text)

    def test_contents_is_untouched_when_no_offsets_are_supplied(self):
        """package_pdf_renderer calls the stamper without a document map."""
        plain = _page_text(_make_pdf([["Cover"], CONTENTS]), 1)
        stamped = _page_text(_stamp_page_numbers(_make_pdf([["Cover"], CONTENTS])), 1)

        self.assertEqual(plain, stamped)


class ContentsLabelMapTests(SimpleTestCase):
    """Each value is a tuple of candidates: a label can point at the FS
    document type in one pack and at a LegalDocument type in another (a
    company's declaration is appended by package assembly, a trust's is
    rendered inside the FS bundle). See tests_pack_contents_numbering."""

    def test_every_declaration_variant_maps_to_the_declaration_document(self):
        for label in ("Directors' Declaration", "Trustee's Declaration",
                      "Proprietor Declaration", "Partners' Declaration"):
            with self.subTest(label=label):
                self.assertIn("DECLARATION", CONTENTS_LABEL_TO_DOC_TYPE[label])

    def test_the_distribution_label_matches_the_contents_page_wording(self):
        self.assertIn(
            "DISTRIBUTION",
            CONTENTS_LABEL_TO_DOC_TYPE["Beneficiaries Profit Distribution Summary"],
        )


class FooterClearsTheNumberBandTests(SimpleTestCase):
    """The reportlab distribution pages draw their own footer.

    At its original 1.0cm it sat exactly on the stamped number, and the page
    extracted as "These financial statements are10".
    """

    def test_the_distribution_footer_sits_above_the_page_number(self):
        from core.fs_template_service import (
            DIST_FOOTER_LOWER_LINE_CM, DIST_FOOTER_UPPER_LINE_CM,
            PAGE_NUMBER_BASELINE_PT,
        )

        points_per_cm = 28.3464567
        for name, cm_value in (
            ("lower", DIST_FOOTER_LOWER_LINE_CM),
            ("upper", DIST_FOOTER_UPPER_LINE_CM),
        ):
            with self.subTest(line=name):
                self.assertGreater(cm_value * points_per_cm,
                                   PAGE_NUMBER_BASELINE_PT + 4)
