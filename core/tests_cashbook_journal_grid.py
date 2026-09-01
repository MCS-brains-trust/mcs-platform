"""The cashbook grid's rendering contract.

Task 5 puts the Tax and GST columns on the journal grid. The subtle part is
what the GST cell *is*: it renders ``gst_override``, whose blank means
"calculate 1/11th", not nil. So the calculated figure may only ever be shown
as a placeholder by the client-side script -- the moment it is rendered as a
value, every row posts an explicit override and the server's own arithmetic
becomes dead code. These tests hold that line.
"""

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


class CashbookGridRenderTest(Require2FAMixin, TestCase):
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
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant", totp_secret="TESTSECRET", totp_confirmed=True,
        )
        self.entity.assigned_accountant = self.user
        self.entity.save(update_fields=["assigned_accountant"])
        self.login_as(self.user)

    def _get(self, name, pk):
        return self.client.get(reverse(name, args=[pk]), secure=True)

    def _gst_override_inputs(self, html):
        """Every rendered gst_override <input>, as raw tags.

        Scanning the whole page for the figure is no good: the grid's own
        script carries it in a comment.
        """
        import re
        return re.findall(r'<input[^>]*name="lines-\d+-gst_override"[^>]*>', html)

    @staticmethod
    def _value_of(tag):
        import re
        m = re.search(r'\svalue="([^"]*)"', tag)
        return m.group(1) if m else ""

    def _tax_of(self, accounts, code):
        for a in accounts:
            if a["client_account_code"] == code:
                return a.get("client_account_tax_code")
        raise AssertionError("account %s not in the picker payload" % code)

    # ---- the create grid --------------------------------------------------

    def test_create_grid_renders_a_tax_code_and_gst_override_input_per_line(self):
        html = self._get("core:adjustment_create", self.fy.pk).content.decode()
        self.assertIn('name="lines-0-tax_code"', html)
        self.assertIn('name="lines-0-gst_override"', html)

    def test_create_grid_never_renders_gst_amount_as_an_input(self):
        """gst_amount is derived. Exposing it would post the split's own
        output back as though the accountant had typed it."""
        html = self._get("core:adjustment_create", self.fy.pk).content.decode()
        self.assertNotIn('name="lines-0-gst_amount"', html)

    def test_the_add_line_template_carries_the_two_new_cells(self):
        """The grid's Add Line button builds a row in JavaScript. A row built
        there without these cells would be a column short and could not carry
        a tax code at all."""
        html = self._get("core:adjustment_create", self.fy.pk).content.decode()
        self.assertIn("name=\"lines-${idx}-tax_code\"", html)
        self.assertIn("name=\"lines-${idx}-gst_override\"", html)

    # ---- the account picker payload --------------------------------------

    def test_the_picker_payload_carries_the_charts_tax_code(self):
        accounts = self._get("core:adjustment_create", self.fy.pk).context["accounts"]
        self.assertEqual(self._tax_of(accounts, "105"), "GST")
        self.assertEqual(self._tax_of(accounts, "1804"), "INP")

    def test_a_trial_balance_line_does_not_strip_the_charts_tax_code(self):
        """The TB name wins the merge, but a TB line carries no tax code. If
        the merge replaced the chart entry wholesale the default would vanish
        for exactly the accounts that have been used before."""
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="105",
            account_name="Sales - per the ledger",
            closing_balance=Decimal("-1000.00"),
            debit=Decimal("0"), credit=Decimal("1000.00"),
        )
        accounts = self._get("core:adjustment_create", self.fy.pk).context["accounts"]
        self.assertEqual(self._tax_of(accounts, "105"), "GST")
        # and the TB's name still wins
        names = {a["client_account_code"]: a["client_account_name"] for a in accounts}
        self.assertEqual(names["105"], "Sales - per the ledger")


class CashbookGridEditAndDetailTest(CashbookGridRenderTest):
    """Same fixture, but with a posted cashbook journal to read back."""

    def setUp(self):
        super().setUp()
        self.journal = AdjustingJournal.objects.create(
            financial_year=self.fy,
            journal_type=AdjustingJournal.JournalType.CASHBOOK,
            journal_date=date(2025, 12, 31),
            description="Oct-Dec 2025",
            created_by=self.user,
        )
        self.journal.lines.create(
            line_number=1, account_code="105", account_name="Sales",
            debit=Decimal("0"), credit=Decimal("23187.00"), tax_code="GST",
        )
        # The contra. Keyed gross like every other line, but N-T, so the split
        # leaves it whole and the journal still balances against the net sale
        # plus its 3380 control row.
        self.journal.lines.create(
            line_number=2, account_code="4080", account_name="Drawings",
            debit=Decimal("23187.00"), credit=Decimal("0"), tax_code="N-T",
        )
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)

    # The parent's create-grid assertions run again here harmlessly; the
    # cases below are what this fixture exists for.

    def test_the_split_stored_net_beside_the_gst(self):
        line = self.journal.lines.get(account_code="105")
        self.assertEqual(line.gst_amount, Decimal("2107.91"))
        self.assertEqual(line.credit, Decimal("21079.09"))
        self.assertIsNone(line.gst_override)

    def test_edit_grid_shows_gross_but_leaves_the_gst_cell_empty(self):
        """The GST the split calculated must not come back as an override.
        The grid shows gross; the override cell stays blank so the next save
        recalculates rather than freezing 2,107.91 into the row."""
        html = self._get("core:journal_edit", self.journal.pk).content.decode()
        self.assertIn("23187.00", html.replace(",", ""))
        tags = self._gst_override_inputs(html)
        self.assertTrue(tags, "the edit grid rendered no gst_override input")
        self.assertEqual(
            [self._value_of(t) for t in tags], [""] * len(tags),
            "the split's own GST came back as an override",
        )

    def test_a_typed_override_does_come_back_in_the_cell(self):
        line = self.journal.lines.get(account_code="105")
        line.gst_override = Decimal("1053.96")
        line.save(update_fields=["gst_override"])
        html = self._get("core:journal_edit", self.journal.pk).content.decode()
        self.assertIn(
            "1053.96",
            [self._value_of(t) for t in self._gst_override_inputs(html)],
        )

    def test_the_edit_formset_excludes_the_generated_control_rows(self):
        html = self._get("core:journal_edit", self.journal.pk).content.decode()
        self.assertNotIn('value="3380"', html)

    # ---- the detail page --------------------------------------------------

    def test_detail_page_shows_the_tax_code_and_the_gst(self):
        html = self._get("core:journal_detail", self.journal.pk).content.decode()
        self.assertIn(">Tax<", html)
        self.assertIn(">GST<", html)
        self.assertIn("<code>GST</code>", html)
        self.assertIn("2107.91", html.replace(",", ""))

    def test_detail_page_marks_the_generated_control_row(self):
        html = self._get("core:journal_detail", self.journal.pk).content.decode()
        self.assertIn("generated", html)
        self.assertIn("3380", html)

    def test_a_non_cashbook_journal_detail_has_no_gst_columns(self):
        plain = AdjustingJournal.objects.create(
            financial_year=self.fy,
            journal_type=AdjustingJournal.JournalType.ADJUSTING,
            journal_date=date(2025, 12, 31), description="Plain",
            created_by=self.user,
        )
        plain.lines.create(
            line_number=1, account_code="4080", account_name="Drawings",
            debit=Decimal("100.00"), credit=Decimal("0"),
        )
        html = self._get("core:journal_detail", plain.pk).content.decode()
        self.assertNotIn(">Tax<", html)
