"""Both doors into the review queue must be gated, and overrides recorded.

There are two, and only one of them has a preview step. ``confirm_import``
follows the parse/preview flow; ``upload_bank_statement`` creates ReviewJobs
straight from the uploaded file with nothing shown to anyone first, which makes
it the more dangerous of the two.
"""
import json
from datetime import date
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from accounts.models import User
from core.models import Entity, FinancialYear
from review.models import ReviewJob


def _login(username):
    user = User.objects.create_user(
        username=username, password="x", role=User.Role.ADMIN,
        totp_secret="dummy-secret-for-test", totp_confirmed=True,
    )
    client = TestClient()
    client.force_login(user)
    session = client.session
    session["2fa_verified"] = True
    session.save()
    return client


# Rows that do not add up to the anchors they are shipped with -- the shape the
# July upload actually had once its July rows were filtered out.
BROKEN_STATEMENT = {
    "filename": "July.pdf",
    "bank": "cba",
    "opening_balance": 26420.53,
    "closing_balance": 14001.89,
    "account_name": "", "bsb": "", "account_number": "",
    "period_start": "", "period_end": "",
    "transactions": [
        {"date": "01/05/2026", "description": "MAY CREDIT", "amount": 1000.00},
        {"date": "02/05/2026", "description": "MAY DEBIT", "amount": -500.00},
    ],
}


def _clean_statement():
    stmt = dict(BROKEN_STATEMENT)
    stmt["closing_balance"] = 26920.53          # 26,420.53 + 500.00
    return stmt


class ConfirmImportGateTests(TestCase):

    def setUp(self):
        self.client_ = _login("gate_confirm_admin")
        self.url = reverse("review:confirm_import")

    def _post(self, stmt, **extra):
        session = self.client_.session
        session["mcs_parsed_statements"] = [stmt]
        session.save()
        payload = {"statements": [stmt]}
        payload.update(extra)
        # confirm_import reads a JSON body, not form data.
        return self.client_.post(
            self.url, json.dumps(payload),
            content_type="application/json", secure=True)

    def test_a_statement_that_does_not_add_up_is_refused(self):
        response = self._post(BROKEN_STATEMENT)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReviewJob.objects.count(), 0)

    def test_the_refusal_says_what_is_wrong_and_offers_an_override(self):
        data = json.loads(self._post(BROKEN_STATEMENT).content)
        self.assertTrue(data["can_override"])
        self.assertIn("do not add up", data["message"])

    def test_a_refused_statement_stays_in_the_session(self):
        """So it can be fixed or overridden rather than re-uploaded."""
        self._post(BROKEN_STATEMENT)
        self.assertIn("mcs_parsed_statements", self.client_.session)

    def test_a_statement_that_adds_up_imports_normally(self):
        response = self._post(_clean_statement())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReviewJob.objects.count(), 1)
        self.assertEqual(ReviewJob.objects.get().verification_status,
                         ReviewJob.VERIFICATION_VERIFIED)

    def test_an_acknowledgement_without_a_reason_is_not_enough(self):
        response = self._post(BROKEN_STATEMENT, acknowledge_unverified="1")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReviewJob.objects.count(), 0)

    def test_a_reason_without_an_acknowledgement_is_not_enough(self):
        response = self._post(BROKEN_STATEMENT,
                              unverified_reason="I checked it by hand")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReviewJob.objects.count(), 0)

    def test_an_acknowledged_override_imports_and_is_recorded(self):
        response = self._post(
            BROKEN_STATEMENT,
            acknowledge_unverified="1",
            unverified_reason="Page 6 is missing from the bank's own PDF",
        )
        self.assertEqual(response.status_code, 200)
        job = ReviewJob.objects.get()
        self.assertTrue(job.was_overridden)
        self.assertEqual(job.override_reason,
                         "Page 6 is missing from the bank's own PDF")
        self.assertIn("do not add up", job.verification_detail)
        self.assertTrue(job.override_by)
        self.assertIsNotNone(job.override_at)

    def test_a_verified_import_records_no_override(self):
        self._post(_clean_statement())
        job = ReviewJob.objects.get()
        self.assertFalse(job.was_overridden)
        self.assertEqual(job.override_reason, "")
        self.assertEqual(job.override_by, "")
        self.assertIsNone(job.override_at)


class BulkUploadGateTests(TestCase):
    """The path with no preview at all."""

    def setUp(self):
        self.client_ = _login("gate_bulk_admin")
        self.url = reverse("review:upload_statement")
        self.entity = Entity.objects.create(
            entity_name="Gate Test Pty Ltd", entity_type="company")
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30))

    def _post(self, extracted, **extra):
        payload = {
            "files": SimpleUploadedFile(
                "July.pdf", b"%PDF-1.4 stub", content_type="application/pdf"),
            "entity_id": str(self.entity.pk),
            "financial_year_id": str(self.fy.pk),
        }
        payload.update(extra)
        with patch("review.pdf_parsers.extract_transactions_from_pdf_direct",
                   return_value=dict(extracted)):
            return self.client_.post(self.url, payload, secure=True)

    def test_a_statement_that_does_not_add_up_creates_no_job(self):
        self._post(BROKEN_STATEMENT)
        self.assertEqual(ReviewJob.objects.count(), 0)

    def test_a_statement_that_adds_up_creates_a_verified_job(self):
        self._post(_clean_statement())
        job = ReviewJob.objects.get()
        self.assertEqual(job.verification_status,
                         ReviewJob.VERIFICATION_VERIFIED)

    def test_an_acknowledged_override_is_recorded_here_too(self):
        self._post(
            BROKEN_STATEMENT,
            acknowledge_unverified="1",
            unverified_reason="Bank reissued the statement mid-period",
        )
        job = ReviewJob.objects.get()
        self.assertTrue(job.was_overridden)
        self.assertEqual(job.override_reason,
                         "Bank reissued the statement mid-period")
