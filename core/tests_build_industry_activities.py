"""build_industry_activities: PDF -> committed activity fixture.

Only the binary text extraction is patched. Everything the command decides --
validate, diff, refuse, write -- runs for real against a temp fixture path, so
these tests cover the parts that can lose data.
"""
import json
import os
import tempfile
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

TEXT = """
Activity code
Abalone fishing 04191
Abattoir operation - except poultry 11110
Accounting service 69320
Bookkeeping service 69320
"""

CODE_MAP = {
    "04191": "Fishing",
    "11110": "Meat Processing",
    "69320": "Accounting Services",
}

EXTRACT = "core.management.commands.build_industry_activities.extract_pdf_text"


class BuildIndustryActivitiesTests(TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w",
        )
        handle.close()
        self.fixture = handle.name
        os.unlink(self.fixture)
        self.addCleanup(
            lambda: os.path.exists(self.fixture) and os.unlink(self.fixture)
        )

    def run_command(self, text=TEXT, code_map=None, **opts):
        with patch(EXTRACT, return_value=text), \
             patch("core.management.commands.build_industry_activities.INDUSTRY_CODE_MAP",
                   code_map if code_map is not None else CODE_MAP):
            return call_command(
                "build_industry_activities", pdf="/nonexistent.pdf",
                out=self.fixture, min_pairs=2, **opts,
            )

    def test_writes_the_index_keyed_by_code(self):
        self.run_command()
        written = json.load(open(self.fixture))
        self.assertEqual(written["11110"], ["Abattoir operation - except poultry"])
        self.assertEqual(
            written["69320"], ["Accounting service", "Bookkeeping service"],
        )

    def test_dry_run_writes_nothing(self):
        self.run_command(dry_run=True)
        self.assertFalse(os.path.exists(self.fixture))

    def test_refuses_a_code_that_is_not_an_official_bic(self):
        text = TEXT + "Something invented 99999\n"
        with self.assertRaises(CommandError) as ctx:
            self.run_command(text=text)
        self.assertIn("99999", str(ctx.exception))
        self.assertFalse(os.path.exists(self.fixture))

    def test_refuses_a_truncated_extraction_and_keeps_the_old_fixture(self):
        json.dump({"04191": ["Kept"]}, open(self.fixture, "w"))
        with patch(EXTRACT, return_value=TEXT), \
             patch("core.management.commands.build_industry_activities.INDUSTRY_CODE_MAP",
                   CODE_MAP):
            with self.assertRaises(CommandError):
                call_command(
                    "build_industry_activities", pdf="/nonexistent.pdf",
                    out=self.fixture, min_pairs=5000,
                )
        self.assertEqual(json.load(open(self.fixture)), {"04191": ["Kept"]})

    def test_refuses_text_that_yielded_no_pairs(self):
        with self.assertRaises(CommandError):
            self.run_command(text="just prose, no codes\n")

    def test_writing_over_an_existing_fixture_replaces_it_wholesale(self):
        json.dump({"04191": ["Abalone fishing"], "11110": ["Gone"]},
                  open(self.fixture, "w"))
        self.run_command()
        written = json.load(open(self.fixture))
        self.assertEqual(written["11110"], ["Abattoir operation - except poultry"])
        self.assertIn("69320", written)


class DiffIndexTests(TestCase):
    """The diff is a pure function so it is asserted as data, not scraped from stdout."""

    def test_names_added_removed_and_changed_codes(self):
        from core.industry_activities import diff_index
        changes = diff_index(
            {"04191": ["Abalone fishing"], "69320": ["Accounting service"]},
            {"04191": ["Abalone fishing"], "11110": ["Meat"]},
        )
        self.assertEqual(changes["codes_added"], ["69320"])
        self.assertEqual(changes["codes_removed"], ["11110"])
        self.assertEqual(changes["codes_changed"], [])

    def test_a_code_whose_activities_changed_is_reported_as_changed(self):
        from core.industry_activities import diff_index
        changes = diff_index({"04191": ["New wording"]}, {"04191": ["Old wording"]})
        self.assertEqual(changes["codes_changed"], ["04191"])
        self.assertEqual(changes["codes_added"], [])
