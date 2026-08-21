"""Importing a HandiLedger ZIP into an entity you already created.

The bug these cover: "Import HandiLedger" on an entity's own page passes that
entity to the importer, and the importer refused because the entity "already
exists" -- which it does, by definition, since the caller named it. So the
import took nothing from the ZIP, and because it recorded no ERRORS the view
reported it in green as "Successfully imported: 0 years, 0 TB lines". A whole
prior-year trial balance silently did not arrive.

The ZIPs here are synthesised in-process: a HandiLedger export is a client's
financial history and must never become a test fixture.
"""
import io
import zipfile

from django.test import TestCase

from .access_ledger_import import import_access_ledger_zip
from .models import Entity, FinancialYear


def build_zip(years=(2023, 2024), name="Testworth Holdings Pty Ltd", code="TEST0001"):
    """A HandiLedger export with the minimum the importer needs to make a year.

    Client.txt carries the entity name at index 2; Year.txt the period start
    and end at 2 and 3, and the three finalised flags at 4, 5 and 6. No
    Balance.txt or Chart.txt, so these import as years with no balances --
    enough to prove which years arrive, which is what the guard decides.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for year in years:
            folder = f"HL_{code}_{year}"
            zf.writestr(
                f"{folder}/Client.txt",
                f'"0","{code}","{name}",""\n',
            )
            zf.writestr(
                f"{folder}/Year.txt",
                f'"{code}",{year},"01/07/{year - 1}","30/06/{year}","Y","Y","Y"\n',
            )
    buf.seek(0)
    return buf


class ImportIntoAnEntityYouNamedTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Testworth Holdings Pty Ltd", entity_type="company")

    def test_a_new_entity_takes_the_import_without_replace_being_ticked(self):
        """The defect. A brand new entity has nothing to overwrite, so being
        told to tick "replace existing" to get anything at all was asking the
        operator to authorise the destruction of data that does not exist."""
        result = import_access_ledger_zip(
            build_zip(), client=None, entity=self.entity, replace_existing=False)
        self.assertEqual(result["years_imported"], 2)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(
            sorted(self.entity.financial_years.values_list("year_label", flat=True)),
            ["2023", "2024"])

    def test_importing_the_same_years_again_is_still_refused(self):
        """The guard has to keep working for the case it was written for: the
        trial balance and depreciation rows are bulk-created without checking
        for an existing year, so a second pass would double every balance."""
        import_access_ledger_zip(build_zip(), client=None, entity=self.entity,
                                 replace_existing=False)
        result = import_access_ledger_zip(
            build_zip(), client=None, entity=self.entity, replace_existing=False)
        self.assertEqual(result["years_imported"], 0)
        self.assertEqual(len(result["warnings"]), 1)
        # Naming the years is the point: "entity already exists" told the
        # operator nothing they could act on.
        self.assertIn("2023, 2024", result["warnings"][0])
        self.assertEqual(FinancialYear.objects.filter(entity=self.entity).count(), 2)

    def test_a_year_the_entity_does_not_have_yet_still_imports(self):
        """Refusal is per year, not per entity: 2023 already being here must
        not block 2025."""
        import_access_ledger_zip(build_zip(years=(2023,)), client=None,
                                 entity=self.entity, replace_existing=False)
        result = import_access_ledger_zip(
            build_zip(years=(2025,)), client=None, entity=self.entity,
            replace_existing=False)
        self.assertEqual(result["years_imported"], 1)
        self.assertEqual(
            sorted(self.entity.financial_years.values_list("year_label", flat=True)),
            ["2023", "2025"])

    def test_replace_still_overwrites_rather_than_appending(self):
        import_access_ledger_zip(build_zip(), client=None, entity=self.entity,
                                 replace_existing=False)
        result = import_access_ledger_zip(
            build_zip(), client=None, entity=self.entity, replace_existing=True)
        self.assertEqual(result["years_imported"], 2)
        self.assertEqual(FinancialYear.objects.filter(entity=self.entity).count(), 2)

    def test_an_entity_merely_matched_by_name_is_still_protected(self):
        """Unchanged behaviour for the other caller: the standalone import page
        passes no entity, so a same-named entity is one it found rather than
        one it was given, and importing into it would be a surprise."""
        import_access_ledger_zip(build_zip(), client=None, entity=self.entity,
                                 replace_existing=True)
        result = import_access_ledger_zip(
            build_zip(), client=None, entity=None, replace_existing=False)
        self.assertEqual(result["years_imported"], 0)
        self.assertIn("already exists", result["warnings"][0])
