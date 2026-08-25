"""A posted tax journal can be reversed.

The tax journal is the one journal that could be created but never undone.
``calculate_tax_journal`` refuses to post unless the year is finalised, and
``journal_delete`` refuses to touch any journal in a finalised year — so once
the tax journal was posted the only way back was reopening a signed-off set of
financials.

Reversal follows the un-post pattern already used for trust distributions: in a
locked year an equal-and-opposite journal backs the amounts out and both
entries stay on file, and in an open year the original is simply voided and its
trial balance lines removed. The original is marked voided either way, which is
what frees the post button to run again with corrected figures.

The re-post guard used to match ``description__icontains="Income tax"``, which
a reversal journal's own description trips. It now keys off the journal type
and posted status — the same structural signal the trust distribution flow
adopted for the same reason.
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
class ReverseTaxJournalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="tax_reversal_admin", password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-tax-reversal", totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Tax Reversal Client")

    def setUp(self):
        self.client.force_login(self.user)
        s = self.client.session
        s["2fa_verified"] = True
        s.save()

    # -- fixtures ---------------------------------------------------------

    def _fy(self, *, name="Reversal Pty Ltd", status=None):
        entity = Entity.objects.create(
            entity_name=name, entity_type="company",
            client=self.client_obj, is_base_rate_entity=False)
        fy = FinancialYear.objects.create(
            entity=entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=status or FinancialYear.Status.FINALISED)
        revenue = AccountMapping.objects.get_or_create(
            standard_code="IS-REV-TAXREV", defaults={
                "line_item_label": "Revenue",
                "financial_statement": "income_statement",
                "statement_section": "Revenue"})[0]
        expenses = AccountMapping.objects.get_or_create(
            standard_code="IS-EXP-TAXREV", defaults={
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
        # The trial balance has to actually balance, otherwise
        # _verify_tb_balance warns on every post and a genuine imbalance
        # introduced by a reversal would be lost in the noise.
        TrialBalanceLine.objects.create(
            financial_year=fy, account_code="2000", account_name="Cash at bank",
            debit=Decimal("400000"), credit=Decimal("0"),
            closing_balance=Decimal("400000"))
        return fy

    def _post_tax(self, fy, profit="200000", base_rate="false"):
        return self.client.post(
            reverse("core:calculate_tax_journal", kwargs={"pk": fy.pk}),
            {"taxable_profit": profit, "is_base_rate_entity": base_rate},
            secure=True, follow=True)

    def _reverse(self, fy):
        return self.client.post(
            reverse("core:reverse_tax_journal", kwargs={"pk": fy.pk}),
            {}, secure=True, follow=True)

    def _posted_tax_journals(self, fy):
        return AdjustingJournal.objects.filter(
            financial_year=fy, journal_type="tax",
            status=AdjustingJournal.JournalStatus.POSTED)

    def _tb_movement(self, fy, account_code):
        """Net debit less credit across every TB line for an account code."""
        lines = TrialBalanceLine.objects.filter(
            financial_year=fy, account_code=account_code)
        return sum(
            (line.debit or Decimal("0")) - (line.credit or Decimal("0"))
            for line in lines
        ) or Decimal("0")

    # -- locked year: contra reversal --------------------------------------

    def test_reversing_a_locked_year_creates_a_contra_journal(self):
        fy = self._fy(name="Contra Co")
        self._post_tax(fy)
        original = AdjustingJournal.objects.get(
            financial_year=fy, journal_type="tax")

        self._reverse(fy)

        reversal = AdjustingJournal.objects.get(
            financial_year=fy, journal_type="tax_reversal")
        self.assertEqual(reversal.status, AdjustingJournal.JournalStatus.POSTED)
        self.assertEqual(reversal.total_debit, original.total_credit)
        self.assertEqual(reversal.total_credit, original.total_debit)

    def test_the_contra_journal_mirrors_each_line(self):
        """Debits and credits swap sides, account for account."""
        fy = self._fy(name="Mirror Co")
        self._post_tax(fy)
        original = AdjustingJournal.objects.get(
            financial_year=fy, journal_type="tax")

        self._reverse(fy)

        reversal = AdjustingJournal.objects.get(
            financial_year=fy, journal_type="tax_reversal")
        src = {l.account_code: (l.debit, l.credit)
               for l in original.lines.all()}
        rev = {l.account_code: (l.debit, l.credit)
               for l in reversal.lines.all()}
        self.assertEqual(set(src), set(rev))
        for code, (debit, credit) in src.items():
            self.assertEqual(rev[code], (credit, debit))

    def test_the_trial_balance_nets_to_nil_for_both_tax_accounts(self):
        """The whole point: the tax is no longer in the numbers."""
        fy = self._fy(name="Nets To Nil Co")
        self._post_tax(fy)
        self.assertNotEqual(self._tb_movement(fy, "4110"), Decimal("0"))

        self._reverse(fy)

        self.assertEqual(self._tb_movement(fy, "4110"), Decimal("0"))
        self.assertEqual(self._tb_movement(fy, "3325"), Decimal("0"))

    def test_the_original_is_kept_and_marked_voided(self):
        """Audit trail: the entry that was posted stays visible."""
        fy = self._fy(name="Kept Co")
        self._post_tax(fy)
        original = AdjustingJournal.objects.get(
            financial_year=fy, journal_type="tax")

        self._reverse(fy)

        original.refresh_from_db()
        self.assertEqual(original.status, AdjustingJournal.JournalStatus.VOIDED)

    def test_the_reversal_names_the_journal_it_reverses(self):
        fy = self._fy(name="Named Co")
        self._post_tax(fy)
        original = AdjustingJournal.objects.get(
            financial_year=fy, journal_type="tax")

        self._reverse(fy)

        reversal = AdjustingJournal.objects.get(
            financial_year=fy, journal_type="tax_reversal")
        self.assertIn(original.reference_number, reversal.description)

    def test_the_year_stays_finalised(self):
        """Reversal must not quietly reopen a signed-off set."""
        fy = self._fy(name="Still Locked Co")
        self._post_tax(fy)

        self._reverse(fy)

        fy.refresh_from_db()
        self.assertEqual(fy.status, FinancialYear.Status.FINALISED)

    # -- re-posting afterwards --------------------------------------------

    def test_the_tax_journal_can_be_posted_again_after_reversal(self):
        """The guard used to match on the description, which the reversal's
        own description trips — reversing then left you unable to re-post."""
        fy = self._fy(name="Repost Co")
        self._post_tax(fy, profit="200000")
        self._reverse(fy)

        self._post_tax(fy, profit="100000")

        posted = self._posted_tax_journals(fy)
        self.assertEqual(posted.count(), 1)
        self.assertEqual(posted.first().total_debit, Decimal("30000"))

    def test_a_reversed_year_offers_the_post_button_again(self):
        fy = self._fy(name="Button Back Co")
        self._post_tax(fy)
        self._reverse(fy)

        response = self.client.get(
            reverse("core:financial_year_detail", kwargs={"pk": fy.pk}),
            secure=True)

        self.assertTrue(response.context["show_tax_journal_btn"])

    def test_a_posted_year_does_not_offer_the_post_button(self):
        """Guard still does its original job."""
        fy = self._fy(name="Button Hidden Co")
        self._post_tax(fy)

        response = self.client.get(
            reverse("core:financial_year_detail", kwargs={"pk": fy.pk}),
            secure=True)

        self.assertFalse(response.context["show_tax_journal_btn"])

    def test_a_legacy_tax_journal_without_a_type_still_blocks_reposting(self):
        """Older journals predate journal_type being set. The guard keeps
        recognising them by description so they are not posted twice."""
        fy = self._fy(name="Legacy Co")
        AdjustingJournal.objects.create(
            financial_year=fy, journal_type="general",
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=fy.end_date,
            description="Income tax on $200,000 taxable profit",
            total_debit=Decimal("60000"), total_credit=Decimal("60000"))

        response = self.client.get(
            reverse("core:financial_year_detail", kwargs={"pk": fy.pk}),
            secure=True)

        self.assertFalse(response.context["show_tax_journal_btn"])

    # -- open year: void ---------------------------------------------------

    def test_an_open_year_voids_instead_of_posting_a_contra(self):
        """No point leaving a contra pair in a year that can still be edited."""
        fy = self._fy(name="Open Year Co")
        self._post_tax(fy)
        fy.status = FinancialYear.Status.REOPENED
        fy.save()

        self._reverse(fy)

        self.assertEqual(
            AdjustingJournal.objects.filter(
                financial_year=fy, journal_type="tax_reversal").count(),
            0)
        self.assertEqual(self._tb_movement(fy, "4110"), Decimal("0"))

    # -- refusals ----------------------------------------------------------

    def test_reversing_without_a_tax_journal_does_nothing(self):
        fy = self._fy(name="Nothing To Reverse Co")

        self._reverse(fy)

        self.assertFalse(
            AdjustingJournal.objects.filter(financial_year=fy).exists())

    def test_reversing_twice_does_not_post_a_second_contra(self):
        fy = self._fy(name="Twice Co")
        self._post_tax(fy)

        self._reverse(fy)
        self._reverse(fy)

        self.assertEqual(
            AdjustingJournal.objects.filter(financial_year=fy).count(), 2)
        self.assertEqual(self._tb_movement(fy, "4110"), Decimal("0"))

    def test_a_user_without_accounting_permission_cannot_reverse(self):
        fy = self._fy(name="No Permission Co")
        self._post_tax(fy)
        viewer = User.objects.create_user(
            username="tax_reversal_viewer", password="testpass123",
            role=User.Role.READ_ONLY,
            totp_secret="dummy-secret-viewer", totp_confirmed=True)
        self.client.force_login(viewer)
        s = self.client.session
        s["2fa_verified"] = True
        s.save()

        self._reverse(fy)

        self.assertEqual(
            AdjustingJournal.objects.filter(
                financial_year=fy, journal_type="tax").count(),
            1)

    def test_a_get_request_does_not_reverse(self):
        """Reversal changes the books — it must not sit behind a plain link."""
        fy = self._fy(name="Get Request Co")
        self._post_tax(fy)

        self.client.get(
            reverse("core:reverse_tax_journal", kwargs={"pk": fy.pk}),
            secure=True)

        self.assertEqual(
            AdjustingJournal.objects.filter(
                financial_year=fy, journal_type="tax").count(),
            1)

    def test_a_non_company_entity_cannot_reverse(self):
        fy = self._fy(name="Trust Co")
        self._post_tax(fy)
        fy.entity.entity_type = "trust"
        fy.entity.save()

        self._reverse(fy)

        self.assertEqual(
            AdjustingJournal.objects.filter(
                financial_year=fy, journal_type="tax").count(),
            1)
