"""
Tests for the review app.

Covers two regressions:

1. NL-search month filter — search_transactions matched months with
   date__contains="/MM/" but every parser stores ISO "YYYY-MM-DD" dates, so
   month-name searches always returned nothing. Now matches ISO "-MM-"
   (keeping the legacy "/MM/" for old rows).

2. Bulk upload Vision fallback — upload_bank_statement previously had no
   Claude Vision OCR rescue (only the single-file parse_statement path did),
   so a PDF the direct parsers couldn't read dead-ended with "No transactions
   could be extracted". Both paths now share _try_vision_fallback.
"""
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as TestClient, TestCase

from accounts.models import User
from review.models import PendingTransaction, ReviewJob


def _make_admin(username):
    return User.objects.create_user(
        username=username,
        password="x",
        role=User.Role.ADMIN,
        totp_secret="dummy-secret-for-test",
        totp_confirmed=True,
    )


def _login(user):
    c = TestClient()
    c.force_login(user)
    session = c.session
    session["2fa_verified"] = True
    session.save()
    return c


class MonthSearchTests(TestCase):
    """Month-name searches must match the ISO dates the parsers store."""

    @classmethod
    def setUpTestData(cls):
        cls.user = _make_admin("review_search_test_admin")
        cls.job = ReviewJob.objects.create(
            client_name="Search Test Client",
            file_name="stmt.pdf",
            submitted_by="tester",
            source="upload",
            total_transactions=2,
        )
        cls.txn_october = PendingTransaction.objects.create(
            job=cls.job, date="2024-10-15", description="Officeworks stationery",
            amount="-45.00",
        )
        cls.txn_november = PendingTransaction.objects.create(
            job=cls.job, date="2024-11-02", description="Bunnings supplies",
            amount="-80.00",
        )

    def _search(self, query):
        c = _login(self.user)
        return c.post(
            f"/api/review/{self.job.pk}/search/",
            data={"query": query},
            content_type="application/json",
            secure=True,
        )

    def test_month_name_matches_iso_dates(self):
        response = self._search("october")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["filters"].get("month"), "October")
        self.assertEqual(payload["transaction_ids"], [str(self.txn_october.pk)])

    def test_month_abbreviation_matches(self):
        response = self._search("nov")
        payload = response.json()
        self.assertEqual(payload["transaction_ids"], [str(self.txn_november.pk)])

    def test_legacy_slash_dates_still_match(self):
        txn_legacy = PendingTransaction.objects.create(
            job=self.job, date="15/10/2024", description="Legacy formatted row",
            amount="-10.00",
        )
        response = self._search("october")
        ids = response.json()["transaction_ids"]
        self.assertIn(str(self.txn_october.pk), ids)
        self.assertIn(str(txn_legacy.pk), ids)


class BulkUploadVisionFallbackTests(TestCase):
    """The bulk upload path must fall back to Vision OCR like the
    single-file path does."""

    VISION_RESULT = {
        "transactions": [
            {"date": "2024-08-01", "description": "EFTPOS Cafe", "amount": -12.50},
            {"date": "2024-08-03", "description": "Salary", "amount": 1000.00},
        ],
        "opening_balance": 100.00,
        "closing_balance": 1087.50,
    }

    @classmethod
    def setUpTestData(cls):
        cls.user = _make_admin("review_upload_test_admin")

    def _upload(self):
        c = _login(self.user)
        pdf = SimpleUploadedFile(
            "unparseable.pdf", b"%PDF-1.4 vision-only content",
            content_type="application/pdf",
        )
        return c.post(
            "/upload-statement/",
            {"files": pdf, "client_name": "Vision Test"},
            secure=True,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_vision_fallback_on_parse_error(self):
        """Direct parser raises → Vision OCR result creates the job."""
        with patch(
            "review.pdf_parsers.extract_transactions_from_pdf_direct",
            side_effect=ValueError("no parser matched"),
        ), patch(
            "review.email_ingestion.extract_transactions_from_pdf",
            return_value=dict(self.VISION_RESULT),
        ) as mock_vision:
            response = self._upload()

        self.assertTrue(mock_vision.called, "Vision OCR must be attempted")
        job = ReviewJob.objects.filter(client_name="Vision Test").first()
        self.assertIsNotNone(job, f"Job must be created from Vision output: {response.content[:300]}")
        self.assertEqual(job.total_transactions, 2)
        self.assertEqual(job.transactions.count(), 2)

    def test_vision_fallback_on_empty_result(self):
        """Direct parse succeeds but finds nothing → Vision OCR runs."""
        with patch(
            "review.pdf_parsers.extract_transactions_from_pdf_direct",
            return_value={"transactions": []},
        ), patch(
            "review.email_ingestion.extract_transactions_from_pdf",
            return_value=dict(self.VISION_RESULT),
        ) as mock_vision:
            self._upload()

        self.assertTrue(mock_vision.called)
        job = ReviewJob.objects.filter(client_name="Vision Test").first()
        self.assertIsNotNone(job)
        self.assertEqual(job.transactions.count(), 2)

    def test_error_reported_when_vision_also_fails(self):
        with patch(
            "review.pdf_parsers.extract_transactions_from_pdf_direct",
            side_effect=ValueError("no parser matched"),
        ), patch(
            "review.email_ingestion.extract_transactions_from_pdf",
            side_effect=RuntimeError("api down"),
        ):
            response = self._upload()

        self.assertFalse(ReviewJob.objects.filter(client_name="Vision Test").exists())
        self.assertIn("Vision OCR", response.content.decode())
