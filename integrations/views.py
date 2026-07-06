"""
Integrations views.

Handles OAuth2 connection flows for Xero and QuickBooks,
trial balance fetching with staged import, and the mapping
review/approval workflow.

Includes the Global Xero Connection which provides practice-level
access to all client Xero organisations via a single advisor login.
"""
import json
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

import requests as http_requests
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.utils import DatabaseError
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import (
    AccountMapping,
    ClientAccountMapping,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
)
from .models import (
    AccountingConnection, ImportLog,
    XeroGlobalConnection, XeroTenant,
    QBGlobalConnection, QBTenant,

)
from .providers import get_provider, get_configured_providers, PROVIDERS, ProviderUserError

logger = logging.getLogger(__name__)

# Shared trial-balance imbalance tolerance. Used both when fetching from the
# provider and when committing a staged import so the two checks agree.
BALANCE_TOLERANCE = Decimal("0.02")


# ---------------------------------------------------------------------------
# Connection management (per-entity, legacy)
# ---------------------------------------------------------------------------

@login_required
def connection_manage(request, entity_pk):
    """Show and manage accounting platform connections for an entity."""
    entity = get_object_or_404(Entity, pk=entity_pk)
    connections = entity.accounting_connections.all()
    configured_providers = get_configured_providers()

    connected_names = set(
        connections.filter(status="active").values_list("provider", flat=True)
    )

    context = {
        "entity": entity,
        "connections": connections,
        "configured_providers": configured_providers,
        "connected_names": connected_names,
    }
    return render(request, "integrations/connection_manage.html", context)


@login_required
def oauth_connect(request, entity_pk, provider_name):
    """Initiate OAuth2 connection to an accounting platform."""
    entity = get_object_or_404(Entity, pk=entity_pk)
    provider = get_provider(provider_name)

    if not provider or not provider.is_configured():
        messages.error(request, f"{provider_name} integration is not configured.")
        return redirect("integrations:connection_manage", entity_pk=entity_pk)

    state = str(uuid.uuid4())
    request.session["oauth_state"] = state
    request.session["oauth_entity_pk"] = str(entity_pk)
    request.session["oauth_provider"] = provider_name
    if provider_name == "quickbooks":
        request.session["qb_global_oauth_state"] = state

    if provider_name == "quickbooks":
        callback_name = "integrations:qb_global_callback"
    elif provider_name == "xero":
        callback_name = "integrations:xero_global_callback"
    else:
        callback_name = "integrations:oauth_callback"
    redirect_uri = request.build_absolute_uri(reverse(callback_name))

    params = provider.get_authorize_params(redirect_uri, state)
    query_string = urlencode(params)
    auth_url = f"{provider.authorize_url}?{query_string}"

    return redirect(auth_url)


@login_required
def oauth_callback(request):
    """Handle OAuth2 callback from the accounting platform."""
    code = request.GET.get("code")
    state = request.GET.get("state")
    error = request.GET.get("error")
    realm_id = request.GET.get("realmId", "")

    expected_state = request.session.get("oauth_state")
    entity_pk = request.session.get("oauth_entity_pk")
    provider_name = request.session.get("oauth_provider")

    if not all([code, state, entity_pk, provider_name]) or state != expected_state:
        messages.error(request, "OAuth authentication failed: invalid state.")
        return redirect("core:entity_list")

    if error:
        messages.error(request, f"OAuth authentication failed: {error}")
        return redirect("integrations:connection_manage", entity_pk=entity_pk)

    provider = get_provider(provider_name)
    entity = get_object_or_404(Entity, pk=entity_pk)

    if provider_name == "quickbooks":
        callback_name = "integrations:qb_global_callback"
    elif provider_name == "xero":
        callback_name = "integrations:xero_global_callback"
    else:
        callback_name = "integrations:oauth_callback"
    redirect_uri = request.build_absolute_uri(reverse(callback_name))
    try:
        tokens = provider.exchange_code(code, redirect_uri)
        tenants = provider.get_tenants(tokens["access_token"])

        if provider_name == "quickbooks" and realm_id:
            tenant_id = realm_id
            tenant_name = "QuickBooks Company"
        elif len(tenants) == 1:
            tenant_id = tenants[0]["id"]
            tenant_name = tenants[0]["name"]
        elif len(tenants) > 1:
            request.session["oauth_tokens"] = tokens
            request.session["oauth_tenants"] = tenants
            return redirect("integrations:select_tenant", entity_pk=entity_pk)
        else:
            tenant_id = ""
            tenant_name = ""

        AccountingConnection.objects.filter(
            entity=entity, provider=provider_name, status="active"
        ).update(status="disconnected")

        conn = AccountingConnection.objects.create(
            entity=entity,
            provider=provider_name,
            status="active",
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_expires_at=timezone.now() + timedelta(seconds=tokens["expires_in"]),
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            connected_by=request.user,
        )

        messages.success(
            request,
            f"Successfully connected to {provider.display_name}"
            + (f" ({tenant_name})" if tenant_name else "")
        )

    except Exception as e:
        logger.error(f"OAuth callback error for {provider_name}: {e}")
        messages.error(request, f"Connection failed: {str(e)}")

    for key in ["oauth_state", "oauth_entity_pk", "oauth_provider", "oauth_tokens", "oauth_tenants"]:
        request.session.pop(key, None)

    return redirect("integrations:connection_manage", entity_pk=entity_pk)


@login_required
def select_tenant(request, entity_pk):
    """Show tenant/organisation selection when multiple are available."""
    entity = get_object_or_404(Entity, pk=entity_pk)
    tenants = request.session.get("oauth_tenants", [])
    provider_name = request.session.get("oauth_provider", "")

    if request.method == "POST":
        tenant_id = request.POST.get("tenant_id", "")
        tenant_name = ""
        for t in tenants:
            if t["id"] == tenant_id:
                tenant_name = t["name"]
                break

        tokens = request.session.get("oauth_tokens", {})

        AccountingConnection.objects.filter(
            entity=entity, provider=provider_name, status="active"
        ).update(status="disconnected")

        AccountingConnection.objects.create(
            entity=entity,
            provider=provider_name,
            status="active",
            access_token=tokens.get("access_token", ""),
            refresh_token=tokens.get("refresh_token", ""),
            token_expires_at=timezone.now() + timedelta(seconds=tokens.get("expires_in", 1800)),
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            connected_by=request.user,
        )

        for key in ["oauth_state", "oauth_entity_pk", "oauth_provider", "oauth_tokens", "oauth_tenants"]:
            request.session.pop(key, None)

        provider = get_provider(provider_name)
        messages.success(request, f"Connected to {provider.display_name} ({tenant_name})")
        return redirect("integrations:connection_manage", entity_pk=entity_pk)

    context = {
        "entity": entity,
        "tenants": tenants,
        "provider_name": provider_name,
    }
    return render(request, "integrations/select_tenant.html", context)


@login_required
@require_POST
def disconnect(request, connection_pk):
    """Disconnect an accounting platform connection."""
    conn = get_object_or_404(AccountingConnection, pk=connection_pk)
    entity_pk = conn.entity_id
    conn.status = "disconnected"
    conn.access_token = ""
    conn.refresh_token = ""
    conn.save()

    messages.success(request, f"Disconnected from {conn.get_provider_display()}.")
    return redirect("integrations:connection_manage", entity_pk=entity_pk)


# ---------------------------------------------------------------------------
# Token refresh helpers
# ---------------------------------------------------------------------------

def _ensure_valid_token(connection):
    """Refresh the access token if needed. Returns True if token is valid."""
    if not connection.needs_refresh:
        return True

    provider = get_provider(connection.provider)
    if not provider:
        return False

    try:
        tokens = provider.refresh_tokens(connection.refresh_token)
        connection.access_token = tokens["access_token"]
        connection.refresh_token = tokens["refresh_token"]
        connection.token_expires_at = timezone.now() + timedelta(
            seconds=tokens["expires_in"]
        )
        connection.status = "active"
        connection.last_error = ""
        connection.save()
        return True
    except Exception as e:
        logger.error(f"Token refresh failed for {connection}: {e}")
        connection.status = "expired"
        connection.last_error = str(e)
        connection.save()
        return False


def _ensure_global_xero_token(connection):
    """Refresh the global Xero connection token if needed. Returns True if valid.

    The Xero refresh token is single-use and rotates on every refresh. Because
    this is a practice-wide connection shared by every import, two concurrent
    imports could race the rotation: the loser sends an already-consumed refresh
    token, gets invalid_grant, and the except branch marks the whole connection
    expired, locking out the practice. We lock the connection row and re-check
    needs_refresh after acquiring the lock so only one refresh actually runs.
    """
    if not connection.needs_refresh:
        return True

    client_id = getattr(settings, "XERO_CLIENT_ID", "")
    client_secret = getattr(settings, "XERO_CLIENT_SECRET", "")

    with transaction.atomic():
        locked = XeroGlobalConnection.objects.select_for_update().get(pk=connection.pk)

        # Another import may have refreshed while we waited for the lock.
        if not locked.needs_refresh:
            connection.access_token = locked.access_token
            connection.refresh_token = locked.refresh_token
            connection.token_expires_at = locked.token_expires_at
            connection.status = locked.status
            return True

        try:
            resp = http_requests.post(
                "https://login.xero.com/identity/connect/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": locked.refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            locked.access_token = data["access_token"]
            locked.refresh_token = data.get("refresh_token", locked.refresh_token)
            locked.token_expires_at = timezone.now() + timedelta(
                seconds=data.get("expires_in", 1800)
            )
            locked.status = "active"
            locked.last_error = ""
            locked.save()

            connection.access_token = locked.access_token
            connection.refresh_token = locked.refresh_token
            connection.token_expires_at = locked.token_expires_at
            connection.status = locked.status
            connection.last_error = ""
            return True
        except Exception as e:
            logger.error(f"Global Xero token refresh failed: {e}")
            locked.status = "expired"
            locked.last_error = str(e)
            locked.save()
            connection.status = locked.status
            connection.last_error = locked.last_error
            return False


# ---------------------------------------------------------------------------
# Trial Balance Import (staged with learning)
# ---------------------------------------------------------------------------

@login_required
def import_from_cloud(request, fy_pk):
    """
    Pull trial balance from connected accounting platform.
    Checks all global connections (Xero, QB) and per-entity connections.
    If multiple providers are available, shows a unified selection page.
    """
    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    entity = fy.entity

    if fy.is_locked:
        messages.error(request, "Cannot import into a finalised financial year.")
        return redirect("core:financial_year_detail", pk=fy_pk)

    xero_conn = XeroGlobalConnection.objects.filter(status="active").first()
    qb_conn = QBGlobalConnection.objects.filter(status="active").first()

    linked_xero = XeroTenant.objects.filter(entity=entity).select_related("connection").first()
    linked_qb = QBTenant.objects.filter(entity=entity).select_related("connection").first()

    # Build the list of available import routes.
    # A route is available if the entity has a saved link OR if a global
    # connection exists (the tenant picker will handle the unlinked case).
    available_options = []
    if linked_xero or xero_conn:
        available_options.append({
            "name": "xero",
            "display": "Xero",
            "url": reverse("integrations:xero_select_tenant_import", kwargs={"fy_pk": fy.pk}),
        })
    if linked_qb or qb_conn:
        available_options.append({
            "name": "quickbooks",
            "display": "QuickBooks Online",
            "url": reverse("integrations:qb_select_tenant_import", kwargs={"fy_pk": fy.pk}),
        })

    if len(available_options) == 1:
        return redirect(available_options[0]["url"])
    if len(available_options) > 1:
        return redirect("integrations:select_provider_import", fy_pk=fy_pk)

    messages.warning(
        request,
        "No accounting platform connected. Connect Xero or QuickBooks first."
    )
    return redirect(f"{reverse('core:entity_detail', kwargs={'pk': entity.pk})}#software")


def _provider_dashboard_url(provider):
    """Return the URL name of the dashboard a user should land on to
    reconnect / reauthorise a given provider.  Falls back to the global
    connections hub so a future provider never dead-ends without an action.
    """
    name = getattr(provider, "name", "") or ""
    if name == "xero":
        return "integrations:xero_global_dashboard"
    if name == "quickbooks":
        return "integrations:qb_global_dashboard"
    return "integrations:connections_hub"


def _apply_bs_movement_differencing(entity, raw_lines):
    """Convert a provider's two-call (period + opening) figure set into
    Model A staged lines: ``account_code``, ``account_name``, ``debit``,
    ``credit``, ``movement_amount``. ``opening_balance`` is INTENTIONALLY
    OMITTED — under Model A the rolled-forward opening is supplied by
    commit_import's rollover-emission pass (B7 Pass B) on a dedicated
    source='rollover' row, never folded into the tb_import row.

    Behaviour:
      - For BS accounts (classified via _is_balance_sheet_account):
        compute true period movement = (closing position at to_date) -
        (closing position at from_date-1) from the provider's period +
        opening figures. Normalise to a single debit or credit side.
      - For P&L accounts: pass the period figures through unchanged
        (Xero's YTD column for P&L = period movement).

    Rows with zero everywhere (no movement) are skipped — consistent with
    the parser's existing zero-row filtering. Note that an account with a
    non-zero opening but zero in-period movement WILL be filtered here
    (no tb_import row); commit_import's rollover-emission pass and
    untouched-rollover block separately preserve those openings.
    """
    from core.views import _is_balance_sheet_account
    from core.models import ChartOfAccount

    coa_sections = dict(
        ChartOfAccount.objects.filter(
            entity_type=entity.entity_type, is_active=True,
        ).values_list("account_code", "section")
    )

    normalized = []
    for entry in raw_lines:
        code = entry.get("account_code") or ""
        name = entry.get("account_name") or ""
        period_debit = Decimal(str(entry.get("period_debit") or "0"))
        period_credit = Decimal(str(entry.get("period_credit") or "0"))
        opening_debit = Decimal(str(entry.get("opening_debit") or "0"))
        opening_credit = Decimal(str(entry.get("opening_credit") or "0"))

        is_bs = _is_balance_sheet_account(code, None, coa_sections)

        if is_bs:
            opening_pos = opening_debit - opening_credit
            closing_pos = period_debit - period_credit
            movement = closing_pos - opening_pos
            if movement >= 0:
                debit = movement
                credit = Decimal("0")
            else:
                debit = Decimal("0")
                credit = -movement
            movement_amount = movement
        else:
            debit = period_debit
            credit = period_credit
            movement_amount = period_debit - period_credit

        if debit == 0 and credit == 0:
            # Zero-movement rows produce no tb_import line; pure rollovers
            # (non-zero opening, zero movement) are preserved by
            # commit_import's rollover-emission / untouched-rollover passes
            # from the snapshot, not from this fetch.
            continue

        normalized.append({
            "account_code": code,
            "account_name": name,
            "debit": debit,
            "credit": credit,
            "movement_amount": movement_amount,
            # NB: opening_balance intentionally absent under Model A.
        })
    return normalized


def _do_cloud_import(
    request,
    fy,
    entity,
    provider,
    access_token,
    tenant_id,
    connection_obj,
    *,
    import_mode="trial_balance",
    from_date=None,
    to_date=None,
):
    """Execute a cloud import and redirect to the review page."""
    try:
        as_at_date = fy.end_date
        if import_mode == "period_movement":
            raw_lines = provider.fetch_period_movement(access_token, tenant_id, from_date, to_date)
            # Some providers (currently Xero) return a two-call shape with
            # separate period and opening figures so the view can compute
            # true period movement for balance-sheet accounts as
            # closing_at_to_date − closing_at_(from_date-1_day). Detect that
            # shape by the presence of the period_debit key and apply the
            # BS/P&L split here; other providers (e.g. QuickBooks) already
            # return correct opening_balance + movement and pass through.
            if raw_lines and "period_debit" in raw_lines[0]:
                raw_lines = _apply_bs_movement_differencing(entity, raw_lines)
        else:
            raw_lines = provider.fetch_trial_balance(access_token, tenant_id, as_at_date, start_date=fy.start_date)
        if not raw_lines:
            if import_mode == "period_movement":
                raise ValueError(
                    f"{provider.display_name} returned no usable lines for "
                    f"{from_date.isoformat()} to {to_date.isoformat()}."
                )
            raise ValueError(
                f"{provider.display_name} returned no usable trial balance lines for {as_at_date.isoformat()}."
            )

        total_debit = sum((line.get("debit") or 0) for line in raw_lines)
        total_credit = sum((line.get("credit") or 0) for line in raw_lines)
        imbalance = total_debit - total_credit
        if import_mode == "trial_balance" and provider.name != "quickbooks" and abs(imbalance) > BALANCE_TOLERANCE:
            raise ValueError(
                f"{provider.display_name} trial balance does not balance for {as_at_date.isoformat()} "
                f"(difference: {imbalance})."
            )

        # Merge duplicate account codes before mapping
        from core.tb_dedup import merge_duplicate_accounts
        raw_lines, merge_warnings = merge_duplicate_accounts(raw_lines)
        for w in merge_warnings:
            messages.warning(request, w)
        _sync_source_accounts_to_entity_coa(entity, raw_lines)
        staged_lines = _apply_learned_mappings(entity, raw_lines)
        from core.models import StagedImport
        StagedImport.objects.update_or_create(
            financial_year=fy,
            defaults={
                "user": request.user,
                "provider_name": provider.display_name,
                "import_mode": import_mode,
                "as_at_date": as_at_date,
                "from_date": from_date,
                "to_date": to_date,
                "lines": staged_lines,
                "merge_warnings": merge_warnings,
            },
        )
        if connection_obj:
            from integrations.models import QBTenantConnection, XeroTenantConnection
            type(connection_obj).objects.filter(pk=connection_obj.pk).update(
                last_sync_at=timezone.now()
            )
        return redirect("integrations:review_import", fy_pk=fy.pk)
    except ProviderUserError as e:
        # Intentionally user-actionable provider error (auth expired or scope
        # revoked). Surface the message verbatim and land the user on the
        # provider's reconnect dashboard, which renders the Connect/Reconnect
        # button. Logged at info level — this is expected periodic behaviour,
        # not a bug.
        logger.info(
            "Cloud import requires user action: provider=%s tenant=%s fy=%s entity=%s msg=%s",
            getattr(provider, "name", "unknown"), tenant_id,
            fy.pk, entity.pk, str(e),
        )
        if connection_obj:
            connection_obj.last_error = str(e)
            connection_obj.save(update_fields=["last_error"])
        messages.error(request, str(e))
        return redirect(_provider_dashboard_url(provider))
    except Exception as e:
        # Unexpected: keep the full stack trace in logs (no silent swallow)
        # but do NOT leak the raw exception text / type to the user.
        logger.exception("Cloud import failed", extra={
            "provider": getattr(provider, "name", "unknown"),
            "tenant_id": tenant_id,
            "financial_year_id": str(fy.pk),
            "entity_id": str(entity.pk),
            "import_mode": import_mode,
            "from_date": from_date.isoformat() if from_date else "",
            "to_date": to_date.isoformat() if to_date else "",
        })
        if connection_obj:
            connection_obj.last_error = str(e)
            connection_obj.save(update_fields=["last_error"])
        messages.error(
            request,
            f"{getattr(provider, 'display_name', 'Cloud')} import failed "
            "unexpectedly. The error has been logged; please try again or "
            "contact support."
        )
        return redirect("core:financial_year_detail", pk=fy.pk)



@login_required
def xero_select_tenant_import(request, fy_pk):
    """
    Show tenant selection for Xero import.
    If the entity already has a linked Xero tenant, pre-fill it and go straight
    to the date picker.  If not, show the full tenant list so the user can pick
    and optionally save the link - mirroring the QuickBooks flow.
    """
    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    entity = fy.entity

    # Use the global connection (new architecture)
    global_conn = XeroGlobalConnection.objects.filter(status="active").first()

    if not global_conn:
        messages.error(request, "No active Xero connection. Please connect Xero first.")
        return redirect("integrations:xero_global_dashboard")

    all_tenants = global_conn.tenants.select_related("entity").all()
    linked_tenant = all_tenants.filter(entity=entity).first()

    if request.method == "POST":
        tenant_id = request.POST.get("tenant_id", "").strip()
        link_tenant = request.POST.get("link_tenant") == "1"
        import_mode = request.POST.get("import_mode", "period_movement")
        from_date_raw = request.POST.get("from_date", "").strip()
        to_date_raw = request.POST.get("to_date", "").strip()

        # If no tenant_id posted, fall back to the already-linked tenant
        if not tenant_id and linked_tenant:
            tenant_id = linked_tenant.tenant_id

        if not tenant_id:
            messages.error(request, "Please select a Xero organisation.")
            return redirect("integrations:xero_select_tenant_import", fy_pk=fy_pk)

        tenant_obj = all_tenants.filter(tenant_id=tenant_id).first()
        if not tenant_obj:
            messages.error(request, "Selected Xero organisation not found.")
            return redirect("integrations:xero_select_tenant_import", fy_pk=fy_pk)

        # Optionally save the link for future imports
        if link_tenant:
            all_tenants.filter(entity=entity).update(entity=None)
            tenant_obj.entity = entity
            tenant_obj.save(update_fields=["entity"])
            linked_tenant = tenant_obj

        from_date = None
        to_date = None
        if import_mode == "period_movement":
            if not from_date_raw or not to_date_raw:
                messages.error(request, "Please choose both a from date and a to date.")
                return redirect("integrations:xero_select_tenant_import", fy_pk=fy_pk)
            try:
                from_date = timezone.datetime.fromisoformat(from_date_raw).date()
                to_date = timezone.datetime.fromisoformat(to_date_raw).date()
            except ValueError:
                messages.error(request, "Invalid import period. Please choose valid dates.")
                return redirect("integrations:xero_select_tenant_import", fy_pk=fy_pk)
            if from_date > to_date:
                messages.error(request, "The from date must be on or before the to date.")
                return redirect("integrations:xero_select_tenant_import", fy_pk=fy_pk)
            # Hard-require the import to start on the financial year's
            # start date. The rolled-forward opening balance is only
            # defined at that instant — a different from_date would
            # compose imported movement on top of an opening that
            # doesn't represent the position immediately before the
            # period, silently producing wrong balance-sheet figures.
            if fy.start_date and from_date != fy.start_date:
                messages.error(
                    request,
                    f"Period-movement imports must start on the financial "
                    f"year start date ({fy.start_date.isoformat()}). The "
                    f"opening balance carried forward from the prior year "
                    f"is only defined at that instant, so importing from "
                    f"{from_date.isoformat()} would produce incorrect "
                    f"balance-sheet figures.",
                )
                return redirect("integrations:xero_select_tenant_import", fy_pk=fy_pk)

        # Ensure token is valid
        if not _ensure_global_xero_token(global_conn):
            messages.error(request, "Xero token expired. Please reconnect.")
            return redirect("integrations:xero_global_dashboard")

        provider = get_provider("xero")
        return _do_cloud_import(
            request,
            fy,
            entity,
            provider,
            global_conn.access_token,
            tenant_obj.tenant_id,
            None,
            import_mode=import_mode,
            from_date=from_date,
            to_date=to_date,
        )

    context = {
        "fy": fy,
        "tenants": all_tenants,
        "linked_tenant": linked_tenant,
        "lock_selected_tenant": linked_tenant is not None,
        "default_from_date": fy.start_date.isoformat() if fy.start_date else "",
        "default_to_date": fy.end_date.isoformat() if fy.end_date else "",
    }
    return render(request, "integrations/xero_select_tenant_import.html", context)


@login_required
def xero_gl_summary_upload(request, fy_pk):
    """Upload a Xero General Ledger Summary XLSX export and stage it for the
    cloud-import review wizard.

    Plugs into the cloud-import pipeline (StagedImport -> review_import ->
    commit_import) so balance-sheet accounts get commit 222f57b's additive
    posting that composes the rolled-forward opening with the imported net
    movement.  The parser deliberately omits the ``opening_balance`` key from
    each staged line so the snapshot fallback in commit_import:888-907
    supplies the rolled-forward prior closing — that is the load-bearing
    contract; see the docstring of ``integrations/xero_gl_summary.py``.
    """
    from core.models import StagedImport, AccountMapping
    from core.tb_dedup import merge_duplicate_accounts
    from .xero_gl_summary import (
        parse_xero_gl_summary,
        XERO_TYPE_TO_STANDARD_CODE,
        resolve_equity_code,
    )

    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    entity = fy.entity

    if getattr(fy, "is_locked", False):
        messages.error(request, "Cannot import into a finalised financial year.")
        return redirect("core:financial_year_detail", pk=fy_pk)

    if not getattr(request.user, "can_do_accounting", False):
        messages.error(request, "You do not have permission.")
        return redirect("core:financial_year_detail", pk=fy_pk)

    if request.method != "POST":
        return render(
            request,
            "integrations/xero_gl_summary_upload.html",
            {"fy": fy},
        )

    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Please choose a Xero GL Summary .xlsx file.")
        return redirect("integrations:xero_gl_summary_upload", fy_pk=fy_pk)

    if upload.size > 20 * 1024 * 1024:
        messages.error(request, "File too large. Maximum size is 20MB.")
        return redirect("integrations:xero_gl_summary_upload", fy_pk=fy_pk)

    import os as _os
    file_ext = _os.path.splitext(upload.name)[1].lower()
    if file_ext != ".xlsx":
        messages.error(
            request,
            f"Unsupported file type: {file_ext}. Only .xlsx is supported.",
        )
        return redirect("integrations:xero_gl_summary_upload", fy_pk=fy_pk)

    try:
        raw_lines, period_from, period_to = parse_xero_gl_summary(upload)
    except ValueError as e:
        messages.error(request, f"Could not parse the file: {e}")
        return redirect("integrations:xero_gl_summary_upload", fy_pk=fy_pk)
    except Exception:
        logger.exception("Xero GL Summary parse failed for fy=%s", fy.pk)
        messages.error(
            request,
            "Could not parse the file. Make sure it is the Xero "
            "General Ledger Summary export in .xlsx format.",
        )
        return redirect("integrations:xero_gl_summary_upload", fy_pk=fy_pk)

    if not raw_lines:
        messages.error(request, "No account rows were found in the file.")
        return redirect("integrations:xero_gl_summary_upload", fy_pk=fy_pk)

    # Hard-require the file's period to start on the financial year's start
    # date. The rolled-forward opening is only defined at that instant, so a
    # mismatched period would compose imported movement on top of an opening
    # that doesn't represent the position immediately before the period —
    # producing silently-wrong balance-sheet figures. Same rule as the
    # cloud-import view enforces at views.py:639-660.
    if fy.start_date and period_from != fy.start_date:
        messages.error(
            request,
            f"GL Summary period start does not match the financial year. "
            f"The file is for '{period_from.isoformat()} to "
            f"{period_to.isoformat()}', but FY{fy.year_label} starts on "
            f"{fy.start_date.isoformat()}. The opening balance carried "
            f"forward from the prior year is only defined at that instant. "
            f"Please re-export the GL Summary in Xero with a 'From' date of "
            f"{fy.start_date.isoformat()}.",
        )
        return redirect("integrations:xero_gl_summary_upload", fy_pk=fy_pk)

    # Merge duplicate account codes (Xero exports can legitimately repeat
    # codes when an account has been renamed mid-period) and sync the source
    # accounts into the entity COA — same helpers the cloud API path uses.
    raw_lines, merge_warnings = merge_duplicate_accounts(raw_lines)
    for w in merge_warnings:
        messages.warning(request, w)
    _sync_source_accounts_to_entity_coa(entity, raw_lines)

    # Build the staged dicts via _apply_learned_mappings so any prior
    # ClientAccountMapping overlays the type-based suggestion that follows.
    staged_lines = _apply_learned_mappings(entity, raw_lines)

    # Pre-resolve the AccountMapping objects we'll need so we make at most
    # one DB query for the whole import.
    suggestion_for_idx = {}
    needed_codes = set()
    for idx, raw_line in enumerate(raw_lines):
        acct_type = (raw_line.get("account_type") or "").lower()
        if acct_type == "equity":
            std_code = resolve_equity_code(entity.entity_type)
        else:
            std_code = XERO_TYPE_TO_STANDARD_CODE.get(acct_type)
        if not std_code:
            logger.warning(
                "Unknown Xero account type %r for account %r — staging "
                "without a suggested mapping.",
                acct_type, raw_line.get("account_name"),
            )
            continue
        suggestion_for_idx[idx] = std_code
        needed_codes.add(std_code)

    mapping_lookup = {
        am.standard_code: am
        for am in AccountMapping.objects.filter(standard_code__in=needed_codes)
    } if needed_codes else {}

    # Overlay the type-based suggestion onto any staged line still marked
    # "new" (i.e. no learned ClientAccountMapping fired). Learned mappings
    # are not overwritten.
    for idx, staged_line in enumerate(staged_lines):
        if staged_line.get("confidence") != "new":
            continue
        std_code = suggestion_for_idx.get(idx)
        if not std_code:
            continue
        am = mapping_lookup.get(std_code)
        if not am:
            logger.warning(
                "AccountMapping standard_code %r not found — has "
                "seed_account_mappings been run?",
                std_code,
            )
            continue
        staged_line["mapped_id"] = str(am.pk)
        staged_line["mapped_label"] = am.line_item_label
        staged_line["confidence"] = "matched"

    # Write to StagedImport (the same model the cloud API path uses) so
    # review_import + commit_import handle this exactly like a cloud-pulled
    # period_movement import.
    StagedImport.objects.update_or_create(
        financial_year=fy,
        defaults={
            "user": request.user,
            "provider_name": "Xero GL Summary",
            "import_mode": "period_movement",
            "as_at_date": period_to,
            "from_date": period_from,
            "to_date": period_to,
            "lines": staged_lines,
            "merge_warnings": merge_warnings,
        },
    )

    return redirect("integrations:review_import", fy_pk=fy.pk)


@login_required
def review_import(request, fy_pk):
    """
    Review page showing fetched trial balance lines with pre-populated
    account mappings from the learning system. Accountant can approve,
    adjust, or reject individual mappings before committing.
    """
    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    from core.models import StagedImport
    try:
        staged_obj = StagedImport.objects.get(financial_year=fy)
        staged = {
            "fy_pk": str(fy.pk),
            "lines": staged_obj.lines,
            "import_mode": staged_obj.import_mode,
            "provider_name": staged_obj.provider_name,
            "as_at_date": staged_obj.as_at_date.isoformat(),
            "from_date": staged_obj.from_date.isoformat() if staged_obj.from_date else "",
            "to_date": staged_obj.to_date.isoformat() if staged_obj.to_date else "",
            "merge_warnings": staged_obj.merge_warnings,
        }
    except StagedImport.DoesNotExist:
        staged = None

    if not staged:
        messages.error(request, "No staged import data found. Please pull again.")
        return redirect("core:financial_year_detail", pk=fy_pk)

    lines = staged["lines"]

    standard_accounts = list(
        AccountMapping.objects.values("id", "standard_code", "line_item_label", "statement_section")
        .order_by("financial_statement", "display_order")
    )
    for sa in standard_accounts:
        sa["id"] = str(sa["id"])

    # Build entity accounts list for the JS search dropdown
    entity = fy.entity
    entity_accts = []
    for ea in EntityChartOfAccount.objects.filter(entity=entity).select_related("maps_to").order_by("account_code"):
        entity_accts.append({
            "code": ea.account_code,
            "name": ea.account_name,
            "section": ea.get_section_display(),
            "section_key": ea.section,
            "maps_to_id": str(ea.maps_to.pk) if ea.maps_to else "",
        })

    total = len(lines)
    auto_mapped = sum(1 for l in lines if l.get("mapped_id") or l.get("entity_acct_code"))
    unmapped = total - auto_mapped

    # Balance check — compute totals from staged data
    total_dr = sum(Decimal(str(l.get("debit", "0"))) for l in lines)
    total_cr = sum(Decimal(str(l.get("credit", "0"))) for l in lines)
    balance_diff = abs(total_dr - total_cr)
    TOLERANCE = BALANCE_TOLERANCE
    balance_blocked = balance_diff > TOLERANCE
    balance_warning = Decimal("0") < balance_diff <= TOLERANCE

    import_mode = staged.get("import_mode", "trial_balance")
    context = {
        "fy": fy,
        "lines": lines,
        "standard_accounts_json": json.dumps(standard_accounts),
        "entity_accounts_json": json.dumps(entity_accts),
        "total": total,
        "auto_mapped": auto_mapped,
        "unmapped": unmapped,
        "provider_name": staged.get("provider_name", "Cloud"),
        "as_at_date": staged.get("as_at_date", ""),
        "from_date": staged.get("from_date", ""),
        "to_date": staged.get("to_date", ""),
        "import_mode": import_mode,
        "balance_total_dr": total_dr,
        "balance_total_cr": total_cr,
        "balance_diff": balance_diff,
        "balance_blocked": balance_blocked if import_mode == "trial_balance" else False,
        "balance_warning": balance_warning if import_mode == "trial_balance" else False,
    }
    return render(request, "integrations/review_import.html", context)


@login_required
@require_POST
def commit_import(request, fy_pk):
    """
    Commit the reviewed import. Creates TrialBalanceLine records and
    updates ClientAccountMapping (the learning system) with any new
    or changed mappings.
    """
    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    from core.models import StagedImport
    try:
        staged_obj = StagedImport.objects.get(financial_year=fy)
        staged = {
            "fy_pk": str(fy.pk),
            "lines": staged_obj.lines,
            "import_mode": staged_obj.import_mode,
            "provider_name": staged_obj.provider_name,
            "as_at_date": staged_obj.as_at_date.isoformat(),
            "from_date": staged_obj.from_date.isoformat() if staged_obj.from_date else "",
            "to_date": staged_obj.to_date.isoformat() if staged_obj.to_date else "",
            "merge_warnings": staged_obj.merge_warnings,
        }
    except StagedImport.DoesNotExist:
        staged = None

    if not staged:
        messages.error(request, "No staged import data found.")
        return redirect("core:financial_year_detail", pk=fy_pk)

    entity = fy.entity
    staged_lines = staged["lines"]

    import_mode = staged.get("import_mode", "trial_balance")

    # Server-side balance validation — only for true trial balance imports
    total_dr = sum(Decimal(str(l.get("debit", "0"))) for l in staged_lines)
    total_cr = sum(Decimal(str(l.get("credit", "0"))) for l in staged_lines)
    balance_diff = abs(total_dr - total_cr)
    TOLERANCE = BALANCE_TOLERANCE

    if import_mode == "trial_balance":
        if balance_diff > TOLERANCE:
            messages.error(
                request,
                f"Import blocked \u2014 Trial Balance is out of balance. "
                f"Total debits ${total_dr:,.2f} vs total credits ${total_cr:,.2f} "
                f"\u2014 a difference of ${balance_diff:,.2f}. "
                f"Please correct the source data and re-import.",
            )
            return redirect("integrations:review_import", fy_pk=fy_pk)

        if balance_diff > 0 and not request.POST.get("rounding_acknowledged"):
            messages.error(
                request,
                f"This TB has a minor rounding difference of ${balance_diff:,.2f}. "
                f"Please tick the rounding acknowledgement checkbox to proceed.",
            )
            return redirect("integrations:review_import", fy_pk=fy_pk)

    # Model A wizard gate: every staged row must have an Entity Account
    # (COA) assigned before commit. The wizard pre-fills via
    # _apply_learned_mappings (CAM target_entity_account, then a courtesy
    # match against existing EntityChartOfAccount codes); anything still
    # blank requires the accountant to either pick an existing COA entry
    # or quick-add one via the AJAX endpoint. Without this gate, the
    # commit would fall back to writing source-system codes into
    # TrialBalanceLine.account_code (the pre-Phase 4 bug).
    unassigned = []
    for i, line in enumerate(staged_lines):
        entity_acct_code = (request.POST.get(f"entity_acct_{i}") or "").strip()
        if not entity_acct_code:
            unassigned.append({
                "index": i + 1,
                "source_code": line.get("account_code", "") or "(no code)",
                "source_name": line.get("account_name", ""),
            })
    if unassigned:
        first_few = ", ".join(
            f"#{u['index']} {u['source_code']} / {u['source_name']}"
            for u in unassigned[:3]
        )
        suffix = f" (and {len(unassigned) - 3} more)" if len(unassigned) > 3 else ""
        messages.error(
            request,
            f"Cannot commit — {len(unassigned)} row(s) have no Entity Account "
            f"(COA) assigned: {first_few}{suffix}. Click the Entity Account "
            f"column to pick an existing account, or use Quick-Add to create "
            f"a new one for the entity COA.",
        )
        return redirect("integrations:review_import", fy_pk=fy_pk)

    imported = 0
    unmapped = 0
    errors = []

    # ------------------------------------------------------------------
    # Snapshot existing comparative data BEFORE deleting lines.
    # Key = account_code. Under Model A there can be multiple rows per
    # code (one rollover + N tb_import rows from a prior import). The
    # snapshot AGGREGATES the numeric fields across all rows for a code
    # so the rolled-forward opening reflects the total per code, not
    # whichever row the database returned first. Non-numeric metadata
    # (mapped_line_item, comparatives_locked, etc.) keeps first-row-wins
    # semantics — the field shape supports only one value per code.
    # ------------------------------------------------------------------
    prior_data = {}
    for line_obj in fy.trial_balance_lines.filter(is_adjustment=False).order_by("account_code"):
        code = line_obj.account_code
        if code not in prior_data:
            prior_data[code] = {
                # Numeric fields (will be aggregated below)
                "opening_balance": Decimal("0"),
                "closing_sum": Decimal("0"),
                "prior_debit": Decimal("0"),
                "prior_credit": Decimal("0"),
                "prior_closing_balance": Decimal("0"),
                # First-row-wins metadata
                "prior_balance_override": line_obj.prior_balance_override,
                "prior_mapped_line_item": line_obj.prior_mapped_line_item,
                "reclassified": line_obj.reclassified,
                "comparatives_locked": line_obj.comparatives_locked,
                "mapped_line_item": line_obj.mapped_line_item,
                "account_name": line_obj.account_name,
                "source": line_obj.source,
            }
        # Aggregate numeric fields across all rows for this code so multi-
        # row Model A state is correctly summarised.
        prior_data[code]["opening_balance"] += line_obj.opening_balance or Decimal("0")
        prior_data[code]["closing_sum"] += line_obj.closing_balance or Decimal("0")
        prior_data[code]["prior_debit"] += line_obj.prior_debit or Decimal("0")
        prior_data[code]["prior_credit"] += line_obj.prior_credit or Decimal("0")
        prior_data[code]["prior_closing_balance"] += line_obj.prior_closing_balance or Decimal("0")

    # Wrap delete + create in a transaction so a failure midway
    # does not leave the entity's TB in a corrupt state.
    from django.db import transaction
    with transaction.atomic():
        fy.trial_balance_lines.filter(is_adjustment=False).delete()

        # =========================================================
        # MODEL A — Pass A: tb_import rows
        # One TrialBalanceLine per staged source line, preserving the
        # per-source-line breakdown for drilldown.  opening_balance is
        # ALWAYS 0 on these rows; closing = debit - credit. The
        # rolled-forward opening (if any) lives on a separate
        # source='rollover' row emitted in Pass B below.
        # =========================================================
        uploaded_codes = set()
        # Track which target codes have already had their prior-year
        # comparative snapshot written. When several staged source lines map to
        # the same StatementHub code we must emit the aggregated comparative
        # only ONCE — otherwise risk_engine re-sums prior_debit/prior_credit/
        # prior_closing_balance per code and multiplies the comparative by the
        # number of contributing rows.
        comparative_written_codes = set()

        for i, line in enumerate(staged_lines):
            mapping_id = request.POST.get(f"mapping_{i}", "").strip()
            entity_acct_code = request.POST.get(f"entity_acct_{i}", "").strip()
            mapped_item = None

            if mapping_id:
                try:
                    mapped_item = AccountMapping.objects.get(pk=mapping_id)
                except AccountMapping.DoesNotExist:
                    pass

            # If an entity account was assigned, look it up for its maps_to
            target_eca = None
            if entity_acct_code:
                try:
                    target_eca = EntityChartOfAccount.objects.select_related("maps_to").get(
                        entity=entity, account_code=entity_acct_code
                    )
                    if target_eca.maps_to and not mapped_item:
                        mapped_item = target_eca.maps_to
                except EntityChartOfAccount.DoesNotExist:
                    target_eca = None

            try:
                # Model A: TrialBalanceLine.account_code is the
                # StatementHub COA code — the entity_acct_code resolved by
                # the wizard. The wizard gate (B5) guarantees this is
                # non-empty by now; defensive fallback to source code only
                # for the legacy code path that bypasses the gate.
                acct_code = entity_acct_code or line["account_code"]
                comp = prior_data.get(acct_code, {})
                # Only the first tb_import row for a given code carries the
                # aggregated comparative; subsequent rows for the same code get
                # zeroes so the per-code total isn't multiplied downstream.
                write_comparative = acct_code not in comparative_written_codes
                if write_comparative:
                    py_debit = comp.get("prior_debit", Decimal("0"))
                    py_credit = comp.get("prior_credit", Decimal("0"))
                    py_closing = comp.get("prior_closing_balance", Decimal("0"))
                else:
                    py_debit = Decimal("0")
                    py_credit = Decimal("0")
                    py_closing = Decimal("0")

                # Model A: tb_import rows always have opening_balance=0.
                # The rolled-forward opening is emitted as a dedicated
                # source='rollover' row in Pass B below. Setting
                # opening_balance non-zero here would double-count it.
                opening = Decimal("0")
                debit = Decimal(str(line.get("debit", "0")))
                credit = Decimal(str(line.get("credit", "0")))
                closing = debit - credit

                description = ""
                if import_mode == "period_movement":
                    period_from = staged.get("from_date", "")
                    period_to = staged.get("to_date", "")
                    provider_name = staged.get("provider_name", "Cloud")
                    movement_amount = Decimal(str(line.get("movement_amount", debit - credit)))
                    description = (
                        f"{provider_name} import {period_from} to {period_to}; "
                        f"net movement {movement_amount}"
                    )

                TrialBalanceLine.objects.create(
                    financial_year=fy,
                    account_code=acct_code,
                    account_name=line["account_name"],
                    opening_balance=opening,
                    debit=debit,
                    credit=credit,
                    closing_balance=closing,
                    mapped_line_item=mapped_item,
                    is_adjustment=False,
                    source='tb_import',
                    description=description,
                    prior_debit=py_debit,
                    prior_credit=py_credit,
                    prior_closing_balance=py_closing,
                    prior_balance_override=comp.get("prior_balance_override", False) if write_comparative else False,
                    prior_mapped_line_item=comp.get("prior_mapped_line_item") if write_comparative else None,
                    reclassified=comp.get("reclassified", False) if write_comparative else False,
                    comparatives_locked=comp.get("comparatives_locked", False) if write_comparative else False,
                )

                comparative_written_codes.add(acct_code)
                uploaded_codes.add(acct_code)

                # Model A: write ClientAccountMapping keyed on the SOURCE
                # code/name (which may be blank for Xero bank accounts
                # named after the entity), retaining the translation so
                # subsequent imports auto-fill entity_acct_code from
                # target_entity_account in _apply_learned_mappings.
                source_code = (line.get("account_code") or "").strip()
                source_name = (line.get("account_name") or "").strip()
                if source_code or source_name:
                    ClientAccountMapping.objects.update_or_create(
                        entity=entity,
                        client_account_code=source_code,
                        client_account_name=source_name,
                        defaults={
                            "target_entity_account": target_eca,
                            "mapped_line_item": mapped_item,
                        },
                    )
                # Record confirmed mappings as global cross-entity hints
                if mapped_item:
                    from core.models import GlobalAccountMappingHint
                    GlobalAccountMappingHint.record_mapping(
                        entity_type=entity.entity_type,
                        account_code=acct_code,
                        account_name=line["account_name"],
                        mapped_line_item=mapped_item,
                    )

                imported += 1
                if not mapped_item:
                    unmapped += 1

            except Exception as e:
                errors.append(f"Line {i + 1} ({line.get('account_code', '?')}): {str(e)}")

        # =========================================================
        # MODEL A — Pass B: rollover emission for uploaded codes
        # For each unique StatementHub code in uploaded_codes that has a
        # non-zero rolled-forward opening in the snapshot, emit exactly
        # ONE source='rollover' row carrying that opening. P&L codes
        # have opening_balance=0 in snapshot and skip this naturally;
        # multiple tb_import rows for the same BS code share this one
        # opening row (no duplication).
        # =========================================================
        emitted_rollover_codes = set()
        for code in uploaded_codes:
            if code in emitted_rollover_codes:
                continue
            comp = prior_data.get(code, {})
            opening = comp.get("opening_balance", Decimal("0"))
            if opening == 0:
                continue
            # A tb_import row (Pass A) usually already carries this code's
            # comparative; only write it here if no earlier row did, so the
            # per-code comparative total is emitted exactly once.
            write_comparative = code not in comparative_written_codes
            TrialBalanceLine.objects.create(
                financial_year=fy,
                account_code=code,
                account_name=comp.get("account_name", ""),
                opening_balance=opening,
                debit=Decimal("0"),
                credit=Decimal("0"),
                closing_balance=opening,  # rollover row: closing = opening
                mapped_line_item=comp.get("mapped_line_item") or comp.get("prior_mapped_line_item"),
                is_adjustment=False,
                source='rollover',
                prior_debit=comp.get("prior_debit", Decimal("0")) if write_comparative else Decimal("0"),
                prior_credit=comp.get("prior_credit", Decimal("0")) if write_comparative else Decimal("0"),
                prior_closing_balance=comp.get("prior_closing_balance", Decimal("0")) if write_comparative else Decimal("0"),
                prior_balance_override=comp.get("prior_balance_override", False) if write_comparative else False,
                prior_mapped_line_item=comp.get("prior_mapped_line_item") if write_comparative else None,
                reclassified=comp.get("reclassified", False) if write_comparative else False,
                comparatives_locked=comp.get("comparatives_locked", False) if write_comparative else False,
            )
            comparative_written_codes.add(code)
            emitted_rollover_codes.add(code)

        # =========================================================
        # Phase 3 — untouched-rollover preservation
        # Re-create rolled-forward and comparative-only lines for codes
        # that existed in the prior snapshot but were NOT in the staged
        # data:
        #   1. P&L accounts from the prior year with no current-year
        #      activity — appear as comparative-only with opening=0.
        #   2. BS accounts with a rolled-forward opening but no
        #      current-period activity (stable fixed assets, accumulated
        #      depreciation, equity balances) — their opening must be
        #      preserved so the TB continues to balance.
        # In both cases debit = credit = 0 and closing = opening.
        # =========================================================
        for code, comp in prior_data.items():
            if code in uploaded_codes:
                continue
            # Skip accounts with no prior activity AND no rolled-forward
            # opening to preserve.
            if (comp["prior_debit"] == 0
                    and comp["prior_credit"] == 0
                    and comp.get("opening_balance", Decimal("0")) == 0):
                continue

            TrialBalanceLine.objects.create(
                financial_year=fy,
                account_code=code,
                account_name=comp.get("account_name", ""),
                opening_balance=comp.get("opening_balance", Decimal("0")),
                debit=Decimal("0"),
                credit=Decimal("0"),
                closing_balance=comp.get("opening_balance", Decimal("0")),
                mapped_line_item=comp.get("mapped_line_item") or comp.get("prior_mapped_line_item"),
                is_adjustment=False,
                source='rollover',
                prior_debit=comp["prior_debit"],
                prior_credit=comp["prior_credit"],
                prior_closing_balance=comp.get("prior_closing_balance", Decimal("0")),
                prior_balance_override=comp.get("prior_balance_override", False),
                prior_mapped_line_item=comp.get("prior_mapped_line_item"),
                reclassified=comp.get("reclassified", False),
                comparatives_locked=comp.get("comparatives_locked", False),
            )

    # Log the import
    connection_pk = staged.get("connection_pk")
    if connection_pk:
        try:
            connection = AccountingConnection.objects.get(pk=connection_pk)
            ImportLog.objects.create(
                connection=connection,
                financial_year=fy,
                imported_by=request.user,
                lines_imported=imported,
                lines_unmapped=unmapped,
                errors=errors,
                as_at_date=staged.get("as_at_date"),
            )
        except AccountingConnection.DoesNotExist:
            pass

    # Surface any merge warnings that were recorded at staging time
    merge_warnings = staged.get("merge_warnings", [])
    for w in merge_warnings:
        messages.warning(request, w)

    StagedImport.objects.filter(financial_year=fy).delete()

    # Auto-trigger risk engine after cloud import
    from core.signals import trigger_risk_recalc
    trigger_risk_recalc(fy, "cloud_import")

    if import_mode == "period_movement":
        messages.success(
            request,
            f"Imported {imported} Xero movement lines for {staged.get('from_date')} to {staged.get('to_date')}. "
            f"{unmapped} unmapped accounts need attention."
        )
    else:
        messages.success(
            request,
            f"Imported {imported} lines from cloud. "
            f"{unmapped} unmapped accounts need attention."
        )
    if errors:
        for err in errors[:5]:
            messages.warning(request, err)

    return redirect("core:financial_year_detail", pk=fy_pk)


# ---------------------------------------------------------------------------
# Quick-Add Entity Account (AJAX from Import Wizard)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def quick_add_entity_account(request):
    """
    AJAX endpoint to create a new EntityChartOfAccount entry from the
    import mapping wizard. Returns JSON with the created account details.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    entity_pk = data.get("entity_pk")
    section = data.get("section", "")
    account_code = data.get("account_code", "").strip()
    account_name = data.get("account_name", "").strip()
    tax_code = data.get("tax_code", "")
    classification = data.get("classification", "")
    maps_to_id = data.get("maps_to_id", "")

    if not entity_pk or not section or not account_code or not account_name:
        return JsonResponse({"success": False, "error": "Entity, section, code, and name are required."}, status=400)

    try:
        entity = Entity.objects.get(pk=entity_pk)
    except Entity.DoesNotExist:
        return JsonResponse({"success": False, "error": "Entity not found."}, status=404)

    # Check for duplicate code
    if EntityChartOfAccount.objects.filter(entity=entity, account_code=account_code).exists():
        return JsonResponse({"success": False, "error": f"Account code {account_code} already exists for this entity."}, status=400)

    maps_to = None
    if maps_to_id:
        try:
            maps_to = AccountMapping.objects.get(pk=maps_to_id)
        except AccountMapping.DoesNotExist:
            pass

    acct = EntityChartOfAccount.objects.create(
        entity=entity,
        account_code=account_code,
        account_name=account_name,
        section=section,
        tax_code=tax_code,
        classification=classification,
        maps_to=maps_to,
        is_custom=True,
    )

    return JsonResponse({
        "success": True,
        "account": {
            "pk": str(acct.pk),
            "code": acct.account_code,
            "name": acct.account_name,
            "section": acct.get_section_display(),
            "maps_to_id": str(maps_to.pk) if maps_to else "",
        },
    })


# ---------------------------------------------------------------------------
# Source account sync
# ---------------------------------------------------------------------------

def _guess_section_from_code(code):
    """Heuristic section assignment based on common Australian COA ranges."""
    from core.models import EntityChartOfAccount
    S = EntityChartOfAccount.StatementSection
    try:
        n = int(str(code).split(".")[0])
    except (ValueError, AttributeError):
        return S.EXPENSES
    if n < 1000:
        return S.REVENUE
    elif n < 2000:
        return S.EXPENSES
    elif n < 3000:
        return S.CURRENT_ASSETS
    elif n < 4000:
        return S.CURRENT_LIABILITIES
    elif n < 5000:
        return S.EQUITY
    else:
        return S.EXPENSES


def _sync_source_accounts_to_entity_coa(entity, raw_lines):
    """Reuse-only sync of source accounts against the entity COA.

    Under Model A, source-system codes (Xero account codes, blank for bank
    accounts named after the entity) are NOT StatementHub COA codes. The
    EntityChartOfAccount must hold only the entity's StatementHub COA. So
    this helper:

      - Matches by account name (case-insensitive) -> ensures active /
        seeds section if missing.
      - Falls back to matching by code (only when source code is non-blank).
      - Does NOT create new EntityChartOfAccount entries. If a source line
        has no name match and no code match, the wizard will require the
        accountant to assign an Entity Account (or quick-add one through
        the existing AJAX endpoint).

    Never overwrites account_name or maps_to.
    """
    from core.models import EntityChartOfAccount

    for line in raw_lines:
        code = str(line.get("account_code", "")).strip()
        name = str(line.get("account_name", "")).strip()
        if not name:
            continue

        section = _guess_section_from_code(code) if code else None

        # Step 1: Match by account name (case-insensitive)
        ea = EntityChartOfAccount.objects.filter(
            entity=entity,
            account_name__iexact=name,
        ).first()

        if ea:
            # Name match found — only ensure the record is active.
            # NEVER overwrite account_name — the accountant's name is the source of truth.
            fields_to_save = ["is_active"]
            ea.is_active = True
            if not ea.section and section:
                ea.section = section
                fields_to_save.append("section")
            ea.save(update_fields=fields_to_save)
            continue

        # Step 2: Match by account code (only when source code is non-blank)
        if code:
            ea = EntityChartOfAccount.objects.filter(
                entity=entity,
                account_code=code,
            ).first()

            if ea:
                # Code match — NEVER overwrite the existing name set by an accountant.
                fields_to_save = ["is_active"]
                ea.is_active = True
                if not ea.section and section:
                    ea.section = section
                    fields_to_save.append("section")
                ea.save(update_fields=fields_to_save)
                continue

        # Step 3: No match — DO NOT create. Under Model A the source code
        # is not a StatementHub COA code, so auto-creating an
        # EntityChartOfAccount with the source code would pollute the COA.
        # The wizard's "Entity Account (COA)" step requires the accountant
        # to either pick an existing COA entry or quick-add one (via the
        # AJAX endpoint integrations:quick_add_entity_account) for any
        # source line not auto-matched here.


# ---------------------------------------------------------------------------
# Learning system helpers
# ---------------------------------------------------------------------------

def _apply_learned_mappings(entity, raw_lines):
    """
    Look up existing ClientAccountMapping records for this entity and
    pre-populate the staged line's mapped_line_item AND target Entity
    Account (StatementHub COA) for each raw line.

    Lookup rules:
      1. Source code non-blank -> match ClientAccountMapping.client_account_code.
      2. Source code blank -> match ClientAccountMapping.client_account_name
         (case-insensitive). Bank accounts named after the entity often
         carry no code in Xero, so name is the only stable identifier.
      3. As a courtesy fallback, if no CAM exists but the source code is
         already a valid EntityChartOfAccount.account_code for this entity,
         pre-fill entity_acct_code with that. This handles the common case
         where Xero codes are HandiLedger-numeric and match the entity COA
         out of the gate.

    No name guessing beyond exact (case-insensitive) match.
    """
    # Index existing CAMs by code (non-blank) and by lowercase name (for
    # blank-code lookups).
    cam_by_code = {}
    cam_by_name = {}
    cam_qs = ClientAccountMapping.objects.filter(entity=entity).select_related(
        "mapped_line_item", "target_entity_account"
    )
    for cam in cam_qs:
        if cam.client_account_code:
            cam_by_code[cam.client_account_code] = cam
        if cam.client_account_name:
            cam_by_name.setdefault(cam.client_account_name.strip().lower(), cam)

    entity_coa_codes = set(
        EntityChartOfAccount.objects.filter(entity=entity, is_active=True)
        .values_list("account_code", flat=True)
    )
    entity_coa_names = {
        ea.account_code: ea.account_name
        for ea in EntityChartOfAccount.objects.filter(entity=entity, is_active=True)
    }

    staged = []
    for line in raw_lines:
        code = str(line.get("account_code", "")).strip()
        name = str(line.get("account_name", "")).strip()

        # Resolve CAM by code, falling back to name when code is blank.
        cam = None
        if code:
            cam = cam_by_code.get(code)
        if cam is None and name:
            cam = cam_by_name.get(name.lower())

        staged_line = {
            "account_code": code,        # source code, may be ""
            "account_name": name,
            "debit": str(line["debit"]),
            "credit": str(line["credit"]),
            "movement_amount": str(line.get("movement_amount", Decimal(str(line["debit"])) - Decimal(str(line["credit"])))),
            "mapped_id": "",
            "mapped_label": "",
            "confidence": "new",
            "entity_acct_code": "",
            "entity_acct_name": "",
        }
        # Carry account_type through for downstream type-based suggestion
        if "account_type" in line:
            staged_line["account_type"] = line["account_type"]
        # Only carry opening_balance into the staged dict when the raw line
        # supplies it. Model A: parsers and BS-differencing helper omit the
        # key so commit_import's rollover-emission pass supplies openings on
        # dedicated rollover rows instead of folding them into tb_import rows.
        if "opening_balance" in line:
            staged_line["opening_balance"] = str(line["opening_balance"])

        if cam:
            if cam.mapped_line_item:
                staged_line["mapped_id"] = str(cam.mapped_line_item.pk)
                staged_line["mapped_label"] = cam.mapped_line_item.line_item_label
                staged_line["confidence"] = "learned"
            if cam.target_entity_account:
                staged_line["entity_acct_code"] = cam.target_entity_account.account_code
                staged_line["entity_acct_name"] = cam.target_entity_account.account_name

        # Courtesy fallback: if no CAM target but the source code directly
        # matches an existing EntityChartOfAccount, pre-fill that. Skipped
        # when source code is blank (no auto-match possible).
        if not staged_line["entity_acct_code"] and code and code in entity_coa_codes:
            staged_line["entity_acct_code"] = code
            staged_line["entity_acct_name"] = entity_coa_names.get(code, "")
            if staged_line["confidence"] == "new":
                staged_line["confidence"] = "matched"

        staged.append(staged_line)

    return staged


# ---------------------------------------------------------------------------
# Global Xero Connection (Advisor-level)
# ---------------------------------------------------------------------------

@login_required
def xero_global_dashboard(request):
    """Dashboard showing the global Xero connection status and tenant list."""
    connection = XeroGlobalConnection.objects.filter(status="active").first()
    if not connection:
        connection = XeroGlobalConnection.objects.first()

    tenants = []
    tenant_count = 0
    if connection and connection.status == "active":
        tenants = connection.tenants.select_related("entity").all()
        tenant_count = tenants.count()

    xero_configured = bool(
        getattr(settings, "XERO_CLIENT_ID", "") and
        getattr(settings, "XERO_CLIENT_SECRET", "")
    )

    context = {
        "connection": connection,
        "tenants": tenants,
        "tenant_count": tenant_count,
        "xero_configured": xero_configured,
    }
    return render(request, "integrations/xero_global_dashboard.html", context)


@login_required
def xero_global_connect(request):
    """Initiate OAuth2 connection to Xero with accounting scopes.
    
    If ?rapid=1 is passed, the callback will auto-redirect back to the
    consent page so the user can quickly add multiple organisations.
    """
    state = str(uuid.uuid4())
    request.session["xero_global_oauth_state"] = state

    # Track rapid-connect mode
    if request.GET.get("rapid") == "1":
        request.session["xero_rapid_connect"] = True
    elif "xero_rapid_connect" not in request.session:
        request.session["xero_rapid_connect"] = False

    client_id = getattr(settings, "XERO_CLIENT_ID", "")
    redirect_uri = request.build_absolute_uri(
        reverse("integrations:xero_global_callback")
    )

    scopes = "openid profile email accounting.reports.read accounting.settings.read accounting.transactions.read offline_access"

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
    }
    query_string = urlencode(params)
    auth_url = f"https://login.xero.com/identity/connect/authorize?{query_string}"
    return redirect(auth_url)
@login_required
def xero_global_callback(request):
    """Handle OAuth2 callback for the global Xero connection.
    
    Accumulates tenants across multiple OAuth flows. If an active
    connection already exists, updates its tokens and adds any new
    tenants. In rapid-connect mode, auto-redirects back to the
    consent page for the next organisation.
    """
    code = request.GET.get("code")
    state = request.GET.get("state")
    error = request.GET.get("error")
    rapid_mode = request.session.get("xero_rapid_connect", False)

    expected_state = request.session.get("xero_global_oauth_state")

    if error:
        messages.error(request, f"Xero connection failed: {error}")
        request.session.pop("xero_rapid_connect", None)
        request.session.pop("xero_global_oauth_state", None)
        return redirect("integrations:xero_global_dashboard")

    if not code or state != expected_state:
        messages.error(request, "Xero connection failed: invalid state.")
        request.session.pop("xero_rapid_connect", None)
        request.session.pop("xero_global_oauth_state", None)
        return redirect("integrations:xero_global_dashboard")

    client_id = getattr(settings, "XERO_CLIENT_ID", "")
    client_secret = getattr(settings, "XERO_CLIENT_SECRET", "")
    redirect_uri = request.build_absolute_uri(
        reverse("integrations:xero_global_callback")
    )

    try:
        # Exchange code for tokens
        resp = http_requests.post(
            "https://login.xero.com/identity/connect/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        tokens = resp.json()

        # Get all tenants from the /connections endpoint
        tenant_resp = http_requests.get(
            "https://api.xero.com/connections",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=15,
        )
        tenant_resp.raise_for_status()
        tenants = tenant_resp.json()

        # Check for existing active connection
        conn = XeroGlobalConnection.objects.filter(status="active").first()

        if conn:
            # Update tokens on existing connection
            conn.access_token = tokens["access_token"]
            conn.refresh_token = tokens.get("refresh_token", conn.refresh_token)
            conn.token_expires_at = timezone.now() + timedelta(
                seconds=tokens.get("expires_in", 1800)
            )
            conn.last_tenant_refresh = timezone.now()
            conn.save()

            # Preserve existing entity links
            existing_links = {
                t.tenant_id: t.entity
                for t in conn.tenants.select_related("entity").all()
                if t.entity
            }

            # Sync tenants: add new ones, keep existing
            existing_tenant_ids = set(conn.tenants.values_list("tenant_id", flat=True))
            new_count = 0
            for t in tenants:
                tid = t.get("tenantId", "")
                if tid not in existing_tenant_ids:
                    xt = XeroTenant.objects.create(
                        connection=conn,
                        tenant_id=tid,
                        tenant_name=t.get("tenantName", "Unknown"),
                        tenant_type=t.get("tenantType", ""),
                    )
                    new_count += 1
                else:
                    # Update name in case it changed
                    conn.tenants.filter(tenant_id=tid).update(
                        tenant_name=t.get("tenantName", "Unknown")
                    )

            total = conn.tenants.count()
            if new_count > 0:
                messages.success(
                    request,
                    f"Added {new_count} new organisation(s)! Total: {total} connected."
                )
            else:
                messages.info(
                    request,
                    f"No new organisations added. Total: {total} connected."
                )
        else:
            # First connection — create new
            XeroGlobalConnection.objects.filter(status="active").update(status="disconnected")

            conn = XeroGlobalConnection.objects.create(
                status="active",
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token", ""),
                token_expires_at=timezone.now() + timedelta(
                    seconds=tokens.get("expires_in", 1800)
                ),
                connected_by=request.user,
                last_tenant_refresh=timezone.now(),
            )

            for t in tenants:
                XeroTenant.objects.create(
                    connection=conn,
                    tenant_id=t.get("tenantId", ""),
                    tenant_name=t.get("tenantName", "Unknown"),
                    tenant_type=t.get("tenantType", ""),
                )

            messages.success(
                request,
                f"Connected to Xero! {len(tenants)} organisation(s) accessible."
            )

    except Exception as e:
        logger.error(f"Xero global OAuth callback error: {e}")
        messages.error(request, f"Xero connection failed: {str(e)}")
        request.session.pop("xero_rapid_connect", None)
        request.session.pop("xero_global_oauth_state", None)
        return redirect("integrations:xero_global_dashboard")

    request.session.pop("xero_global_oauth_state", None)

    # In rapid-connect mode, auto-redirect back to consent page
    if rapid_mode:
        return redirect(reverse("integrations:xero_global_connect") + "?rapid=1")

    return redirect("integrations:xero_global_dashboard")


@login_required
@require_POST
def xero_global_refresh_tenants(request):
    """Refresh the list of tenants from the global Xero connection."""
    conn = XeroGlobalConnection.objects.filter(status="active").first()
    if not conn:
        messages.error(request, "No active Xero connection.")
        return redirect("integrations:xero_global_dashboard")

    if not _ensure_global_xero_token(conn):
        messages.error(request, "Xero token expired. Please reconnect.")
        return redirect("integrations:xero_global_dashboard")

    try:
        resp = http_requests.get(
            "https://api.xero.com/connections",
            headers={"Authorization": f"Bearer {conn.access_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        tenants = resp.json()

        # Preserve entity links — build a map of tenant_id → entity
        existing_links = {
            t.tenant_id: t.entity
            for t in conn.tenants.select_related("entity").all()
            if t.entity
        }

        # Delete old tenants and recreate — wrap in a transaction so a failure
        # midway can't leave the connection with its tenant list wiped and only
        # partially rebuilt (which would drop entity links).
        with transaction.atomic():
            conn.tenants.all().delete()

            for t in tenants:
                tid = t.get("tenantId", "")
                xt = XeroTenant.objects.create(
                    connection=conn,
                    tenant_id=tid,
                    tenant_name=t.get("tenantName", "Unknown"),
                    tenant_type=t.get("tenantType", ""),
                )
                # Restore entity link if it existed
                if tid in existing_links:
                    xt.entity = existing_links[tid]
                    xt.save(update_fields=["entity"])

            conn.last_tenant_refresh = timezone.now()
            conn.save(update_fields=["last_tenant_refresh"])

        messages.success(request, f"Refreshed tenant list: {len(tenants)} organisation(s) found.")

    except Exception as e:
        logger.error(f"Xero tenant refresh failed: {e}")
        messages.error(request, f"Failed to refresh tenants: {str(e)}")

    return redirect("integrations:xero_global_dashboard")


@login_required
def xero_stop_rapid(request):
    """Stop rapid-connect mode and return to dashboard."""
    request.session.pop("xero_rapid_connect", None)
    conn = XeroGlobalConnection.objects.filter(status="active").first()
    total = conn.tenants.count() if conn else 0
    messages.success(request, f"Rapid connect stopped. {total} organisation(s) connected.")
    return redirect("integrations:xero_global_dashboard")


@login_required
@require_POST
def xero_global_disconnect(request):
    """Disconnect the global Xero connection."""
    XeroGlobalConnection.objects.filter(status="active").update(
        status="disconnected",
        access_token="",
        refresh_token="",
    )
    messages.success(request, "Disconnected from Xero.")
    return redirect("integrations:xero_global_dashboard")


# ---------------------------------------------------------------------------
# Xero Practice Manager (XPM) Integration
# ---------------------------------------------------------------------------

@login_required
def xpm_dashboard(request):
    """XPM integration dashboard: connection status, sync history, manual trigger."""
    from .models import XPMConnection, XPMSyncLog

    connection = XPMConnection.objects.filter(status="active").first()
    if not connection:
        connection = XPMConnection.objects.first()

    sync_logs = XPMSyncLog.objects.all()[:20] if connection else []

    # Check if Xero credentials are configured
    xero_configured = bool(
        getattr(settings, "XERO_CLIENT_ID", "") and
        getattr(settings, "XERO_CLIENT_SECRET", "")
    )

    context = {
        "connection": connection,
        "sync_logs": sync_logs,
        "xero_configured": xero_configured,
    }
    return render(request, "integrations/xpm_dashboard.html", context)


@login_required
def xpm_connect(request):
    """Initiate OAuth2 connection to Xero for Practice Manager access."""
    state = str(uuid.uuid4())
    request.session["xpm_oauth_state"] = state

    client_id = getattr(settings, "XERO_CLIENT_ID", "")
    redirect_uri = request.build_absolute_uri(
        reverse("integrations:xpm_callback")
    )

    # XPM requires practicemanager scope in addition to standard Xero scopes
    scopes = "openid profile email practicemanager offline_access"

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
    }
    query_string = urlencode(params)
    auth_url = f"https://login.xero.com/identity/connect/authorize?{query_string}"

    return redirect(auth_url)


@login_required
def xpm_callback(request):
    """Handle OAuth2 callback for XPM connection."""
    from .models import XPMConnection

    code = request.GET.get("code")
    state = request.GET.get("state")
    error = request.GET.get("error")

    expected_state = request.session.get("xpm_oauth_state")

    if error:
        messages.error(request, f"XPM connection failed: {error}")
        return redirect("integrations:xpm_dashboard")

    if not code or state != expected_state:
        messages.error(request, "XPM connection failed: invalid state.")
        return redirect("integrations:xpm_dashboard")

    client_id = getattr(settings, "XERO_CLIENT_ID", "")
    client_secret = getattr(settings, "XERO_CLIENT_SECRET", "")
    redirect_uri = request.build_absolute_uri(
        reverse("integrations:xpm_callback")
    )

    try:
        # Exchange code for tokens
        resp = http_requests.post(
            "https://login.xero.com/identity/connect/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        tokens = resp.json()

        # Get tenants
        tenant_resp = http_requests.get(
            "https://api.xero.com/connections",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=15,
        )
        tenant_resp.raise_for_status()
        tenants = tenant_resp.json()

        if not tenants:
            messages.error(request, "No Xero organisations found.")
            return redirect("integrations:xpm_dashboard")

        # Use the first tenant (or the practice manager tenant)
        tenant = tenants[0]
        tenant_id = tenant.get("tenantId", "")
        tenant_name = tenant.get("tenantName", "Unknown")

        # If multiple tenants, store in session for selection
        if len(tenants) > 1:
            request.session["xpm_tokens"] = tokens
            request.session["xpm_tenants"] = [
                {"id": t["tenantId"], "name": t.get("tenantName", "Unknown")}
                for t in tenants
            ]
            return redirect("integrations:xpm_select_tenant")

        # Deactivate existing connections
        XPMConnection.objects.filter(status="active").update(status="disconnected")

        # Create new connection
        conn = XPMConnection.objects.create(
            status="active",
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", ""),
            token_expires_at=timezone.now() + timedelta(
                seconds=tokens.get("expires_in", 1800)
            ),
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            connected_by=request.user,
        )

        messages.success(request, f"Connected to Xero Practice Manager ({tenant_name})")

    except Exception as e:
        logger.error(f"XPM OAuth callback error: {e}")
        messages.error(request, f"XPM connection failed: {str(e)}")

    request.session.pop("xpm_oauth_state", None)
    return redirect("integrations:xpm_dashboard")


@login_required
def xpm_select_tenant(request):
    """Select which Xero tenant to use for XPM."""
    from .models import XPMConnection

    tenants = request.session.get("xpm_tenants", [])
    tokens = request.session.get("xpm_tokens", {})

    if request.method == "POST":
        tenant_id = request.POST.get("tenant_id", "")
        tenant_name = ""
        for t in tenants:
            if t["id"] == tenant_id:
                tenant_name = t["name"]
                break

        XPMConnection.objects.filter(status="active").update(status="disconnected")

        XPMConnection.objects.create(
            status="active",
            access_token=tokens.get("access_token", ""),
            refresh_token=tokens.get("refresh_token", ""),
            token_expires_at=timezone.now() + timedelta(
                seconds=tokens.get("expires_in", 1800)
            ),
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            connected_by=request.user,
        )

        for key in ["xpm_tokens", "xpm_tenants", "xpm_oauth_state"]:
            request.session.pop(key, None)

        messages.success(request, f"Connected to Xero Practice Manager ({tenant_name})")
        return redirect("integrations:xpm_dashboard")

    return render(request, "integrations/xpm_select_tenant.html", {
        "tenants": tenants,
    })


@login_required
@require_POST
def xpm_disconnect(request):
    """Disconnect from XPM."""
    from .models import XPMConnection

    XPMConnection.objects.filter(status="active").update(
        status="disconnected",
        access_token="",
        refresh_token="",
    )
    messages.success(request, "Disconnected from Xero Practice Manager.")
    return redirect("integrations:xpm_dashboard")


@login_required
@require_POST
def xpm_sync_now(request):
    """Trigger a manual full sync from XPM."""
    from .models import XPMConnection
    from .xpm_sync import run_full_sync

    connection = XPMConnection.objects.filter(status="active").first()
    if not connection:
        messages.error(request, "No active XPM connection. Connect first.")
        return redirect("integrations:xpm_dashboard")

    try:
        sync_log = run_full_sync(connection, user=request.user)

        if sync_log.status == "completed":
            messages.success(
                request,
                f"XPM sync completed: {sync_log.clients_created} clients created, "
                f"{sync_log.clients_updated} updated, "
                f"{sync_log.entities_created} entities created."
            )
        elif sync_log.status == "partial":
            messages.warning(
                request,
                f"XPM sync completed with errors: {sync_log.clients_created} created, "
                f"{sync_log.clients_updated} updated. "
                f"{len(sync_log.errors)} errors."
            )
        else:
            messages.error(request, f"XPM sync failed: {sync_log.errors}")

    except Exception as e:
        messages.error(request, f"XPM sync failed: {str(e)}")

    return redirect("integrations:xpm_dashboard")


# ---------------------------------------------------------------------------
# Token refresh helpers for QB
# ---------------------------------------------------------------------------

def _ensure_qb_tenant_token(qb_tenant):
    """Refresh a QBTenant's access token if needed. Returns True if valid.

    Intuit refresh tokens rotate on use, so two concurrent imports for the same
    tenant would race the rotation and one would fail with invalid_grant. Lock
    the tenant row and re-check needs_refresh after acquiring the lock so only
    one refresh actually runs.
    """
    if not qb_tenant.needs_refresh:
        return True

    import base64
    client_id = getattr(settings, "QBO_CLIENT_ID", "")
    client_secret = getattr(settings, "QBO_CLIENT_SECRET", "")
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    with transaction.atomic():
        locked = QBTenant.objects.select_for_update().get(pk=qb_tenant.pk)

        # Another import may have refreshed while we waited for the lock.
        if not locked.needs_refresh:
            qb_tenant.access_token = locked.access_token
            qb_tenant.refresh_token = locked.refresh_token
            qb_tenant.token_expires_at = locked.token_expires_at
            return True

        try:
            resp = http_requests.post(
                "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": locked.refresh_token,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            locked.access_token = data["access_token"]
            locked.refresh_token = data.get("refresh_token", locked.refresh_token)
            locked.token_expires_at = timezone.now() + timedelta(
                seconds=data.get("expires_in", 3600)
            )
            locked.save()

            qb_tenant.access_token = locked.access_token
            qb_tenant.refresh_token = locked.refresh_token
            qb_tenant.token_expires_at = locked.token_expires_at
            return True
        except Exception as e:
            logger.error(f"QB tenant token refresh failed for {qb_tenant}: {e}")
            return False




# ---------------------------------------------------------------------------
# Connections Hub — unified view of all platform connections
# ---------------------------------------------------------------------------

@login_required
def connections_hub(request):
    """Unified dashboard showing all accounting platform connections."""
    xero_conn = XeroGlobalConnection.objects.filter(status="active").first()
    qb_conn = QBGlobalConnection.objects.filter(status="active").first()
    xero_configured = bool(getattr(settings, "XERO_CLIENT_ID", "") and getattr(settings, "XERO_CLIENT_SECRET", ""))
    qb_configured = bool(getattr(settings, "QBO_CLIENT_ID", "") and getattr(settings, "QBO_CLIENT_SECRET", ""))
    context = {
        "xero_conn": xero_conn,
        "xero_tenant_count": xero_conn.tenants.count() if xero_conn else 0,
        "qb_conn": qb_conn,
        "qb_tenant_count": qb_conn.tenants.count() if qb_conn else 0,
        "xero_configured": xero_configured,
        "qb_configured": qb_configured,
    }
    return render(request, "integrations/connections_hub.html", context)


# ---------------------------------------------------------------------------
# Provider selection for import (when multiple providers are connected)
# ---------------------------------------------------------------------------

@login_required
def select_provider_import(request, fy_pk):
    """Show provider selection when multiple platforms are connected."""
    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    entity = fy.entity

    providers = []
    xero_conn = XeroGlobalConnection.objects.filter(status="active").first()
    if xero_conn:
        linked = XeroTenant.objects.filter(connection=xero_conn, entity=entity).first()
        providers.append({
            "name": "xero", "display": "Xero", "icon": "bi-cloud",
            "linked_name": linked.tenant_name if linked else None,
            "count": xero_conn.tenants.count(),
            "url": reverse("integrations:xero_select_tenant_import", args=[fy_pk]),
        })

    qb_conn = QBGlobalConnection.objects.filter(status="active").first()
    if qb_conn:
        linked = QBTenant.objects.filter(connection=qb_conn, entity=entity).first()
        providers.append({
            "name": "quickbooks", "display": "QuickBooks Online", "icon": "bi-cloud",
            "linked_name": linked.company_name if linked else None,
            "count": qb_conn.tenants.count(),
            "url": reverse("integrations:qb_select_tenant_import", args=[fy_pk]),
        })

    context = {
        "fy": fy,
        "providers": providers,
    }
    return render(request, "integrations/select_provider_import.html", context)


# ---------------------------------------------------------------------------
# Global QuickBooks Connection (Advisor-level)
# ---------------------------------------------------------------------------

@login_required
def qb_global_dashboard(request):
    """Dashboard showing the global QuickBooks connection status and company list."""
    connection = QBGlobalConnection.objects.filter(status="active").first()
    if not connection:
        connection = QBGlobalConnection.objects.first()

    tenants = []
    tenant_count = 0
    if connection and connection.status == "active":
        tenants = connection.tenants.select_related("entity").all()
        tenant_count = tenants.count()

    qb_configured = bool(
        getattr(settings, "QBO_CLIENT_ID", "") and
        getattr(settings, "QBO_CLIENT_SECRET", "")
    )

    context = {
        "connection": connection,
        "tenants": tenants,
        "tenant_count": tenant_count,
        "qb_configured": qb_configured,
    }
    return render(request, "integrations/qb_global_dashboard.html", context)


@login_required
def qb_global_connect(request):
    """Initiate OAuth2 connection to QuickBooks Online.
    
    Each QBO company requires its own OAuth flow. The realmId (company ID)
    comes back in the callback URL. Rapid mode auto-redirects back.
    """
    state = str(uuid.uuid4())
    request.session["qb_global_oauth_state"] = state

    if request.GET.get("rapid") == "1":
        request.session["qb_rapid_connect"] = True
    elif "qb_rapid_connect" not in request.session:
        request.session["qb_rapid_connect"] = False

    client_id = getattr(settings, "QBO_CLIENT_ID", "")
    redirect_uri = request.build_absolute_uri(
        reverse("integrations:qb_global_callback")
    )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "com.intuit.quickbooks.accounting",
        "state": state,
    }
    query_string = urlencode(params)
    auth_url = f"https://appcenter.intuit.com/connect/oauth2?{query_string}"

    return redirect(auth_url)


@login_required
def qb_global_callback(request):
    """Handle OAuth2 callback for QuickBooks Online.
    
    QBO returns the realmId as a query parameter, which identifies the company.
    Each company gets its own token set stored in QBTenant.
    """
    code = request.GET.get("code")
    state = request.GET.get("state")
    error = request.GET.get("error")
    realm_id = request.GET.get("realmId", "")
    rapid_mode = request.session.get("qb_rapid_connect", False)

    expected_state = request.session.get("qb_global_oauth_state")

    # Only the per-entity flow (oauth_connect) sets oauth_state/oauth_provider
    # to *this* request's freshly-minted state. The global flow (qb_global_connect)
    # leaves oauth_state stale from any earlier abandoned per-entity attempt, so
    # matching the returned state is what tells us the flow genuinely came from
    # the per-entity path and should auto-link to that entity.
    from_per_entity_flow = (
        request.session.get("oauth_provider") == "quickbooks"
        and request.session.get("oauth_state")
        and request.session.get("oauth_state") == state
    )

    def _clear_oauth_session():
        for key in [
            "qb_global_oauth_state", "qb_rapid_connect",
            "oauth_state", "oauth_entity_pk", "oauth_provider",
            "oauth_tokens", "oauth_tenants",
        ]:
            request.session.pop(key, None)

    if error:
        messages.error(request, f"QuickBooks connection failed: {error}")
        _clear_oauth_session()
        return redirect("integrations:qb_global_dashboard")

    if not code or state != expected_state:
        messages.error(request, "QuickBooks connection failed: invalid state.")
        _clear_oauth_session()
        return redirect("integrations:qb_global_dashboard")

    if not realm_id:
        messages.error(request, "QuickBooks connection failed: no company ID returned.")
        _clear_oauth_session()
        return redirect("integrations:qb_global_dashboard")

    import base64
    client_id = getattr(settings, "QBO_CLIENT_ID", "")
    client_secret = getattr(settings, "QBO_CLIENT_SECRET", "")
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    redirect_uri = request.build_absolute_uri(
        reverse("integrations:qb_global_callback")
    )

    try:
        # Exchange code for tokens
        resp = http_requests.post(
            "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=15,
        )
        resp.raise_for_status()
        tokens = resp.json()

        # Get company name from QBO API
        company_name = f"QBO Company {realm_id}"
        try:
            info_resp = http_requests.get(
                f"https://quickbooks.api.intuit.com/v3/company/{realm_id}/companyinfo/{realm_id}",
                headers={
                    "Authorization": f"Bearer {tokens['access_token']}",
                    "Accept": "application/json",
                },
                params={"minorversion": "65"},
                timeout=15,
            )
            if info_resp.status_code == 200:
                info_data = info_resp.json()
                company_name = info_data.get("CompanyInfo", {}).get("CompanyName", company_name)
        except Exception:
            pass  # Use default name

        # Get or create the global connection
        conn = QBGlobalConnection.objects.filter(status="active").first()
        if not conn:
            conn = QBGlobalConnection.objects.create(
                status="active",
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token", ""),
                token_expires_at=timezone.now() + timedelta(seconds=tokens.get("expires_in", 3600)),
                connected_by=request.user,
            )

        # Only auto-link to an entity when this callback genuinely came from the
        # per-entity connect flow; otherwise a stale oauth_entity_pk from an
        # abandoned attempt would wrongly bind this company to that entity.
        entity_pk = request.session.get("oauth_entity_pk") if from_per_entity_flow else None
        entity = None
        if entity_pk:
            entity = Entity.objects.filter(pk=entity_pk).first()
            if entity:
                QBTenant.objects.filter(entity=entity).exclude(realm_id=realm_id).update(entity=None)
        # Create or update the tenant
        tenant, created = QBTenant.objects.update_or_create(
            connection=conn,
            realm_id=realm_id,
            defaults={
                "company_name": company_name,
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token", ""),
                "token_expires_at": timezone.now() + timedelta(seconds=tokens.get("expires_in", 3600)),
                "entity": entity,
            },
        )

        # Update the global connection tokens too
        conn.access_token = tokens["access_token"]
        conn.refresh_token = tokens.get("refresh_token", conn.refresh_token)
        conn.token_expires_at = timezone.now() + timedelta(seconds=tokens.get("expires_in", 3600))
        conn.save()

        if entity:
            messages.success(request, f"Connected {company_name} to {entity.entity_name}.")
        elif created:
            messages.success(request, f"Connected to {company_name}! Total: {conn.tenants.count()} companies.")
        else:
            messages.info(request, f"Refreshed connection to {company_name}. Total: {conn.tenants.count()} companies.")

    except Exception as e:
        logger.error(f"QB global OAuth callback error: {e}")
        messages.error(request, f"QuickBooks connection failed: {str(e)}")
        _clear_oauth_session()
        return redirect("integrations:qb_global_dashboard")

    # Decide the redirect target before clearing session state.
    redirect_to_entity = entity_pk if (entity and from_per_entity_flow) else None
    request.session.pop("qb_global_oauth_state", None)
    if rapid_mode:
        # Preserve rapid-connect state for the next organisation; other one-shot
        # oauth keys are cleared so a later flow can't read them stale.
        for key in ["oauth_state", "oauth_entity_pk", "oauth_provider", "oauth_tokens", "oauth_tenants"]:
            request.session.pop(key, None)
        return redirect(reverse("integrations:qb_global_connect") + "?rapid=1")
    _clear_oauth_session()
    if redirect_to_entity:
        return redirect("integrations:connection_manage", entity_pk=redirect_to_entity)
    return redirect("integrations:qb_global_dashboard")


@login_required
def qb_stop_rapid(request):
    """Stop rapid-connect mode for QuickBooks."""
    request.session.pop("qb_rapid_connect", None)
    conn = QBGlobalConnection.objects.filter(status="active").first()
    total = conn.tenants.count() if conn else 0
    messages.success(request, f"Rapid connect stopped. {total} QuickBooks company(ies) connected.")
    return redirect("integrations:qb_global_dashboard")


@login_required
@require_POST
def qb_global_disconnect(request):
    """Disconnect the global QuickBooks connection."""
    QBGlobalConnection.objects.filter(status="active").update(
        status="disconnected",
        access_token="",
        refresh_token="",
    )
    messages.success(request, "Disconnected from QuickBooks Online.")
    return redirect("integrations:qb_global_dashboard")


@login_required
def qb_select_tenant_import(request, fy_pk):
    """Select which QBO company and period to import movement from."""
    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    entity = fy.entity

    conn = QBGlobalConnection.objects.filter(status="active").first()
    if not conn:
        messages.error(request, "No active QuickBooks connection. Please connect first.")
        return redirect("integrations:qb_global_dashboard")

    tenants = conn.tenants.select_related("entity").all()
    linked_tenant = tenants.filter(entity=entity).first()

    if request.method == "POST":
        realm_id = request.POST.get("tenant_id", "")
        link_tenant = request.POST.get("link_tenant") == "1"
        import_mode = "trial_balance"
        from_date = None
        to_date = None

        if not realm_id:
            messages.error(request, "Please select a company.")
            return redirect("integrations:qb_select_tenant_import", fy_pk=fy_pk)

        tenant_obj = tenants.filter(realm_id=realm_id).first()
        if not tenant_obj:
            messages.error(request, "Company not found.")
            return redirect("integrations:qb_select_tenant_import", fy_pk=fy_pk)

        if link_tenant:
            tenants.filter(entity=entity).update(entity=None)
            tenant_obj.entity = entity
            tenant_obj.save(update_fields=["entity"])

        if not _ensure_qb_tenant_token(tenant_obj):
            messages.error(
                request,
                f"The QuickBooks token for '{tenant_obj.company_name}' has expired and could not be "
                f"refreshed automatically. Please go to Connections → QuickBooks and reconnect this company."
            )
            return redirect("integrations:qb_select_tenant_import", fy_pk=fy_pk)

        provider = get_provider("quickbooks")
        if not provider:
            messages.error(request, "QuickBooks integration is not configured on this server.")
            return redirect("integrations:qb_global_dashboard")

        return _do_cloud_import(
            request,
            fy,
            entity,
            provider,
            tenant_obj.access_token,
            tenant_obj.realm_id,
            None,
            import_mode=import_mode,
            from_date=from_date,
            to_date=to_date,
        )

    context = {
        "fy": fy,
        "tenants": tenants,
        "linked_tenant": linked_tenant,
        "provider_name": "QuickBooks Online",
        "default_from_date": fy.start_date.isoformat() if fy.start_date else "",
        "default_to_date": fy.end_date.isoformat() if fy.end_date else "",
    }
    return render(request, "integrations/qb_select_tenant_import.html", context)

# MYOB views removed — not currently supported

# Placeholder to avoid import errors
def _myob_removed():
    pass
# END MYOB PLACEHOLDER
