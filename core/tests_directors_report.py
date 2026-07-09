"""
Tests — Director's Report wizard save flow.

Previously the wizard's saveReport() was an alert-only stub, the view passed
no `sections` to the template (so no textareas rendered), and no code ever
created a directors_report LegalDocument — so the report could never enter a
package bundle despite having a PDF template slot
(core/pdf/directors_report.html).

Now directors_report_save persists a LegalDocument context record (same
pattern as the other compliance documents), mapping wizard sections onto the
keys the bundle PDF template renders, and the wizard prefills saved content.
"""
from datetime import date

from django.test import Client as TestClient, TestCase

from accounts.models import User
from core.models import (
    Client as ClientModel,
    Entity,
    EntityOfficer,
    FinancialYear,
    LegalDocument,
)


class DirectorsReportWizardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="directors_report_test_admin",
            password="x",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Directors Report Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Report Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
            acn="123456789",
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        cls.director = EntityOfficer.objects.create(
            entity=cls.entity, full_name="Dora Director", role="director",
        )

    def setUp(self):
        self.test_client = TestClient()
        self.test_client.force_login(self.user)
        session = self.test_client.session
        session["2fa_verified"] = True
        session.save()

    def _save(self, sections):
        return self.test_client.post(
            f"/years/{self.fy.pk}/compliance/directors-report/save/",
            data={"sections": sections},
            content_type="application/json",
            secure=True,
        )

    def test_wizard_renders_section_textareas(self):
        """The wizard must render all eight section textareas (previously the
        view passed no `sections`, so none rendered)."""
        response = self.test_client.get(
            f"/years/{self.fy.pk}/compliance/directors-report/", secure=True,
        )
        self.assertEqual(response.status_code, 200)
        for sid in (
            "principal_activities", "review_of_operations", "significant_changes",
            "events_after_reporting", "likely_developments",
            "environmental_regulation", "dividends", "indemnification_insurance",
        ):
            self.assertContains(response, f'id="section-{sid}"')
        self.assertContains(response, "Dora Director")

    def test_save_creates_legal_document(self):
        response = self._save({
            "principal_activities": "Widget manufacturing.",
            "review_of_operations": "Profit up 10% on prior year.",
            "dividends": "A fully franked dividend of $10,000 was paid.",
        })
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")

        doc = LegalDocument.objects.get(pk=payload["document_id"])
        self.assertEqual(doc.document_type, "directors_report")
        self.assertEqual(doc.financial_year, self.fy)
        self.assertEqual(doc.status, "generated")
        ctx = doc.context_data
        # Raw sections stored for wizard round-trip
        self.assertEqual(ctx["sections"]["principal_activities"], "Widget manufacturing.")
        # Mapped onto the PDF template keys
        self.assertEqual(ctx["principal_activities"], "Widget manufacturing.")
        self.assertEqual(ctx["operating_results"], "Profit up 10% on prior year.")
        self.assertEqual(ctx["dividends_paid"], "A fully franked dividend of $10,000 was paid.")
        self.assertEqual(ctx["signatories"], [{"name": "Dora Director", "role": "Director"}])

    def test_resave_updates_instead_of_duplicating(self):
        self._save({"principal_activities": "First draft."})
        response = self._save({"principal_activities": "Second draft."})
        self.assertEqual(response.status_code, 200)

        docs = LegalDocument.objects.filter(
            financial_year=self.fy, document_type="directors_report",
        )
        self.assertEqual(docs.count(), 1, "Re-saving must update, not duplicate")
        self.assertEqual(
            docs.first().context_data["sections"]["principal_activities"],
            "Second draft.",
        )

    def test_wizard_prefills_saved_sections(self):
        self._save({"principal_activities": "Prefilled activities text."})
        response = self.test_client.get(
            f"/years/{self.fy.pk}/compliance/directors-report/", secure=True,
        )
        self.assertContains(response, "Prefilled activities text.")

    def test_save_rejects_bad_json(self):
        response = self.test_client.post(
            f"/years/{self.fy.pk}/compliance/directors-report/save/",
            data="not-json",
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 400)
