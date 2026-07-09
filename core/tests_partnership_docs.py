"""
Regression tests — partnership document views.

core/views_partnership_docs.py previously crashed on real data:
  * partner_statements ordered EntityOfficer by "name" (field is full_name)
    → FieldError on page load;
  * its P&L totals read line.net_balance / line.balance / line.account,
    none of which exist on TrialBalanceLine → AttributeError;
  * generate_partnership_tax_summary used p.name (field is full_name).

The fix reuses the canonical _get_tb_sections/_sum_section bucketing and the
real field names. These tests drive both views through the test client.
"""
from datetime import date
from decimal import Decimal

from django.test import Client as TestClient, TestCase

from accounts.models import User
from core.models import (
    Client as ClientModel,
    Entity,
    EntityOfficer,
    FinancialYear,
    LegalDocument,
    TrialBalanceLine,
)


class PartnershipDocsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="partnership_docs_test_admin",
            password="x",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Partnership Docs Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Smith & Jones Partnership",
            entity_type="partnership",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        cls.partner_a = EntityOfficer.objects.create(
            entity=cls.entity, full_name="Alice Smith", role="partner",
        )
        cls.partner_b = EntityOfficer.objects.create(
            entity=cls.entity, full_name="Bob Jones", role="partner",
        )
        # Revenue (income bucket, credit-normal → negative closing) and an
        # expense line (1200-1999 bucket, debit-normal).
        TrialBalanceLine.objects.create(
            financial_year=cls.fy,
            account_code="0200",
            account_name="Consulting fees",
            opening_balance=Decimal("0"),
            debit=Decimal("0"),
            credit=Decimal("100000"),
            closing_balance=Decimal("-100000.00"),
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy,
            account_code="1500",
            account_name="Rent",
            opening_balance=Decimal("0"),
            debit=Decimal("40000"),
            credit=Decimal("0"),
            closing_balance=Decimal("40000.00"),
        )

    def setUp(self):
        self.test_client = TestClient()
        self.test_client.force_login(self.user)
        session = self.test_client.session
        session["2fa_verified"] = True
        session.save()

    def test_partner_statements_page_renders(self):
        """The page must render with correct partner names and P&L totals
        (previously FieldError / AttributeError)."""
        response = self.test_client.get(
            f"/years/{self.fy.pk}/partner-statements/", secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice Smith")
        self.assertContains(response, "Bob Jones")
        self.assertEqual(response.context["total_revenue"], Decimal("100000.00"))
        self.assertEqual(response.context["total_expenses"], Decimal("40000.00"))
        self.assertEqual(response.context["net_profit"], Decimal("60000.00"))

    def test_partner_statements_excludes_ceased_partners(self):
        EntityOfficer.objects.create(
            entity=self.entity,
            full_name="Carol Gone",
            role="partner",
            date_ceased=date(2023, 6, 30),
        )
        response = self.test_client.get(
            f"/years/{self.fy.pk}/partner-statements/", secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Carol Gone")

    def test_non_partnership_entity_shows_error_not_crash(self):
        company = Entity.objects.create(
            entity_name="Not A Partnership Pty Ltd",
            entity_type="company",
            client=self.client_obj,
        )
        fy = FinancialYear.objects.create(
            entity=company,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        response = self.test_client.get(
            f"/years/{fy.pk}/partner-statements/", secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "only available for partnership entities")

    def test_generate_partnership_tax_summary(self):
        """Previously crashed on p.name; must create a LegalDocument with
        the partners' real names in context_data."""
        response = self.test_client.post(
            f"/years/{self.fy.pk}/partnership-tax-summary/", secure=True,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")

        doc = LegalDocument.objects.get(pk=payload["document_id"])
        self.assertEqual(doc.document_type, "partnership_tax_summary")
        names = [p["name"] for p in doc.context_data["partners"]]
        self.assertIn("Alice Smith", names)
        self.assertIn("Bob Jones", names)

    def test_generate_partner_statements(self):
        response = self.test_client.post(
            f"/years/{self.fy.pk}/partner-statements/generate/",
            data={
                "allocations": [
                    {
                        "partner_id": str(self.partner_a.pk),
                        "percentage": 60,
                        "partner_share": 36000,
                    },
                    {
                        "partner_id": str(self.partner_b.pk),
                        "percentage": 40,
                        "partner_share": 24000,
                    },
                ]
            },
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        docs = LegalDocument.objects.filter(
            financial_year=self.fy, document_type="partner_statement",
        )
        self.assertEqual(docs.count(), 2)
        titles = sorted(d.title for d in docs)
        self.assertIn("Alice Smith", titles[0])
