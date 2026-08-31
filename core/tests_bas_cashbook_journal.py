from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.bas_utils import calculate_gst_for_period, get_period_dates
from core.gst_journal import split_cashbook_journal
from core.models import (
    AdjustingJournal, Entity, EntityChartOfAccount, FinancialYear, JournalLine,
)


class CashbookBasTest(TestCase):
    """ELLIOTT JAQUES JE-001, the journal this feature was built for."""

    JE001 = [
        ("105", "Sales", "0", "23187.00", "GST"),
        ("1510", "Accountancy", "250.00", "0", "INP"),
        ("1804", "M/V car - Fuel & oil", "1990.40", "0", "INP"),
        ("1845", "Protective clothing", "200.00", "0", "INP"),
        ("1940", "Telephone & Internet", "85.00", "0", "INP"),
        ("1940", "Telephone & Internet", "50.00", "0", "INP"),
        ("1809", "M/V car - Other", "605.00", "0", "INP"),
        ("1946", "Tools", "510.00", "0", "inp"),
        ("1800", "Materials & supplies", "570.00", "0", "INP"),
        ("1808", "M/V car - Repairs", "345.00", "0", "INP"),
        ("4080", "Drawings", "18581.60", "0", "N-T"),
    ]

    CHART = [
        ("105", "Sales", "GST", "revenue"),
        ("1510", "Accountancy", "INP", "expenses"),
        ("1800", "Materials & supplies", "INP", "expenses"),
        ("1804", "M/V car - Fuel & oil", "INP", "expenses"),
        ("1808", "M/V car - Repairs", "INP", "expenses"),
        ("1809", "M/V car - Other", "INP", "expenses"),
        ("1845", "Protective clothing", "INP", "expenses"),
        ("1940", "Telephone & Internet", "INP", "expenses"),
        ("1946", "Tools", "inp", "expenses"),
        ("3380", "GST payable control account", "", "liabilities"),
        ("4080", "Drawings", "", "capital_accounts"),
    ]

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="ELLIOTT JAQUES TEST",
            entity_type=Entity.EntityType.SOLE_TRADER,
            is_gst_registered=True,
        )
        for code, name, tax, section in self.CHART:
            EntityChartOfAccount.objects.create(
                entity=self.entity, account_code=code, account_name=name,
                tax_code=tax, section=section,
            )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="Q2 2026",
            start_date=date(2025, 10, 1), end_date=date(2025, 12, 31),
        )

    def _journal(self, journal_type, rows):
        journal = AdjustingJournal.objects.create(
            financial_year=self.fy, journal_type=journal_type,
            journal_date=date(2025, 12, 31), status="posted",
            description="Oct-Dec 2025 Income & Expenses",
        )
        for i, (code, name, dr, cr, tax) in enumerate(rows, start=1):
            JournalLine.objects.create(
                journal=journal, line_number=i,
                account_code=code, account_name=name,
                debit=Decimal(dr), credit=Decimal(cr), tax_code=tax,
            )
        return journal

    def _q2(self):
        start, end = get_period_dates(self.fy, "quarterly", 2)
        return calculate_gst_for_period(self.fy, start, end)

    def test_cashbook_journal_reports_the_accounts_method_figures(self):
        journal = self._journal(
            AdjustingJournal.JournalType.CASHBOOK, self.JE001,
        )
        split_cashbook_journal(journal)
        bas = self._q2()["bas_data"]
        # G1/G11 stay GROSS — they are turnover and purchases.
        self.assertEqual(bas["G1"], Decimal("23187.00"))
        self.assertEqual(bas["G11"], Decimal("4605.40"))
        # 1A/1B are the GST actually recorded, not G8/11 and G19/11.
        self.assertEqual(bas["1A"], Decimal("2107.91"))
        self.assertEqual(bas["1B"], Decimal("418.68"))
        self.assertEqual(bas["gst_payable"], Decimal("1689.23"))
        # G9/G20 move with 1A/1B so the worksheet cannot contradict itself.
        self.assertEqual(bas["G9"], bas["1A"])
        self.assertEqual(bas["G20"], bas["1B"])

    def test_the_3380_control_lines_contribute_to_no_g_label(self):
        journal = self._journal(
            AdjustingJournal.JournalType.CASHBOOK, self.JE001,
        )
        split_cashbook_journal(journal)
        result = self._q2()
        codes = {
            line["code"]
            for line in result["sales_lines"] + result["purchase_lines"]
            + result["capital_lines"]
        }
        self.assertNotIn("3380", codes)

    def test_an_explicit_nt_tax_code_keeps_drawings_off_the_worksheet(self):
        journal = self._journal(
            AdjustingJournal.JournalType.CASHBOOK, self.JE001,
        )
        split_cashbook_journal(journal)
        result = self._q2()
        codes = {
            line["code"]
            for line in result["sales_lines"] + result["purchase_lines"]
            + result["capital_lines"]
        }
        self.assertNotIn("4080", codes)

    def test_3380_ties_to_the_bas_net(self):
        """The whole point of the feature, asserted directly."""
        journal = self._journal(
            AdjustingJournal.JournalType.CASHBOOK, self.JE001,
        )
        split_cashbook_journal(journal)
        controls = journal.lines.filter(is_gst_control=True)
        control_net = (
            sum(l.credit for l in controls) - sum(l.debit for l in controls)
        )
        self.assertEqual(control_net, self._q2()["bas_data"]["gst_payable"])

    def test_a_legacy_gross_journal_reports_exactly_as_before(self):
        """gst_amount=0 and tax_code='' must fall back to 1/11 of the gross."""
        rows = [(c, n, dr, cr, "") for c, n, dr, cr, _ in self.JE001]
        self._journal(AdjustingJournal.JournalType.GENERAL, rows)
        bas = self._q2()["bas_data"]
        self.assertEqual(bas["G1"], Decimal("23187.00"))
        self.assertEqual(bas["G11"], Decimal("4605.40"))
        self.assertEqual(bas["1A"], Decimal("2107.91"))
        self.assertEqual(bas["1B"], Decimal("418.68"))
