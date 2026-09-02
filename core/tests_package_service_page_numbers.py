"""The other pack builder never stamped page numbers at all.

There are two: ``package_pdf_renderer.build_package_bundle`` and
``package_service._combine_pdfs``. The first at least called
``_stamp_page_numbers`` (without offsets, so its Contents stayed blank); the
second never called it, so every document appended behind the financial
statements came out with no page number on it whatsoever, and the Contents
could name none of them.

Numbering is absolute from the cover and the first two pages -- cover and
Contents -- are counted but not stamped, so a body page carries its own
1-based position.
"""
from datetime import date
from io import BytesIO

from django.core.files.base import ContentFile
from django.test import TestCase

from core.models import Client, Entity, FinancialYear, LegalDocument
from core.package_service import _combine_pdfs


def _pdf(pages):
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf)
    for text in pages:
        c.drawString(100, 700, text)
        c.showPage()
    c.save()
    return buf.getvalue()


class CombinePdfsStampsPageNumbersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.entity = Entity.objects.create(
            entity_name="Stamp Test Company", entity_type="company",
            client=Client.objects.create(name="Stamp Client"),
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )

    def _legal(self, doc_type, pages):
        doc = LegalDocument.objects.create(
            entity=self.entity, financial_year=self.fy,
            document_type=doc_type, title=doc_type, status="generated",
        )
        doc.pdf_file.save(f"{doc_type}.pdf", ContentFile(_pdf(pages)), save=True)
        return doc

    def _combined_text(self, include):
        from pypdf import PdfReader

        path = _combine_pdfs(self.fy, self.entity, include)
        self.assertIsNotNone(path, "_combine_pdfs returned None")
        reader = PdfReader(path)
        return [p.extract_text() for p in reader.pages]

    def test_body_pages_of_appended_documents_are_numbered(self):
        """Four pages: 1 and 2 unstamped, 3 and 4 carry their own number."""
        self._legal("directors_declaration", ["Cover", "Contents"])
        self._legal("solvency_resolution", ["Resolution p1", "Resolution p2"])
        pages = self._combined_text(
            {"directors_declaration", "solvency_resolution"})

        self.assertEqual(len(pages), 4)
        self.assertIn(
            "3", pages[2],
            "an appended document carried no page number at all",
        )
        self.assertIn("4", pages[3])

    def test_the_cover_and_contents_are_not_stamped(self):
        self._legal("directors_declaration", ["Cover", "Contents"])
        pages = self._combined_text({"directors_declaration"})
        self.assertNotIn("1", pages[0])
        self.assertNotIn("2", pages[1])
