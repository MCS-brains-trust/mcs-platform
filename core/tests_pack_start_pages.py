"""The pack must record where each document starts, or its Contents is blank.

``build_package_bundle`` merged every document and then called
``_stamp_page_numbers(raw_bytes)`` with no offsets map. Per that function's own
docstring, "without it only the footer numbers are drawn and the Contents is
left alone".

For a trust that went unnoticed: ``generate_combined_pdf`` had already stamped
the Contents while building the FS bundle, and the FS bundle is the front of
the pack, so those numbers happened to be right. A COMPANY pack excludes
DECLARATION from the FS bundle and appends it afterwards as a LegalDocument --
so the declaration, the solvency resolution and the management representation
letter were never in any offsets map, and their Contents rows were blank.

Stamping twice was also wrong in itself: the footer number was drawn over the
one generate_combined_pdf had already drawn.
"""
import io

from django.test import SimpleTestCase

from core.package_pdf_renderer import _merge_start_pages


class MergeStartPagesTests(SimpleTestCase):
    """The FS bundle's own offsets, shifted to where the bundle sits."""

    def test_the_fs_bundle_at_the_front_keeps_its_offsets(self):
        merged = _merge_start_pages(
            fs_start_pages={"DETAILED_PL": 3, "BALANCE_SHEET": 5},
            fs_offset=0,
            legal_start_pages={},
        )
        self.assertEqual(merged, {"DETAILED_PL": 3, "BALANCE_SHEET": 5})

    def test_an_offset_fs_bundle_has_its_offsets_shifted(self):
        """Defensive: the FS bundle is first today, but the merge should not
        silently produce wrong numbers if that order ever changes."""
        merged = _merge_start_pages(
            fs_start_pages={"DETAILED_PL": 3},
            fs_offset=4,
            legal_start_pages={},
        )
        self.assertEqual(merged, {"DETAILED_PL": 7})

    def test_appended_legal_documents_carry_their_own_pages(self):
        merged = _merge_start_pages(
            fs_start_pages={"DETAILED_PL": 3},
            fs_offset=0,
            legal_start_pages={"directors_declaration": 9,
                               "solvency_resolution": 10,
                               "management_rep_letter": 11},
        )
        self.assertEqual(merged, {
            "DETAILED_PL": 3,
            "directors_declaration": 9,
            "solvency_resolution": 10,
            "management_rep_letter": 11,
        })

    def test_a_company_pack_resolves_all_three_blank_labels(self):
        """End of the chain: the merged map answers the labels that were blank."""
        from core.fs_template_service import _resolve_contents_start

        merged = _merge_start_pages(
            fs_start_pages={"DETAILED_PL": 3, "COMPILATION": 8},
            fs_offset=0,
            legal_start_pages={"directors_declaration": 9,
                               "solvency_resolution": 10,
                               "management_rep_letter": 11},
        )
        self.assertEqual(
            _resolve_contents_start("Directors' Declaration", merged), 9)
        self.assertEqual(
            _resolve_contents_start("Solvency Resolution", merged), 10)
        self.assertEqual(
            _resolve_contents_start("Management Representation Letter", merged), 11)
        self.assertEqual(
            _resolve_contents_start("Compilation Report", merged), 8)
