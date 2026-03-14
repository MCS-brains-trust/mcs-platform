"""
Provider configurations for Xero, MYOB, and QuickBooks Online.

Each provider defines its OAuth2 endpoints, scopes, and the logic
to parse a trial balance response into a normalised list of dicts.
"""
import re
import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS = {}



def register_provider(name):
    """Decorator to register a provider class."""
    def decorator(cls):
        PROVIDERS[name] = cls()
        return cls
    return decorator


class BaseProvider:
    """Base class for accounting platform providers."""

    name = ""
    display_name = ""
    icon_class = ""  # Bootstrap icon class

    # OAuth2 endpoints
    authorize_url = ""
    token_url = ""
    scopes = ""

    supports_period_movement_import = False

    def get_client_id(self):
        raise NotImplementedError

    def get_client_secret(self):
        raise NotImplementedError

    def get_authorize_params(self, redirect_uri, state):
        """Return the query params for the OAuth2 authorization URL."""
        return {
            "response_type": "code",
            "client_id": self.get_client_id(),
            "redirect_uri": redirect_uri,
            "scope": self.scopes,
            "state": state,
        }

    def exchange_code(self, code, redirect_uri):
        """Exchange authorization code for tokens. Returns dict with tokens."""
        raise NotImplementedError

    def refresh_tokens(self, refresh_token):
        """Refresh an expired access token. Returns dict with new tokens."""
        raise NotImplementedError

    def get_tenants(self, access_token):
        """Return list of available tenants/organisations. Each is a dict with id and name."""
        raise NotImplementedError

    def fetch_trial_balance(self, access_token, tenant_id, as_at_date):
        """
        Fetch trial balance from the provider.
        Returns a list of dicts, each with:
            account_code, account_name, opening_balance, debit, credit
        """
        raise NotImplementedError

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        """
        Fetch period movement from the provider.
        Returns a list of dicts, each with:
            account_code, account_name, opening_balance, debit, credit, movement_amount
        """
        raise NotImplementedError

    def is_configured(self):
        """Check if the provider's API credentials are set."""
        try:
            return bool(self.get_client_id() and self.get_client_secret())
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Xero
# ---------------------------------------------------------------------------

@register_provider("xero")
class XeroProvider(BaseProvider):
    name = "xero"
    display_name = "Xero"
    icon_class = "bi-cloud"
    supports_period_movement_import = True

    authorize_url = "https://login.xero.com/identity/connect/authorize"
    token_url = "https://login.xero.com/identity/connect/token"
    scopes = "openid profile email accounting.reports.read accounting.settings.read offline_access"

    def get_client_id(self):
        return getattr(settings, "XERO_CLIENT_ID", "")

    def get_client_secret(self):
        return getattr(settings, "XERO_CLIENT_SECRET", "")

    def get_authorize_params(self, redirect_uri, state):
        params = super().get_authorize_params(redirect_uri, state)
        params["access_type"] = "offline"
        return params

    def exchange_code(self, code, redirect_uri):
        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.get_client_id(),
                "client_secret": self.get_client_secret(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_in": data.get("expires_in", 1800),
        }

    def refresh_tokens(self, refresh_token):
        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.get_client_id(),
                "client_secret": self.get_client_secret(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_in": data.get("expires_in", 1800),
        }

    def get_tenants(self, access_token):
        resp = requests.get(
            "https://api.xero.com/connections",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        return [
            {"id": t["tenantId"], "name": t.get("tenantName", "Unknown")}
            for t in resp.json()
        ]

    def fetch_trial_balance(self, access_token, tenant_id, as_at_date):
        """
        Xero GET /Reports/TrialBalance.

        Returns a normalised list of account-level lines. Raises a clear
        exception when the API response is structurally valid but does not
        contain usable account rows for import.
        """
        params = {}
        if as_at_date:
            params["date"] = as_at_date.isoformat()

        resp = requests.get(
            "https://api.xero.com/api.xro/2.0/Reports/TrialBalance",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Xero-Tenant-Id": tenant_id,
                "Accept": "application/json",
            },
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        reports = data.get("Reports", [])
        if not reports:
            raise ValueError(
                f"Xero returned no reports for tenant {tenant_id}."
            )

        report = reports[0]
        rows = report.get("Rows", [])
        lines = []
        row_type_counts = {}
        section_row_type_counts = {}

        for row_group in rows:
            row_type = row_group.get("RowType", "Unknown")
            row_type_counts[row_type] = row_type_counts.get(row_type, 0) + 1

            if row_type != "Section":
                continue

            for row in row_group.get("Rows", []):
                child_type = row.get("RowType", "Unknown")
                section_row_type_counts[child_type] = section_row_type_counts.get(child_type, 0) + 1
                if child_type != "Row":
                    continue

                cells = row.get("Cells", [])
                if len(cells) < 5:
                    logger.warning(
                        "Xero trial balance row had fewer than 5 cells",
                        extra={
                            "tenant_id": tenant_id,
                            "report_name": report.get("ReportName", ""),
                            "row": row,
                        },
                    )
                    continue

                account_str = cells[0].get("Value", "")
                if not account_str or account_str.strip().lower() == "total":
                    continue

                match = re.match(r"^(.+?)\s*\((\S+)\)\s*$", account_str)
                if match:
                    account_name = match.group(1).strip()
                    account_code = match.group(2)
                else:
                    account_name = account_str.strip()
                    account_code = ""

                debit = _to_decimal(cells[1].get("Value", "0"))
                credit = _to_decimal(cells[2].get("Value", "0"))
                ytd_debit = _to_decimal(cells[3].get("Value", "0"))
                ytd_credit = _to_decimal(cells[4].get("Value", "0"))
                opening = (ytd_debit - ytd_credit) - (debit - credit)

                lines.append({
                    "account_code": account_code,
                    "account_name": account_name,
                    "opening_balance": opening,
                    "debit": ytd_debit,
                    "credit": ytd_credit,
                })

        report_date = report.get("ReportDate", "")
        if not lines:
            logger.error(
                "Xero trial balance returned no usable account rows",
                extra={
                    "tenant_id": tenant_id,
                    "requested_date": as_at_date.isoformat() if as_at_date else "",
                    "report_name": report.get("ReportName", ""),
                    "report_date": report_date,
                    "top_level_row_types": row_type_counts,
                    "section_row_types": section_row_type_counts,
                    "raw_preview": json.dumps(rows[:3], default=str)[:2000],
                },
            )
            raise ValueError(
                "Xero returned no account-level trial balance rows for the selected organisation and date. "
                f"Requested date: {as_at_date.isoformat() if as_at_date else 'not supplied'}. "
                f"Reported date: {report_date or 'unknown'}."
            )

        return lines

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        """
        Xero General Ledger Summary style period import.

        Returns account movement lines derived from the provider's net movement
        style report, normalised into the same debit/credit structure used by
        the existing import wizard.
        """
        if not from_date or not to_date:
            raise ValueError("Xero period movement import requires both from_date and to_date.")

        resp = requests.get(
            "https://api.xero.com/api.xro/2.0/Reports/GeneralLedgerSummary",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Xero-Tenant-Id": tenant_id,
                "Accept": "application/json",
            },
            params={
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        reports = data.get("Reports", [])
        if not reports:
            raise ValueError(
                f"Xero returned no reports for tenant {tenant_id}."
            )

        report = reports[0]
        rows = report.get("Rows", [])
        lines = []
        row_type_counts = {}

        for row in rows:
            row_type = row.get("RowType", "Unknown")
            row_type_counts[row_type] = row_type_counts.get(row_type, 0) + 1
            if row_type != "Row":
                continue

            cells = row.get("Cells", [])
            if len(cells) < 5:
                continue

            account_name = (cells[0].get("Value") or "").strip()
            account_code = (cells[1].get("Value") or "").strip()
            net_movement = _to_decimal(cells[4].get("Value", "0"))
            debit = net_movement if net_movement > 0 else Decimal("0")
            credit = -net_movement if net_movement < 0 else Decimal("0")

            if account_name and account_name.lower() != "total" and net_movement != 0:
                lines.append({
                    "account_code": account_code,
                    "account_name": account_name,
                    "opening_balance": Decimal("0"),
                    "debit": debit,
                    "credit": credit,
                    "movement_amount": net_movement,
                })

        if not lines:
            logger.error(
                "Xero General Ledger Summary returned no usable account rows",
                extra={
                    "tenant_id": tenant_id,
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                    "report_name": report.get("ReportName", ""),
                    "row_types": row_type_counts,
                    "raw_preview": json.dumps(rows[:3], default=str)[:2000],
                },
            )
            raise ValueError(
                "Xero returned no usable General Ledger Summary account rows for the selected period. "
                f"Period: {from_date.isoformat()} to {to_date.isoformat()}."
            )

        return lines


# ---------------------------------------------------------------------------
# MYOB
# ---------------------------------------------------------------------------

@register_provider("myob")
class MYOBProvider(BaseProvider):
    name = "myob"
    display_name = "MYOB"
    icon_class = "bi-cloud"

    authorize_url = "https://secure.myob.com/oauth2/account/authorize"
    token_url = "https://secure.myob.com/oauth2/v1/authorize"
    scopes = "CompanyFile"

    def get_client_id(self):
        return getattr(settings, "MYOB_CLIENT_ID", "")

    def get_client_secret(self):
        return getattr(settings, "MYOB_CLIENT_SECRET", "")

    def exchange_code(self, code, redirect_uri):
        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.get_client_id(),
                "client_secret": self.get_client_secret(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_in": data.get("expires_in", 1800),
        }

    def refresh_tokens(self, refresh_token):
        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.get_client_id(),
                "client_secret": self.get_client_secret(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_in": data.get("expires_in", 1800),
        }

    def get_tenants(self, access_token):
        """
        MYOB: List company files (tenants).
        Endpoint: GET https://api.myob.com/accountright/
        """
        resp = requests.get(
            "https://api.myob.com/accountright/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("Items", data if isinstance(data, list) else [])
        return [
            {
                "id": t.get("Uri") or t.get("Id") or t.get("CompanyFileId"),
                "name": t.get("Name", "Unknown"),
            }
            for t in items
        ]

    def fetch_trial_balance(self, access_token, tenant_id, as_at_date):
        """
        MYOB: GET /GeneralLedger/Account to get all accounts with balances.
        Note: tenant_id is the Company File URI (e.g. https://api.myob.com/accountright/{cf-guid})
        """
        url = f"{tenant_id}/GeneralLedger/Account"
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("Items", [])
        lines = []
        for acct in items:
            number = acct.get("Number", "")
            name = acct.get("Name", "")
            opening = Decimal(str(acct.get("OpeningBalance", "0") or "0"))
            closing = Decimal(str(acct.get("CurrentBalance", "0") or "0"))
            if closing >= 0:
                debit = closing
                credit = Decimal("0")
            else:
                debit = Decimal("0")
                credit = -closing
            lines.append({
                "account_code": number,
                "account_name": name,
                "opening_balance": opening,
                "debit": debit,
                "credit": credit,
            })
        return lines


# ---------------------------------------------------------------------------
# QuickBooks Online
# ---------------------------------------------------------------------------

@register_provider("quickbooks")
class QuickBooksProvider(BaseProvider):
    name = "quickbooks"
    display_name = "QuickBooks"
    icon_class = "bi-lightning-charge"
    supports_period_movement_import = True

    authorize_url = "https://appcenter.intuit.com/connect/oauth2"
    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    scopes = "com.intuit.quickbooks.accounting"

    def get_client_id(self):
        return getattr(settings, "QB_CLIENT_ID", "")

    def get_client_secret(self):
        return getattr(settings, "QB_CLIENT_SECRET", "")

    def get_authorize_params(self, redirect_uri, state):
        params = super().get_authorize_params(redirect_uri, state)
        params["scope"] = self.scopes
        return params

    def exchange_code(self, code, redirect_uri):
        resp = requests.post(
            self.token_url,
            auth=(self.get_client_id(), self.get_client_secret()),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_in": data.get("expires_in", 3600),
            "realm_id": data.get("realmId") or data.get("realm_id") or "",
        }

    def refresh_tokens(self, refresh_token):
        resp = requests.post(
            self.token_url,
            auth=(self.get_client_id(), self.get_client_secret()),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_in": data.get("expires_in", 3600),
        }

    def get_tenants(self, access_token):
        """QBO doesn't have a tenants endpoint; realm_id comes from the callback."""
        return []

    def fetch_trial_balance(self, access_token, tenant_id, as_at_date):
        """
        QBO: GET /v3/company/{realmId}/reports/TrialBalance
        """
        base_url = f"https://quickbooks.api.intuit.com/v3/company/{tenant_id}"
        params = {"minorversion": "65"}
        if as_at_date:
            params["date_macro"] = ""
            params["end_date"] = as_at_date.isoformat()
            params["start_date"] = ""
        resp = requests.get(
            f"{base_url}/reports/TrialBalance",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        lines = []
        rows_data = data.get("Rows", {}).get("Row", [])
        for section in rows_data:
            section_rows = section.get("Rows", {}).get("Row", [])
            for row in section_rows:
                col_data = row.get("ColData", [])
                if len(col_data) < 3:
                    continue
                account_str = col_data[0].get("value", "")
                debit = _to_decimal(col_data[1].get("value", "0"))
                credit = _to_decimal(col_data[2].get("value", "0"))
                account_id = col_data[0].get("id", "")
                account_code = account_id if account_id else ""
                account_name = account_str
                lines.append({
                    "account_code": account_code,
                    "account_name": account_name,
                    "opening_balance": Decimal("0"),
                    "debit": debit,
                    "credit": credit,
                })
        return lines

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        """
        QBO: GET /v3/company/{realmId}/reports/GeneralLedger
        Extract account-level net movements from summary rows in the report and
        convert them into the debit/credit structure expected by the import wizard.
        """
        if not from_date or not to_date:
            raise ValueError("QuickBooks period movement import requires both from_date and to_date.")

        base_url = f"https://quickbooks.api.intuit.com/v3/company/{tenant_id}"
        params = {
            "minorversion": "65",
            "start_date": from_date.isoformat(),
            "end_date": to_date.isoformat(),
        }

        resp = requests.get(
            f"{base_url}/reports/GeneralLedger",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        rows_data = data.get("Rows", {}).get("Row", [])
        lines = []
        row_types = {}

        def append_account_row(account_code, account_name, beginning_balance, net_activity):
            account_name = (account_name or "").strip()
            if not account_name or account_name.lower() == "total":
                return
            beginning_balance = _to_decimal(beginning_balance)
            net_activity = _to_decimal(net_activity)
            if beginning_balance == 0 and net_activity == 0:
                return
            debit = net_activity if net_activity > 0 else Decimal("0")
            credit = -net_activity if net_activity < 0 else Decimal("0")
            lines.append({
                "account_code": (account_code or "").strip(),
                "account_name": account_name,
                "opening_balance": beginning_balance,
                "debit": debit,
                "credit": credit,
                "movement_amount": net_activity,
            })

        def handle_row(row):
            if not isinstance(row, dict):
                return

            row_type = row.get("type") or row.get("RowType") or "Unknown"
            row_types[row_type] = row_types.get(row_type, 0) + 1

            header = row.get("Header") or {}
            header_cols = header.get("ColData") or []
            summary = row.get("Summary") or {}
            summary_cols = summary.get("ColData") or []
            children = row.get("Rows", {}).get("Row", []) or []

            if header_cols and summary_cols:
                account_name = (header_cols[0].get("value") or "").strip()
                account_code = header_cols[0].get("id", "") or ""
                beginning_balance = summary_cols[0].get("value", "0") if len(summary_cols) > 0 else "0"
                net_activity = summary_cols[3].get("value", "0") if len(summary_cols) > 3 else "0"
                append_account_row(account_code, account_name, beginning_balance, net_activity)
                return

            col_data = row.get("ColData", []) or []
            if row_type.lower() == "data" and len(col_data) >= 5:
                first_value = (col_data[0].get("value") or "").strip()
                first_id = (col_data[0].get("id") or "").strip()
                looks_like_transaction_row = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", first_value))
                if not looks_like_transaction_row and (first_id or len(col_data) >= 6):
                    append_account_row(
                        first_id,
                        first_value,
                        col_data[1].get("value", "0"),
                        col_data[4].get("value", "0"),
                    )
                    return

            for child in children:
                handle_row(child)

        for row in rows_data:
            handle_row(row)

        deduped = {}
        for line in lines:
            key = line["account_code"] or line["account_name"]
            if key not in deduped:
                deduped[key] = line.copy()
            else:
                deduped[key]["opening_balance"] += line["opening_balance"]
                deduped[key]["debit"] += line["debit"]
                deduped[key]["credit"] += line["credit"]
                deduped[key]["movement_amount"] += line["movement_amount"]

        lines = list(deduped.values())

        if not lines:
            logger.error(
                "QuickBooks General Ledger returned no usable account rows",
                extra={
                    "tenant_id": tenant_id,
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                    "row_types": row_types,
                    "raw_preview": json.dumps(rows_data[:3], default=str)[:2000],
                },
            )
            raise ValueError(
                "QuickBooks returned no usable General Ledger account rows for the selected period. "
                f"Period: {from_date.isoformat()} to {to_date.isoformat()}."
            )

        return lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_decimal(value):
    """Safely convert a value to Decimal."""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def get_provider(name):
    """Get a provider instance by name."""
    return PROVIDERS.get(name)


def get_configured_providers():
    """Return list of providers that have API credentials configured."""
    return [p for p in PROVIDERS.values() if p.is_configured()]
