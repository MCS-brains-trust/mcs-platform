# core/tests_entity_identity_panel.py
"""The entity page shows JT's identity values, and keeps working when JT is down.

Working data is always current — the page fetches on load. When the fetch fails
the page falls back to the last successful fetch and says plainly that it could
not reach Job Tracker. It never blocks, and it never silently shows stale values
as if they were fresh.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.jt_identity import IdentityResult
from core.models import Entity

XPM_ID = "aaaaaaaa-0000-0000-0000-000000000009"

# The entity page renders real templates, and prod uses whitenoise's manifest
# storage — without this every render raises "Missing staticfiles manifest
# entry". Same override the bank/TB suites use.
STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
OK_FIELDS = {
    "legalName": {"status": "held", "value": "Example Holdings Pty Ltd"},
    "entityType": {"status": "held", "value": "Company"},
    "abn": {"status": "held", "value": "11222333444"},
    "address": {"status": "held", "value": "1 Example Street"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class EntityIdentityPanelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ross", email="ross@mcands.com.au", password="x" * 14,
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        # Require2FAMiddleware wants BOTH: a confirmed TOTP secret on the user
        # (has_2fa) and the step performed this session. Repo convention, see
        # core/tests_directors_report.py.
        session["2fa_verified"] = True
        session.save()
        self.entity = Entity.objects.create(
            entity_name="Example Holdings Pty Ltd", entity_type="company",
            xpm_client_id=XPM_ID, assigned_accountant=self.user,
        )

    def url(self):
        return reverse("core:entity_detail", args=[self.entity.pk])

    def test_successful_fetch_renders_jt_values_and_writes_the_cache(self):
        with patch("core.views.fetch_identity",
                   return_value=IdentityResult(state="ok", fields=OK_FIELDS)):
            response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "11222333444")
        self.assertNotContains(response, "could not reach Job Tracker")
        self.entity.refresh_from_db()
        self.assertEqual(self.entity.jt_identity_cache, OK_FIELDS)
        self.assertIsNotNone(self.entity.jt_identity_fetched_at)

    def test_unavailable_falls_back_to_the_cache_and_says_so(self):
        self.entity.jt_identity_cache = OK_FIELDS
        self.entity.jt_identity_fetched_at = timezone.now()
        self.entity.save(update_fields=["jt_identity_cache", "jt_identity_fetched_at"])
        with patch("core.views.fetch_identity",
                   return_value=IdentityResult(state="unavailable")):
            response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not reach Job Tracker")
        self.assertContains(response, "11222333444")   # last-known still shown

    def test_unavailable_does_not_overwrite_the_cache(self):
        self.entity.jt_identity_cache = OK_FIELDS
        self.entity.save(update_fields=["jt_identity_cache"])
        with patch("core.views.fetch_identity", return_value=IdentityResult(state="unavailable")):
            self.client.get(self.url(), secure=True)
        self.entity.refresh_from_db()
        self.assertEqual(self.entity.jt_identity_cache, OK_FIELDS)

    def test_unlinked_entity_does_not_call_jt_and_renders_normally(self):
        self.entity.xpm_client_id = ""
        self.entity.save(update_fields=["xpm_client_id"])
        with patch("core.views.fetch_identity") as fetch:
            response = self.client.get(self.url(), secure=True)
        fetch.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "could not reach Job Tracker")

    def test_not_found_flags_a_wrong_link_rather_than_an_outage(self):
        with patch("core.views.fetch_identity", return_value=IdentityResult(state="not_found")):
            response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not found in Job Tracker")
        self.assertNotContains(response, "could not reach Job Tracker")
