"""The curated "Common" optgroup must say what it means.

This list rotted silently: 13 of its 35 entries were ANZSIC classes rather than
ATO BICs and were dropped with a logger.warning nobody reads, and 11 more were
valid codes for a completely different industry -- Police Services standing in
for recruitment, Central Government Administration for commercial cleaning.
Pairing each code with the label it is meant to be turns both into test
failures instead of log noise.
"""
from django.test import TestCase

from core.industry_codes import (
    COMMON_INDUSTRY_CODES,
    COMMON_OPTGROUP_LABEL,
    INDUSTRY_CHOICES,
    INDUSTRY_CODE_MAP,
)


class CommonIndustryGroupTests(TestCase):
    def test_every_code_is_a_real_bic(self):
        unknown = sorted(set(COMMON_INDUSTRY_CODES) - set(INDUSTRY_CODE_MAP))
        self.assertEqual(unknown, [], "these are not ATO BICs")

    def test_every_code_is_paired_with_its_official_label(self):
        """The check that makes a hand-typed code impossible to get wrong."""
        wrong = {
            code: {"expected": expected, "official": INDUSTRY_CODE_MAP.get(code)}
            for code, expected in COMMON_INDUSTRY_CODES.items()
            if INDUSTRY_CODE_MAP.get(code) != expected
        }
        self.assertEqual(wrong, {})

    def test_nothing_is_silently_dropped_from_the_rendered_group(self):
        group = dict(INDUSTRY_CHOICES).get(COMMON_OPTGROUP_LABEL, [])
        self.assertEqual(len(group), len(COMMON_INDUSTRY_CODES))

    def test_the_trades_the_firm_actually_bills_are_present(self):
        """Each of these was missing or wrong before the repair."""
        for code, label in [
            ("69310", "Legal Services"),
            ("67200", "Real Estate Services"),
            ("95110", "Hairdressing and Beauty Services"),
            ("42510", "Clothing Retailing"),
            ("45200", "Pubs, Taverns and Bars"),
            ("94199", "Other Automotive Repair and Maintenance"),
            ("39220", "Tyre Retailing"),
            ("32310", "Plumbing Services"),
            ("32440", "Painting and Decorating Services"),
            ("85310", "Dental Services"),
            ("72110", "Employment Placement and Recruitment Services"),
            ("73110", "Building and Other Industrial Cleaning Services"),
        ]:
            self.assertIn(code, COMMON_INDUSTRY_CODES, f"{label} missing")
            self.assertEqual(COMMON_INDUSTRY_CODES[code], label)

    def test_the_unintended_entries_are_gone(self):
        """Codes nothing suggests were ever meant to be quick picks."""
        for code, what in [
            ("75100", "Central Government Administration"),
            ("77110", "Police Services"),
            ("46220", "Urban Bus Transport"),
            ("32110", "Land Development and Subdivision"),
            ("32120", "Site Preparation Services"),
        ]:
            self.assertNotIn(code, COMMON_INDUSTRY_CODES, f"{what} should not be here")
