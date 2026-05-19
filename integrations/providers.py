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
# User-facing provider exception
# ---------------------------------------------------------------------------

class ProviderUserError(Exception):
    """Raised for user-actionable provider errors (auth token expired, scope
    revoked, etc.) that must be shown verbatim to the user along with a
    reconnect path.

    Catch sites are expected to surface ``str(exc)`` directly to the user and
    redirect them to the provider's reconnect dashboard. Do NOT use this
    class for incidental bugs or unexpected API failures — those should
    propagate as their natural exception type so the generic catch can log
    the trace and show a non-leaky failure message.
    """


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

    @staticmethod
    def _extract_xero_account_code(cells_0):
        """Extract account code from a Xero row's first cell Attributes."""
        for attr in (cells_0.get("Attributes") or []):
            if attr.get("Id") == "account":
                return attr.get("Value", "")
        # Fallback: first attribute's Value
        attrs = cells_0.get("Attributes") or []
        return attrs[0].get("Value", "") if attrs else ""

    def _process_xero_tb_row(self, row, lines):
        """Process a single Xero TrialBalance Row into the lines list."""
        cells = row.get("Cells", [])
        if len(cells) < 3:
            return
        account_name = (cells[0].get("Value") or "").strip()
        if not account_name:
            return
        code = self._extract_xero_account_code(cells[0])
        debit = _to_decimal(cells[1].get("Value"))
        credit = _to_decimal(cells[2].get("Value"))
        if debit == 0 and credit == 0:
            return
        lines.append({
            "account_code": code,
            "account_name": account_name,
            "opening_balance": Decimal("0"),
            "debit": debit,
            "credit": credit,
            "movement_amount": debit - credit,
        })

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
        if resp.status_code == 401:
            raise ProviderUserError(
                "Xero authorisation expired. Please reconnect your "
                "Xero account from the Integrations page."
            )
        if resp.status_code == 403:
            raise ProviderUserError(
                "Xero connection is missing a required permission "
                "(accounting.reports.read). Please reconnect your Xero "
                "account from the Integrations page to grant it."
            )
        resp.raise_for_status()
        report = (resp.json().get("Reports") or [{}])[0]
        rows = report.get("Rows", [])
        lines = []
        for row in rows:
            row_type = row.get("RowType")
            if row_type == "Section":
                for child in row.get("Rows", []):
                    if child.get("RowType") == "Row":
                        self._process_xero_tb_row(child, lines)
            elif row_type == "Row":
                self._process_xero_tb_row(row, lines)
        return lines

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        """
        Fetch net movement for the period using the Xero Finance API
        FinancialStatements/TrialBalance endpoint, which returns debit,
        credit, and movement per account for a date range.

        Requires the 'finance.statements.read' OAuth scope on the connection.
        """
        url = "https://api.xero.com/finance.xro/1.0/FinancialStatements/TrialBalance"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Xero-tenant-id": tenant_id,
            "Accept": "application/json",
        }
        params = {
            "startDate": from_date.isoformat(),
            "endDate": to_date.isoformat(),
        }
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 403:
            raise ProviderUserError(
                "The Xero connection does not have the 'finance.statements.read' "
                "scope. Please reconnect Xero from the Integrations page to "
                "grant the required permission."
            )
        if resp.status_code == 401:
            raise ProviderUserError(
                "Xero authorisation expired. Please reconnect your "
                "Xero account from the Integrations page."
            )
        resp.raise_for_status()
        data = resp.json()

        # The Finance API returns a list of account objects directly.
        # Each account has: accountId, accountCode, accountName, accountType,
        # accountClass, reportingCode, reportingCodeName,
        # accountMovement: { debits, credits, movement }
        accounts = data if isinstance(data, list) else data.get("accounts", [])

        lines = []
        for acct in accounts:
            movement_data = acct.get("accountMovement") or {}
            debit = Decimal(str(movement_data.get("debits") or "0"))
            credit = Decimal(str(movement_data.get("credits") or "0"))
            movement = Decimal(str(movement_data.get("movement") or "0"))

            # Skip accounts with zero movement
            if debit == 0 and credit == 0:
                continue

            account_code = (acct.get("accountCode") or "")[:50]
            account_name = acct.get("accountName") or ""

            lines.append({
                "account_code": account_code,
                "account_name": account_name,
                "opening_balance": Decimal("0"),
                "debit": debit,
                "credit": credit,
                "movement_amount": movement,
            })

        if not lines:
            raise ValueError(
                f"Xero returned no net movement for {from_date} to {to_date}. "
                "Check that the date range has transactions and that the Xero "
                "organisation is correct."
            )
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

    def _fetch_qbo_account_codes(self, access_token, tenant_id):
        """Fetch account list and return dict of {internal_id: account_number}.
        Uses AcctNum if set, falls back to account Name."""
        base_url = f"https://quickbooks.api.intuit.com/v3/company/{tenant_id}"
        resp = requests.get(
            f"{base_url}/query",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params={
                "query": "SELECT Id, AcctNum, Name FROM Account MAXRESULTS 1000",
                "minorversion": "65",
            },
            timeout=30,
        )
        if resp.status_code == 401:
            raise ProviderUserError(
                "QuickBooks token has expired. Please reconnect via Connections → QuickBooks."
            )
        if resp.status_code == 403:
            raise ProviderUserError(
                "QuickBooks connection is missing a required permission "
                "(com.intuit.quickbooks.accounting). Please reconnect via "
                "Connections → QuickBooks to grant it."
            )
        resp.raise_for_status()
        accounts = resp.json().get("QueryResponse", {}).get("Account", [])
        return {
            a["Id"]: a.get("AcctNum") or a["Id"]
            for a in accounts
        }

    def _fetch_qbo_tb_sides(self, access_token, tenant_id, end_date, account_codes=None):
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
        if resp.status_code == 401:
            raise ProviderUserError(
                "QuickBooks token has expired. Please reconnect via Connections → QuickBooks."
            )
        if resp.status_code == 403:
            raise ProviderUserError(
                "QuickBooks connection is missing a required permission "
                "(com.intuit.quickbooks.accounting). Please reconnect via "
                "Connections → QuickBooks to grant it."
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
                    internal_id = cols[0].get("id", "")
                    name = (cols[0].get("value", "") or "").strip()
                    resolved_code = (account_codes or {}).get(internal_id) or name
                    key = resolved_code
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

    def _fetch_qbo_gl_summary(self, access_token, tenant_id, start_date, end_date, tb_sides, account_codes=None):
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
        if resp.status_code == 401:
            raise ProviderUserError(
                "QuickBooks token has expired. Please reconnect via Connections → QuickBooks."
            )
        if resp.status_code == 403:
            raise ProviderUserError(
                "QuickBooks connection is missing a required permission "
                "(com.intuit.quickbooks.accounting). Please reconnect via "
                "Connections → QuickBooks to grant it."
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
            internal_id = header_cols[0].get("id", "")
            account_code = (account_codes or {}).get(internal_id) or account_name
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
            key = account_code  # already resolved above
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
        account_codes = self._fetch_qbo_account_codes(access_token, tenant_id)
        tb_sides = self._fetch_qbo_tb_sides(access_token, tenant_id, end_date, account_codes)
        return self._fetch_qbo_gl_summary(
            access_token, tenant_id, start_date, end_date, tb_sides, account_codes
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
