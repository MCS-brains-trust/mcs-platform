"""The picked JT client must still be legible after a server-side rejection.

The browser blocks an incomplete submit itself, but some errors are server-only
-- the ACN check digit is the one an operator meets in practice -- and those DO
round-trip. On that re-render the client the operator picked has to still be
shown as a NAME, in the box they picked it from, or the pick looks lost and gets
redone by hand.

Invented client data throughout.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from accounts.oidc_views import ENTRA_SESSION_KEY

XPM_ID = "cccccccc-1111-2222-3333-555555555555"
PICKED_NAME = "Wombat Holdings Pty Ltd"

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class PickedClientSurvivesServerRejectionTests(TestCase):
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

    def submit_with_a_bad_acn(self):
        """Everything a company needs, but an ACN that fails its check digit."""
        return self.client.post(reverse("core:entity_create"), {
            "entity_name": PICKED_NAME,
            "xpm_client_id": XPM_ID,
            "jt_client_name": PICKED_NAME,
            "entity_type": "company",
            "abn": "12345678901",
            "acn": "123456789",          # invalid check digit, refused server-side
            "industry": "30110",
            "tfn": "123456782",
            "contact_email": "someone@example.invalid",
            "is_small_business_entity": "true",
            "is_base_rate_entity": "true",
            "reporting_framework": "SPFR",
            "suburb": "SOMEWHERE",
            "state": "VIC",
            "postcode": "3000",
            "country": "Australia",
        }, secure=True)

    def test_the_acn_really_is_rejected_server_side(self):
        response = self.submit_with_a_bad_acn()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "check digit")

    def test_the_search_box_still_shows_the_picked_client(self):
        response = self.submit_with_a_bad_acn()
        body = response.content.decode()
        self.assertIn('id="jtClientSearch"', body)
        # The whole tag, not one line of it: the value attribute sits on the next
        # line in the template and a line-wise check silently misses it.
        tag_start = body.index('<input type="text" class="form-control" id="jtClientSearch"')
        tag = body[tag_start:body.index(">", tag_start)]
        self.assertIn(PICKED_NAME, tag, f"search box did not carry the name back: {tag}")

    def test_the_linked_label_shows_a_name_not_a_bare_uuid(self):
        response = self.submit_with_a_bad_acn()
        body = response.content.decode()
        label = body.split('id="jtPicked"')[1].split("</span>")[0]
        self.assertIn(PICKED_NAME, label)
        self.assertNotIn(XPM_ID, label)
