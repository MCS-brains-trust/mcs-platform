"""The "Import from software" button on the financial-year page.

It was a hard-coded disabled placeholder titled "Software integration coming
soon", in all three places it appeared, while the cloud import flow behind it
(integrations.select_provider_import and the provider views) was implemented
and reachable by URL. Nothing in any template linked to it, so linking an
entity to Xero appeared to achieve nothing: the button never changed.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Entity, FinancialYear
from core.views import _linked_cloud_providers
from integrations.models import XeroGlobalConnection, XeroTenant


class LinkedCloudProviderTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(entity_name="Cloudworth Pty Ltd",
                                            entity_type="company")

    def _connection(self, status="active"):
        return XeroGlobalConnection.objects.create(
            status=status,
            access_token="x", refresh_token="y",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )

    def test_an_entity_with_no_link_offers_nothing(self):
        self.assertEqual(_linked_cloud_providers(self.entity), [])

    def test_a_linked_tenant_on_an_active_connection_offers_xero(self):
        XeroTenant.objects.create(
            connection=self._connection(), entity=self.entity,
            tenant_id="t-1", tenant_name="Cloudworth Pty Ltd")
        self.assertEqual(_linked_cloud_providers(self.entity), ["Xero"])

    def test_a_connection_that_is_not_active_offers_nothing(self):
        """A stale authorisation cannot import, and the provider page would
        have nothing to show -- there are four disconnected Xero connections
        on prod alongside the live one, so this is the normal state of things
        rather than an edge case."""
        XeroTenant.objects.create(
            connection=self._connection(status="disconnected"), entity=self.entity,
            tenant_id="t-2", tenant_name="Cloudworth Pty Ltd")
        self.assertEqual(_linked_cloud_providers(self.entity), [])

    def test_a_tenant_linked_to_someone_else_is_not_ours(self):
        other = Entity.objects.create(entity_name="Someone Else Pty Ltd",
                                      entity_type="company")
        XeroTenant.objects.create(
            connection=self._connection(), entity=other,
            tenant_id="t-3", tenant_name="Someone Else Pty Ltd")
        self.assertEqual(_linked_cloud_providers(self.entity), [])


# Rendering this page needs static files resolved, and the manifest storage
# only has a manifest where collectstatic has run -- true of the deployed
# checkout, not of a fresh worktree or a CI workspace. The plain backend keeps
# the test about the button rather than about the environment it runs in.
@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class ImportFromSoftwareButtonTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cloudbtn", email="cloudbtn@example.com", password="secret123",
            role="admin", totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.entity = Entity.objects.create(entity_name="Cloudworth Pty Ltd",
                                            entity_type="company")
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
        )

    def _get(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()
        return self.client.get(
            reverse("core:financial_year_detail", kwargs={"pk": self.fy.pk}),
            secure=True)

    def test_the_placeholder_wording_is_gone(self):
        self.assertNotContains(self._get(), "Software integration coming soon")

    def test_a_linked_entity_gets_a_link_to_the_import_flow(self):
        connection = XeroGlobalConnection.objects.create(
            status="active", access_token="x", refresh_token="y",
            token_expires_at=timezone.now() + timedelta(hours=1))
        XeroTenant.objects.create(connection=connection, entity=self.entity,
                                  tenant_id="t-1", tenant_name="Cloudworth Pty Ltd")
        response = self._get()
        self.assertContains(response, reverse(
            "integrations:select_provider_import", kwargs={"fy_pk": self.fy.pk}))

    def test_an_unlinked_entity_is_sent_to_the_connections_hub(self):
        """Rather than a dead control: the reason it cannot import is that
        nothing is linked, so the button says so and goes where that is fixed."""
        response = self._get()
        self.assertNotContains(response, reverse(
            "integrations:select_provider_import", kwargs={"fy_pk": self.fy.pk}))
        self.assertContains(response, reverse("integrations:connections_hub"))

    def test_a_finalised_year_still_refuses_but_says_why(self):
        self.fy.status = "finalised"
        self.fy.save()
        response = self._get()
        self.assertContains(response, "Change status to Draft to import a trial balance")
