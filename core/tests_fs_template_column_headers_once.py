"""The year/$ column block belongs in the page header, once, not per section.

_add_financial_table put `Note / {{ year }} / {{ prior_year }}` in every
section heading row and a `$ / $` row under it, so the Detailed Balance Sheet
repeated the block above Current Assets, Non-Current Assets, Current
Liabilities, Non-Current Liabilities and Equity, and the Detailed P&L repeated
it above Income and Expenses. Its comment claimed this was the Handiledger
layout; the reference pack (handiledger_reference/) prints the block once at
the top of each page, under the title, with bare section headings below.

Moving the block into the Word page header reproduces that: a page header
repeats on page 2 of the balance sheet on its own.
"""
from django.test import SimpleTestCase

from core.management.commands.generate_fs_templates import (
    _build_balance_sheet, _build_detailed_pl,
)

YEAR_TAG = "{{ year }}"
PRIOR_TAG = "{{ prior_year }}"

# Every entity type the command generates templates for. "trust_unit" is
# deliberately absent -- a unit trust reads the trust rows.
ENTITY_TYPES = ["company", "trust", "partnership", "sole_trader"]


def _body_rows(doc):
    """Every table row in the document body, as a list of cell strings."""
    return [
        [c.text.strip() for c in row.cells]
        for table in doc.tables
        for row in table.rows
    ]


def _header_text(doc):
    header = doc.sections[0].header
    parts = [p.text for p in header.paragraphs]
    for table in header.tables:
        for row in table.rows:
            parts.extend(c.text for c in row.cells)
    return "\n".join(parts)


class ColumnHeaderBlockTests(SimpleTestCase):
    """Asserted for both statements and, on the balance sheet, for the
    hand-built Non-Current Liabilities and Equity chain tables too."""

    def test_balance_sheet_body_has_no_repeated_column_block(self):
        for entity_type in ENTITY_TYPES:
            with self.subTest(entity_type=entity_type):
                rows = _body_rows(_build_balance_sheet(entity_type))

                offenders = [r for r in rows if YEAR_TAG in r or PRIOR_TAG in r]
                self.assertEqual(offenders, [],
                                 "section heading rows still carry year tags")

                dollar_rows = [r for r in rows if any(c == "$" for c in r)]
                self.assertEqual(dollar_rows, [],
                                 "dollar rows still present in the body")

    def test_detailed_pl_body_has_no_repeated_column_block(self):
        for entity_type in ENTITY_TYPES:
            with self.subTest(entity_type=entity_type):
                rows = _body_rows(_build_detailed_pl(entity_type))

                offenders = [r for r in rows if YEAR_TAG in r or PRIOR_TAG in r]
                self.assertEqual(offenders, [],
                                 "section heading rows still carry year tags")

                dollar_rows = [r for r in rows if any(c == "$" for c in r)]
                self.assertEqual(dollar_rows, [],
                                 "dollar rows still present in the body")

    def test_page_header_carries_the_column_block(self):
        for entity_type in ENTITY_TYPES:
            for builder in (_build_balance_sheet, _build_detailed_pl):
                with self.subTest(entity_type=entity_type, doc=builder.__name__):
                    text = _header_text(builder(entity_type))

                    self.assertIn("Note", text)
                    self.assertIn(YEAR_TAG, text)
                    self.assertIn(PRIOR_TAG, text)
                    self.assertIn("$", text)

    def test_section_headings_survive_in_the_body(self):
        """Stripping the column cells must not strip the labels themselves."""
        rows = _body_rows(_build_balance_sheet("trust"))
        labels = {r[0] for r in rows if r}

        for heading in (
            "Current Assets", "Non-Current Assets", "Current Liabilities",
            "Non-Current Liabilities", "Equity",
        ):
            self.assertIn(heading, labels)

    def test_documents_without_amount_columns_keep_a_plain_header(self):
        """Only the two statements opt in -- Notes/Declaration must not get it."""
        from core.management.commands.generate_fs_templates import _build_notes

        text = _header_text(_build_notes("trust"))
        self.assertNotIn(YEAR_TAG, text)
        self.assertNotIn("$", text)
