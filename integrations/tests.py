from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import Entity, FinancialYear
from integrations.models import XeroGlobalConnection, XeroTenant
from integrations.providers import XeroProvider
from integrations.views import xero_select_tenant_import


class XeroProviderPeriodMovementTests(TestCase):
    def setUp(self):
        self.provider = XeroProvider()

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_parses_net_movement_rows(self, mock_get):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "Reports": [
                {
                    "ReportName": "General Ledger Summary",
                    "ReportDate": "30 June 2025",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {"Value": "Sales"},
                                {"Value": "200"},
                                {"Value": "0.00"},
                                {"Value": "1250.00"},
                                {"Value": "-1250.00"},
                                {"Value": "Revenue"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {"Value": "Bank Fees"},
                                {"Value": "610"},
                                {"Value": "80.00"},
                                {"Value": "0.00"},
                                {"Value": "80.00"},
                                {"Value": "Expense"},
                            ],
                        },
                    ],
                }
            ]
        }
        mock_get.return_value = response

        lines = self.provider.fetch_period_movement(
            "token",
            "tenant-1",
            date(2024, 7, 1),
            date(2025, 6, 30),
        )

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["account_code"], "200")
        self.assertEqual(lines[0]["movement_amount"], Decimal("-1250.00"))
        self.assertEqual(lines[0]["debit"], Decimal("0"))
        self.assertEqual(lines[0]["credit"], Decimal("1250.00"))
        self.assertEqual(lines[1]["movement_amount"], Decimal("80.00"))
        self.assertEqual(lines[1]["debit"], Decimal("80.00"))
        self.assertEqual(lines[1]["credit"], Decimal("0"))

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_raises_for_empty_rows(self, mock_get):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "Reports": [
                {
                    "ReportName": "General Ledger Summary",
                    "ReportDate": "30 June 2025",
                    "Rows": [{"RowType": "SummaryRow", "Cells": []}],
                }
            ]
        }
        mock_get.return_value = response

        with self.assertRaisesMessage(ValueError, "no usable General Ledger Summary account rows"):
            self.provider.fetch_period_movement(
                "token",
                "tenant-1",
                date(2024, 7, 1),
                date(2025, 6, 30),
            )


class XeroPeriodImportViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="tester",
            email="tester@example.com",
            password="secret123",
        )
        self.entity = Entity.objects.create(entity_name="Li Penman Trust")
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        self.global_conn = XeroGlobalConnection.objects.create(
            status="active",
            access_token="token",
            refresh_token="refresh",
        )
        self.tenant = XeroTenant.objects.create(
            global_connection=self.global_conn,
            tenant_id="tenant-1",
            tenant_name="000 Pool Care",
        )

    def _add_session(self, request):
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        return request

    @patch("integrations.views._ensure_global_xero_token", return_value=True)
    @patch("integrations.views._do_cloud_import")
    def test_xero_select_tenant_import_posts_period_movement_dates(self, mock_import, mock_ensure):
        request = self.factory.post(
            reverse("integrations:xero_select_tenant_import", kwargs={"fy_pk": self.fy.pk}),
            data={
                "tenant_id": self.tenant.tenant_id,
                "link_tenant": "1",
                "import_mode": "period_movement",
                "from_date": "2024-07-01",
                "to_date": "2025-06-30",
            },
        )
        request.user = self.user
        self._add_session(request)

        response = xero_select_tenant_import(request, self.fy.pk)

        self.assertEqual(response.status_code, 200)
        mock_import.assert_called_once()
        _, kwargs = mock_import.call_args
        self.assertEqual(kwargs["import_mode"], "period_movement")
        self.assertEqual(kwargs["from_date"], date(2024, 7, 1))
        self.assertEqual(kwargs["to_date"], date(2025, 6, 30))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.entity_id, self.entity.pk)

    @patch("integrations.views._ensure_global_xero_token", return_value=True)
    def test_xero_select_tenant_import_rejects_missing_dates(self, mock_ensure):
        request = self.factory.post(
            reverse("integrations:xero_select_tenant_import", kwargs={"fy_pk": self.fy.pk}),
            data={
                "tenant_id": self.tenant.tenant_id,
                "import_mode": "period_movement",
                "from_date": "",
                "to_date": "2025-06-30",
            },
        )
        request.user = self.user
        self._add_session(request)

        response = xero_select_tenant_import(request, self.fy.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("integrations:xero_select_tenant_import", kwargs={"fy_pk": self.fy.pk}), response.url)
        messages = [m.message for m in get_messages(request)]
        self.assertTrue(any("choose both a from date and a to date" in m for m in messages))
