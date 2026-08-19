# core/tests_generated_document_identity_snapshot.py
"""A generated statement freezes the identity it was generated with.

Reopening a prior-year statement must show what it said when it was signed, not
what Job Tracker says today. The snapshot is written once, at insert, and never
re-derived: a later regeneration produces a NEW GeneratedDocument row with its
own snapshot.
"""
from datetime import date

from django.test import TestCase
from django.utils import timezone

from core.models import Entity, FinancialYear, GeneratedDocument

XPM_ID = "aaaaaaaa-0000-0000-0000-000000000009"
FIELDS = {
    "legalName": {"status": "held", "value": "Example Holdings Pty Ltd"},
    "abn": {"status": "held", "value": "11222333444"},
}


class IdentitySnapshotTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Example Holdings Pty Ltd", entity_type="company",
            xpm_client_id=XPM_ID,
            jt_identity_cache=FIELDS, jt_identity_fetched_at=timezone.now(),
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )

    def _doc(self):
        return GeneratedDocument.objects.create(financial_year=self.fy, file="generated/x.docx")

    def test_snapshot_is_written_on_insert(self):
        doc = self._doc()
        self.assertEqual(doc.identity_snapshot["source"], "job_tracker")
        self.assertEqual(doc.identity_snapshot["xpm_client_id"], XPM_ID)
        self.assertEqual(doc.identity_snapshot["fields"], FIELDS)
        self.assertTrue(doc.identity_snapshot["read_at"])

    def test_a_later_jt_change_does_not_rewrite_a_signed_statement(self):
        doc = self._doc()
        self.entity.jt_identity_cache = {
            "legalName": {"status": "held", "value": "Renamed Holdings Pty Ltd"},
        }
        self.entity.save(update_fields=["jt_identity_cache"])
        doc.status = GeneratedDocument.DocumentStatus.FINAL
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(
            doc.identity_snapshot["fields"]["legalName"]["value"], "Example Holdings Pty Ltd",
        )

    def test_an_unlinked_entity_snapshots_statementhub_s_own_values(self):
        self.entity.xpm_client_id = ""
        self.entity.jt_identity_cache = {}
        self.entity.jt_identity_fetched_at = None
        self.entity.save()
        doc = self._doc()
        self.assertEqual(doc.identity_snapshot["source"], "statementhub")
        self.assertEqual(
            doc.identity_snapshot["fields"]["legalName"]["value"], "Example Holdings Pty Ltd",
        )
        self.assertIsNone(doc.identity_snapshot["read_at"])

    def test_an_explicit_snapshot_is_never_overwritten(self):
        given = {"source": "job_tracker", "xpm_client_id": XPM_ID, "read_at": None, "fields": {}}
        doc = GeneratedDocument.objects.create(
            financial_year=self.fy, file="generated/y.docx", identity_snapshot=given,
        )
        self.assertEqual(doc.identity_snapshot, given)

    def test_save_makes_no_network_call(self):
        from unittest.mock import patch
        with patch("core.jt_identity.requests.get") as get:
            self._doc()
        get.assert_not_called()
