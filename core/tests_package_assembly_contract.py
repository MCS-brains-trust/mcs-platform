"""Package assembly must actually assemble, and must speak the same document
vocabulary as the rest of the platform.

Three faults, all silent, all found while regenerating Minli Enterprise Unit
Trust's FY2026 client pack:

1. ``_combine_pdfs`` opens with ``from PyPDF2 import PdfMerger``. PyPDF2 is not
   installed and has not been since requirements pinned ``pypdf>=5.0.0``;
   pypdf 5 removed ``PdfMerger`` outright in favour of ``PdfWriter.append``.
   The ImportError is caught, logged at warning, and assembly carries on --
   returning ``combined_pdf: None`` while still stamping
   ``package_assembled = True``. No client package PDF has ever been produced.

2. ``PACKAGE_CONTENTS`` and ``DOCUMENT_ORDER`` use document-type keys that are
   not ``LegalDocument.document_type`` choices: ``cover_letter`` for
   ``client_cover_letter``, ``management_representation_letter`` for
   ``management_rep_letter``, ``trust_distribution_minutes`` for
   ``distribution_minutes``, ``shareholder_loan_acknowledgment`` for
   ``shareholder_loan_ack``. The checklist therefore never recognises the
   documents that generation actually writes, re-generates them under the
   invalid keys on every run, and ``_combine_pdfs`` then looks for the real
   ones under names nothing stores. Minli FY2026 finished with both spellings
   of two documents and a third with no file at all.

3. ``_log_activity`` passes ``action=`` to ``ActivityLog``, which has no such
   field -- it takes ``event_type`` and a non-blank ``title``. Every assembly
   log line has been swallowed by the surrounding except.

The vocabulary tests are the ones that matter going forward: they fail the
moment either map drifts from the model's own choices again.
"""
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.test import TestCase

from core.models import (
    ActivityLog,
    Client,
    Entity,
    FinancialYear,
    LegalDocument,
    TrialBalanceLine,
)
from core.package_service import (
    DOCUMENT_ORDER,
    PACKAGE_CONTENTS,
    _combine_pdfs,
    assemble_package,
)

D = Decimal

# "financial_statements" is a GeneratedDocument type, not a LegalDocument one --
# it is the only member of these maps that is legitimately not a legal document.
GENERATED_DOCUMENT_KEYS = {"financial_statements"}


def _one_page_pdf(text):
    """A real single-page PDF, so the merger has something genuine to read."""
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 700, text)
    c.showPage()
    c.save()
    return buf.getvalue()


class PackageDocumentVocabularyTests(TestCase):
    """Every key in the assembly maps must be a real document type."""

    @classmethod
    def setUpTestData(cls):
        cls.legal_types = {c[0] for c in LegalDocument._meta.get_field("document_type").choices}

    def test_package_contents_keys_are_real_document_types(self):
        unknown = {
            doc_type
            for required in PACKAGE_CONTENTS.values()
            for doc_type, _label, _always, _auto in required
            if doc_type not in self.legal_types and doc_type not in GENERATED_DOCUMENT_KEYS
        }
        self.assertEqual(
            unknown, set(),
            "PACKAGE_CONTENTS names document types that LegalDocument cannot store, "
            "so the checklist can never match what generation writes",
        )

    def test_document_order_keys_are_real_document_types(self):
        unknown = {
            doc_type
            for doc_type in DOCUMENT_ORDER
            if doc_type not in self.legal_types and doc_type not in GENERATED_DOCUMENT_KEYS
        }
        self.assertEqual(
            unknown, set(),
            "DOCUMENT_ORDER names document types nothing stores, so _combine_pdfs "
            "silently skips those documents",
        )

    def test_every_auto_generatable_type_has_a_generator(self):
        from core.package_service import AUTO_GENERATORS

        auto_types = {
            doc_type
            for required in PACKAGE_CONTENTS.values()
            for doc_type, _label, _always, auto in required
            if auto
        }
        self.assertEqual(
            auto_types - set(AUTO_GENERATORS), set(),
            "a document type is marked auto-generatable but has no generator, "
            "so the package stays permanently incomplete",
        )


class CombinePdfsTests(TestCase):
    """The merger must merge. It has been returning None since the pypdf move."""

    @classmethod
    def setUpTestData(cls):
        cls.entity = Entity.objects.create(
            entity_name="Merger Test Trust", entity_type="trust",
            client=Client.objects.create(name="Merger Client"),
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )

    def test_combines_legal_document_pdfs_into_one_file(self):
        import os

        from pypdf import PdfReader

        doc = LegalDocument.objects.create(
            entity=self.entity, financial_year=self.fy,
            document_type="client_cover_letter",
            title="Cover Letter", status="generated",
        )
        doc.pdf_file.save("cover.pdf", ContentFile(_one_page_pdf("Cover Letter")), save=True)

        # Financial statements deliberately excluded: this asserts the merge
        # step itself, not FS rendering.
        path = _combine_pdfs(self.fy, self.entity, {"client_cover_letter"})

        self.assertIsNotNone(
            path, "_combine_pdfs returned None -- the PDF merge never ran",
        )
        self.assertTrue(os.path.exists(path), f"combined PDF not written to {path}")
        self.assertEqual(len(PdfReader(path).pages), 1)


class AssemblePackageTests(TestCase):
    """Assembly must not duplicate what is already there, and must log."""

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Assembly Test Trust", entity_type="trust",
            client=Client.objects.create(name="Assembly Client"),
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="4199",
            account_name="Undistributed income", debit=D("0"), credit=D("100"),
            closing_balance=D("-100"), source="manual_journal",
        )
        # Everything the trust package requires, stored under the canonical
        # LegalDocument.document_type names that generation actually writes.
        for doc_type in (
            "distribution_minutes",
            "management_rep_letter",
            "engagement_letter",
            "client_cover_letter",
        ):
            LegalDocument.objects.create(
                entity=self.entity, financial_year=self.fy,
                document_type=doc_type, title=doc_type, status="generated",
            )

    def test_does_not_regenerate_documents_that_already_exist(self):
        before = set(
            LegalDocument.objects.filter(financial_year=self.fy)
            .values_list("document_type", flat=True)
        )

        result = assemble_package(str(self.fy.pk))

        after = set(
            LegalDocument.objects.filter(financial_year=self.fy)
            .values_list("document_type", flat=True)
        )
        self.assertEqual(
            after, before,
            "assembly created documents under names that duplicate the ones "
            f"already present: {sorted(after - before)}",
        )
        self.assertEqual(result["auto_generated"], [])
        # Without this the test passes vacuously: the duplicates are only
        # absent because every auto-generation attempt blew up first.
        self.assertEqual(
            result["generation_errors"], [],
            "assembly tried to generate documents that were already present",
        )

    def test_records_an_activity_log_entry(self):
        assemble_package(str(self.fy.pk))

        self.assertTrue(
            ActivityLog.objects.filter(
                financial_year=self.fy, event_type="package_assembled",
            ).exists(),
            "no package_assembled activity was logged -- _log_activity's "
            "exception handler swallowed the failure",
        )
