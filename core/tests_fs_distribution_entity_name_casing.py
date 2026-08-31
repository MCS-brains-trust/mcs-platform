"""The distribution summary must use the entity name as stored.

In the Dr Services Family Trust FY2026 pack, pages 10-11 were headed
"DR SERVICES FAMILY TRUST" while every other page read "Dr Services Family
Trust". _build_beneficiary_distribution_summary upper-cased the name; no other
document does.

Handiledger's own packs are uppercase throughout because the name is stored
that way (see handiledger_reference/Financial Statements SCARFT.pdf, which is
"SCARTON FAMILY TRUST" on every page). Rendering the stored name verbatim
reproduces that for an uppercase entity and stays consistent for a mixed-case
one -- upper-casing here can only ever disagree with the rest of the pack.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.fs_template_service import _build_beneficiary_distribution_summary
from core.models import (
    Client, ClientAccountMapping, Entity, EntityOfficer, FinancialYear,
    TrialBalanceLine,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

MIXED_CASE_NAME = "Dr Services Family Trust"


def _pdf_text(buffer):
    from pypdf import PdfReader

    buffer.seek(0)
    return "\n".join(page.extract_text() or "" for page in PdfReader(buffer).pages)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DistributionEntityNameCasingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Casing Test Client")
        cls.entity = Entity.objects.create(
            entity_name=MIXED_CASE_NAME, entity_type="trust", client=cls.client_obj,
        )
        cls.officer = EntityOfficer.objects.create(
            entity=cls.entity, full_name="Ronen Davidov",
            role="beneficiary", display_order=1,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        ClientAccountMapping.objects.create(
            entity=cls.entity, client_account_code="4004.01",
            client_account_name="Funds loaned to trust",
            beneficiary_officer=cls.officer,
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="4004.01",
            account_name="Funds loaned to trust",
            closing_balance=Decimal("-24049.02"), credit=Decimal("24049.02"),
            source="manual_journal",
        )

    def test_name_is_rendered_as_stored(self):
        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))

        self.assertIn(MIXED_CASE_NAME, text)
        self.assertNotIn(MIXED_CASE_NAME.upper(), text)

    def test_an_uppercase_entity_name_is_left_uppercase(self):
        """Not a lower-casing rule -- the stored name is reproduced verbatim."""
        self.entity.entity_name = "SCARTON FAMILY TRUST"
        self.entity.save(update_fields=["entity_name"])

        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))
        self.assertIn("SCARTON FAMILY TRUST", text)
