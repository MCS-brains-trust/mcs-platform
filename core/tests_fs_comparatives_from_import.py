"""Imported trial balance rows must keep their comparative figures.

DJLH Properties FY2025 issued financial statements showing 2024 expenses of
20,474 where HandiLedger showed 119,880, and 2024 total assets of 1,006,640
where HandiLedger showed 4,338,604. The 2024 figures were not missing from
StatementHub — every one of them sat on the FY2025 trial balance rows,
matching HandiLedger to the cent.

_get_tb_sections read them only from rollover rows:

    if getattr(line, "source", "") == "rollover":
        py = line.prior_debit - line.prior_credit
    else:
        py = Decimal("0")

Rows written by a trial balance import carry source="tb_import", so their
comparatives were forced to zero however well populated they were. The only
accounts that kept a 2024 figure were those with no 2025 activity, which
existed solely as rollover rows from the year-end roll-forward.

The check was guarding something real. Account 1670 carries two rows — a
tb_import row holding 2025 and a rollover row holding 2024 — and the section
aggregation sums py_amount across rows for an account. Reading the prior
figure from both would report 37,591 where the truth is 18,795. So the prior
figure has to be taken once per account: from the rollover row where one
exists, and from whichever row carries it otherwise.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    Client as ClientModel,
    Entity,
    FinancialYear,
    TrialBalanceLine,
)


class ComparativesSurviveImportTests(TestCase):
    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Comparatives Client")
        self.entity = Entity.objects.create(
            entity_name="Comparing Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)

    def _line(self, code, name, closing, prior_dr="0", prior_cr="0", source="tb_import"):
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            debit=Decimal("0"), credit=Decimal("0"),
            closing_balance=Decimal(closing),
            prior_debit=Decimal(prior_dr), prior_credit=Decimal(prior_cr),
            source=source)

    def _prior_for(self, code):
        """Prior-year total the statements would report for an account.

        Mirrors the downstream aggregation, which sums py_amount across every
        row carrying the same account code.
        """
        from core.fs_template_service import _get_tb_sections
        sections = _get_tb_sections(self.fy)
        total = Decimal("0")
        for entries in sections.values():
            for e in entries:
                if e["account_code"] == code:
                    total += e["py_amount"]
        return total

    def test_an_imported_row_keeps_its_comparative(self):
        """DJLH account 1510: 5,942.56 was on the row and printed as nothing."""
        self._line("1510", "Consultants - Accounting", "3293.65",
                   prior_dr="5942.56", source="tb_import")

        self.assertEqual(self._prior_for("1510"), Decimal("5942.56"))

    def test_a_rollover_row_still_supplies_its_comparative(self):
        self._line("1515", "Advertising & promotion", "0.00",
                   prior_dr="382.00", source="rollover")

        self.assertEqual(self._prior_for("1515"), Decimal("382.00"))

    def test_a_credit_comparative_keeps_its_sign(self):
        self._line("3625", "Bank loans", "-676911.78",
                   prior_cr="689568.55", source="tb_import")

        self.assertEqual(self._prior_for("3625"), Decimal("-689568.55"))

    def test_the_comparative_is_counted_once_when_both_rows_exist(self):
        """DJLH account 1670 — the case the rollover check was protecting."""
        self._line("1670", "Contractor, sub-contractor & commission", "11954.54",
                   prior_dr="0", source="tb_import")
        self._line("1670", "Contractor, sub-contractor & commission", "0.00",
                   prior_dr="18795.45", source="rollover")

        self.assertEqual(self._prior_for("1670"), Decimal("18795.45"))

    def test_the_rollover_row_wins_when_both_carry_a_comparative(self):
        """A rollover row is the year-end position; an import row may restate."""
        self._line("1760", "Interest Expense", "49996.23",
                   prior_dr="80000.00", source="tb_import")
        self._line("1760", "Interest Expense", "0.00",
                   prior_dr="84546.75", source="rollover")

        self.assertEqual(self._prior_for("1760"), Decimal("84546.75"))

    def test_two_imported_rows_do_not_double_the_comparative(self):
        """An adjustment alongside an import must not restate the prior year."""
        self._line("1850", "Rates and Permits", "8555.10",
                   prior_dr="5502.75", source="tb_import")
        self._line("1850", "Rates and Permits", "100.00",
                   prior_dr="5502.75", source="manual_journal")

        self.assertEqual(self._prior_for("1850"), Decimal("5502.75"))

    def test_an_account_with_no_comparative_reports_nil(self):
        self._line("1612", "Development cost", "95802.00", source="tb_import")

        self.assertEqual(self._prior_for("1612"), Decimal("0"))

    def test_the_djlh_expenses_total_matches_handiledger(self):
        """Regression: the eight accounts whose 2024 figures were discarded."""
        rows = [
            ("1510", "Consultants - Accounting", "5942.56"),
            ("1545", "Bank Fees", "395.00"),
            ("1680", "Fees, Licences & Registrations", "762.00"),
            ("1760", "Interest Expense", "84546.75"),
            ("1798", "Management Fee", "1300.64"),
            ("1850", "Rates and Permits", "5502.75"),
            ("1925", "Subscriptions", "430.28"),
            ("1966", "Water", "525.67"),
        ]
        for code, name, prior in rows:
            self._line(code, name, "1.00", prior_dr=prior, source="tb_import")

        total = sum(self._prior_for(code) for code, _n, _p in rows)

        self.assertEqual(total, Decimal("99405.65"))
