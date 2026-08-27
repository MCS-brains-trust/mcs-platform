"""
The chart of accounts owns the account name; an import never renames it.

Found on Minli Enterprise Unit Trust FY2026, where a Xero import left 25
trial balance rows carrying the Xero account name against a StatementHub
chart code — 620 read "Rental Income" instead of "Rents received", and
2000 read "PENMAN PROPERTY NOMINEES PTY L" instead of "Cash at bank".

The wizard already resolves every staged row to an EntityChartOfAccount
before commit (the Model A gate), so ``account_code`` was correct on those
rows. Only the name came from the source system. Where several source rows
map to one chart code the name must collapse to the chart's single name,
and the beneficiary-suffixed sub-accounts (4053.01 vs 4053.02) must stay
distinguishable — stripping the suffix renders two equity rows identical.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    StagedImport,
)


def _staged_line(code, name, debit="0", credit="0"):
    """A staged row as the wizard writes it, carrying the SOURCE name."""
    return {
        "account_code": code,
        "account_name": name,
        "debit": debit,
        "credit": credit,
        "movement_amount": debit,
        "mapped_id": "",
        "mapped_label": "",
        "confidence": "matched",
    }


class CommitImportUsesChartNameTests(TestCase):
    """commit_import must name every TB row from the entity chart."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="importer",
            email="importer@example.com",
            password="secret123",
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        self.entity = Entity.objects.create(entity_name="Minli Enterprise Unit Trust")
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
        )
        for code, name, section in [
            ("620", "Rents received", "revenue"),
            ("2000", "Cash at bank", "current_assets"),
            ("1670", "Contractor, sub-contractor & commission", "expenses"),
            ("4053.01", "Physical distribution — Double Water International Pty Ltd", "equity"),
            ("4053.02", "Physical distribution — Penman Property Nominees Pty Ltd", "equity"),
        ]:
            EntityChartOfAccount.objects.create(
                entity=self.entity,
                account_code=code,
                account_name=name,
                section=section,
            )

    def _commit(self, rows):
        """Stage ``rows`` then POST the commit. rows = [(line, chart_code)]."""
        StagedImport.objects.create(
            financial_year=self.fy,
            user=self.user,
            provider_name="Xero",
            import_mode="trial_balance",
            as_at_date=self.fy.end_date,
            lines=[line for line, _ in rows],
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        post = {}
        for i, (_, chart_code) in enumerate(rows):
            post[f"entity_acct_{i}"] = chart_code
            post[f"mapping_{i}"] = ""
        return self.client.post(
            reverse("integrations:commit_import", kwargs={"fy_pk": self.fy.pk}),
            data=post,
            secure=True,
        )

    def _names_by_code(self):
        return {
            (line.account_code, line.account_name)
            for line in self.fy.trial_balance_lines.all()
        }

    def test_source_name_does_not_override_chart_name(self):
        # Exactly the Minli rows: Xero calls 620 "Rental Income" and the
        # bank account after the unit holder. The chart calls them
        # "Rents received" and "Cash at bank", and the chart wins.
        self._commit([
            (_staged_line("200", "Rental Income", credit="1513.83"), "620"),
            (_staged_line("090", "PENMAN PROPERTY NOMINEES PTY L", debit="1513.83"), "2000"),
        ])

        self.assertEqual(
            self._names_by_code(),
            {("620", "Rents received"), ("2000", "Cash at bank")},
        )

    def test_many_source_rows_collapse_to_the_one_chart_name(self):
        # Minli 1670 received three differently-named Xero rows.
        self._commit([
            (_staged_line("300", "Contractor - Others", debit="100.00"), "1670"),
            (_staged_line("301", "Commission Paid", debit="200.00"), "1670"),
            (_staged_line("302", "Contract Labour", debit="300.00"), "1670"),
            (_staged_line("090", "PENMAN PROPERTY NOMINEES PTY L", credit="600.00"), "2000"),
        ])

        names = {
            line.account_name
            for line in self.fy.trial_balance_lines.filter(account_code="1670")
        }
        self.assertEqual(names, {"Contractor, sub-contractor & commission"})

    def test_beneficiary_suffixed_subaccounts_stay_distinguishable(self):
        # Xero sends both sub-accounts as the bare "Physical distribution";
        # the chart's beneficiary suffix is what tells the two equity rows
        # apart on the face of the balance sheet.
        self._commit([
            (_staged_line("400", "Physical distribution", debit="179426.03"), "4053.01"),
            (_staged_line("400", "Physical distribution", credit="179426.03"), "4053.02"),
        ])

        self.assertEqual(
            self._names_by_code(),
            {
                ("4053.01", "Physical distribution — Double Water International Pty Ltd"),
                ("4053.02", "Physical distribution — Penman Property Nominees Pty Ltd"),
            },
        )
