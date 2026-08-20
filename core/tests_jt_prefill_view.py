"""The endpoint the create-entity picker calls after you choose a JT client.

fetch_identity is patched throughout: these tests are about what the view does
with each of JT's three outcomes, and a live HTTP call would make them flaky.
The envelope SHAPE they feed it was taken from a real fetch against prod JT, so
the mapping is not being tested against an invented contract. Values are made up.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from accounts.oidc_views import ENTRA_SESSION_KEY
from core.jt_identity import IdentityResult

XPM_ID = "bbbbbbbb-1111-2222-3333-444444444444"

HELD_ENVELOPE = {
    "legalName": {"status": "held", "value": "Wombat Holdings Pty Ltd"},
    "entityType": {"status": "held", "value": "Company"},
    "abn": {"status": "held", "value": "12 345 678 901"},
    "address": {"status": "held", "value": "14 Example Pde"},
    "city": {"status": "held", "value": "SOMEWHERE"},
    "region": {"status": "held", "value": "VIC"},
    "postCode": {"status": "held", "value": "3000"},
    "country": {"status": "held", "value": "Australia"},
    "tfn": {"status": "restricted", "masked": "***-***-777"},
    "email": {"status": "not_held"},
}


class JtClientPrefillViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ross", email="ross@mcands.com.au", password="x" * 14,
            role=User.Role.ADMIN,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session[ENTRA_SESSION_KEY] = True
        session["2fa_verified"] = True
        session.save()

    def url(self, xpm_id=XPM_ID):
        return reverse("core:htmx_jt_client_prefill", args=[xpm_id])

    def test_returns_the_mapped_form_fields(self):
        with patch("core.views.fetch_identity",
                   return_value=IdentityResult(state="ok", fields=HELD_ENVELOPE)):
            response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 200)
        fields = response.json()["fields"]
        self.assertEqual(fields["entity_name"], "Wombat Holdings Pty Ltd")
        self.assertEqual(fields["entity_type"], "company")
        self.assertEqual(fields["abn"], "12345678901")
        self.assertEqual(fields["suburb"], "SOMEWHERE")

    def test_never_returns_a_tfn(self):
        with patch("core.views.fetch_identity",
                   return_value=IdentityResult(state="ok", fields=HELD_ENVELOPE)):
            response = self.client.get(self.url(), secure=True)
        self.assertNotIn("tfn", response.json()["fields"])
        self.assertNotIn("777", response.content.decode())

    def test_reports_unavailable_and_prefills_nothing_when_jt_is_down(self):
        with patch("core.views.fetch_identity",
                   return_value=IdentityResult(state="unavailable")):
            response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["unavailable"])
        self.assertEqual(body["fields"], {})

    def test_reports_not_found_when_jt_has_no_such_client(self):
        with patch("core.views.fetch_identity",
                   return_value=IdentityResult(state="not_found")):
            response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["not_found"])
        self.assertEqual(body["fields"], {})

    def test_anonymous_callers_are_refused(self):
        self.client.logout()
        with patch("core.views.fetch_identity",
                   return_value=IdentityResult(state="ok", fields=HELD_ENVELOPE)) as fetch:
            response = self.client.get(self.url(), secure=True)
        self.assertNotEqual(response.status_code, 200)
        fetch.assert_not_called()
