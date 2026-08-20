"""The parse-statement endpoint must correct its anchors after filtering.

The logic lives in ``rebase_anchors_for_kept_rows`` and is covered by
tests_period_filter_anchors. What is tested here is the wiring, because the
defect was precisely that the wiring did not exist: the endpoint filtered the
rows and then reported the statement's own opening and closing balances
unchanged, so the preview showed figures that could not add up.
"""
import json
from unittest.mock import patch

from django.test import Client as TestClient, TestCase
from django.urls import reverse

from accounts.models import User


def _login():
    user = User.objects.create_user(
        username="parse_period_admin",
        password="x",
        role=User.Role.ADMIN,
        totp_secret="dummy-secret-for-test",
        totp_confirmed=True,
    )
    client = TestClient()
    client.force_login(user)
    session = client.session
    session["2fa_verified"] = True
    session.save()
    return client


# A statement straddling 30 June, as CBA issues them: May and June belong to
# the year being worked on, July does not.
EXTRACTED = {
    "bank": "cba",
    "opening_balance": 26420.53,
    "closing_balance": 14001.89,
    "account_name": "", "bsb": "", "account_number": "",
    "period_start": "", "period_end": "",
    "transactions": [
        {"date": "01/05/2026", "description": "MAY CREDIT", "amount": 1000.00},
        {"date": "30/06/2026", "description": "JUNE DEBIT", "amount": -500.00},
        {"date": "05/07/2026", "description": "JULY DEBIT", "amount": -8000.00},
        {"date": "30/07/2026", "description": "JULY CREDIT", "amount": 200.00},
    ],
}


class ParseStatementPeriodTests(TestCase):

    def setUp(self):
        self.client_ = _login()
        self.url = reverse("review:parse_statement")

    def _post(self):
        with patch("review.pdf_parsers.extract_transactions_from_pdf_direct",
                   return_value=dict(EXTRACTED)):
            from django.core.files.uploadedfile import SimpleUploadedFile
            return self.client_.post(
                self.url,
                {
                    "file": SimpleUploadedFile(
                        "July.pdf", b"%PDF-1.4 stub",
                        content_type="application/pdf"),
                    "period_start": "2026-05-01",
                    "period_end": "2026-06-30",
                },
                secure=True,
            )

    def test_only_the_in_period_rows_come_back(self):
        data = json.loads(self._post().content)
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["transactions"]), 2)
        self.assertEqual(data["excluded"], 2)

    def test_the_closing_balance_is_no_longer_the_whole_statements(self):
        data = json.loads(self._post().content)
        self.assertNotAlmostEqual(data["closing_balance"], 14001.89, places=2)

    def test_the_reported_anchors_agree_with_the_rows_reported(self):
        data = json.loads(self._post().content)
        movements = sum(t["amount"] for t in data["transactions"])
        self.assertAlmostEqual(
            data["opening_balance"] + movements,
            data["closing_balance"],
            places=2,
        )

    def test_the_user_is_told_what_was_left_out(self):
        data = json.loads(self._post().content)
        warning = data["period_filter_warning"]
        self.assertIn("2 transaction(s)", warning)
        self.assertIn("05/07/2026", warning)
        self.assertIn("30/07/2026", warning)
