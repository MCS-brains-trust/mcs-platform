"""
Regression tests for defects found while importing Berwick Mechanical
Services FY2025 from QuickBooks on 2026-08-18.

1. The review wizard reported "Needs Mapping: 0" while every row was in
   fact missing its statement-line mapping, so the Confirm button stayed
   disabled with no on-screen explanation.
2. Reconnecting a QuickBooks company through the *global* flow dropped the
   entity link, leaving the entity's Software tab reading a stale row on a
   disconnected connection.
"""

from datetime import date
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import Entity, FinancialYear, StagedImport
from integrations.models import QBGlobalConnection, QBTenant
from integrations.views import qb_global_callback, review_import


def _prepare_request(request, user):
    request.user = user
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


def _staged_line(code, name, debit="0", credit="0", mapped_id="", entity_acct_code=""):
    return {
        "account_code": code,
        "account_name": name,
        "debit": debit,
        "credit": credit,
        "movement_amount": debit,
        "mapped_id": mapped_id,
        "mapped_label": "",
        "confidence": "matched" if entity_acct_code else "new",
        "entity_acct_code": entity_acct_code,
        "entity_acct_name": name if entity_acct_code else "",
    }


class _ReviewImportBase(TestCase):
    """Shared setup for the review-wizard tests (holds no tests itself)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="wizard",
            email="wizard@example.com",
            password="secret123",
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        self.entity = Entity.objects.create(entity_name="Berwick Mechanical")
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

    def _render(self, lines):
        StagedImport.objects.create(
            financial_year=self.fy,
            user=self.user,
            provider_name="QuickBooks",
            import_mode="trial_balance",
            as_at_date=self.fy.end_date,
            lines=lines,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()
        return self.client.get(
            reverse("integrations:review_import", kwargs={"fy_pk": self.fy.pk}),
            secure=True,
        )

class ReviewImportUnmappedCountTests(_ReviewImportBase):
    """A row with an entity COA code but no statement line is NOT mapped."""

    def test_entity_coa_code_alone_does_not_count_as_mapped(self):
        # Exactly the Berwick case: the source code matched an existing
        # entity COA entry, but no statement line was ever assigned.
        response = self._render([
            _staged_line("4", "Sales", credit="527439.26", entity_acct_code="4"),
            _staged_line("62", "Purchases", debit="220001.82", entity_acct_code="62"),
        ])

        self.assertEqual(response.context["unmapped"], 2)
        self.assertEqual(response.context["auto_mapped"], 0)

    def test_statement_line_mapping_counts_as_mapped(self):
        response = self._render([
            _staged_line(
                "4", "Sales", credit="527439.26",
                mapped_id="00000000-0000-0000-0000-000000000001",
                entity_acct_code="4",
            ),
        ])

        self.assertEqual(response.context["unmapped"], 0)
        self.assertEqual(response.context["auto_mapped"], 1)


class QBGlobalCallbackEntityLinkTests(TestCase):
    """Reconnecting a QB company must not orphan its entity link."""

    REALM = "193514898526184"

    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="qbconn",
            email="qbconn@example.com",
            password="secret123",
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        self.entity = Entity.objects.create(entity_name="Berwick Mechanical")

    def _run_callback(self, state="state-123"):
        request = self.factory.get(
            reverse("integrations:qb_global_callback"),
            data={"code": "auth-code", "state": state, "realmId": self.REALM},
        )
        _prepare_request(request, self.user)
        request.session["qb_global_oauth_state"] = state
        request.session.save()

        token_response = Mock(status_code=200)
        token_response.raise_for_status = Mock()
        token_response.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }
        info_response = Mock(status_code=200)
        info_response.json.return_value = {
            "CompanyInfo": {"CompanyName": "Berwick Mechanical Services Pty Ltd"}
        }

        with patch("integrations.views.http_requests.post", return_value=token_response), \
             patch("integrations.views.http_requests.get", return_value=info_response):
            return qb_global_callback(request)

    def test_reconnect_carries_link_from_disconnected_connection(self):
        # The exact production shape: the only row holding the entity link
        # sits on a connection that has since been disconnected.
        old_conn = QBGlobalConnection.objects.create(
            status="disconnected", access_token="", refresh_token=""
        )
        QBTenant.objects.create(
            connection=old_conn,
            realm_id=self.REALM,
            company_name="Berwick Mechanical Services Pty Ltd",
            entity=self.entity,
        )
        QBGlobalConnection.objects.create(
            status="active", access_token="a", refresh_token="r"
        )

        self._run_callback()

        active = QBTenant.objects.get(
            connection__status="active", realm_id=self.REALM
        )
        self.assertEqual(active.entity_id, self.entity.pk)

    def test_reconnect_does_not_clear_existing_link_on_same_row(self):
        conn = QBGlobalConnection.objects.create(
            status="active", access_token="a", refresh_token="r"
        )
        QBTenant.objects.create(
            connection=conn,
            realm_id=self.REALM,
            company_name="Berwick Mechanical Services Pty Ltd",
            entity=self.entity,
        )

        self._run_callback()

        tenant = QBTenant.objects.get(connection=conn, realm_id=self.REALM)
        self.assertEqual(tenant.entity_id, self.entity.pk)


class ReviewImportUnmappedWarningTests(_ReviewImportBase):
    """The wizard must show *why* the Confirm button is disabled.

    import_wizard.js writes its explanation into #unmappedWarning; the
    element was missing from the template, so an operator saw a dead
    button and no reason for it.
    """

    def test_template_renders_the_unmapped_warning_element(self):
        response = self._render([
            _staged_line("4", "Sales", credit="527439.26", entity_acct_code="4"),
        ])

        self.assertContains(response, 'id="unmappedWarning"')
