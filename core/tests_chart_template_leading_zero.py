"""The chart template must not carry a code twice, once padded and once not.

Noticed 2026-08-24: the picker offered "0620 Rents received" and "620 Rents
received", the same account under two codes. The global ChartOfAccount held
36 such pairs for company, 35 for partnership and 36 for sole trader --
identical name, identical section, even identical display_order.

The padding is what the source carries: data/ChartofAccounts*.xlsx holds only
the four-digit HandiLedger form, so `import_chart_of_accounts` recreates the
padded row on every run. StatementHub's own convention is unpadded -- see
_strip_revenue_leading_zero in core/access_ledger_import.py, "HandiLedger
uses 4-digit codes like 0575 for revenue, but StatementHub uses 575".

Deliberately a de-duplication and NOT a renumbering. A padded code with no
unpadded twin is a real, distinct account -- the trust template's
0200 Sales - Livestock, 0401 Proceeds - Hay -- and live trial balances
already post to padded revenue codes (Hazaway, Veronica Cerratti). Those are
left exactly as they are.
"""
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from core.models import ChartOfAccount
from core.management.commands.import_chart_of_accounts import (
    canonical_template_code,
)


class CanonicalTemplateCodeTests(TestCase):
    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="company", account_code="620",
            account_name="Rents received", section="revenue", display_order=33)

    def test_a_padded_code_collapses_onto_its_unpadded_twin(self):
        self.assertEqual(
            canonical_template_code("company", "0620", "Rents received"), "620")

    def test_a_padded_code_with_no_twin_is_left_alone(self):
        """0200 Sales - Livestock is a real trust account, not a duplicate."""
        self.assertEqual(
            canonical_template_code("trust", "0200", "Sales - Livestock"),
            "0200")

    def test_a_twin_under_a_different_name_is_not_merged(self):
        """Same number, different account. Merging would silently overwrite."""
        self.assertEqual(
            canonical_template_code("company", "0620", "Something else"),
            "0620")

    def test_suspense_keeps_its_code(self):
        self.assertEqual(
            canonical_template_code("company", "0000", "Suspense"), "0000")

    def test_an_unpadded_code_is_returned_unchanged(self):
        self.assertEqual(
            canonical_template_code("company", "620", "Rents received"), "620")

    def test_the_twin_must_be_the_same_entity_type(self):
        """company has 620; trust does not, so trust's 0620 stays put."""
        self.assertEqual(
            canonical_template_code("trust", "0620", "Rents received"), "0620")


class ImportDoesNotResurrectPaddedDuplicatesTests(TestCase):
    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="company", account_code="620",
            account_name="Rents received", section="revenue", display_order=33)

    def _import(self, accounts):
        payload = {"company": accounts}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "chart.json"
            path.write_text(json.dumps(payload))
            call_command("import_chart_of_accounts", json=str(path), verbosity=0)

    def test_importing_the_padded_source_row_does_not_create_a_second_account(self):
        self._import([{"account_code": "0620",
                       "account_name": "Rents received",
                       "section": "Revenue"}])
        codes = set(ChartOfAccount.objects.filter(entity_type="company")
                    .values_list("account_code", flat=True))
        self.assertEqual(codes, {"620"})

    def test_the_surviving_row_still_takes_the_sources_updates(self):
        self._import([{"account_code": "0620",
                       "account_name": "Rents received",
                       "section": "Revenue", "tax_code": "GST"}])
        row = ChartOfAccount.objects.get(entity_type="company", account_code="620")
        self.assertEqual(row.tax_code, "GST")

    def test_a_padded_account_with_no_twin_still_imports(self):
        self._import([{"account_code": "0200",
                       "account_name": "Sales - Livestock",
                       "section": "Revenue"}])
        self.assertTrue(ChartOfAccount.objects.filter(
            entity_type="company", account_code="0200").exists())
