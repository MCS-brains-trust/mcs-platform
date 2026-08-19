# core/tests_entity_by_xpm.py
"""JT hands SH an XPM client id; SH resolves it to one of its own entities.

JT never learns SH's entity UUIDs — it passes the XPM id it already holds on
Client.xpmId. One XPM client can legitimately own several SH entities, so all
three branches are real, not defensive.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from accounts.oidc_views import ENTRA_SESSION_KEY
from core.models import Entity

XPM_ID = "aaaaaaaa-0000-0000-0000-000000000009"

# These branches render real templates, and prod uses whitenoise's manifest
# storage — without this every render raises "Missing staticfiles manifest
# entry". Same override the bank/TB suites use.
STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class EntityByXpmTests(TestCase):
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

    def _entity(self, name, xpm_client_id=XPM_ID, **kwargs):
        return Entity.objects.create(
            entity_name=name, entity_type="company", xpm_client_id=xpm_client_id,
            assigned_accountant=self.user, **kwargs
        )

    def url(self, xpm_id=XPM_ID):
        return reverse("core:entity_by_xpm", args=[xpm_id])

    def test_single_linked_entity_redirects_to_its_detail_page(self):
        entity = self._entity("Hazaway Pty Ltd")
        response = self.client.get(self.url(), secure=True)
        self.assertRedirects(
            response, reverse("core:entity_detail", args=[entity.pk]),
            fetch_redirect_response=False,
        )

    def test_no_linked_entity_renders_the_unlinked_page(self):
        response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/entity_by_xpm_unlinked.html")
        self.assertContains(response, "No StatementHub entity is linked")

    def test_several_linked_entities_render_a_chooser(self):
        self._entity("Hazaway Pty Ltd")
        self._entity("Hazaway Family Trust")
        response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/entity_by_xpm_chooser.html")
        self.assertContains(response, "Hazaway Pty Ltd")
        self.assertContains(response, "Hazaway Family Trust")

    def test_archived_entities_are_not_resolved(self):
        self._entity("Old Co", is_archived=True)
        response = self.client.get(self.url(), secure=True)
        self.assertTemplateUsed(response, "core/entity_by_xpm_unlinked.html")

    def test_a_restricted_user_does_not_resolve_someone_elses_entity(self):
        other = User.objects.create_user(
            username="lyn", email="lyn@mcands.com.au", password="x" * 14,
            role=User.Role.ACCOUNTANT,
        )
        Entity.objects.create(
            entity_name="Not Mine", entity_type="trust", xpm_client_id=XPM_ID,
            assigned_accountant=self.user,
        )
        self.client.force_login(other)
        session = self.client.session
        session[ENTRA_SESSION_KEY] = True
        session["2fa_verified"] = True
        session.save()
        response = self.client.get(self.url(), secure=True)
        self.assertTemplateUsed(response, "core/entity_by_xpm_unlinked.html")

    def test_blank_xpm_id_never_matches_the_unpopulated_rows(self):
        # Entity.xpm_client_id is blank on almost every production row today.
        # A blank lookup must not resolve to "all of them".
        Entity.objects.create(
            entity_name="Unlinked Co", entity_type="company", xpm_client_id="",
            assigned_accountant=self.user,
        )
        response = self.client.get(self.url("%20"), secure=True)
        self.assertTemplateUsed(response, "core/entity_by_xpm_unlinked.html")

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url(), secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
