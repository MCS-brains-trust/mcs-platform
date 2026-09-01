from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    AdjustingJournal, Entity, EntityChartOfAccount, FinancialYear,
    TrialBalanceLine,
)
from core.test_support import Require2FAMixin


class CashbookJournalViewTest(Require2FAMixin, TestCase):
    CHART = [
        ("105", "Sales", "GST", "revenue"),
        ("1804", "M/V car - Fuel & oil", "INP", "expenses"),
        ("3380", "GST payable control account", "", "liabilities"),
        ("4080", "Drawings", "", "capital_accounts"),
    ]

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Cashbook Client",
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
        User = get_user_model()
        # Require2FAMiddleware bounces an authenticated user with no TOTP
        # configured to the setup-2fa page before it ever looks at the
        # session flag, so the user needs both halves: the secret here, and
        # the verified session that login_as sets.
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant", totp_secret="TESTSECRET", totp_confirmed=True,
        )
        # An accountant reaches only the entities assigned to them; an
        # unassigned entity is denied outright, so assign this one rather
        # than reaching for a role that can see everything.
        self.entity.assigned_accountant = self.user
        self.entity.save(update_fields=["assigned_accountant"])
        self.login_as(self.user)

    def _post(self, journal_type, rows):
        data = {
            "journal_type": journal_type,
            "journal_date": "2025-12-31",
            "description": "Oct-Dec 2025 Income & Expenses",
            "narration": "",
            "lines-TOTAL_FORMS": str(len(rows)),
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for i, (code, name, dr, cr, tax, override) in enumerate(rows):
            data[f"lines-{i}-account_code"] = code
            data[f"lines-{i}-account_name"] = name
            data[f"lines-{i}-description"] = ""
            data[f"lines-{i}-debit"] = dr
            data[f"lines-{i}-credit"] = cr
            data[f"lines-{i}-tax_code"] = tax
            data[f"lines-{i}-gst_override"] = override
        # SECURE_SSL_REDIRECT is on; without secure=True this is a bare 301.
        return self.client.post(
            reverse("core:adjustment_create", args=[self.fy.pk]),
            data, secure=True, follow=True,
        )

    def test_posting_a_cashbook_journal_splits_gst_and_posts_net_to_the_tb(self):
        self._post("cashbook", [
            ("105", "Sales", "0", "23187.00", "GST", ""),
            ("1804", "M/V car - Fuel & oil", "1990.40", "0", "INP", ""),
            ("4080", "Drawings", "21196.60", "0", "N-T", ""),
        ])
        journal = AdjustingJournal.objects.get(financial_year=self.fy)
        self.assertEqual(journal.journal_type, "cashbook")

        sales = journal.lines.get(account_code="105", is_gst_control=False)
        self.assertEqual(sales.credit, Decimal("21079.09"))
        self.assertEqual(sales.gst_amount, Decimal("2107.91"))

        self.assertEqual(journal.lines.filter(is_gst_control=True).count(), 2)
        self.assertEqual(journal.total_debit, journal.total_credit)
        self.assertEqual(journal.total_debit, Decimal("23187.00"))

        tb = TrialBalanceLine.objects.get(
            financial_year=self.fy, account_code="3380",
        )
        # closing_balance is debit - credit: 180.95 - 2107.91
        self.assertEqual(tb.closing_balance, Decimal("-1926.96"))

    def test_a_typed_gst_override_is_respected_end_to_end(self):
        self._post("cashbook", [
            ("105", "Sales", "0", "23187.00", "GST", ""),
            ("1804", "M/V car - Fuel & oil", "1990.40", "0", "INP", "145.50"),
            ("4080", "Drawings", "21196.60", "0", "N-T", ""),
        ])
        journal = AdjustingJournal.objects.get(financial_year=self.fy)
        fuel = journal.lines.get(account_code="1804")
        self.assertEqual(fuel.gst_amount, Decimal("145.50"))
        # The non-creditable 35.45 stays in the expense.
        self.assertEqual(fuel.debit, Decimal("1844.90"))

    def test_general_journal_is_not_split(self):
        self._post("general", [
            ("105", "Sales", "0", "1000.00", "", ""),
            ("4080", "Drawings", "1000.00", "0", "", ""),
        ])
        journal = AdjustingJournal.objects.get(financial_year=self.fy)
        self.assertEqual(journal.lines.filter(is_gst_control=True).count(), 0)
        self.assertEqual(
            journal.lines.get(account_code="105").credit, Decimal("1000.00"),
        )

    def test_editing_a_cashbook_journal_round_trips_without_drifting(self):
        """Re-posting the same gross figures must not re-split the net ones."""
        self._post("cashbook", [
            ("105", "Sales", "0", "23187.00", "GST", ""),
            ("1804", "M/V car - Fuel & oil", "1990.40", "0", "INP", ""),
            ("4080", "Drawings", "21196.60", "0", "N-T", ""),
        ])
        journal = AdjustingJournal.objects.get(financial_year=self.fy)
        keyed = list(
            journal.lines.filter(is_gst_control=False).order_by("line_number")
        )

        data = {
            "journal_type": "cashbook",
            "journal_date": "2025-12-31",
            "description": "Oct-Dec 2025 Income & Expenses",
            "narration": "",
            "lines-TOTAL_FORMS": str(len(keyed)),
            "lines-INITIAL_FORMS": str(len(keyed)),
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for i, line in enumerate(keyed):
            # The grid posts GROSS, which is what the edit view renders back.
            gross_dr = line.debit + line.gst_amount if line.debit else Decimal("0")
            gross_cr = line.credit + line.gst_amount if line.credit else Decimal("0")
            data[f"lines-{i}-id"] = str(line.id)
            data[f"lines-{i}-journal"] = str(journal.id)
            data[f"lines-{i}-account_code"] = line.account_code
            data[f"lines-{i}-account_name"] = line.account_name
            data[f"lines-{i}-description"] = line.description
            data[f"lines-{i}-debit"] = str(gross_dr)
            data[f"lines-{i}-credit"] = str(gross_cr)
            data[f"lines-{i}-tax_code"] = line.tax_code
            data[f"lines-{i}-gst_override"] = ""

        self.client.post(
            reverse("core:journal_edit", args=[journal.pk]),
            data, secure=True, follow=True,
        )
        journal.refresh_from_db()
        sales = journal.lines.get(account_code="105", is_gst_control=False)
        self.assertEqual(sales.credit, Decimal("21079.09"))
        self.assertEqual(sales.gst_amount, Decimal("2107.91"))
        self.assertEqual(journal.lines.filter(is_gst_control=True).count(), 2)
        self.assertEqual(journal.total_debit, Decimal("23187.00"))
