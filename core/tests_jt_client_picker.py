# core/tests_jt_client_picker.py
"""Creating an entity starts by naming the Job Tracker client it belongs to.

The XPM client uuid is the canonical key across JT, CoWorker and StatementHub,
so it must be captured at creation — not typed from memory later. JT being down
must degrade to "type the id by hand", never to a blocked form.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.forms import EntityForm
from core.jt_identity import ClientSearchResult

ROWS = [
    {"xpmId": "xpm-1", "displayName": "Example Holdings Pty Ltd",
     "entityType": "Company", "abn": "11222333444"},
    {"xpmId": "xpm-2", "displayName": "Example Family Trust",
     "entityType": "Trust", "abn": "55666777888"},
]


class JtClientPickerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ross", email="ross@mcands.com.au", password="x" * 14,
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        # Require2FAMiddleware wants BOTH a confirmed TOTP secret on the user and
        # the step performed this session. Repo convention, see
        # core/tests_directors_report.py.
        session["2fa_verified"] = True
        session.save()

    def url(self, q):
        return f"{reverse('core:htmx_jt_client_search')}?q={q}"

    def test_results_render_one_row_per_client(self):
        with patch("core.views.search_clients",
                   return_value=ClientSearchResult(failed=False, clients=ROWS)):
            response = self.client.get(self.url("example"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Example Holdings Pty Ltd")
        self.assertContains(response, "Example Family Trust")
        self.assertContains(response, "xpm-1")

    def test_no_matches_says_so(self):
        with patch("core.views.search_clients",
                   return_value=ClientSearchResult(failed=False, clients=[])):
            response = self.client.get(self.url("zzzz"), secure=True)
        self.assertContains(response, "No matching Job Tracker clients")

    def test_a_failed_search_degrades_to_manual_entry(self):
        with patch("core.views.search_clients", return_value=ClientSearchResult(failed=True)):
            response = self.client.get(self.url("example"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not reach Job Tracker")

    def test_the_form_saves_the_picked_id(self):
        self.assertIn("xpm_client_id", EntityForm(user=self.user).fields)

    def test_anonymous_cannot_search_the_client_book(self):
        self.client.logout()
        response = self.client.get(self.url("example"), secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
