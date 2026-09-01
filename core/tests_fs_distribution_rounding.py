"""Displayed figures must add up on the page.

Amounts print to whole dollars, so the sum of the rounded components need not
equal the rounded sum. Dr Services Family Trust FY2026 printed components
totalling 107,899 against a closing balance of 107,898.

Three places on the document have this shape:

  * the per-beneficiary reconciliation column
  * "Total of beneficiary loans" / "Total Beneficiary Funds"
  * "Total Profit (Loss)" on page 1

The reconciliation absorbs its difference into "Funds loaned to trust", which
_resolve already derives from the other four figures -- it is the one line with
no independent value of its own. The two totals become the sum of the figures
actually printed above them rather than a separately-rounded exact sum.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.fs_template_service import _build_beneficiary_distribution_summary
from core.models import (
    Client, ClientAccountMapping, Entity, EntityOfficer, FinancialYear,
    TaxPlanningScenario, TrialBalanceLine, TrustWorkspace,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LABELS = {
    "Opening balance - Beneficiary", "Funds loaned to trust",
    "Profit distribution for year", "Physical distribution", "Closing balance",
    "Total of beneficiary loans", "Total Beneficiary Funds",
    "Undistributed income (loss)", "Total Profit (Loss)",
}


def _num(text):
    text = text.strip()
    if text in ("-", "—", ""):
        return 0
    neg = text.startswith("(") and text.endswith(")")
    value = int(text.strip("()").replace(",", ""))
    return -value if neg else value


def _pairs(fy):
    """[(label, displayed int), ...] in document order, single-column docs."""
    from pypdf import PdfReader

    buf = _build_beneficiary_distribution_summary({"_fy": fy})
    buf.seek(0)
    text = "\n".join(p.extract_text() or "" for p in PdfReader(buf).pages)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    out = []
    for i, line in enumerate(lines):
        if line in LABELS and i + 1 < len(lines):
            out.append((line, _num(lines[i + 1])))
    return out


def _mapping(entity, officer, code, name):
    ClientAccountMapping.objects.create(
        entity=entity, client_account_code=code,
        client_account_name=name, beneficiary_officer=officer,
    )


@override_settings(STORAGES=STORAGES_OVERRIDE)
class ReconciliationColumnFootsTests(TestCase):
    """Dr Services FY2026 figures: .73 + .02 - .43 components, .32 closing."""

    @classmethod
    def setUpTestData(cls):
        cls.entity = Entity.objects.create(
            entity_name="Rounding Trust", entity_type="trust",
            client=Client.objects.create(name="Rounding Client"),
        )
        cls.officer = EntityOfficer.objects.create(
            entity=cls.entity, full_name="Ronen Davidov",
            role="beneficiary", display_order=1,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        for code, name in (
            ("4000.01", "Opening balance - Beneficiary"),
            ("4004.01", "Funds loaned to trust"),
            ("4053.01", "Physical distribution"),
        ):
            _mapping(cls.entity, cls.officer, code, name)
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="4000.01",
            account_name="Opening balance - Beneficiary",
            closing_balance=Decimal("-32703.73"),
            prior_credit=Decimal("32703.73"), source="rollover",
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="4004.01",
            account_name="Funds loaned to trust",
            closing_balance=Decimal("-24049.02"),
            credit=Decimal("24049.02"), source="manual_journal",
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="4053.01",
            account_name="Physical distribution",
            closing_balance=Decimal("9866.43"),
            debit=Decimal("9866.43"), source="manual_journal",
        )

    def test_displayed_components_sum_to_the_displayed_closing_balance(self):
        """The defect: 32,704 + 24,049 - 9,866 = 46,887, printed against 46,886."""
        got = dict(_pairs(self.fy))
        movement = (
            got["Opening balance - Beneficiary"]
            + got["Funds loaned to trust"]
            + got["Profit distribution for year"]
            + got["Physical distribution"]
        )
        self.assertEqual(movement, got["Closing balance"])

    def test_the_closing_balance_itself_is_unchanged(self):
        """Only the derived plug absorbs the rounding, never the real figures."""
        got = dict(_pairs(self.fy))
        self.assertEqual(got["Closing balance"], 46886)
        self.assertEqual(got["Opening balance - Beneficiary"], 32704)
        self.assertEqual(got["Physical distribution"], -9866)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TotalsTieToTheBalanceSheetTests(TestCase):
    """Two closings of 100.40 each: 100 + 100 printed, exact sum 200.80 -> 201.

    This total also appears on the face of the balance sheet, where
    ``_net_beneficiary_accounts`` writes the same per-officer nets and
    ``_sum_section`` totals them from the exact Decimals. Summing the printed
    figures instead gave 200 here against 201 there -- Minli Enterprise Unit
    Trust FY2027 printed beneficiary loans as 2,039,009 on the distribution
    summary and 2,039,010 on the balance sheet, one dollar apart inside one
    client pack.

    Round-then-sum cannot fix that by being adopted everywhere: the balance
    sheet's own "Total Current Liabilities" is derived from exact values, so
    rounding the group subtotal first only moves the break one line down.
    Sum-then-round is the only basis on which the schedule, the group subtotal
    and every total above it agree.

    The cost is that a column of rounded closings need not visually add to the
    rounded total. That is inherent to whole-dollar presentation and is what
    the balance sheet has always done. The per-beneficiary reconciliation still
    foots exactly -- ``funds_loaned`` absorbs it, unchanged from the fix that
    introduced it.
    """

    @classmethod
    def setUpTestData(cls):
        cls.entity = Entity.objects.create(
            entity_name="Totals Trust", entity_type="trust",
            client=Client.objects.create(name="Totals Client"),
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        cls.officers = []
        for n, name in enumerate(["Alice Adams", "Bob Barker"], start=1):
            officer = EntityOfficer.objects.create(
                entity=cls.entity, full_name=name,
                role="beneficiary", display_order=n,
            )
            cls.officers.append(officer)
            code = f"4004.{n:02d}"
            _mapping(cls.entity, officer, code, "Funds loaned to trust")
            TrialBalanceLine.objects.create(
                financial_year=cls.fy, account_code=code,
                account_name="Funds loaned to trust",
                closing_balance=Decimal("-100.40"),
                credit=Decimal("100.40"), source="manual_journal",
            )

    def test_total_of_beneficiary_loans_is_the_rounded_exact_sum(self):
        pairs = _pairs(self.fy)
        closings = [v for label, v in pairs if label == "Closing balance"]
        totals = dict(pairs)

        self.assertEqual(closings, [100, 100])
        # 100.40 + 100.40 = 200.80 -> 201, which is what the balance sheet
        # prints for the same two officers. Summing the printed 100s gives 200.
        self.assertEqual(totals["Total of beneficiary loans"], 201)
        self.assertEqual(totals["Total Beneficiary Funds"], 201)

    def test_the_total_equals_what_the_balance_sheet_prints(self):
        """Tie the schedule to the face of the statements, not to the column."""
        from core.fs_template_service import (
            _get_tb_sections, _net_beneficiary_accounts, _sum_section,
        )

        # The same two calls build_company_context makes for a trust.
        sections = _get_tb_sections(self.fy)
        _net_beneficiary_accounts(self.fy, sections)
        bs_total = -_sum_section(sections["current_liabilities"])

        totals = dict(_pairs(self.fy))
        self.assertEqual(totals["Total of beneficiary loans"], round(bs_total))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TotalProfitMatchesTheSharesTests(TestCase):
    """Two shares of 100.40: 100 + 100 printed, exact sum 200.80 -> 201."""

    @classmethod
    def setUpTestData(cls):
        cls.entity = Entity.objects.create(
            entity_name="Profit Trust", entity_type="trust",
            client=Client.objects.create(name="Profit Client"),
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        officers = []
        for n, name in enumerate(["Alice Adams", "Bob Barker"], start=1):
            officer = EntityOfficer.objects.create(
                entity=cls.entity, full_name=name,
                role="beneficiary", display_order=n,
            )
            officers.append(officer)
            _mapping(cls.entity, officer, f"4004.{n:02d}", "Funds loaned to trust")
            TrialBalanceLine.objects.create(
                financial_year=cls.fy, account_code=f"4004.{n:02d}",
                account_name="Funds loaned to trust",
                closing_balance=Decimal("-100.40"),
                credit=Decimal("100.40"), source="manual_journal",
            )
        workspace = TrustWorkspace.objects.create(financial_year=cls.fy)
        scenario = TaxPlanningScenario.objects.create(
            financial_year=cls.fy, scenario_name="Test",
            distributions=[
                {"beneficiary_id": str(o.pk), "proposed_distribution": "100.40"}
                for o in officers
            ],
        )
        workspace.selected_tax_scenario = scenario
        workspace.save(update_fields=["selected_tax_scenario"])

    def test_total_profit_equals_the_printed_shares(self):
        from pypdf import PdfReader

        buf = _build_beneficiary_distribution_summary({"_fy": self.fy})
        buf.seek(0)
        page1 = PdfReader(buf).pages[0].extract_text()
        lines = [l.strip() for l in page1.split("\n") if l.strip()]

        shares = [
            _num(lines[i + 1]) for i, l in enumerate(lines) if l.startswith("- ")
        ]
        undistributed = _num(
            lines[lines.index("Undistributed income (loss)") + 1]
        )
        total = _num(lines[lines.index("Total Profit (Loss)") + 1])

        self.assertEqual(shares, [100, 100])
        self.assertEqual(total, sum(shares) + undistributed)
