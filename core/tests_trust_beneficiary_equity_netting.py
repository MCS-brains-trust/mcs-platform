"""Trust equity must hold only cumulative P&L — never beneficiary loan movement.

Modelled on Dr Services Family Trust FY2026, whose issued statements showed an
equity block of four rows:

    Opening balance - Beneficiary — Ronen Davidov     22,837
    Funds loaned to trust — Ronen Davidov               (836)
    Undistributed income                            (117,951)
    Current year profit / (loss)                      89,900
    Total Equity                                      (6,051)

against a "Beneficiary loan: Ronen Davidov" current liability of 113,949.

Three defects produced that:

1. ``_get_tb_sections`` classifies purely by numeric account-code range, so
   every 4000-4999 code lands in equity regardless of the section recorded on
   ``EntityChartOfAccount``. 4110.NN ("Funds loaned to trust", section
   "liabilities" per beneficiary_account_service.BENEFICIARY_PARENT_CODES) is
   dragged into equity by that range check.
2. ``_net_beneficiary_accounts`` only nets 4004./9003./4053. back out, so
   4000.NN ("Opening balance - Beneficiary") is stranded in equity and 4110.NN
   is never netted at all.
3. The 4199 appropriation row and the injected "Current year profit / (loss)"
   row are rendered separately and labelled "Undistributed income", when a
   fully-distributed year leaves nothing undistributed.

The reference presentation is the firm's HandiLedger output: equity is a single
"Accumulated losses" line, and every beneficiary account — opening balance,
funds loaned, physical distributions — nets into one "Beneficiary loan: <name>"
line classified by sign (credit → current liabilities, debit → current assets).
"""
from datetime import date
from decimal import Decimal

from django.test import override_settings

from core.fs_template_service import build_trust_context
from core.models import EntityOfficer, FinancialYear, TrialBalanceLine
from core.tests_beneficiary_accounts import BeneficiaryAccountTestBase

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Dr Services Family Trust FY2026 trial balance (debit-positive, credit-negative).
# Sums to zero. Net profit = 138,676.98 - 48,777.23 = 89,899.75, fully
# distributed: DR 4199 / CR 4004.01.
TRUST_TB_ROWS = [
    ("0630",    "Sales",                           Decimal("-138676.98")),
    ("1510",    "Accountancy",                     Decimal("48777.23")),
    ("2000",    "Cash at bank",                    Decimal("134421.17")),
    ("2101",    "Trade debtors",                   Decimal("3148.00")),
    ("2890",    "Motor vehicles (cost)",           Decimal("38354.00")),
    ("2895",    "Less: Accumulated depreciation",  Decimal("-19160.75")),
    ("3380",    "GST payable control account",     Decimal("-12767.02")),
    ("3523",    "Hire purchase",                   Decimal("-20280.50")),
    ("3524",    "Less: Unexpired interest charges", Decimal("6565.19")),
    ("3565",    "Loan - Jewish Care",              Decimal("-22382.24")),
    ("4000.01", "Opening balance - Beneficiary",   Decimal("-22837.30")),
    ("4004.01", "Funds loaned to trust",           Decimal("-113948.77")),
    ("4110.01", "Funds loaned to trust",           Decimal("836.48")),
    ("4199",    "Undistributed income",            Decimal("117951.49")),
]

BENEFICIARY_NAME = "Ronen Davidov"


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TrustBeneficiaryEquityNettingTests(BeneficiaryAccountTestBase):
    """``self.trust`` (entity_type="trust") comes from BeneficiaryAccountTestBase."""

    def setUp(self):
        # Creating the officer auto-provisions the 4000.01 / 4004.01 / 4053.01 /
        # 4110.01 child accounts, each carrying the section recorded on
        # BENEFICIARY_PARENT_CODES — which is exactly what defect 1 ignores.
        self.officer = EntityOfficer.objects.create(
            entity=self.trust,
            full_name=BENEFICIARY_NAME,
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
            display_order=1,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.trust,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        for code, name, cy in TRUST_TB_ROWS:
            TrialBalanceLine.objects.create(
                financial_year=self.fy,
                account_code=code,
                account_name=name,
                closing_balance=cy,
                debit=cy if cy > 0 else Decimal("0"),
                credit=-cy if cy < 0 else Decimal("0"),
                source="tb_import",
            )
        self.ctx = build_trust_context(self.fy, include_watermark=False)
        self.sections = self.ctx["_sections"]

    @staticmethod
    def _names(rows):
        return [r.get("account_name", "") for r in rows]

    def test_all_four_beneficiary_codes_net_into_one_liability_line(self):
        """4000.01 + 4004.01 + 4110.01 = 135,949.59 credit, one current liability.

        Fails before the fix: 4004.01 alone nets to 113,948.77 because 4000.01
        is left in equity and 4110.01 never reaches the netting function.
        """
        rows = [
            r for r in self.sections["current_liabilities"]
            if r["account_name"] == f"Beneficiary loan: {BENEFICIARY_NAME}"
        ]
        self.assertEqual(len(rows), 1, self._names(self.sections["current_liabilities"]))
        self.assertEqual(rows[0]["cy_amount"], Decimal("-135949.59"))

    def test_equity_holds_no_beneficiary_rows(self):
        """No "Opening balance", "Funds loaned" or beneficiary name in equity.

        Fails before the fix: equity carries "Opening balance - Beneficiary —
        Ronen Davidov" (22,837) and "Funds loaned to trust — Ronen Davidov" (836).
        """
        for name in self._names(self.sections["equity"]):
            lowered = name.lower()
            self.assertNotIn("opening balance", lowered)
            self.assertNotIn("funds loaned", lowered)
            self.assertNotIn(BENEFICIARY_NAME.lower(), lowered)

    def test_equity_is_a_single_accumulated_losses_line(self):
        """A fully-distributed year leaves one row: Accumulated losses (28,051.74).

        Fails before the fix: equity has four rows, two of them labelled
        "Undistributed income" (117,951) and "Current year profit / (loss)".
        """
        rows = self.sections["equity"]
        self.assertEqual(len(rows), 1, self._names(rows))
        self.assertEqual(rows[0]["account_name"], "Accumulated losses")
        self.assertEqual(rows[0]["cy_amount"], Decimal("28051.74"))

    def test_undistributed_income_label_is_gone(self):
        """Nothing distributed remains labelled "Undistributed income"."""
        self.assertNotIn(
            "undistributed income",
            " ".join(self._names(self.sections["equity"])).lower(),
        )

    def test_balance_sheet_still_balances_at_negative_28051(self):
        """Net assets and total equity both (28,051.74), not (6,050.92)."""
        assets = sum(r["cy_amount"] for r in self.sections["current_assets"]) + sum(
            r["cy_amount"] for r in self.sections["noncurrent_assets"])
        liabs = -(sum(r["cy_amount"] for r in self.sections["current_liabilities"])
                  + sum(r["cy_amount"] for r in self.sections["noncurrent_liabilities"]))
        equity = -sum(r["cy_amount"] for r in self.sections["equity"])
        self.assertEqual(assets - liabs, Decimal("-28051.74"))
        self.assertEqual(equity, Decimal("-28051.74"))

    def test_chart_section_decides_class_and_code_range_decides_currency(self):
        """4110.01 is a liability by chart section despite its 4xxx code.

        The motor vehicle rows confirm the code range still splits current from
        non-current within a class that the chart records only as "assets".
        """
        self.assertIn("Motor vehicles (cost)", self._names(self.sections["noncurrent_assets"]))
        self.assertIn("Cash at bank", self._names(self.sections["current_assets"]))
        self.assertIn("Loan - Jewish Care", self._names(self.sections["noncurrent_liabilities"]))
        self.assertIn("GST payable control account", self._names(self.sections["current_liabilities"]))
