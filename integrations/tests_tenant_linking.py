"""
Tests for attaching an already-connected accounting file to an entity.

Before this, the OAuth callback was the only writer of QBTenant.entity /
XeroTenant.entity, so a company that was never linked during its own
authorisation could not be attached at all — the wizard told you to "link a
QuickBooks company first" and no screen could do it.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Entity
from integrations.models import (
    QBGlobalConnection,
    QBTenant,
    XeroGlobalConnection,
    XeroTenant,
)


class _LinkingBase(TestCase):
    """Shared fixtures (holds no tests itself)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="linker",
            email="linker@example.com",
            password="secret123",
            role="admin",
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        self.entity = Entity.objects.create(entity_name="Berwick Mechanical")
        self.other_entity = Entity.objects.create(entity_name="Liebac Unit Trust")

        self.qb_conn = QBGlobalConnection.objects.create(
            status="active", access_token="a", refresh_token="r"
        )
        self.qb = QBTenant.objects.create(
            connection=self.qb_conn,
            realm_id="193514898526184",
            company_name="Berwick Mechanical Services Pty Ltd",
        )
        self.xero_conn = XeroGlobalConnection.objects.create(
            status="active", access_token="a", refresh_token="r"
        )
        self.xero = XeroTenant.objects.create(
            connection=self.xero_conn,
            tenant_id="xero-tenant-1",
            tenant_name="Berwick Mechanical Services",
        )

    def _login(self, user=None):
        self.client.force_login(user or self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _url(self, entity=None):
        return reverse("integrations:link_tenant")

    def _post(self, **data):
        data.setdefault("entity_pk", str(data.pop("entity", self.entity).pk
                                          if "entity" in data else self.entity.pk))
        return self.client.post(self._url(), data, secure=True)

class TenantLinkingTests(_LinkingBase):
    # -- linking ---------------------------------------------------------

    def test_links_a_quickbooks_company_to_the_entity(self):
        self._login()

        self._post(provider="quickbooks", tenant_pk=str(self.qb.pk), confirm="1")

        self.qb.refresh_from_db()
        self.assertEqual(self.qb.entity_id, self.entity.pk)

    def test_links_a_xero_organisation_to_the_entity(self):
        self._login()

        self._post(provider="xero", tenant_pk=str(self.xero.pk), confirm="1")

        self.xero.refresh_from_db()
        self.assertEqual(self.xero.entity_id, self.entity.pk)

    def test_linking_xero_leaves_the_quickbooks_link_alone(self):
        self.qb.entity = self.entity
        self.qb.save(update_fields=["entity"])
        self._login()

        self._post(provider="xero", tenant_pk=str(self.xero.pk), confirm="1")

        self.qb.refresh_from_db()
        self.assertEqual(self.qb.entity_id, self.entity.pk)

    # -- displacement ----------------------------------------------------

    def test_confirmed_link_displaces_the_entitys_previous_company(self):
        previous = QBTenant.objects.create(
            connection=self.qb_conn,
            realm_id="old-realm",
            company_name="Stale Co",
            entity=self.entity,
        )
        self._login()

        self._post(provider="quickbooks", tenant_pk=str(self.qb.pk), confirm="1")

        previous.refresh_from_db()
        self.qb.refresh_from_db()
        self.assertIsNone(previous.entity_id)
        self.assertEqual(self.qb.entity_id, self.entity.pk)

    def test_confirmed_link_takes_the_company_from_another_entity(self):
        self.qb.entity = self.other_entity
        self.qb.save(update_fields=["entity"])
        self._login()

        self._post(provider="quickbooks", tenant_pk=str(self.qb.pk), confirm="1")

        self.qb.refresh_from_db()
        self.assertEqual(self.qb.entity_id, self.entity.pk)

    def test_unconfirmed_displacement_changes_nothing(self):
        self.qb.entity = self.other_entity
        self.qb.save(update_fields=["entity"])
        self._login()

        response = self._post(provider="quickbooks", tenant_pk=str(self.qb.pk))

        self.qb.refresh_from_db()
        self.assertEqual(self.qb.entity_id, self.other_entity.pk)
        self.assertContains(response, "Liebac Unit Trust")

    def test_link_with_nothing_to_displace_needs_no_confirmation(self):
        self._login()

        self._post(provider="quickbooks", tenant_pk=str(self.qb.pk))

        self.qb.refresh_from_db()
        self.assertEqual(self.qb.entity_id, self.entity.pk)

    # -- unlinking -------------------------------------------------------

    def test_unlink_clears_the_link(self):
        self.qb.entity = self.entity
        self.qb.save(update_fields=["entity"])
        self._login()

        self._post(provider="quickbooks", tenant_pk="", confirm="1")

        self.qb.refresh_from_db()
        self.assertIsNone(self.qb.entity_id)

    # -- permissions -----------------------------------------------------

    def test_read_only_user_cannot_link(self):
        reader = get_user_model().objects.create_user(
            username="reader",
            email="reader@example.com",
            password="secret123",
            role="read_only",
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        self._login(reader)

        self._post(provider="quickbooks", tenant_pk=str(self.qb.pk), confirm="1")

        self.qb.refresh_from_db()
        self.assertIsNone(self.qb.entity_id)

    def test_anonymous_user_cannot_link(self):
        self.client.post(
            self._url(),
            {
                "entity_pk": str(self.entity.pk),
                "provider": "quickbooks",
                "tenant_pk": str(self.qb.pk),
                "confirm": "1",
            },
            secure=True,
        )

        self.qb.refresh_from_db()
        self.assertIsNone(self.qb.entity_id)


class TenantLinkingUITests(_LinkingBase):
    """Both entry points must offer the link, not just the endpoint."""

    def test_entity_connections_page_lists_connectable_files(self):
        self._login()

        response = self.client.get(
            reverse(
                "integrations:connection_manage",
                kwargs={"entity_pk": self.entity.pk},
            ),
            secure=True,
        )

        self.assertContains(response, "Berwick Mechanical Services Pty Ltd")
        self.assertContains(response, str(self.qb.pk))
        self.assertContains(response, str(self.xero.pk))

    def test_entity_connections_page_shows_the_current_link(self):
        self.qb.entity = self.entity
        self.qb.save(update_fields=["entity"])
        self._login()

        response = self.client.get(
            reverse(
                "integrations:connection_manage",
                kwargs={"entity_pk": self.entity.pk},
            ),
            secure=True,
        )

        self.assertContains(response, "Unlink")

    def test_quickbooks_dashboard_offers_entity_assignment(self):
        self._login()

        response = self.client.get(
            reverse("integrations:qb_global_dashboard"), secure=True
        )

        # The row offers this entity as an assignment target, posting to the
        # linking endpoint.
        self.assertContains(response, reverse("integrations:link_tenant"))
        self.assertContains(response, f'value="{self.entity.pk}"')

    def test_xero_dashboard_offers_entity_assignment(self):
        self._login()

        response = self.client.get(
            reverse("integrations:xero_global_dashboard"), secure=True
        )

        self.assertContains(response, reverse("integrations:link_tenant"))
        self.assertContains(response, f'value="{self.entity.pk}"')
