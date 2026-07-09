from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import Entity, FinancialYear
from integrations.models import QBGlobalConnection, QBTenant, XeroGlobalConnection, XeroTenant
from integrations.providers import ProviderUserError, QuickBooksProvider, XeroProvider
from integrations.views import qb_select_tenant_import, xero_select_tenant_import


def _mock_response(payload, status_code=200):
    """Build a Mock that looks like a requests.Response with a JSON body."""
    response = Mock()
    response.status_code = status_code
    response.ok = status_code < 400
    response.raise_for_status = Mock()
    response.json.return_value = payload
    return response


def _xero_row(account_label, account_code, *amounts):
    """Build a Xero TrialBalance account Row with the given amount cells."""
    cells = [
        {
            "Value": account_label,
            "Attributes": [{"Id": "account", "Value": account_code}],
        }
    ]
    cells.extend({"Value": amount} for amount in amounts)
    return {"RowType": "Row", "Cells": cells}


class XeroProviderPeriodMovementTests(TestCase):
    """XeroProvider.fetch_period_movement issues two Reports/TrialBalance
    calls (period with fromDate, opening as at day-before-from_date) and
    merges them per account_code into period_*/opening_* figures."""

    # Call A: date=to_date + fromDate=from_date → 5-column report where the
    # YTD Debit / YTD Credit columns carry the requested-period figures and
    # the plain Debit/Credit columns only carry the final month (must be
    # ignored by the parser).
    PERIOD_REPORT = {
        "Reports": [
            {
                "ReportName": "Trial Balance",
                "ReportDate": "30 June 2025",
                "Rows": [
                    {
                        "RowType": "Header",
                        "Cells": [
                            {"Value": "Account"},
                            {"Value": "Debit"},
                            {"Value": "Credit"},
                            {"Value": "YTD Debit"},
                            {"Value": "YTD Credit"},
                        ],
                    },
                    {
                        "RowType": "Section",
                        "Title": "Revenue",
                        "Rows": [
                            # final-month Credit of 99.00 must NOT be used
                            _xero_row("Sales (200)", "200", "0.00", "99.00", "0.00", "1250.00"),
                        ],
                    },
                    {
                        "RowType": "Section",
                        "Title": "Liabilities",
                        "Rows": [
                            _xero_row("GST (820)", "820", "0.00", "0.00", "0.00", "45.00"),
                        ],
                    },
                    {
                        "RowType": "Section",
                        "Title": "Total",
                        "Rows": [{"RowType": "SummaryRow", "Cells": []}],
                    },
                ],
            }
        ]
    }

    # Call B: date=(from_date - 1 day), no fromDate → 3-column report whose
    # plain Debit/Credit columns carry the cumulative opening position.
    OPENING_REPORT = {
        "Reports": [
            {
                "ReportName": "Trial Balance",
                "ReportDate": "30 June 2024",
                "Rows": [
                    {
                        "RowType": "Header",
                        "Cells": [
                            {"Value": "Account"},
                            {"Value": "Debit"},
                            {"Value": "Credit"},
                        ],
                    },
                    {
                        "RowType": "Section",
                        "Title": "Assets",
                        "Rows": [
                            _xero_row("Business Bank Account (090)", "090", "500.00", "0.00"),
                        ],
                    },
                    {
                        "RowType": "Section",
                        "Title": "Liabilities",
                        "Rows": [
                            _xero_row("GST (820)", "820", "0.00", "30.00"),
                        ],
                    },
                ],
            }
        ]
    }

    def setUp(self):
        self.provider = XeroProvider()

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_merges_period_and_opening_reports(self, mock_get):
        mock_get.side_effect = [
            _mock_response(self.PERIOD_REPORT),
            _mock_response(self.OPENING_REPORT),
        ]

        lines = self.provider.fetch_period_movement(
            "token",
            "tenant-1",
            date(2024, 7, 1),
            date(2025, 6, 30),
        )

        # Two TrialBalance calls: period (with fromDate) then opening
        # (as at the day before from_date, without fromDate).
        self.assertEqual(mock_get.call_count, 2)
        period_params = mock_get.call_args_list[0].kwargs["params"]
        self.assertEqual(period_params["date"], "2025-06-30")
        self.assertEqual(period_params["fromDate"], "2024-07-01")
        self.assertEqual(period_params["paymentsOnly"], "false")
        opening_params = mock_get.call_args_list[1].kwargs["params"]
        self.assertEqual(opening_params["date"], "2024-06-30")
        self.assertNotIn("fromDate", opening_params)

        by_code = {line["account_code"]: line for line in lines}
        self.assertEqual(set(by_code), {"200", "820", "090"})

        # P&L account present only in the period report.
        sales = by_code["200"]
        self.assertEqual(sales["account_name"], "Sales (200)")
        self.assertEqual(sales["period_debit"], Decimal("0"))
        # YTD Credit (1250.00), NOT the final-month Credit column (99.00).
        self.assertEqual(sales["period_credit"], Decimal("1250.00"))
        self.assertEqual(sales["opening_debit"], Decimal("0"))
        self.assertEqual(sales["opening_credit"], Decimal("0"))

        # Balance-sheet account present in both reports gets merged.
        gst = by_code["820"]
        self.assertEqual(gst["period_credit"], Decimal("45.00"))
        self.assertEqual(gst["opening_credit"], Decimal("30.00"))
        self.assertEqual(gst["period_debit"], Decimal("0"))
        self.assertEqual(gst["opening_debit"], Decimal("0"))

        # Account present only in the opening report keeps zero period figures.
        bank = by_code["090"]
        self.assertEqual(bank["account_name"], "Business Bank Account (090)")
        self.assertEqual(bank["opening_debit"], Decimal("500.00"))
        self.assertEqual(bank["opening_credit"], Decimal("0"))
        self.assertEqual(bank["period_debit"], Decimal("0"))
        self.assertEqual(bank["period_credit"], Decimal("0"))

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_returns_empty_for_reports_without_account_rows(self, mock_get):
        empty_report = {
            "Reports": [
                {
                    "ReportName": "Trial Balance",
                    "ReportDate": "30 June 2025",
                    "Rows": [{"RowType": "SummaryRow", "Cells": []}],
                }
            ]
        }
        mock_get.side_effect = [
            _mock_response(empty_report),
            _mock_response(empty_report),
        ]

        lines = self.provider.fetch_period_movement(
            "token",
            "tenant-1",
            date(2024, 7, 1),
            date(2025, 6, 30),
        )

        self.assertEqual(lines, [])

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_raises_user_error_on_expired_token(self, mock_get):
        mock_get.return_value = _mock_response({}, status_code=401)

        with self.assertRaisesMessage(ProviderUserError, "Xero authorisation expired"):
            self.provider.fetch_period_movement(
                "token",
                "tenant-1",
                date(2024, 7, 1),
                date(2025, 6, 30),
            )

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_raises_user_error_on_missing_scope(self, mock_get):
        mock_get.return_value = _mock_response({}, status_code=403)

        with self.assertRaisesMessage(ProviderUserError, "accounting.reports.read"):
            self.provider.fetch_period_movement(
                "token",
                "tenant-1",
                date(2024, 7, 1),
                date(2025, 6, 30),
            )


class QuickBooksProviderPeriodMovementTests(TestCase):
    """QuickBooksProvider.fetch_period_movement orchestrates three calls:
    an Account query (code map), a TrialBalance (debit/credit side per
    account) and a GeneralLedger (net activity per account section)."""

    ACCOUNTS_PAYLOAD = {
        "QueryResponse": {
            "Account": [
                {"Id": "1", "AcctNum": "1150", "Name": "ATO Clearing Account"},
                {"Id": "2", "Name": "Sales"},  # no AcctNum → falls back to Id
            ]
        }
    }

    TB_PAYLOAD = {
        "Rows": {
            "Row": [
                {
                    "type": "Data",
                    "ColData": [
                        {"value": "ATO Clearing Account", "id": "1"},
                        {"value": "137077.06"},
                        {"value": ""},
                    ],
                },
                {
                    "type": "Section",
                    "Rows": {
                        "Row": [
                            {
                                "type": "Data",
                                "ColData": [
                                    {"value": "Sales", "id": "2"},
                                    {"value": ""},
                                    {"value": "345304.15"},
                                ],
                            }
                        ]
                    },
                },
            ]
        }
    }

    @staticmethod
    def _gl_data_row(*, label="", amount="", balance=""):
        """Build a 9-column GeneralLedger Data row (amount=idx 7, balance=idx 8)."""
        values = [label, "", "", "", "", "", "", amount, balance]
        return {"type": "Data", "ColData": [{"value": v} for v in values]}

    @classmethod
    def _gl_payload(cls):
        return {
            "Rows": {
                "Row": [
                    {
                        "type": "Section",
                        "Header": {
                            "ColData": [{"value": "ATO Clearing Account", "id": "1"}]
                        },
                        "Rows": {
                            "Row": [
                                cls._gl_data_row(
                                    label="Beginning Balance", balance="104252.06"
                                ),
                                cls._gl_data_row(
                                    label="2024-07-16",
                                    amount="32825.00",
                                    balance="137077.06",
                                ),
                            ]
                        },
                        "Summary": {
                            "ColData": [{"value": "Total for ATO Clearing Account"}]
                        },
                    },
                    {
                        "type": "Section",
                        "Header": {"ColData": [{"value": "Sales", "id": "2"}]},
                        "Rows": {
                            "Row": [
                                cls._gl_data_row(
                                    label="2024-08-01",
                                    amount="345304.15",
                                    balance="345304.15",
                                ),
                            ]
                        },
                    },
                    {
                        # Section with zero net activity must be skipped.
                        "type": "Section",
                        "Header": {"ColData": [{"value": "Dormant Account", "id": "9"}]},
                        "Rows": {
                            "Row": [cls._gl_data_row(label="2024-09-01")]
                        },
                    },
                ]
            }
        }

    def setUp(self):
        self.provider = QuickBooksProvider()

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_parses_gl_sections_with_tb_sides(self, mock_get):
        mock_get.side_effect = [
            _mock_response(self.ACCOUNTS_PAYLOAD),
            _mock_response(self.TB_PAYLOAD),
            _mock_response(self._gl_payload()),
        ]

        lines = self.provider.fetch_period_movement(
            "token",
            "realm-1",
            date(2025, 7, 1),
            date(2026, 3, 14),
        )

        # Account query → TrialBalance → GeneralLedger, in that order.
        self.assertEqual(mock_get.call_count, 3)
        urls = [c.args[0] for c in mock_get.call_args_list]
        self.assertTrue(urls[0].endswith("/company/realm-1/query"))
        self.assertTrue(urls[1].endswith("/company/realm-1/reports/TrialBalance"))
        self.assertTrue(urls[2].endswith("/company/realm-1/reports/GeneralLedger"))
        gl_params = mock_get.call_args_list[2].kwargs["params"]
        self.assertEqual(gl_params["start_date"], "2025-07-01")
        self.assertEqual(gl_params["end_date"], "2026-03-14")

        self.assertEqual(len(lines), 2)

        ato = lines[0]
        # AcctNum wins over the internal QBO Id.
        self.assertEqual(ato["account_code"], "1150")
        self.assertEqual(ato["account_name"], "ATO Clearing Account")
        self.assertEqual(ato["opening_balance"], Decimal("104252.06"))
        # TB side is debit and ending balance positive → debit convention.
        self.assertEqual(ato["debit"], Decimal("32825.00"))
        self.assertEqual(ato["credit"], Decimal("0"))
        self.assertEqual(ato["movement_amount"], Decimal("32825.00"))

        sales = lines[1]
        # No AcctNum → falls back to the internal Id.
        self.assertEqual(sales["account_code"], "2")
        self.assertEqual(sales["account_name"], "Sales")
        self.assertEqual(sales["opening_balance"], Decimal("0"))
        # TB side is credit → positive net activity lands on the credit side.
        self.assertEqual(sales["debit"], Decimal("0"))
        self.assertEqual(sales["credit"], Decimal("345304.15"))
        self.assertEqual(sales["movement_amount"], Decimal("345304.15"))

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_ignores_top_level_detail_rows(self, mock_get):
        # GeneralLedger payloads whose top level only contains loose Data
        # rows (no account Sections) yield no account lines.
        gl_payload = {
            "Rows": {
                "Row": [
                    self._gl_data_row(
                        label="2024-07-16", amount="32825.00", balance="137077.06"
                    )
                ]
            }
        }
        mock_get.side_effect = [
            _mock_response({"QueryResponse": {}}),
            _mock_response({"Rows": {}}),
            _mock_response(gl_payload),
        ]

        lines = self.provider.fetch_period_movement(
            "token",
            "realm-1",
            date(2025, 7, 1),
            date(2026, 3, 14),
        )

        self.assertEqual(lines, [])

    @patch("integrations.providers.requests.get")
    def test_fetch_period_movement_raises_user_error_on_expired_token(self, mock_get):
        mock_get.return_value = _mock_response({}, status_code=401)

        with self.assertRaisesMessage(ProviderUserError, "QuickBooks token has expired"):
            self.provider.fetch_period_movement(
                "token",
                "realm-1",
                date(2025, 7, 1),
                date(2026, 3, 14),
            )


def _prepare_request(request, user):
    """Attach user, session and message storage to a RequestFactory request."""
    request.user = user
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


class QuickBooksPeriodImportViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="qbtester",
            email="qbtester@example.com",
            password="secret123",
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        self.entity = Entity.objects.create(entity_name="Berwick Mechanical")
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
        )
        self.global_conn = QBGlobalConnection.objects.create(
            status="active",
            access_token="token",
            refresh_token="refresh",
        )
        self.tenant = QBTenant.objects.create(
            connection=self.global_conn,
            realm_id="realm-1",
            company_name="Berwick Mechanical Services Pty Ltd",
            access_token="tenant-token",
            refresh_token="tenant-refresh",
        )

    @patch("integrations.views._ensure_qb_tenant_token", return_value=True)
    @patch("integrations.views._do_cloud_import")
    def test_qb_select_tenant_import_forces_trial_balance_mode(self, mock_import, mock_ensure):
        # The QB view intentionally hard-codes trial_balance mode: any
        # posted import_mode / period dates are ignored.
        request = self.factory.post(
            reverse("integrations:qb_select_tenant_import", kwargs={"fy_pk": self.fy.pk}),
            data={
                "tenant_id": self.tenant.realm_id,
                "link_tenant": "1",
                "import_mode": "period_movement",
                "from_date": "2025-07-01",
                "to_date": "2026-03-14",
            },
        )
        _prepare_request(request, self.user)

        response = qb_select_tenant_import(request, self.fy.pk)

        self.assertIs(response, mock_import.return_value)
        mock_import.assert_called_once()
        args, kwargs = mock_import.call_args
        self.assertEqual(args[5], self.tenant.realm_id)
        self.assertEqual(kwargs["import_mode"], "trial_balance")
        self.assertIsNone(kwargs["from_date"])
        self.assertIsNone(kwargs["to_date"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.entity_id, self.entity.pk)

    @patch("integrations.views._ensure_qb_tenant_token", return_value=True)
    def test_qb_select_tenant_import_rejects_missing_tenant(self, mock_ensure):
        request = self.factory.post(
            reverse("integrations:qb_select_tenant_import", kwargs={"fy_pk": self.fy.pk}),
            data={"tenant_id": ""},
        )
        _prepare_request(request, self.user)

        response = qb_select_tenant_import(request, self.fy.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("integrations:qb_select_tenant_import", kwargs={"fy_pk": self.fy.pk}),
            response.url,
        )
        messages = [m.message for m in get_messages(request)]
        self.assertTrue(any("Please select a company" in m for m in messages))


class XeroPeriodImportViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="tester",
            email="tester@example.com",
            password="secret123",
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
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
            connection=self.global_conn,
            tenant_id="tenant-1",
            tenant_name="000 Pool Care",
        )

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
        _prepare_request(request, self.user)

        response = xero_select_tenant_import(request, self.fy.pk)

        self.assertIs(response, mock_import.return_value)
        mock_import.assert_called_once()
        args, kwargs = mock_import.call_args
        self.assertEqual(args[5], self.tenant.tenant_id)
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
        _prepare_request(request, self.user)

        response = xero_select_tenant_import(request, self.fy.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("integrations:xero_select_tenant_import", kwargs={"fy_pk": self.fy.pk}),
            response.url,
        )
        messages = [m.message for m in get_messages(request)]
        self.assertTrue(any("choose both a from date and a to date" in m for m in messages))

    @patch("integrations.views._ensure_global_xero_token", return_value=True)
    def test_xero_select_tenant_import_rejects_from_date_off_fy_start(self, mock_ensure):
        # Period-movement imports must start exactly on the FY start date;
        # any other from_date composes movement onto an undefined opening.
        request = self.factory.post(
            reverse("integrations:xero_select_tenant_import", kwargs={"fy_pk": self.fy.pk}),
            data={
                "tenant_id": self.tenant.tenant_id,
                "import_mode": "period_movement",
                "from_date": "2024-08-01",
                "to_date": "2025-06-30",
            },
        )
        _prepare_request(request, self.user)

        response = xero_select_tenant_import(request, self.fy.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("integrations:xero_select_tenant_import", kwargs={"fy_pk": self.fy.pk}),
            response.url,
        )
        messages = [m.message for m in get_messages(request)]
        self.assertTrue(
            any("must start on the financial year start date" in m for m in messages)
        )
