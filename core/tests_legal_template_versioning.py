"""
Regression tests — legal document template upload / replace versioning.

LegalDocumentTemplate.document_type was unique=True while the upload and
replace views deactivate the current row and create a new one with the same
document_type — so every re-upload raised IntegrityError (only the first
upload of each type ever worked). Migration 0141 drops the column-level
unique and enforces uniqueness only among is_active=True rows via a partial
UniqueConstraint, preserving superseded versions for provenance (generated
LegalDocuments FK their template).
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client as TestClient, TestCase

from accounts.models import User
from core.models import LegalDocumentTemplate


def _docx_upload(name="template.docx"):
    """A minimal (not actually valid docx) upload — variable extraction
    failures are caught and logged by the views, which is fine here."""
    return SimpleUploadedFile(
        name,
        b"PK\x03\x04 not-really-a-docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


class LegalTemplateVersioningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="legal_template_test_admin",
            password="x",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )

    def setUp(self):
        self.test_client = TestClient()
        self.test_client.force_login(self.admin)
        session = self.test_client.session
        session["2fa_verified"] = True
        session.save()

    def _upload(self, doc_type="div7a_loan_agreement", name="Div 7A Loan Agreement"):
        return self.test_client.post(
            "/legal-templates/upload/",
            {
                "name": name,
                "document_type": doc_type,
                "template_file": _docx_upload(),
            },
            secure=True,
        )

    def test_reupload_same_type_versions_up(self):
        """Second upload of the same document_type must succeed (was
        IntegrityError) and version up, keeping the old row inactive."""
        resp1 = self._upload()
        self.assertEqual(resp1.status_code, 200, resp1.content)

        resp2 = self._upload()
        self.assertEqual(resp2.status_code, 200, resp2.content)

        rows = LegalDocumentTemplate.objects.filter(
            document_type="div7a_loan_agreement"
        ).order_by("version")
        self.assertEqual(rows.count(), 2)
        self.assertEqual([r.version for r in rows], [1, 2])
        self.assertFalse(rows[0].is_active)
        self.assertTrue(rows[1].is_active)

    def test_replace_endpoint_versions_up(self):
        resp1 = self._upload()
        self.assertEqual(resp1.status_code, 200)
        original = LegalDocumentTemplate.objects.get(
            document_type="div7a_loan_agreement", is_active=True
        )

        resp = self.test_client.post(
            f"/legal-templates/{original.pk}/replace/",
            {"template_file": _docx_upload("v2.docx")},
            secure=True,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        payload = resp.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["version"], 2)

        original.refresh_from_db()
        self.assertFalse(original.is_active)
        active = LegalDocumentTemplate.objects.get(
            document_type="div7a_loan_agreement", is_active=True
        )
        self.assertEqual(active.version, 2)
        # New version needs fresh solicitor approval
        self.assertFalse(active.solicitor_approved)

    def test_two_active_rows_still_blocked_by_constraint(self):
        """The partial constraint must still forbid two ACTIVE rows of the
        same type."""
        LegalDocumentTemplate.objects.create(
            name="T1", document_type="solvency_resolution",
            template_file=_docx_upload(), version=1, is_active=True,
        )
        with self.assertRaises(IntegrityError):
            LegalDocumentTemplate.objects.create(
                name="T2", document_type="solvency_resolution",
                template_file=_docx_upload(), version=2, is_active=True,
            )

    def test_inactive_history_rows_allowed(self):
        """Multiple inactive rows of the same type are allowed (history)."""
        for v in (1, 2, 3):
            LegalDocumentTemplate.objects.create(
                name=f"T v{v}", document_type="dividend_minutes",
                template_file=_docx_upload(), version=v, is_active=(v == 3),
            )
        self.assertEqual(
            LegalDocumentTemplate.objects.filter(
                document_type="dividend_minutes"
            ).count(),
            3,
        )
