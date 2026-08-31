from decimal import Decimal

from django.test import TestCase

from core.models import AdjustingJournal, JournalLine


class CashbookModelFieldsTest(TestCase):
    def test_cashbook_journal_type_exists(self):
        self.assertEqual(AdjustingJournal.JournalType.CASHBOOK, "cashbook")
        self.assertIn(
            ("cashbook", "Cashbook (Cash Basis)"),
            AdjustingJournal.JournalType.choices,
        )

    def test_journal_line_gst_fields_default_to_today_behaviour(self):
        line = JournalLine()
        self.assertEqual(line.tax_code, "")
        self.assertEqual(line.gst_amount, Decimal("0"))
        self.assertIs(line.is_gst_control, False)
