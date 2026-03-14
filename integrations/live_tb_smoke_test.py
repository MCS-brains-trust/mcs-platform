"""
Ad hoc live smoke checks for StatementHub accounting integrations.

Run inside Django context, for example:
    python manage.py shell < integrations/live_tb_smoke_test.py

This script is intentionally operational rather than unit-test based because it
is meant to validate live OAuth tokens, tenant availability, parser behavior,
and trial balance balance integrity in a deployed environment.
"""

from datetime import date
from decimal import Decimal

from integrations.models import XeroGlobalConnection, XeroTenant, QBGlobalConnection, QBTenant
from integrations.providers import get_provider
from integrations.views import _ensure_global_xero_token, _ensure_qb_tenant_token


def summarise_lines(lines):
    total_debit = sum((line.get("debit") or Decimal("0")) for line in lines)
    total_credit = sum((line.get("credit") or Decimal("0")) for line in lines)
    return {
        "line_count": len(lines),
        "total_debit": str(total_debit),
        "total_credit": str(total_credit),
        "difference": str(total_debit - total_credit),
        "sample": lines[:5],
    }


print("=== XERO ===")
xconn = XeroGlobalConnection.objects.filter(status="active").first() or XeroGlobalConnection.objects.first()
xtenant = XeroTenant.objects.first()
if not xconn:
    print({"status": "blocked", "reason": "No XeroGlobalConnection found"})
elif not xtenant:
    print({"status": "blocked", "reason": "No XeroTenant found"})
else:
    refreshed = _ensure_global_xero_token(xconn)
    xconn.refresh_from_db()
    print({
        "refresh_ok": refreshed,
        "connection_status": xconn.status,
        "token_expires_at": str(xconn.token_expires_at),
        "tenant": xtenant.tenant_name,
        "tenant_id": xtenant.tenant_id,
        "last_error": xconn.last_error,
    })
    if refreshed:
        provider = get_provider("xero")
        try:
            lines = provider.fetch_trial_balance(xconn.access_token, xtenant.tenant_id, date(2025, 6, 30))
            print({"status": "ok", **summarise_lines(lines)})
        except Exception as exc:
            print({"status": "failed", "error": type(exc).__name__, "detail": str(exc)})


print("=== QUICKBOOKS ===")
qconn = QBGlobalConnection.objects.filter(status="active").first() or QBGlobalConnection.objects.first()
qtenant = QBTenant.objects.first()
if not qconn:
    print({"status": "blocked", "reason": "No QBGlobalConnection found"})
elif not qtenant:
    print({"status": "blocked", "reason": "No QBTenant found"})
else:
    refreshed = _ensure_qb_tenant_token(qtenant)
    qtenant.refresh_from_db()
    print({
        "refresh_ok": refreshed,
        "company_name": qtenant.company_name,
        "realm_id": qtenant.realm_id,
        "token_expires_at": str(qtenant.token_expires_at),
    })
    if refreshed:
        provider = get_provider("quickbooks")
        try:
            lines = provider.fetch_trial_balance(qtenant.access_token, qtenant.realm_id, date(2025, 6, 30))
            print({"status": "ok", **summarise_lines(lines)})
        except Exception as exc:
            print({"status": "failed", "error": type(exc).__name__, "detail": str(exc)})
