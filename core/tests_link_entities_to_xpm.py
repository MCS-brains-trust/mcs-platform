# core/tests_link_entities_to_xpm.py
"""Linking the existing entities to their XPM clients, under review.

The command proposes; a human confirms; only then is anything written. A wrong
link here surfaces years later as a statement filed against the wrong client, so
--dry-run is the default and --apply refuses to invent rows of its own.
"""
import csv
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from core.jt_identity import ClientSearchResult
from core.management.commands.link_entities_to_xpm import normalise_abn
from core.models import Entity


class NormaliseAbnTests(TestCase):
    def test_strips_everything_but_digits(self):
        self.assertEqual(normalise_abn("11 222 333 444"), "11222333444")
        self.assertEqual(normalise_abn("11-222-333-444"), "11222333444")
        self.assertEqual(normalise_abn(None), "")
        self.assertEqual(normalise_abn("  "), "")


class LinkEntitiesDryRunTests(TestCase):
    def setUp(self):
        # Entity.abn is max_length=11, so a StatementHub ABN is always bare
        # digits — a spaced one cannot be stored. Normalisation still earns its
        # place for the values coming back from JT, which is what
        # test_matches_on_normalised_abn covers.
        self.entity = Entity.objects.create(
            entity_name="Example Holdings Pty Ltd", entity_type="company",
            abn="11222333444",
        )

    def _run(self, *args, rows=None, **kwargs):
        out = StringIO()
        result = ClientSearchResult(failed=False, clients=rows if rows is not None else [])
        with patch("core.management.commands.link_entities_to_xpm.search_clients",
                   return_value=result):
            call_command("link_entities_to_xpm", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_dry_run_writes_nothing_to_the_database(self):
        printed = self._run(rows=[{"xpmId": "xpm-1", "displayName": "Example Holdings Pty Ltd",
                                   "entityType": "Company", "abn": "11222333444"}])
        self.entity.refresh_from_db()
        self.assertEqual(self.entity.xpm_client_id, "")
        self.assertIn("DRY RUN", printed)
        self.assertIn("single", printed)

    def test_matches_on_normalised_abn(self):
        printed = self._run(rows=[{"xpmId": "xpm-1", "displayName": "Example Holdings Pty Ltd",
                                   "entityType": "Company", "abn": "11-222-333-444"}])
        self.assertIn("xpm-1", printed)

    def test_multiple_matches_are_flagged_for_manual_linking(self):
        printed = self._run(rows=[
            {"xpmId": "xpm-1", "displayName": "Example Holdings Pty Ltd", "entityType": "Company", "abn": "11222333444"},
            {"xpmId": "xpm-2", "displayName": "Example Holdings (old)", "entityType": "Company", "abn": "11222333444"},
        ])
        self.assertIn("multiple", printed)

    def test_no_match_is_flagged(self):
        printed = self._run(rows=[])
        self.assertIn("none", printed)

    def test_an_entity_with_no_abn_is_flagged_without_searching(self):
        Entity.objects.create(entity_name="No ABN Trust", entity_type="trust", abn="")
        printed = self._run()
        self.assertIn("no_abn", printed)

    def test_the_proposal_file_is_written(self):
        path = "/tmp/xpm_link_proposals_test.csv"
        self._run("--out", path,
                  rows=[{"xpmId": "xpm-1", "displayName": "Example Holdings Pty Ltd",
                         "entityType": "Company", "abn": "11222333444"}])
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["xpm_client_id"], "xpm-1")
        self.assertEqual(rows[0]["match"], "single")


class LinkEntitiesApplyTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Example Holdings Pty Ltd", entity_type="company", abn="11222333444",
        )
        self.path = "/tmp/xpm_link_apply_test.csv"
        with open(self.path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["entity_id", "entity_name", "entity_abn",
                             "xpm_client_id", "jt_display_name", "match"])
            writer.writerow([str(self.entity.pk), self.entity.entity_name, "11222333444",
                             "xpm-1", "Example Holdings Pty Ltd", "single"])

    def test_apply_requires_a_reviewed_file(self):
        with self.assertRaises(CommandError):
            call_command("link_entities_to_xpm", "--apply", stdout=StringIO())

    def test_apply_writes_only_the_rows_in_the_file(self):
        other = Entity.objects.create(entity_name="Untouched Co", entity_type="company",
                                      abn="99888777666")
        call_command("link_entities_to_xpm", "--apply", "--from-file", self.path, stdout=StringIO())
        self.entity.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.entity.xpm_client_id, "xpm-1")
        self.assertEqual(other.xpm_client_id, "")

    def test_apply_never_overwrites_an_existing_link(self):
        self.entity.xpm_client_id = "already-linked"
        self.entity.save(update_fields=["xpm_client_id"])
        out = StringIO()
        call_command("link_entities_to_xpm", "--apply", "--from-file", self.path, stdout=out)
        self.entity.refresh_from_db()
        self.assertEqual(self.entity.xpm_client_id, "already-linked")
        self.assertIn("skipped", out.getvalue())

    def test_apply_ignores_a_row_with_a_blank_id(self):
        with open(self.path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["entity_id", "entity_name", "entity_abn",
                             "xpm_client_id", "jt_display_name", "match"])
            writer.writerow([str(self.entity.pk), self.entity.entity_name, "11222333444",
                             "", "", "none"])
        call_command("link_entities_to_xpm", "--apply", "--from-file", self.path, stdout=StringIO())
        self.entity.refresh_from_db()
        self.assertEqual(self.entity.xpm_client_id, "")
