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
            lines.append({
                "account_code": code,
                "account_name": account_name,
                "opening_balance": Decimal("0"),
                "debit": debit,
                "credit": credit,
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

    def fetch_trial_balance(self, access_token, tenant_id, as_at_date, start_date=None):
        base_url = f"https://quickbooks.api.intuit.com/v3/company/{tenant_id}"
        params = {
            "minorversion": "65",
            "end_date": as_at_date.isoformat(),
            "accounting_method": "Accrual",
        }
        if start_date:
            params["start_date"] = start_date.isoformat()
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

        # Debug: log first 3 rows for verification
        for i, r in enumerate(rows_data[:3]):
            logger.info("QBO TB row %d: type=%s ColData=%s", i, r.get("type"), r.get("ColData"))

        def walk(rows):
            for row in rows:
                row_type = row.get("type", "")
                if row_type == "Data":
                    cols = row.get("ColData", [])
                    if len(cols) >= 3:
                        code = cols[0].get("id", "")
                        name = (cols[0].get("value", "") or "").strip()
                        if not name:
                            continue
                        debit = _to_decimal(cols[1].get("value"))
                        credit = _to_decimal(cols[2].get("value"))
                        lines.append({
                            "account_code": code,
                            "account_name": name,
                            "opening_balance": Decimal("0"),
                            "debit": debit,
                            "credit": credit,
                        })
                elif row_type == "Section":
                    # Recurse into child rows within sections
                    child_rows = row.get("Rows", {}).get("Row", [])
                    walk(child_rows)

        walk(rows_data)
        return lines

    def _fetch_account_types(self, access_token, tenant_id):
        """Fetch account type classifications from the QBO Account query API.

        Returns dict mapping account name → AccountType string.
        """
        base_url = f"https://quickbooks.api.intuit.com/v3/company/{tenant_id}"
        resp = requests.get(
            f"{base_url}/query",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params={
                "minorversion": "65",
                "query": "SELECT Id, Name, AccountType, AccountSubType FROM Account",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        accounts = data.get("QueryResponse", {}).get("Account", [])
        account_types = {}
        for acct in accounts:
            name = (acct.get("Name") or "").strip()
            acct_type = (acct.get("AccountType") or "").strip()
            if name and acct_type:
                account_types[name] = acct_type
        logger.info("QBO account types fetched: %d accounts", len(account_types))
        return account_types

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        # Fetch account type classifications before processing GL rows
        account_types = self._fetch_account_types(access_token, tenant_id)

        base_url = f"https://quickbooks.api.intuit.com/v3/company/{tenant_id}"
        params = {
            "minorversion": "65",
            "start_date": from_date.isoformat(),
            "end_date": to_date.isoformat(),
            "accounting_method": "Accrual",
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
        columns = data.get("Columns", {}).get("Column", []) or []
        lines = []
        row_types = {}

        # Debug: log the raw structure so we can diagnose parsing issues
        first_row_type = ""
        first_row_keys = ""
        if rows_data:
            first_row_type = rows_data[0].get("RowType") or rows_data[0].get("type") or "?"
            first_row_keys = ",".join(rows_data[0].keys())
        logger.info(
            "QB GL structure: %d columns, %d rows, first_row_type=%s, first_row_keys=%s, cols=%s",
            len(columns),
            len(rows_data),
            first_row_type,
            first_row_keys,
            ",".join(c.get("ColTitle", "") for c in columns[:10]),
        )

        def _normalize_header(value):
            value = (value or "").strip().lower()
            return re.sub(r"[^a-z0-9]+", "_", value).strip("_")

        column_names = [
            _normalize_header(
                col.get("ColTitle")
                or col.get("colTitle")
                or col.get("MetaData", {}).get("Name")
                or ""
            )
            for col in columns
        ]

        def _col_value(col):
            if not isinstance(col, dict):
                return ""
            return ((col.get("value") or col.get("Value") or "")).strip()

        def _col_id(col):
            if not isinstance(col, dict):
                return ""
            return ((col.get("id") or col.get("Id") or "")).strip()

        def _col_map(cols):
            mapped = {}
            for idx, col in enumerate(cols or []):
                key = column_names[idx] if idx < len(column_names) else f"col_{idx}"
                mapped[key] = _col_value(col)
            return mapped

        def _extract_numeric_values(cols):
            values = []
            for col in cols or []:
                raw = _col_value(col)
                if raw == "":
                    continue
                try:
                    values.append(_to_decimal(raw))
                except Exception:
                    continue
            return values

        def _extract_net_activity(mapped, numeric_values=None):
            # Prefer explicit net activity keys (present in some QB report variants)
            for key in (
                "net_activity_total",
                "net_activity",
                "net_change",
            ):
                value = mapped.get(key, "")
                if value not in ("", None):
                    return _to_decimal(value)
            # The standard QB GeneralLedger report has 'balance' (ending balance) and
            # 'beginning_balance' columns. Net Activity = Ending Balance - Beginning Balance.
            # This is always correct regardless of account type or sign convention.
            ending = mapped.get("balance", "")
            beginning = mapped.get("beginning_balance_total") or mapped.get("beginning_balance") or ""
            if ending not in ("", None) and beginning not in ("", None):
                return _to_decimal(ending) - _to_decimal(beginning)
            # Fallback: if we only have ending balance, use it as net (accounts with no opening balance)
            if ending not in ("", None):
                return _to_decimal(ending)
            # Last resort: use the last numeric value
            numeric_values = numeric_values or []
            if numeric_values:
                return numeric_values[-1]
            return Decimal("0")

        def _extract_opening_balance(mapped, numeric_values=None):
            for key in ("beginning_balance_total", "beginning_balance", "opening_balance"):
                value = mapped.get(key, "")
                if value not in ("", None):
                    return _to_decimal(value)
            numeric_values = numeric_values or []
            if len(numeric_values) > 1:
                return numeric_values[0]
            return Decimal("0")

        def _looks_like_detail_row(value):
            value = (value or "").strip()
            if not value:
                return True
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return True
            if value.lower() in {"beginning balance", "total", "subtotal", "ending balance"}:
                return True
            return False

        # QBO AccountType values (as returned by Account query API) and their natural side
        DEBIT_TYPES = {
            'Bank', 'Other Current Asset', 'Fixed Asset',
            'Other Asset', 'Accounts Receivable',
            'Cost of Goods Sold', 'Expense', 'Other Expense',
        }
        CREDIT_TYPES = {
            'Credit Card', 'Other Current Liability',
            'Long Term Liability', 'Equity', 'Retained Earnings',
            'Income', 'Other Income', 'Accounts Payable',
        }

        def append_account_row(account_code, account_name, closing_balance, opening_balance="0"):
            account_name = (account_name or "").strip()
            account_code = (account_code or "").strip()
            opening_balance = _to_decimal(opening_balance)
            closing_balance = _to_decimal(closing_balance)
            if not account_name or _looks_like_detail_row(account_name):
                return
            if closing_balance == 0:
                return
            # QBO GL Balance column is always positive (absolute value).
            # Look up account type from the Account query API to determine side.
            acct_type = account_types.get(account_name, "")
            closing_balance = abs(closing_balance)
            if acct_type in DEBIT_TYPES:
                debit = closing_balance
                credit = Decimal("0")
            elif acct_type in CREDIT_TYPES:
                debit = Decimal("0")
                credit = closing_balance
            else:
                logger.warning(
                    "QBO GL unknown AccountType %r for %r — defaulting to debit",
                    acct_type, account_name,
                )
                debit = closing_balance
                credit = Decimal("0")
            movement_amount = closing_balance - opening_balance
            lines.append({
                "account_code": account_code,
                "account_name": account_name,
                "opening_balance": opening_balance,
                "debit": debit,
                "credit": credit,
                "movement_amount": movement_amount,
            })

        def _get_balance_col_index():
            """Return the index of the 'balance' column in column_names."""
            try:
                return column_names.index("balance")
            except ValueError:
                return len(column_names) - 1  # last column as fallback

        def _extract_balance_from_col_data(col_data):
            """Extract the balance value from a Data row's ColData list."""
            bal_idx = _get_balance_col_index()
            if bal_idx < len(col_data):
                val = _col_value(col_data[bal_idx])
                if val not in ("", None):
                    return _to_decimal(val)
            # Fallback: last numeric value
            nums = _extract_numeric_values(col_data)
            return nums[-1] if nums else None

        def _extract_closing_balance_from_children(children):
            """Extract closing balance from child Data rows.

            The QB GeneralLedger API returns child Data rows for each account Section:
              - First Data row: 'Beginning Balance' label + opening balance in Balance col
              - Middle Data rows: individual transactions (date in col 0, running balance in last col)
              - Last Data row: the closing balance in the Balance column (ColData index 8)

            Returns (closing_balance, beginning_balance) or (None, None) if not found.
            """
            data_rows = [
                c for c in (children or [])
                if isinstance(c, dict)
                and (c.get("type") or c.get("RowType") or "").lower() == "data"
            ]
            if not data_rows:
                return None, None

            beginning_balance = None

            # First Data row: should be 'Beginning Balance'
            first_col_data = data_rows[0].get("ColData", []) or []
            first_label = _col_value(first_col_data[0]).lower().strip() if first_col_data else ""
            if first_label == "beginning balance":
                beginning_balance = _extract_balance_from_col_data(first_col_data)

            # Last Data row: always holds the closing balance in the Balance column,
            # regardless of whether its label is a date, 'Ending Balance', or anything else.
            last_col_data = data_rows[-1].get("ColData", []) or []
            closing_balance = _extract_balance_from_col_data(last_col_data)

            if closing_balance is not None:
                return closing_balance, (beginning_balance if beginning_balance is not None else Decimal("0"))

            return None, None

        _debug_row_count = [0]

        def handle_row(row, current_account_code="", current_account_name=""):
            if not isinstance(row, dict):
                return
            row_type = row.get("type") or row.get("RowType") or "Unknown"
            row_types[row_type] = row_types.get(row_type, 0) + 1
            header = row.get("Header") or {}
            header_cols = header.get("ColData") or row.get("Header", {}).get("Columns") or []
            summary = row.get("Summary") or {}
            summary_cols = summary.get("ColData") or summary.get("Columns") or []
            children = row.get("Rows", {}).get("Row", []) or row.get("Rows") or []
            col_data = row.get("ColData", []) or []
            header_name = _col_value(header_cols[0]) if header_cols else ""
            header_code = _col_id(header_cols[0]) if header_cols else ""
            if header_name and not _looks_like_detail_row(header_name):
                current_account_name = header_name
            if header_code:
                current_account_code = header_code

            # For Section rows with an account name and child Data rows:
            # extract closing balance from the last child Data row's Balance column.
            # Account type for side assignment is looked up from the account_types dict
            # (fetched via separate Account query API call).
            if row_type.lower() == "section" and current_account_name:
                closing_balance, beginning_balance = _extract_closing_balance_from_children(children)

                if closing_balance is not None:
                    append_account_row(
                        current_account_code,
                        current_account_name,
                        closing_balance,
                        beginning_balance if beginning_balance is not None else Decimal("0"),
                    )
                    return

                # If no child Data rows, this is a category grouping Section —
                # recurse into its children (which are individual account Sections).
                for child in children:
                    handle_row(child, current_account_code=current_account_code,
                               current_account_name="")
                return

            # Skip individual Data rows — they are transactions, not account summaries.
            if row_type.lower() == "data":
                return

            # Recurse into non-Section, non-Data rows (e.g. nested groups)
            for child in children:
                handle_row(child, current_account_code=current_account_code,
                           current_account_name=current_account_name)

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
                    "column_names": column_names,
                },
            )
            raise ValueError(
                "QuickBooks returned no usable General Ledger account rows for the selected period. "
                f"Period: {from_date.isoformat()} to {to_date.isoformat()}."
            )
        return lines

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
