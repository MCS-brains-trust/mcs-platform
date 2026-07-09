"""
Tests for core/tax_engine.py — trust tax-planning calculation engine.

Regression coverage for the FY2025 stale-bracket bug: migration 0035 seeded
FY2025 TaxReferenceData with the superseded pre-Stage-3 individual scale
(19%/32.5%/37%/45%; $45k/$120k/$180k). get_tax_rates() is DB-first, so those
rows silently overrode the correct Stage-3 fallback. Migration 0140 corrects
the seed; these tests pin both the DB path (get_tax_rates on a migrated DB)
and the arithmetic of the individual scale, Medicare levy shade-in, and the
LITO two-step taper.

All expected figures are hand-computed from the legislated 2024-25 scale:
    $0–$18,200         nil
    $18,201–$45,000    16%
    $45,001–$135,000   30%
    $135,001–$190,000  37%
    $190,001+          45%
"""
from decimal import Decimal

from django.test import TestCase

from core.tax_engine import (
    calc_individual_gross_tax,
    calc_lito,
    calc_medicare_levy,
    calculate_beneficiary_tax,
    get_tax_rates,
)

D = Decimal


class GetTaxRatesFY2025Tests(TestCase):
    """The DB seed for FY2025 must be Stage-3 law (regression for 0035 seed)."""

    def test_fy2025_brackets_are_stage3(self):
        rates = get_tax_rates("FY2025")
        self.assertEqual(rates["tax_free_threshold"], D("18200"))
        self.assertEqual(rates["bracket_1_rate"], D("0.16"))
        self.assertEqual(rates["bracket_1_upper"], D("45000"))
        self.assertEqual(rates["bracket_2_rate"], D("0.30"))
        self.assertEqual(rates["bracket_2_upper"], D("135000"))
        self.assertEqual(rates["bracket_3_rate"], D("0.37"))
        self.assertEqual(rates["bracket_3_upper"], D("190000"))
        self.assertEqual(rates["bracket_4_rate"], D("0.45"))

    def test_fy2025_medicare_threshold_is_2024_25(self):
        rates = get_tax_rates("FY2025")
        self.assertEqual(rates["medicare_low_income_threshold"], D("27222"))

    def test_unseeded_year_falls_back_to_stage3_defaults(self):
        """A label with no DB rows must fall back to the Stage-3 defaults."""
        rates = get_tax_rates("FY2099")
        self.assertEqual(rates["bracket_1_rate"], D("0.16"))
        self.assertEqual(rates["bracket_2_upper"], D("135000"))
        self.assertEqual(rates["bracket_3_upper"], D("190000"))


class IndividualGrossTaxTests(TestCase):
    """Progressive scale arithmetic at each bracket boundary (FY2025 rates)."""

    @classmethod
    def setUpTestData(cls):
        cls.rates = get_tax_rates("FY2025")

    def test_below_threshold_is_nil(self):
        self.assertEqual(calc_individual_gross_tax(D("18200"), self.rates), D("0"))

    def test_at_45000(self):
        # (45,000 - 18,200) x 16% = 4,288
        self.assertEqual(calc_individual_gross_tax(D("45000"), self.rates), D("4288.00"))

    def test_at_135000(self):
        # 4,288 + (135,000 - 45,000) x 30% = 31,288
        self.assertEqual(calc_individual_gross_tax(D("135000"), self.rates), D("31288.00"))

    def test_at_190000(self):
        # 31,288 + (190,000 - 135,000) x 37% = 51,638
        self.assertEqual(calc_individual_gross_tax(D("190000"), self.rates), D("51638.00"))

    def test_at_200000(self):
        # 51,638 + (200,000 - 190,000) x 45% = 56,138
        self.assertEqual(calc_individual_gross_tax(D("200000"), self.rates), D("56138.00"))

    def test_100000_not_old_scale(self):
        """$100k under Stage 3 = 20,788; under the old scale it was 22,967.
        Guards against the stale seed regressing."""
        self.assertEqual(calc_individual_gross_tax(D("100000"), self.rates), D("20788.00"))


class MedicareLevyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rates = get_tax_rates("FY2025")

    def test_nil_at_or_below_lower_threshold(self):
        self.assertEqual(calc_medicare_levy(D("27222"), self.rates), D("0"))

    def test_shade_in_band(self):
        # 10c per $1 above the lower threshold: (30,000 - 27,222) x 10% = 277.80
        self.assertEqual(calc_medicare_levy(D("30000"), self.rates), D("277.80"))

    def test_full_levy_above_upper_threshold(self):
        # Upper = 27,222 / 0.8 = 34,027.50; at $50,000 → 2% flat = 1,000
        self.assertEqual(calc_medicare_levy(D("50000"), self.rates), D("1000.00"))

    def test_shade_in_meets_full_levy_at_upper(self):
        upper = self.rates["medicare_low_income_threshold"] / D("0.8")
        shaded = (upper - self.rates["medicare_low_income_threshold"]) * D("0.10")
        self.assertEqual(shaded.quantize(D("0.01")), (upper * D("0.02")).quantize(D("0.01")))


class LitoTests(TestCase):
    """ATO two-step taper: $700 max; -5c/$ over $37,500; -1.5c/$ over $45,000."""

    @classmethod
    def setUpTestData(cls):
        cls.rates = get_tax_rates("FY2025")

    def test_full_offset_up_to_start(self):
        self.assertEqual(calc_lito(D("37500"), self.rates), D("700"))

    def test_first_taper(self):
        # 700 - (40,000 - 37,500) x 5% = 575
        self.assertEqual(calc_lito(D("40000"), self.rates), D("575.00"))

    def test_at_second_threshold(self):
        # 700 - 7,500 x 5% = 325 at $45,000
        self.assertEqual(calc_lito(D("45000"), self.rates), D("325.00"))

    def test_second_taper(self):
        # 325 - (50,000 - 45,000) x 1.5% = 250
        self.assertEqual(calc_lito(D("50000"), self.rates), D("250.00"))

    def test_nil_at_shade_out_end(self):
        self.assertEqual(calc_lito(D("66667"), self.rates), D("0"))


class BeneficiaryTaxIntegrationTests(TestCase):
    """End-to-end: an individual beneficiary on $45,000 total taxable income."""

    def test_individual_45000_net_tax(self):
        rates = get_tax_rates("FY2025")
        result = calculate_beneficiary_tax(
            beneficiary_type="individual",
            outside_income=D("0"),
            proposed_distribution=D("45000"),
            franking_credits_share=D("0"),
            rates=rates,
        )
        # Gross 4,288 + Medicare 900 - LITO 325 = 4,863
        self.assertEqual(result["gross_tax_payable"], D("4288.00"))
        self.assertEqual(result["medicare_levy"], D("900.00"))
        self.assertEqual(result["lito_offset"], D("325.00"))
        self.assertEqual(result["net_tax_payable"], D("4863.00"))

    def test_company_flat_base_rate(self):
        rates = get_tax_rates("FY2025")
        result = calculate_beneficiary_tax(
            beneficiary_type="company",
            outside_income=D("0"),
            proposed_distribution=D("100000"),
            franking_credits_share=D("0"),
            rates=rates,
        )
        self.assertEqual(result["net_tax_payable"], D("25000.00"))
