"""Parsing the ATO BIC PDF's text into {code: [activity, ...]}.

The document is a two-column "activity ..... code" list, 47 pages, with page
furniture interleaved. These samples are hand-written in that shape: the real
PDF is 951KB of binary and does not belong in the repo, so the parser is tested
against text and the command is what feeds it real text.
"""
from django.test import TestCase

from core.industry_activities import (
    MIN_EXPECTED_PAIRS,
    ActivityParseError,
    parse_bic_text,
    validate_against_codes,
)

SAMPLE = """
Business industry codes
Activity code
Abalone fishing 04191
Abattoir operation - except poultry 11110
Abattoir operation - poultry 11120
Accounting service 69320
Bookkeeping service 69320
"""


class ParseBicTextTests(TestCase):
    def test_groups_activities_under_their_code(self):
        out = parse_bic_text(SAMPLE)
        self.assertEqual(out["11110"], ["Abattoir operation - except poultry"])
        self.assertEqual(out["04191"], ["Abalone fishing"])

    def test_several_activities_can_share_one_code(self):
        out = parse_bic_text(SAMPLE)
        self.assertEqual(
            sorted(out["69320"]), ["Accounting service", "Bookkeeping service"],
        )

    def test_page_furniture_is_not_mistaken_for_an_activity(self):
        """'Activity code' is a column heading, and there is no code on that line."""
        out = parse_bic_text(SAMPLE)
        flat = [a for acts in out.values() for a in acts]
        self.assertNotIn("Activity", flat)
        self.assertNotIn("code", flat)
        self.assertNotIn("Business industry codes", flat)

    def test_a_bare_code_with_no_activity_is_skipped(self):
        out = parse_bic_text("Activity code\n 69320\nAccounting service 69320\n")
        self.assertEqual(out["69320"], ["Accounting service"])

    def test_duplicate_activity_lines_are_collapsed(self):
        out = parse_bic_text("Abalone fishing 04191\nAbalone fishing 04191\n")
        self.assertEqual(out["04191"], ["Abalone fishing"])

    def test_activities_are_sorted_so_the_fixture_diffs_cleanly(self):
        out = parse_bic_text("Zebra farming 01440\nAlpaca farming 01440\n")
        self.assertEqual(out["01440"], ["Alpaca farming", "Zebra farming"])

    def test_a_layout_change_that_matches_nothing_is_an_error_not_an_empty_index(self):
        with self.assertRaises(ActivityParseError):
            parse_bic_text("no codes here at all\njust prose\n")


class ValidateAgainstCodesTests(TestCase):
    KNOWN = {"04191": "Fishing", "11110": "Meat Processing"}

    def test_accepts_an_index_whose_codes_are_all_official(self):
        validate_against_codes(
            {"04191": ["Abalone fishing"], "11110": ["Abattoir operation"]},
            self.KNOWN, min_pairs=2,
        )

    def test_rejects_a_code_that_is_not_an_official_bic(self):
        with self.assertRaises(ActivityParseError) as ctx:
            validate_against_codes(
                {"04191": ["Abalone fishing"], "99999": ["Invented"]},
                self.KNOWN, min_pairs=2,
            )
        self.assertIn("99999", str(ctx.exception))

    def test_rejects_an_index_that_collapsed_below_the_floor(self):
        with self.assertRaises(ActivityParseError) as ctx:
            validate_against_codes({"04191": ["Abalone fishing"]}, self.KNOWN, min_pairs=500)
        self.assertIn("500", str(ctx.exception))

    def test_the_floor_defends_the_real_document_size(self):
        """The live document carries ~2,817 pairs; the floor must be near it."""
        self.assertGreaterEqual(MIN_EXPECTED_PAIRS, 2000)
