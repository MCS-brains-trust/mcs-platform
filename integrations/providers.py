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

        def walk(rows):
            for row in rows:
                if row.get("type") == "Section":
                    header_cols = row.get("Header", {}).get("ColData", [])
                    code = header_cols[0].get("id", "") if header_cols else ""
                    name = header_cols[0].get("value", "") if header_cols else ""
                    summary = row.get("Summary", {}).get("ColData", [])
                    if summary:
                        opening = _to_decimal(summary[1].get("value", "0")) if len(summary) > 1 else Decimal("0")
                        debit = _to_decimal(summary[2].get("value", "0")) if len(summary) > 2 else Decimal("0")
                        credit = _to_decimal(summary[3].get("value", "0")) if len(summary) > 3 else Decimal("0")
                        lines.append({
                            "account_code": code,
                            "account_name": name,
                            "opening_balance": opening,
                            "debit": debit,
                            "credit": credit,
                        })
                    child_rows = row.get("Rows", {}).get("Row", [])
                    walk(child_rows)
                elif row.get("type") == "Data":
                    cols = row.get("ColData", [])
                    if len(cols) >= 4:
                        code = cols[0].get("id", "")
                        name = cols[0].get("value", "")
                        opening = _to_decimal(cols[1].get("value", "0"))
                        debit = _to_decimal(cols[2].get("value", "0"))
                        credit = _to_decimal(cols[3].get("value", "0"))
                        lines.append({
                            "account_code": code,
                            "account_name": name,
                            "opening_balance": opening,
                            "debit": debit,
                            "credit": credit,
                        })

        walk(rows_data)
        return lines

    def fetch_period_movement(self, access_token, tenant_id, from_date, to_date):
        """
        QBO: GET /v3/company/{realmId}/reports/GeneralLedger
        Import net movement only from the report's Net Activity column.
        """
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
            for key in (
                "net_activity_total",
                "net_activity",
                "net_change",
                "change",
                "amount",
                "total",
            ):
                value = mapped.get(key, "")
                if value not in ("", None):
                    return _to_decimal(value)
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

        def append_account_row(account_code, account_name, net_activity, opening_balance="0"):
            account_name = (account_name or "").strip()
            account_code = (account_code or "").strip()
            opening_balance = _to_decimal(opening_balance)
            net_activity = _to_decimal(net_activity)
            if not account_name or _looks_like_detail_row(account_name):
                return
            if net_activity == 0:
                return
            debit = net_activity if net_activity > 0 else Decimal("0")
            credit = -net_activity if net_activity < 0 else Decimal("0")
            lines.append({
                "account_code": account_code,
                "account_name": account_name,
                "opening_balance": opening_balance,
                "debit": debit,
                "credit": credit,
                "movement_amount": net_activity,
            })

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
            if summary_cols and current_account_name:
                mapped = _col_map(summary_cols)
                numeric_values = _extract_numeric_values(summary_cols)
                net_activity = _extract_net_activity(mapped, numeric_values)
                opening_balance = _extract_opening_balance(mapped, numeric_values)
                append_account_row(current_account_code, current_account_name, net_activity, opening_balance)
                return
            if row_type.lower() == "data" and col_data:
                first_value = _col_value(col_data[0])
                first_id = _col_id(col_data[0])
                candidate_name = first_value or current_account_name
                candidate_code = first_id or current_account_code
                if not _looks_like_detail_row(candidate_name):
                    mapped = _col_map(col_data)
                    numeric_values = _extract_numeric_values(col_data)
                    net_activity = _extract_net_activity(mapped, numeric_values)
                    opening_balance = _extract_opening_balance(mapped, numeric_values)
                    append_account_row(candidate_code, candidate_name, net_activity, opening_balance)
                    return
            for child in children:
                handle_row(child, current_account_code=current_account_code, current_account_name=current_account_name)

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
