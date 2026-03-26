"""
Provider configurations for Xero, MYOB, and QuickBooks Online.

Each provider defines its OAuth2 endpoints, scopes, and the logic
to parse a trial balance response into a normalised list of dicts.
"""
import logging
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
    icon_class = ""

    authorize_url = ""
    token_url = ""
    scopes = ""

    supports_period_movement_import = False

    def get_client_id(self):
        raise NotImplementedError

    def get_client_secret(self):
        raise NotImplementedError

    def get_authorize_params(self, redirect_uri, state):
        return {
            "response_type": "code",
            "client_id": self.get_client_id(),
            "redirect_uri": redirect_uri,
            "scope": self.scopes,
            "state": state,
        }

    def exchange_code(self, code, redirect_uri):
        raise NotImplementedError

    def refresh_tokens(self, refresh_token):
        raise NotImplementedError

    def get_tenants(self, access_token):
        raise NotImplementedError

    def fetch_trial_balance(self, access_token, tenant_id, as_at_date, start_date=None):
        raise NotImplementedError

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        raise NotImplementedError

    def is_configured(self):
        return bool(self.get_client_id() and self.get_client_secret())


@register_provider("xero")
class XeroProvider(BaseProvider):
    name = "xero"
    display_name = "Xero"
    icon_class = "bi bi-cloud"
    authorize_url = "https://login.xero.com/identity/connect/authorize"
    token_url = "https://identity.xero.com/connect/token"
    scopes = "offline_access accounting.reports.read"
    supports_period_movement_import = True

    def get_client_id(self):
        return getattr(settings, "XERO_CLIENT_ID", "")

    def get_client_secret(self):
        return getattr(settings, "XERO_CLIENT_SECRET", "")

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
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def refresh_tokens(self, refresh_token):
        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.get_client_id(),
                "client_secret": self.get_client_secret(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_tenants(self, access_token):
        resp = requests.get(
            "https://api.xero.com/connections",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": t.get("tenantId"),
                "name": t.get("tenantName"),
                "raw": t,
            }
            for t in data
        ]

    def fetch_trial_balance(self, access_token, tenant_id, as_at_date, start_date=None):
        url = "https://api.xero.com/api.xro/2.0/Reports/TrialBalance"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Xero-tenant-id": tenant_id,
            "Accept": "application/json",
        }
        params = {"date": as_at_date.isoformat()}
        if start_date:
            params["fromDate"] = start_date.isoformat()
            params["paymentsOnly"] = "false"
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        report = (resp.json().get("Reports") or [{}])[0]
        rows = report.get("Rows", [])
        lines = []
        for row in rows:
            if row.get("RowType") != "Row":
                continue
            cells = row.get("Cells", [])
            if len(cells) < 4:
                continue
            account_name = (cells[0].get("Value") or "").strip()
            if not account_name:
                continue
            code = (cells[0].get("Attributes") or [{}])[0].get("Value", "") if cells[0].get("Attributes") else ""
            debit = _to_decimal(cells[1].get("Value"))
            credit = _to_decimal(cells[2].get("Value"))
            if debit == 0 and credit == 0:
                continue
            lines.append({
                "account_code": code,
                "account_name": account_name,
                "opening_balance": Decimal("0"),
                "debit": debit,
                "credit": credit,
                "movement_amount": debit - credit,
            })
        return lines

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        url = "https://api.xero.com/api.xro/2.0/Reports/GeneralLedger"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Xero-tenant-id": tenant_id,
            "Accept": "application/json",
        }
        params = {
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
        }
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        report = (resp.json().get("Reports") or [{}])[0]
        rows = report.get("Rows", [])
        lines = []
        current_section = ""
        for row in rows:
            row_type = row.get("RowType")
            if row_type == "Header":
                continue
            if row_type == "Section":
                section_rows = row.get("Rows", [])
                header = row.get("Title", "")
                if header:
                    current_section = header
                for child in section_rows:
                    if child.get("RowType") != "Row":
                        continue
                    cells = child.get("Cells", [])
                    if len(cells) < 6:
                        continue
                    account_name = (cells[0].get("Value") or current_section or "").strip()
                    if not account_name:
                        continue
                    movement = _to_decimal(cells[4].get("Value"))
                    if movement == 0:
                        continue
                    debit = movement if movement > 0 else Decimal("0")
                    credit = -movement if movement < 0 else Decimal("0")
                    lines.append({
                        "account_code": "",
                        "account_name": account_name,
                        "opening_balance": _to_decimal(cells[1].get("Value")),
                        "debit": debit,
                        "credit": credit,
                        "movement_amount": movement,
                    })
        return lines


@register_provider("quickbooks")
class QuickBooksProvider(BaseProvider):
    name = "quickbooks"
    display_name = "QuickBooks"
    icon_class = "bi bi-quickbooks"
    authorize_url = "https://appcenter.intuit.com/connect/oauth2"
    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    scopes = "com.intuit.quickbooks.accounting"
    supports_period_movement_import = True

    def get_client_id(self):
        return getattr(settings, "QBO_CLIENT_ID", "")

    def get_client_secret(self):
        return getattr(settings, "QBO_CLIENT_SECRET", "")

    def get_authorize_params(self, redirect_uri, state):
        params = super().get_authorize_params(redirect_uri, state)
        params["response_type"] = "code"
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
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def refresh_tokens(self, refresh_token):
        resp = requests.post(
            self.token_url,
            auth=(self.get_client_id(), self.get_client_secret()),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_tenants(self, access_token):
        return []

    def _fetch_qbo_tb_sides(self, access_token, tenant_id, end_date):
        """Fetch QBO TrialBalance to determine debit/credit side per account.

        Returns dict of {account_key: 'D' or 'C'}.
        """
        base_url = f"https://quickbooks.api.intuit.com/v3/company/{tenant_id}"
        resp = requests.get(
            f"{base_url}/reports/TrialBalance",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params={
                "minorversion": "65",
                "end_date": end_date.isoformat(),
                "accounting_method": "Accrual",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        rows_data = data.get("Rows", {}).get("Row", [])
        sides = {}

        def walk(rows):
            for row in rows:
                row_type = row.get("type", "")
                if row_type == "Data" or ("ColData" in row and row_type != "Section"):
                    cols = row.get("ColData", [])
                    if len(cols) < 3:
                        continue
                    code = cols[0].get("id", "")
                    name = (cols[0].get("value", "") or "").strip()
                    key = code or name
                    if not key:
                        continue
                    raw_debit = (cols[1].get("value") or "").strip()
                    raw_credit = (cols[2].get("value") or "").strip()
                    if raw_debit:
                        sides[key] = "D"
                    elif raw_credit:
                        sides[key] = "C"
                elif row_type == "Section":
                    walk(row.get("Rows", {}).get("Row", []))

        walk(rows_data)
        return sides

    def _fetch_qbo_gl_summary(self, access_token, tenant_id, start_date, end_date, tb_sides):
        """Fetch QBO GeneralLedger and compute net activity per account.

        Sums the Amount column (index 7) from transaction Data rows.
        Uses tb_sides and beginning_balance to determine the correct
        debit/credit convention for each account.
        """
        base_url = f"https://quickbooks.api.intuit.com/v3/company/{tenant_id}"
        resp = requests.get(
            f"{base_url}/reports/GeneralLedger",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params={
                "minorversion": "65",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "accounting_method": "Accrual",
            },
            timeout=60,
        )
        if not resp.ok:
            logger.error("QBO GL error %s: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("Rows", {}).get("Row", [])

        lines = []

        for row in rows:
            if row.get("type") != "Section":
                continue

            header_cols = row.get("Header", {}).get("ColData", [])
            if not header_cols:
                continue
            account_name = (header_cols[0].get("value", "") or "").strip()
            account_code = header_cols[0].get("id", "") or account_name
            if not account_name:
                continue

            child_rows = row.get("Rows", {}).get("Row", [])

            beginning_balance = Decimal("0")
            net = Decimal("0")

            last_balance = Decimal("0")
            for data_row in child_rows:
                if data_row.get("type") == "Section":
                    continue
                cols = data_row.get("ColData", [])
                if not cols:
                    continue
                label = (cols[0].get("value", "") or "").strip()

                if label == "Beginning Balance":
                    if len(cols) > 8:
                        beginning_balance = _to_decimal(
                            (cols[8].get("value") or "").strip() or "0"
                        )
                    continue

                if len(cols) > 7:
                    amount_str = (cols[7].get("value") or "").strip()
                    if amount_str:
                        net += _to_decimal(amount_str)
                # Capture running balance for sign convention detection
                if len(cols) > 8:
                    bal_str = (cols[8].get("value") or "").strip()
                    if bal_str:
                        last_balance = _to_decimal(bal_str)

            ending_balance = last_balance

            if net == 0:
                continue

            # Determine sign convention from TB side and ending balance
            key = account_code or account_name
            tb_side = tb_sides.get(key, "D")
            use_credit_convention = (tb_side == "C") or (tb_side == "D" and ending_balance < 0)

            if use_credit_convention:
                if net > 0:
                    out_debit, out_credit = Decimal("0"), net
                else:
                    out_debit, out_credit = abs(net), Decimal("0")
            else:
                if net > 0:
                    out_debit, out_credit = net, Decimal("0")
                else:
                    out_debit, out_credit = Decimal("0"), abs(net)

            lines.append({
                "account_code": account_code,
                "account_name": account_name,
                "opening_balance": beginning_balance,
                "debit": out_debit,
                "credit": out_credit,
                "movement_amount": net,
            })

        return lines

    def _fetch_qbo_net_activity(self, access_token, tenant_id, start_date, end_date):
        """Orchestrate TB sides + GL summary for correct debit/credit assignment."""
        tb_sides = self._fetch_qbo_tb_sides(access_token, tenant_id, end_date)
        return self._fetch_qbo_gl_summary(
            access_token, tenant_id, start_date, end_date, tb_sides
        )

    def fetch_trial_balance(self, access_token, tenant_id, as_at_date, start_date=None):
        if not start_date:
            start_date = as_at_date.replace(month=7, day=1, year=as_at_date.year - 1)
        return self._fetch_qbo_net_activity(access_token, tenant_id, start_date, as_at_date)

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        return self._fetch_qbo_net_activity(access_token, tenant_id, from_date, to_date)

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
