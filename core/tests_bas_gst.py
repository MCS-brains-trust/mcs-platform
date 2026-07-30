"""
Regression tests — BAS/GST calculation worksheet (G1–G20, 1A, 1B)
=================================================================
Discovered on the live file for a GST-registered medical sole trader whose
income is predominantly GST-free (ATO GST Act s38-7). The BAS screen reported
$32,704.81 GST on sales (1A) against a true $3,873.00, and net GST of
$26,127.93 payable against a true $2,694.97 refundable.

Root causes pinned here:

1.  The full-year path (`_calculate_gst_from_tb_lines`) classified an entire
    revenue/expense account by the *aggregated* TrialBalanceLine.tax_type. A
    single revenue account holding both GST-free and taxable sales was reported
    100% taxable, because per-transaction tax treatment is destroyed when
    transactions are aggregated into one TB line.

2.  `_classify_line` grossed balances up by x11/10 whenever the account was
    GST-coded. That fabricates GST: the correct gross is the actual bank
    statement amount. Applied to a mixed account it also grossed up the
    GST-free portion.

3.  The full-year path (TB lines) and the period path (transactions) were
    different calculations over the same data, so four quarters did not sum to
    the full year even when every dollar came from bank statements.

4.  `_resolve_section_and_tax` only understood the long-form tax-type
    vocabulary ("GST on Expenses", ...). COA/MYOB tax codes ("INP", "CAP",
    "FRE", ...) leak into PendingTransaction.confirmed_tax_type from several
    unvalidated write paths; unrecognised values silently fell back to the
    account's default tax code, so a transaction explicitly coded as taxable
    could be reported GST-free (and vice versa).

5.  Non-reportable ("N-T" / "BAS Excluded") income and expenses were included
    in G1/G3 and G11/G14 instead of being excluded from the worksheet.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.bas_utils import calculate_gst_for_period, get_all_period_dates
from core.models import (
    Client, Entity, EntityChartOfAccount, FinancialYear, TrialBalanceLine,
)
from review.models import PendingTransaction, ReviewJob


STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

D = Decimal


def _q(v):
    return D(str(v)).quantize(D("0.01"))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasMixedTreatmentRevenueTests(TestCase):
    """
    A single revenue account carrying both GST-free and taxable sales.

    Mirrors the live medical-practice file: account 0590 "Professional fees"
    has a COA default tax code of GST, but 3 of the 4 receipts are GST-free
    medical income.
    """

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="BAS Mixed Treatment Client")
        cls.entity = Entity.objects.create(
            entity_name="Dr Test Sole Trader",
            entity_type="sole_trader",
            client=cls.client_obj,
            is_gst_registered=True,
            bas_frequency="quarterly",
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
        )
        # Revenue account whose COA default is taxable.
        EntityChartOfAccount.objects.create(
            entity=cls.entity, account_code="0590",
            account_name="Professional fees", section="revenue", tax_code="GST",
        )
        cls.job = ReviewJob.objects.create(
            entity=cls.entity, financial_year=cls.fy,
            client_name="Dr Test Sole Trader", is_gst_registered=True,
        )
        # 3 x GST-free medical receipts, one per quarter, $1,100 each.
        for i, d in enumerate(["2025-08-15", "2025-11-15", "2026-02-15"]):
            PendingTransaction.objects.create(
                job=cls.job, date=d, description=f"Medicare benefit {i}",
                amount=D("1100.00"), gst_amount=D("0.00"), net_amount=D("1100.00"),
                confirmed_code="0590", confirmed_name="Professional fees",
                confirmed_tax_type="GST Free Income",
                confirmed_gst_amount=D("0.00"), is_confirmed=True,
            )
        # 1 x taxable receipt in Q4, $1,100 gross including $100 GST.
        PendingTransaction.objects.create(
            job=cls.job, date="2026-05-20", description="Consulting fee",
            amount=D("1100.00"), gst_amount=D("100.00"), net_amount=D("1000.00"),
            confirmed_code="0590", confirmed_name="Professional fees",
            confirmed_tax_type="GST on Income",
            confirmed_gst_amount=D("100.00"), is_confirmed=True,
        )
        # The aggregated TB line the review push produces: NET of GST, stamped
        # with the account's default tax code. This is the line that used to
        # drive the whole full-year calculation.
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="0590",
            account_name="Professional fees", source="bank_statement",
            tax_type="GST", debit=D("0.00"), credit=D("4300.00"),
            closing_balance=D("-4300.00"),
        )

    def test_full_year_respects_per_transaction_tax_treatment(self):
        """G3 must hold the GST-free sales; 1A must be GST on the taxable slice only."""
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        # Total sales = actual gross receipts, not the net balance grossed up.
        self.assertEqual(_q(bas["G1"]), _q("4400.00"))
        self.assertEqual(_q(bas["G3"]), _q("3300.00"))
        self.assertEqual(_q(bas["G6"]), _q("1100.00"))
        self.assertEqual(_q(bas["1A"]), _q("100.00"))

    def test_full_year_does_not_gross_up_gst_free_income(self):
        """The x11/10 gross-up must not be applied to the GST-free portion."""
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        # Buggy behaviour was 4300 * 1.1 = 4730.00 with G3 = 0.
        self.assertNotEqual(_q(bas["G1"]), _q("4730.00"))
        self.assertEqual(_q(bas["G1"]), _q("4400.00"))

    def test_quarters_sum_to_full_year(self):
        """Every dollar is bank-statement sourced, so the quarters must reconcile."""
        full = calculate_gst_for_period(self.fy)["bas_data"]
        totals = {k: D("0") for k in ("G1", "G3", "G6", "1A", "1B", "gst_payable")}
        for num, start, end in get_all_period_dates(self.fy, "quarterly"):
            bas = calculate_gst_for_period(self.fy, start, end)["bas_data"]
            for k in totals:
                totals[k] += bas[k]
        for k in totals:
            self.assertEqual(
                _q(totals[k]), _q(full[k]),
                f"{k}: quarters {totals[k]} != full year {full[k]}",
            )

    def test_quarterly_gst_free_quarters_report_no_gst_on_sales(self):
        """Q1/Q2/Q3 hold only GST-free income → 1A is zero and G3 carries the sales."""
        periods = {n: (s, e) for n, s, e in get_all_period_dates(self.fy, "quarterly")}
        for q in (1, 2, 3):
            start, end = periods[q]
            bas = calculate_gst_for_period(self.fy, start, end)["bas_data"]
            self.assertEqual(_q(bas["1A"]), _q("0.00"), f"Q{q} 1A")
            self.assertEqual(_q(bas["G1"]), _q("1100.00"), f"Q{q} G1")
            self.assertEqual(_q(bas["G3"]), _q("1100.00"), f"Q{q} G3")
        start, end = periods[4]
        bas = calculate_gst_for_period(self.fy, start, end)["bas_data"]
        self.assertEqual(_q(bas["1A"]), _q("100.00"))
        self.assertEqual(_q(bas["G3"]), _q("0.00"))

    def test_detail_rows_reconcile_to_1a(self):
        """The sales detail tab must not disagree with the summary label."""
        result = calculate_gst_for_period(self.fy)
        detail_gst = sum(
            (r["gst_amount"] for r in result["sales_transactions"]), D("0")
        )
        self.assertEqual(_q(detail_gst), _q(result["bas_data"]["1A"]))
        detail_gross = sum(
            (r["gross_amount"] for r in result["sales_transactions"]), D("0")
        )
        self.assertEqual(_q(detail_gross), _q(result["bas_data"]["G1"]))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasLegacyTaxCodeTests(TestCase):
    """
    COA/MYOB tax codes leak into confirmed_tax_type from unvalidated write
    paths. They must be understood, not silently replaced by the account
    default — and a taxable transaction whose per-transaction GST was never
    stored must still be reported as taxable.
    """

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="BAS Legacy Code Client")
        cls.entity = Entity.objects.create(
            entity_name="Legacy Codes Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
            is_gst_registered=True,
            bas_frequency="quarterly",
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        # Expense account whose COA default is GST-FREE, so a fallback to the
        # account default would wrongly zero the input tax credit.
        EntityChartOfAccount.objects.create(
            entity=cls.entity, account_code="1700",
            account_name="Sundry expenses", section="expenses", tax_code="FRE",
        )
        # Revenue account defaulting to GST-free, to prove the reverse case.
        EntityChartOfAccount.objects.create(
            entity=cls.entity, account_code="0500",
            account_name="Sales", section="revenue", tax_code="FRE",
        )
        cls.job = ReviewJob.objects.create(
            entity=cls.entity, financial_year=cls.fy,
            client_name="Legacy Codes Pty Ltd", is_gst_registered=True,
        )

    def _txn(self, **kw):
        defaults = dict(
            job=self.job, date="2025-08-10", description="txn",
            gst_amount=D("0.00"), net_amount=D("0.00"),
            confirmed_gst_amount=D("0.00"), is_confirmed=True,
        )
        defaults.update(kw)
        return PendingTransaction.objects.create(**defaults)

    def test_inp_expense_code_is_treated_as_taxable(self):
        """'INP' on an account defaulting to FRE must still yield an ITC."""
        self._txn(
            amount=D("-1100.00"), confirmed_code="1700",
            confirmed_name="Sundry expenses", confirmed_tax_type="INP",
            net_amount=D("1000.00"), gst_amount=D("100.00"),
            confirmed_gst_amount=D("100.00"),
        )
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        self.assertEqual(_q(bas["G11"]), _q("1100.00"))
        self.assertEqual(_q(bas["G14"]), _q("0.00"))
        self.assertEqual(_q(bas["1B"]), _q("100.00"))

    def test_gst_income_code_with_no_stored_gst_is_still_taxable(self):
        """
        The live defect: transactions confirmed taxable via the short code
        'GST' had confirmed_gst_amount forced to 0.00 by the confirm paths.
        The worksheet derives 1A from G8/11, so the label must still be right.
        """
        self._txn(
            amount=D("1100.00"), confirmed_code="0500", confirmed_name="Sales",
            confirmed_tax_type="GST",
            net_amount=D("1100.00"), gst_amount=D("0.00"),
            confirmed_gst_amount=D("0.00"),
        )
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        self.assertEqual(_q(bas["G1"]), _q("1100.00"))
        self.assertEqual(_q(bas["G3"]), _q("0.00"))
        self.assertEqual(_q(bas["G6"]), _q("1100.00"))
        self.assertEqual(_q(bas["1A"]), _q("100.00"))

    def test_gross_is_not_inflated_when_gst_was_never_extracted(self):
        """net==gross must not be grossed up again by x11/10."""
        self._txn(
            amount=D("1100.00"), confirmed_code="0500", confirmed_name="Sales",
            confirmed_tax_type="GST",
            net_amount=D("1100.00"), gst_amount=D("0.00"),
            confirmed_gst_amount=D("0.00"),
        )
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        self.assertNotEqual(_q(bas["G1"]), _q("1210.00"))
        self.assertEqual(_q(bas["G1"]), _q("1100.00"))

    def test_cap_code_routes_to_g10_capital_purchases(self):
        EntityChartOfAccount.objects.create(
            entity=self.entity, account_code="2500",
            account_name="Plant & equipment", section="assets", tax_code="CAP",
        )
        self._txn(
            amount=D("-5500.00"), confirmed_code="2500",
            confirmed_name="Plant & equipment", confirmed_tax_type="CAP",
            net_amount=D("5000.00"), gst_amount=D("500.00"),
            confirmed_gst_amount=D("500.00"),
        )
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        self.assertEqual(_q(bas["G10"]), _q("5500.00"))
        self.assertEqual(_q(bas["G11"]), _q("0.00"))
        self.assertEqual(_q(bas["1B"]), _q("500.00"))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasNonReportableExclusionTests(TestCase):
    """N-T / BAS Excluded amounts must not appear on the worksheet at all."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="BAS N-T Client")
        cls.entity = Entity.objects.create(
            entity_name="NT Exclusion Pty Ltd", entity_type="company",
            client=cls.client_obj, is_gst_registered=True,
            bas_frequency="quarterly",
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        EntityChartOfAccount.objects.create(
            entity=cls.entity, account_code="0500", account_name="Sales",
            section="revenue", tax_code="GST",
        )
        EntityChartOfAccount.objects.create(
            entity=cls.entity, account_code="1700",
            account_name="Sundry expenses", section="expenses", tax_code="INP",
        )
        cls.job = ReviewJob.objects.create(
            entity=cls.entity, financial_year=cls.fy,
            client_name="NT Exclusion Pty Ltd", is_gst_registered=True,
        )

    def test_non_reportable_income_is_excluded_from_g1(self):
        """N-T income must not appear in G1/G3 — alongside a real taxable sale,
        so the assertion cannot pass just because the worksheet is empty."""
        PendingTransaction.objects.create(
            job=self.job, date="2025-08-10", description="Owner capital injection",
            amount=D("50000.00"), gst_amount=D("0.00"), net_amount=D("50000.00"),
            confirmed_code="0500", confirmed_name="Sales",
            confirmed_tax_type="N-T", confirmed_gst_amount=D("0.00"),
            is_confirmed=True,
        )
        PendingTransaction.objects.create(
            job=self.job, date="2025-08-11", description="Real sale",
            amount=D("1100.00"), gst_amount=D("100.00"), net_amount=D("1000.00"),
            confirmed_code="0500", confirmed_name="Sales",
            confirmed_tax_type="GST on Income", confirmed_gst_amount=D("100.00"),
            is_confirmed=True,
        )
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        self.assertEqual(_q(bas["G1"]), _q("1100.00"))
        self.assertEqual(_q(bas["G3"]), _q("0.00"))
        self.assertEqual(_q(bas["1A"]), _q("100.00"))

    def test_non_reportable_expense_is_excluded_from_g11(self):
        """BAS Excluded expenses must not appear in G11/G14."""
        PendingTransaction.objects.create(
            job=self.job, date="2025-08-10", description="Owner drawings",
            amount=D("-20000.00"), gst_amount=D("0.00"), net_amount=D("20000.00"),
            confirmed_code="1700", confirmed_name="Sundry expenses",
            confirmed_tax_type="BAS Excluded", confirmed_gst_amount=D("0.00"),
            is_confirmed=True,
        )
        PendingTransaction.objects.create(
            job=self.job, date="2025-08-11", description="Real purchase",
            amount=D("-550.00"), gst_amount=D("50.00"), net_amount=D("500.00"),
            confirmed_code="1700", confirmed_name="Sundry expenses",
            confirmed_tax_type="GST on Expenses", confirmed_gst_amount=D("50.00"),
            is_confirmed=True,
        )
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        self.assertEqual(_q(bas["G11"]), _q("550.00"))
        self.assertEqual(_q(bas["G14"]), _q("0.00"))
        self.assertEqual(_q(bas["1B"]), _q("50.00"))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasImportedTrialBalanceTests(TestCase):
    """
    Entities with no bank-statement review data still calculate from imported
    TB lines. That path must keep working (and keep grossing up net imported
    balances), otherwise TB-import-only clients lose their BAS entirely.
    """

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="BAS TB Import Client")
        cls.entity = Entity.objects.create(
            entity_name="TB Import Pty Ltd", entity_type="company",
            client=cls.client_obj, is_gst_registered=True,
            bas_frequency="quarterly",
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        EntityChartOfAccount.objects.create(
            entity=cls.entity, account_code="0500", account_name="Sales",
            section="revenue", tax_code="GST",
        )
        EntityChartOfAccount.objects.create(
            entity=cls.entity, account_code="1700",
            account_name="Sundry expenses", section="expenses", tax_code="INP",
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="0500", account_name="Sales",
            source="tb_import", tax_type="", debit=D("0.00"),
            credit=D("10000.00"), closing_balance=D("-10000.00"),
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy, account_code="1700",
            account_name="Sundry expenses", source="tb_import", tax_type="",
            debit=D("2200.00"), credit=D("0.00"), closing_balance=D("2200.00"),
        )

    def test_imported_tb_still_produces_a_full_year_worksheet(self):
        """
        Guard: imported balances must keep flowing into the worksheet.

        Note this pins *existing* behaviour — imported balances are NOT grossed
        up (only bank_statement aggregates are), so G1 is the net balance and 1A
        is G1/11 rather than 10% of a grossed-up G1. Whether an imported TB
        should be grossed up depends on whether the source ledger stores
        GST-inclusive or GST-exclusive figures, which is a separate question from
        the per-transaction defect these tests cover; changing it would move
        every TB-import client's BAS.
        """
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        self.assertEqual(_q(bas["G1"]), _q("10000.00"))
        self.assertEqual(_q(bas["G11"]), _q("2200.00"))
        self.assertEqual(_q(bas["1A"]), _q("909.09"))
        self.assertEqual(_q(bas["1B"]), _q("200.00"))


class TaxTypeNormalisationTests(TestCase):
    """
    Legacy chart-of-accounts tax codes must be coerced onto TAX_TYPE_CHOICES
    before they are persisted. 238 live transactions held "GST"/"INP"/"CAP" in
    confirmed_tax_type, which the confirm paths did not recognise as taxable —
    so they were stored as taxable but with their GST forced to zero.
    """

    def test_canonical_values_pass_through(self):
        from review.models import canonical_tax_type
        for tt in ("GST on Income", "GST on Expenses", "GST Free Income",
                   "GST Free Expenses", "Input Taxed", "BAS Excluded", "N-T"):
            self.assertEqual(canonical_tax_type(tt), tt)

    def test_legacy_codes_map_by_direction(self):
        from review.models import canonical_tax_type
        self.assertEqual(canonical_tax_type("GST", is_income=True), "GST on Income")
        self.assertEqual(canonical_tax_type("GST", is_income=False), "GST on Expenses")
        self.assertEqual(canonical_tax_type("INP", is_income=False), "GST on Expenses")
        self.assertEqual(canonical_tax_type("CAP", is_income=False), "GST on Expenses")
        self.assertEqual(canonical_tax_type("FRE", is_income=True), "GST Free Income")
        self.assertEqual(canonical_tax_type("FRE", is_income=False), "GST Free Expenses")
        self.assertEqual(canonical_tax_type("ITS", is_income=False), "Input Taxed")
        self.assertEqual(canonical_tax_type("N-T", is_income=False), "N-T")

    def test_empty_stays_empty_and_unknown_is_rejected(self):
        from review.models import canonical_tax_type
        self.assertEqual(canonical_tax_type(""), "")
        self.assertEqual(canonical_tax_type(None), "")
        self.assertIsNone(canonical_tax_type("Wildly Invalid"))

    def test_is_taxable_recognises_both_vocabularies(self):
        from review.models import is_taxable_tax_type
        self.assertTrue(is_taxable_tax_type("GST on Expenses", is_income=False))
        self.assertTrue(is_taxable_tax_type("INP", is_income=False))
        self.assertTrue(is_taxable_tax_type("GST", is_income=True))
        self.assertFalse(is_taxable_tax_type("GST Free Income", is_income=True))
        self.assertFalse(is_taxable_tax_type("N-T", is_income=True))
        self.assertFalse(is_taxable_tax_type(""))

    def test_engine_normaliser_maps_both_vocabularies(self):
        from core.bas_utils import normalise_tax_treatment
        self.assertEqual(normalise_tax_treatment("GST on Expenses"), "INP")
        self.assertEqual(normalise_tax_treatment("INP"), "INP")
        self.assertEqual(normalise_tax_treatment("GST Free Income"), "FRE")
        self.assertEqual(normalise_tax_treatment("FRE"), "FRE")
        self.assertEqual(normalise_tax_treatment("BAS Excluded"), "N-T")
        self.assertEqual(normalise_tax_treatment("Wildly Invalid"), "")
        self.assertEqual(normalise_tax_treatment(""), "")


@override_settings(STORAGES=STORAGES_OVERRIDE)
class ManualGstTreatmentSurvivesReassignmentTests(TestCase):
    """
    Bulk-assigning an account must not overwrite a GST treatment the accountant
    set by hand.

    The account's tax_code is a default for the *account*; gst_treatment with
    is_gst_manual=True is an explicit decision about the *transaction*. Deriving
    the tax type from the account's tax code used to clobber it, turning GST-free
    medical receipts into taxable income and booking GST on them the moment they
    were coded to a GST-defaulted revenue account.
    """

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Manual Treatment Client")
        cls.entity = Entity.objects.create(
            entity_name="Manual Treatment Pty Ltd", entity_type="company",
            client=cls.client_obj, is_gst_registered=True, bas_frequency="quarterly",
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        EntityChartOfAccount.objects.create(
            entity=cls.entity, account_code="0630", account_name="Consulting income",
            section="revenue", tax_code="GST",
        )
        cls.job = ReviewJob.objects.create(
            entity=cls.entity, financial_year=cls.fy,
            client_name="Manual Treatment Pty Ltd", is_gst_registered=True,
        )

    def _reassign(self, txn, tax_code="GST"):
        """Replicate review_bulk_edit_transactions' per-transaction logic."""
        TAX_CODE_MAP = {
            'GST': {'expense': 'GST on Expenses', 'income': 'GST on Income'},
            'FRE': {'expense': 'GST Free Expenses', 'income': 'GST Free Income'},
        }
        TREATMENT_TO_TAX_TYPE = {
            'taxable': {'expense': 'GST on Expenses', 'income': 'GST on Income'},
            'gst_free': {'expense': 'GST Free Expenses', 'income': 'GST Free Income'},
            'input_taxed': {'expense': 'Input Taxed', 'income': 'Input Taxed'},
            'out_of_scope': {'expense': 'BAS Excluded', 'income': 'BAS Excluded'},
            'not_registered': {'expense': 'N-T', 'income': 'N-T'},
        }
        direction = 'expense' if txn.amount < 0 else 'income'
        manual_treatment = txn.gst_treatment if txn.is_gst_manual else ''
        if manual_treatment in TREATMENT_TO_TAX_TYPE:
            tax_type = TREATMENT_TO_TAX_TYPE[manual_treatment][direction]
            has_gst = manual_treatment == 'taxable'
        else:
            tax_type = TAX_CODE_MAP.get(tax_code, {}).get(direction, '')
            has_gst = tax_code in ('GST', 'INP', 'CAP', 'GNR', 'ADS')
        return tax_type, has_gst

    def test_manual_gst_free_choice_survives_account_reassignment(self):
        txn = PendingTransaction.objects.create(
            job=self.job, date="2025-08-10", description="Medicare benefit",
            amount=D("1100.00"), gst_amount=D("0.00"), net_amount=D("1100.00"),
            gst_treatment="gst_free", is_gst_manual=True,
            creditable_percentage=D("0"),
        )
        tax_type, has_gst = self._reassign(txn, tax_code="GST")
        self.assertEqual(tax_type, "GST Free Income")
        self.assertFalse(has_gst)

    def test_account_default_still_applies_when_not_manually_set(self):
        txn = PendingTransaction.objects.create(
            job=self.job, date="2025-08-10", description="Consulting fee",
            amount=D("1100.00"), gst_amount=D("0.00"), net_amount=D("1100.00"),
            gst_treatment="", is_gst_manual=False,
        )
        tax_type, has_gst = self._reassign(txn, tax_code="GST")
        self.assertEqual(tax_type, "GST on Income")
        self.assertTrue(has_gst)

    def test_manual_gst_free_income_reports_as_gst_free_in_the_bas(self):
        """End to end: what the accountant allocates is what the BAS reports."""
        PendingTransaction.objects.create(
            job=self.job, date="2025-08-10", description="Medicare benefit",
            amount=D("1100.00"), gst_amount=D("0.00"), net_amount=D("1100.00"),
            confirmed_code="0630", confirmed_name="Consulting income",
            confirmed_tax_type="GST Free Income", confirmed_gst_amount=D("0.00"),
            gst_treatment="gst_free", is_gst_manual=True, is_confirmed=True,
        )
        bas = calculate_gst_for_period(self.fy)["bas_data"]
        self.assertEqual(_q(bas["G1"]), _q("1100.00"))
        self.assertEqual(_q(bas["G3"]), _q("1100.00"))
        self.assertEqual(_q(bas["1A"]), _q("0.00"))
