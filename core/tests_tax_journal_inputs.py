"""Posting a tax journal asks for the figures instead of assuming them.

Both tax routes derived everything: the tax base was the accounting profit
straight off the trial balance, and the rate came from the entity's base-rate
flag. One click posted the result. Accounting profit is not taxable income —
add-backs, non-deductibles and prior-year losses all sit between them — so
the amount posted was wrong except by coincidence.

The taxable profit is now entered, and the base-rate answer is prefilled from
the entity's setup but confirmed at posting time. The entity flag is
deliberately NOT written back: posting a journal should not quietly edit
entity data, so the journal records the rate actually used.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import (
    AccountMapping,
    AdjustingJournal,
    Client as ClientModel,
    Entity,
    FinancialYear,
    TrialBalanceLine,
)
from core.tests_fs_company_generation import STORAGES_OVERRIDE


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TaxJournalAsksForItsFiguresTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="tax_journal_admin", password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-tax-journal", totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Tax Journal Client")

    def setUp(self):
        self.client.force_login(self.user)
        s = self.client.session
        s["2fa_verified"] = True
        s.save()

    def _fy(self, *, base_rate=False, name="Taxwell Pty Ltd"):
        entity = Entity.objects.create(
            entity_name=name, entity_type="company",
            client=self.client_obj, is_base_rate_entity=base_rate)
        fy = FinancialYear.objects.create(
            entity=entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)
        # A real accounting profit of 400,000 sits in the trial balance, and
        # it must never be what gets taxed. Without it the "posts nothing"
        # tests below would pass merely because an empty TB reads as a loss.
        revenue = AccountMapping.objects.get_or_create(
            standard_code="IS-REV-TAX", defaults={
                "line_item_label": "Revenue",
                "financial_statement": "income_statement",
                "statement_section": "Revenue"})[0]
        expenses = AccountMapping.objects.get_or_create(
            standard_code="IS-EXP-TAX", defaults={
                "line_item_label": "Other expenses",
                "financial_statement": "income_statement",
                "statement_section": "Expenses"})[0]
        TrialBalanceLine.objects.create(
            financial_year=fy, account_code="620", account_name="Sales",
            mapped_line_item=revenue, credit=Decimal("500000"),
            debit=Decimal("0"), closing_balance=Decimal("-500000"))
        TrialBalanceLine.objects.create(
            financial_year=fy, account_code="1510", account_name="Accountancy",
            mapped_line_item=expenses, debit=Decimal("100000"),
            credit=Decimal("0"), closing_balance=Decimal("100000"))
        return fy

    def test_the_fixture_really_has_an_accounting_profit(self):
        """Otherwise every assertion below is testing an empty trial balance."""
        from core.views import _calculate_net_profit
        fy = self._fy(name="Fixture Sanity Co")
        self.assertEqual(_calculate_net_profit(fy), Decimal("400000"))

    def _post(self, fy, **data):
        return self.client.post(
            reverse("core:calculate_tax_journal", kwargs={"pk": fy.pk}),
            data, secure=True, follow=True)

    def _journal(self, fy):
        return AdjustingJournal.objects.filter(
            financial_year=fy, journal_type="tax").first()

    def test_the_entered_profit_is_what_gets_taxed(self):
        """Nothing in the trial balance decides the amount any more."""
        fy = self._fy(name="Entered Profit Co")
        self._post(fy, taxable_profit="200000", is_base_rate_entity="false")
        journal = self._journal(fy)
        self.assertIsNotNone(journal)
        self.assertEqual(journal.total_debit, Decimal("60000"))

    def test_the_submitted_answer_sets_the_rate_not_the_entity_flag(self):
        """Entity is flagged standard rate; the form says base rate. The form
        wins, because it is the answer given for this year."""
        fy = self._fy(base_rate=False, name="Form Wins Co")
        self._post(fy, taxable_profit="200000", is_base_rate_entity="true")
        self.assertEqual(self._journal(fy).total_debit, Decimal("50000"))

    def test_the_entity_flag_is_left_alone(self):
        fy = self._fy(base_rate=False, name="Flag Untouched Co")
        self._post(fy, taxable_profit="200000", is_base_rate_entity="true")
        fy.entity.refresh_from_db()
        self.assertFalse(fy.entity.is_base_rate_entity)

    def test_the_tax_is_rounded_up(self):
        """Unchanged behaviour: ceil, as the old derivation did."""
        fy = self._fy(name="Rounding Co")
        self._post(fy, taxable_profit="1001", is_base_rate_entity="false")
        self.assertEqual(self._journal(fy).total_debit, Decimal("301"))

    def test_an_entity_with_no_base_rate_flag_can_still_post(self):
        """The old blocker refused outright; the question is now asked."""
        fy = self._fy(name="Unset Flag Co")
        fy.entity.is_base_rate_entity = None
        fy.entity.save()
        self._post(fy, taxable_profit="200000", is_base_rate_entity="true")
        self.assertIsNotNone(self._journal(fy))

    def test_a_blank_profit_does_not_fall_back_to_accounting_profit(self):
        fy = self._fy(name="Blank Profit Co")
        self._post(fy, taxable_profit="", is_base_rate_entity="false")
        self.assertIsNone(self._journal(fy))

    def test_a_non_numeric_profit_posts_nothing(self):
        fy = self._fy(name="Rubbish Profit Co")
        self._post(fy, taxable_profit="abc", is_base_rate_entity="false")
        self.assertIsNone(self._journal(fy))

    def test_a_nil_or_negative_profit_posts_nothing(self):
        fy = self._fy(name="Loss Co")
        self._post(fy, taxable_profit="0", is_base_rate_entity="false")
        self.assertIsNone(self._journal(fy))
        self._post(fy, taxable_profit="-5000", is_base_rate_entity="false")
        self.assertIsNone(self._journal(fy))

    def test_an_unanswered_base_rate_question_posts_nothing(self):
        fy = self._fy(name="No Answer Co")
        self._post(fy, taxable_profit="200000")
        self.assertIsNone(self._journal(fy))

    def test_a_thousands_separator_is_accepted(self):
        """Accountants paste figures with commas and dollar signs."""
        fy = self._fy(name="Formatted Figure Co")
        self._post(fy, taxable_profit="$200,000", is_base_rate_entity="false")
        self.assertEqual(self._journal(fy).total_debit, Decimal("60000"))

    def test_a_second_journal_is_still_refused(self):
        fy = self._fy(name="Duplicate Guard Co")
        self._post(fy, taxable_profit="200000", is_base_rate_entity="false")
        self._post(fy, taxable_profit="999999", is_base_rate_entity="false")
        self.assertEqual(AdjustingJournal.objects.filter(
            financial_year=fy, journal_type="tax").count(), 1)

    def test_a_draft_year_is_still_refused(self):
        fy = self._fy(name="Draft Year Co")
        fy.status = FinancialYear.Status.DRAFT
        fy.save()
        self._post(fy, taxable_profit="200000", is_base_rate_entity="false")
        self.assertIsNone(self._journal(fy))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TaxProvisionAsksForItsFiguresTests(TestCase):
    """The provision route is the other door onto the same posting.

    Fixing only the red button would have left the automatic behaviour intact
    here: one click, accounting profit times a rate read off the entity.

    Note this route refuses a LOCKED year, the opposite preconditionrom
    calculate_tax_journal, so its fixture is a draft year. That asymmetry is
    pre-existing and deliberately left alone.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="tax_provision_admin", password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-tax-prov", totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Tax Provision Client")

    def setUp(self):
        self.client.force_login(self.user)
        s = self.client.session
        s["2fa_verified"] = True
        s.save()

    def _fy(self, *, base_rate=False, name="Provisionwell Pty Ltd"):
        entity = Entity.objects.create(
            entity_name=name, entity_type="company",
            client=self.client_obj, is_base_rate_entity=base_rate)
        fy = FinancialYear.objects.create(
            entity=entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.DRAFT)
        revenue = AccountMapping.objects.get_or_create(
            standard_code="IS-REV-TAX", defaults={
                "line_item_label": "Revenue",
                "financial_statement": "income_statement",
                "statement_section": "Revenue"})[0]
        TrialBalanceLine.objects.create(
            financial_year=fy, account_code="620", account_name="Sales",
            mapped_line_item=revenue, credit=Decimal("400000"),
            debit=Decimal("0"), closing_balance=Decimal("-400000"))
        return fy

    def _post(self, fy, **data):
        return self.client.post(
            reverse("core:auto_tax_provision", kwargs={"pk": fy.pk}),
            data, secure=True)

    def _journal(self, fy):
        return AdjustingJournal.objects.filter(
            financial_year=fy, journal_type="tax_provision").first()

    def test_the_entered_profit_is_what_gets_provisioned(self):
        fy = self._fy(name="Provision Entered Co")
        self._post(fy, taxable_profit="100000", is_base_rate_entity="false")
        journal = self._journal(fy)
        self.assertIsNotNone(journal)
        self.assertEqual(journal.total_debit, Decimal("30000"))

    def test_the_submitted_answer_sets_the_rate(self):
        fy = self._fy(base_rate=False, name="Provision Rate Co")
        self._post(fy, taxable_profit="100000", is_base_rate_entity="true")
        self.assertEqual(self._journal(fy).total_debit, Decimal("25000"))

    def test_a_blank_profit_posts_nothing(self):
        fy = self._fy(name="Provision Blank Co")
        response = self._post(fy, is_base_rate_entity="false")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self._journal(fy))

    def test_an_unanswered_base_rate_question_posts_nothing(self):
        fy = self._fy(name="Provision No Answer Co")
        response = self._post(fy, taxable_profit="100000")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self._journal(fy))

    def test_an_entity_with_no_base_rate_flag_can_still_post(self):
        fy = self._fy(name="Provision Unset Flag Co")
        fy.entity.is_base_rate_entity = None
        fy.entity.save()
        self._post(fy, taxable_profit="100000", is_base_rate_entity="true")
        self.assertIsNotNone(self._journal(fy))

    def test_the_status_endpoint_offers_the_prefills(self):
        """The dialog needs a starting figure and the entity's own answer."""
        fy = self._fy(base_rate=True, name="Provision Prefill Co")
        response = self.client.get(
            reverse("core:tax_provision_status", kwargs={"pk": fy.pk}),
            secure=True)
        data = response.json()
        self.assertTrue(data["eligible"], data)
        self.assertEqual(Decimal(str(data["suggested_taxable_profit"])),
                         Decimal("400000"))
        self.assertTrue(data["is_base_rate_entity"])

    def test_the_status_endpoint_is_eligible_without_the_flag(self):
        fy = self._fy(name="Provision Status Unset Co")
        fy.entity.is_base_rate_entity = None
        fy.entity.save()
        response = self.client.get(
            reverse("core:tax_provision_status", kwargs={"pk": fy.pk}),
            secure=True)
        data = response.json()
        self.assertTrue(data["eligible"], data)
        self.assertIsNone(data["is_base_rate_entity"])


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TheTaxDialogRendersTests(TestCase):
    """The button must open a dialog, not post on a bare confirm()."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="tax_dialog_admin", password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-tax-dialog", totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Tax Dialog Client")

    def setUp(self):
        self.client.force_login(self.user)
        s = self.client.session
        s["2fa_verified"] = True
        s.save()
        entity = Entity.objects.create(
            entity_name="Dialogwell Pty Ltd", entity_type="company",
            client=self.client_obj, is_base_rate_entity=True)
        self.fy = FinancialYear.objects.create(
            entity=entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)
        revenue = AccountMapping.objects.get_or_create(
            standard_code="IS-REV-TAX", defaults={
                "line_item_label": "Revenue",
                "financial_statement": "income_statement",
                "statement_section": "Revenue"})[0]
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="620", account_name="Sales",
            mapped_line_item=revenue, credit=Decimal("400000"),
            debit=Decimal("0"), closing_balance=Decimal("-400000"))

    def _html(self):
        response = self.client.get(
            reverse("core:financial_year_detail", kwargs={"pk": self.fy.pk}),
            secure=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_dialog_and_its_two_inputs_render(self):
        html = self._html()
        self.assertIn('id="taxJournalModal"', html)
        self.assertIn('name="taxable_profit"', html)
        self.assertIn('name="is_base_rate_entity"', html)

    def test_the_taxable_profit_is_prefilled_with_the_accounting_profit(self):
        self.assertIn('value="400000"', self._html())

    def test_the_base_rate_question_is_prefilled_from_the_entity(self):
        html = self._html()
        self.assertIn('<option value="true" selected>', html)

    def test_the_button_no_longer_posts_on_a_bare_confirm(self):
        """It opens the dialog instead."""
        html = self._html()
        self.assertIn('data-bs-target="#taxJournalModal"', html)
        self.assertNotIn("confirm('Calculate and post income tax journal?')", html)
