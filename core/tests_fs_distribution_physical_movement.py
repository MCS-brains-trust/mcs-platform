"""Physical distribution is a movement line, not a cash deduction.

It was rendered under a "Less:" band after the subtotal, holding an unsigned
debit-positive figure. That produced a row whose sign flipped between columns
on the same statement -- Dr Services Family Trust FY2026 printed 9,866 for the
current year and (23,266) for the prior -- and a reader had to work out that a
bracketed number under "Less:" adds.

Physical distributions are journal movements on the beneficiary loan, the same
as funds loaned to trust. So the line now sits in the movement block carrying
its own sign: money out of the loan prints negative, money in prints positive,
and the column foots by addition.

Handiledger's own reference is no help here -- it prints a positive 2,000 under
"Less:" that ADDS (handiledger_reference/Handiledger distribution.pdf, Jess
Scarton FY2023: 289,240 -> 291,240) while bracketing the funds-loaned row on
the line above. There is no single rule there to copy.
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


def _pdf_text(buffer):
    from pypdf import PdfReader

    buffer.seek(0)
    return "\n".join(page.extract_text() or "" for page in PdfReader(buffer).pages)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class PhysicalDistributionMovementTests(TestCase):
    """Dr Services FY2026 shape: 32,703.73 b/f, 24,049.02 loaned in,
    9,866.43 paid out. Closing 46,886.32."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Physical Movement Client")
        cls.entity = Entity.objects.create(
            entity_name="Physical Movement Trust", entity_type="trust",
            client=cls.client_obj,
        )
        cls.officer = EntityOfficer.objects.create(
            entity=cls.entity, full_name="Ronen Davidov",
            role="beneficiary", display_order=1,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        for code, name in (
            ("4000.01", "Opening balance - Beneficiary"),
            ("4004.01", "Funds loaned to trust"),
            ("4053.01", "Physical distribution"),
        ):
            ClientAccountMapping.objects.create(
                entity=cls.entity, client_account_code=code,
                client_account_name=name, beneficiary_officer=cls.officer,
            )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="4000.01",
            account_name="Opening balance - Beneficiary",
            closing_balance=Decimal("-32703.73"),
            prior_credit=Decimal("32703.73"), source="rollover",
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="4004.01",
            account_name="Funds loaned to trust",
            closing_balance=Decimal("-24049.02"),
            credit=Decimal("24049.02"), source="manual_journal",
        )

    def _paid_out(self):
        """A debit on 4053: cash out of the loan."""
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="4053.01",
            account_name="Physical distribution",
            closing_balance=Decimal("9866.43"),
            debit=Decimal("9866.43"), source="manual_journal",
        )

    def _paid_in(self):
        """A credit on 4053: a movement the other way, equally legitimate."""
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="4053.01",
            account_name="Physical distribution",
            closing_balance=Decimal("-9866.43"),
            credit=Decimal("9866.43"), source="manual_journal",
        )

    def test_the_less_band_is_gone(self):
        self._paid_out()
        self.assertNotIn("Less:", _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy})))

    def test_a_payment_out_prints_negative(self):
        self._paid_out()
        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))
        self.assertIn("Physical distribution\n(9,866)", text)

    def test_a_movement_the_other_way_prints_positive(self):
        self._paid_in()
        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))
        self.assertIn("Physical distribution\n9,866", text)
        self.assertNotIn("(9,866)", text)

    def test_it_sits_with_the_movements_directly_above_closing_balance(self):
        self._paid_out()
        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))
        lines = [l for l in text.split("\n") if l.strip()]
        idx = lines.index("Physical distribution")
        self.assertEqual(lines[idx - 2], "Profit distribution for year")
        self.assertEqual(lines[idx + 2], "Closing balance")

    def test_the_pre_physical_subtotal_row_is_gone(self):
        """56,753 was the subtotal struck before the physical row was deducted.

        With physical folded into the movements there is nothing to subtotal --
        the running total and the closing balance are the same figure, so only
        the closing balance is printed.
        """
        self._paid_out()
        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))
        self.assertNotIn("56,753", text)
        self.assertEqual(text.count("46,886"), 3)  # closing, loans total, funds total

    def test_the_row_is_omitted_when_there_is_no_movement(self):
        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))
        self.assertNotIn("Physical distribution", text)
