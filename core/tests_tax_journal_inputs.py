"""
Tests for the tax journal posting with accountant-supplied inputs.

The Calculate & Post Tax Journal button no longer posts blind from the trial
balance: the modal asks for the taxable profit (prefilled with the TB
accounting profit) and whether the company is a base rate entity, and the
view computes the journal from those answers.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase, Client as TestClient, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import (
    Client, Entity, FinancialYear, TrialBalanceLine, AccountMapping,
    AdjustingJournal, JournalLine,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TaxJournalInputsTestCase(TestCase):
    """The tax journal posts from the form's profit and base-rate answers."""

    @classmethod
    def setUpTestData(cls):
        two_fa_kwargs = {"totp_secret": "TESTSECRET", "totp_confirmed": True}
        cls.accountant = User.objects.create_user(
            username="tj_accountant", password="testpass123",
            role=User.Role.ACCOUNTANT, first_name="Tax", last_name="Acct",
            **two_fa_kwargs,
        )
        cls.client_obj = Client.objects.create(name="TJ Test Client")
        cls.entity = Entity.objects.create(
            entity_name="TJ Company",
            entity_type="company",
            client=cls.client_obj,
            assigned_accountant=cls.accountant,
            is_base_rate_entity=None,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status="finalised",
        )
        cls.revenue_mapping = AccountMapping.objects.create(
            standard_code="REV001",
            line_item_label="Sales Revenue",
            financial_statement="income_statement",
            statement_section="Revenue",
            display_order=100,
        )
        cls.expense_mapping = AccountMapping.objects.create(
            standard_code="EXP001",
            line_item_label="Operating Expenses",
            financial_statement="income_statement",
            statement_section="Expenses",
            display_order=200,
        )
        # TB accounting profit = 40,000 (only the modal prefill, not the post)
        TrialBalanceLine.objects.create(
            financial_year=cls.fy,
            account_code="1000",
            account_name="Sales Revenue",
            debit=Decimal("0"),
            credit=Decimal("100000"),
            mapped_line_item=cls.revenue_mapping,
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy,
            account_code="5000",
            account_name="Operating Expenses",
            debit=Decimal("60000"),
            credit=Decimal("0"),
            mapped_line_item=cls.expense_mapping,
        )

    def setUp(self):
        self.client = TestClient()
        self.client.force_login(self.accountant)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()
        self.url = reverse("core:calculate_tax_journal", args=[self.fy.pk])

    def _post(self, **data):
        return self.client.post(self.url, data, secure=True)

    def _tax_journals(self):
        return AdjustingJournal.objects.filter(
            financial_year=self.fy, journal_type="tax",
        )

    # --- Posting from the supplied inputs ---

    def test_posts_from_supplied_profit_at_base_rate(self):
        response = self._post(tax_profit="100000", is_base_rate_entity="true")
        self.assertEqual(response.status_code, 302)
        journal = self._tax_journals().get()
        self.assertEqual(journal.total_debit, Decimal("25000"))
        self.assertEqual(journal.total_credit, Decimal("25000"))
        lines = {l.account_code: l for l in journal.lines.all()}
        self.assertEqual(lines["4110"].debit, Decimal("25000"))
        self.assertEqual(lines["3325"].credit, Decimal("25000"))

    def test_posts_from_supplied_profit_at_standard_rate(self):
        self._post(tax_profit="100000", is_base_rate_entity="false")
        journal = self._tax_journals().get()
        self.assertEqual(journal.total_debit, Decimal("30000"))

    def test_supplied_profit_overrides_the_trial_balance(self):
        """TB profit is 40,000 but the accountant enters taxable income 55,000."""
        self._post(tax_profit="55000", is_base_rate_entity="true")
        journal = self._tax_journals().get()
        self.assertEqual(journal.total_debit, Decimal("13750"))

    def test_tax_rounds_up_to_the_next_dollar(self):
        self._post(tax_profit="55000.50", is_base_rate_entity="true")
        journal = self._tax_journals().get()
        # 55000.50 * 0.25 = 13750.125 -> 13751
        self.assertEqual(journal.total_debit, Decimal("13751"))

    def test_profit_accepts_currency_formatting(self):
        self._post(tax_profit="$55,000.00", is_base_rate_entity="true")
        journal = self._tax_journals().get()
        self.assertEqual(journal.total_debit, Decimal("13750"))

    def test_base_rate_answer_is_saved_on_the_entity(self):
        self.assertIsNone(self.entity.is_base_rate_entity)
        self._post(tax_profit="100000", is_base_rate_entity="true")
        self.entity.refresh_from_db()
        self.assertIs(self.entity.is_base_rate_entity, True)

    def test_standard_rate_answer_is_saved_on_the_entity(self):
        self._post(tax_profit="100000", is_base_rate_entity="false")
        self.entity.refresh_from_db()
        self.assertIs(self.entity.is_base_rate_entity, False)

    def test_unset_base_rate_flag_no_longer_blocks_posting(self):
        """The modal supplies the answer, so a blank entity flag can't block."""
        self._post(tax_profit="100000", is_base_rate_entity="true")
        self.assertEqual(self._tax_journals().count(), 1)

    def test_journal_description_records_the_taxable_profit(self):
        self._post(tax_profit="55000", is_base_rate_entity="true")
        journal = self._tax_journals().get()
        self.assertIn("Income tax", journal.description)
        self.assertIn("taxable profit $55,000.00", journal.description)
        self.assertIn("25% (Base Rate Entity)", journal.description)

    def test_journal_posts_to_the_trial_balance(self):
        self._post(tax_profit="100000", is_base_rate_entity="true")
        journal = self._tax_journals().get()
        tb = TrialBalanceLine.objects.filter(
            financial_year=self.fy, source_journal=journal,
        )
        self.assertEqual(tb.count(), 2)

    # --- Validation ---

    def test_missing_profit_posts_nothing(self):
        self._post(is_base_rate_entity="true")
        self.assertFalse(self._tax_journals().exists())

    def test_invalid_profit_posts_nothing(self):
        self._post(tax_profit="not-a-number", is_base_rate_entity="true")
        self.assertFalse(self._tax_journals().exists())

    def test_missing_base_rate_answer_posts_nothing(self):
        self._post(tax_profit="100000")
        self.assertFalse(self._tax_journals().exists())
        # And the entity flag stays untouched
        self.entity.refresh_from_db()
        self.assertIsNone(self.entity.is_base_rate_entity)

    def test_zero_or_loss_profit_posts_nothing(self):
        self._post(tax_profit="0", is_base_rate_entity="true")
        self._post(tax_profit="-5000", is_base_rate_entity="true")
        self.assertFalse(self._tax_journals().exists())

    def test_duplicate_income_tax_journal_is_refused(self):
        self._post(tax_profit="100000", is_base_rate_entity="true")
        self._post(tax_profit="100000", is_base_rate_entity="true")
        self.assertEqual(self._tax_journals().count(), 1)

    def test_unfinalised_year_is_refused(self):
        fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status="in_review",
        )
        url = reverse("core:calculate_tax_journal", args=[fy.pk])
        self.client.post(url, {"tax_profit": "100000", "is_base_rate_entity": "true"}, secure=True)
        self.assertFalse(
            AdjustingJournal.objects.filter(financial_year=fy, journal_type="tax").exists()
        )

    def test_get_is_not_allowed(self):
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 405)

    # --- Modal prefill on the detail page ---

    def test_detail_page_prefills_the_tb_accounting_profit(self):
        response = self.client.get(
            reverse("core:financial_year_detail", args=[self.fy.pk]), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["tax_journal_default_profit"], Decimal("40000.00"),
        )
        self.assertContains(response, 'id="taxJournalModal"')

    def test_detail_page_hides_the_modal_once_posted(self):
        self._post(tax_profit="100000", is_base_rate_entity="true")
        response = self.client.get(
            reverse("core:financial_year_detail", args=[self.fy.pk]), secure=True,
        )
        self.assertNotContains(response, 'id="taxJournalModal"')
        self.assertNotIn("tax_journal_default_profit", response.context)
