"""A comparative counts wherever it is stored, not only on a rollover row.

Kinross Builders FY2026, 2026-09-04: the prior-year column stopped balancing
after a QuickBooks import — it netted to -6,007.00 against a set of figures that
net to zero in the database.

The trial balance screen derived each row's prior-year figure like this:

    if line.source == 'rollover':
        line._py = prior_debit - prior_credit
    else:
        line._py = Decimal('0')

commit_import preserves comparatives by snapshotting them and writing them onto
the rows it recreates, and those rows are source='tb_import'. So every
comparative the import carried forward was thrown away by the screen: 27 rows on
Kinross FY2026, Sales at -2,099,805.00 and Materials at 1,574,849.00 among them.
What was left came from the rollover rows alone and did not balance, because half
the ledger had been dropped out of the total.

Sixteen financial years across six clients were affected, five of them showing a
visibly unbalanced prior column. It predates the import: Berwick Mechanical
Services has carried it since FY2018.

Verified before the change that summing prior_debit/prior_credit across *every*
row reproduces the prior year's closing balances exactly, account for account,
and that no account anywhere carries a comparative on both a rollover row and a
non-rollover one — so counting them all cannot double up.

The rollover test still governs _cy, which is a different question: a rollover
row's closing_balance is the new year's opening balance.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Entity, EntityChartOfAccount, FinancialYear, TrialBalanceLine
from core.test_support import Require2FAMixin


class ComparativesSurviveAnImportTest(Require2FAMixin, TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Kinross Builders Pty Ltd", entity_type=Entity.EntityType.COMPANY)
        for code, name, section in [
            ("630", "Sales", "revenue"),
            ("1800", "Materials & supplies", "expenses"),
            ("2000", "Cash at bank", "assets"),
        ]:
            EntityChartOfAccount.objects.create(
                entity=self.entity, account_code=code, account_name=name, section=section)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30))
        User = get_user_model()
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant", totp_secret="TESTSECRET", totp_confirmed=True)
        self.entity.assigned_accountant = self.user
        self.entity.save(update_fields=["assigned_accountant"])
        self.login_as(self.user)

    def _line(self, code, name, source, dr="0", cr="0", pdr="0", pcr="0", adj=False):
        dr, cr = Decimal(dr), Decimal(cr)
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            source=source, is_adjustment=adj, debit=dr, credit=cr,
            closing_balance=dr - cr,
            prior_debit=Decimal(pdr), prior_credit=Decimal(pcr))

    def _imported_year(self):
        """Kinross FY2026's shape: comparatives split across both row types.

        A year with them ALL on tb_import rows would still look balanced once
        they were dropped -- nothing against nothing. The imbalance appears
        precisely because the rollover rows survive the cull and the imported
        ones do not, leaving one side of the ledger standing alone.
        """
        self._line("2000", "Cash at bank", "rollover", pdr="600.00")
        self._line("630", "Sales", "tb_import", cr="2000.00", pcr="1500.00")
        self._line("1800", "Materials & supplies", "tb_import", dr="1200.00", pdr="900.00")

    def test_the_prior_year_column_balances_after_an_import(self):
        self._imported_year()

        response = self.client.get(
            reverse("core:financial_year_detail", args=[self.fy.pk]), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["total_prior_debit"],
            response.context["total_prior_credit"],
        )

    def test_the_comparatives_are_the_ones_that_were_imported(self):
        self._imported_year()

        response = self.client.get(
            reverse("core:financial_year_detail", args=[self.fy.pk]), secure=True)

        self.assertEqual(response.context["total_prior_debit"], Decimal("1500.00"))
        self.assertEqual(response.context["total_prior_credit"], Decimal("1500.00"))

    def test_a_rollover_rows_comparative_still_counts(self):
        """The behaviour that already worked must keep working."""
        self._line("630", "Sales", "rollover", pcr="1500.00")
        self._line("1800", "Materials & supplies", "rollover", pdr="1500.00")

        response = self.client.get(
            reverse("core:financial_year_detail", args=[self.fy.pk]), secure=True)

        self.assertEqual(response.context["total_prior_debit"], Decimal("1500.00"))
        self.assertEqual(response.context["total_prior_credit"], Decimal("1500.00"))
