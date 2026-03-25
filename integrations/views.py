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

import requests as http_requests
from django.conf import settings
from django.contrib import messages
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
from .providers import get_provider, get_configured_providers, PROVIDERS

logger = logging.getLogger(__name__)


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

    callback_name = (
        "integrations:qb_global_callback"
        if provider_name == "quickbooks"
        else "integrations:oauth_callback"
    )
    redirect_uri = request.build_absolute_uri(reverse(callback_name))

    params = provider.get_authorize_params(redirect_uri, state)
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
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

    callback_name = (
        "integrations:qb_global_callback"
        if provider_name == "quickbooks"
        else "integrations:oauth_callback"
    )
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
    """Refresh the global Xero connection token if needed. Returns True if valid."""
    if not connection.needs_refresh:
        return True

    client_id = getattr(settings, "XERO_CLIENT_ID", "")
    client_secret = getattr(settings, "XERO_CLIENT_SECRET", "")

    try:
        resp = http_requests.post(
            "https://login.xero.com/identity/connect/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": connection.refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        connection.access_token = data["access_token"]
        connection.refresh_token = data.get("refresh_token", connection.refresh_token)
        connection.token_expires_at = timezone.now() + timedelta(
            seconds=data.get("expires_in", 1800)
        )
        connection.status = "active"
        connection.last_error = ""
        connection.save()
        return True
    except Exception as e:
        logger.error(f"Global Xero token refresh failed: {e}")
        connection.status = "expired"
        connection.last_error = str(e)
        connection.save()
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

    linked_xero = XeroTenant.objects.filter(entity=entity).select_related("connection").first()
    linked_qb = QBTenant.objects.filter(entity=entity).select_related("connection").first()

    linked_options = []
    if linked_xero:
        linked_options.append({
            "name": "xero",
            "display": "Xero",
            "url": reverse("integrations:xero_select_tenant_import", kwargs={"fy_pk": fy.pk}),
        })
    if linked_qb:
        linked_options.append({
            "name": "quickbooks",
            "display": "QuickBooks Online",
            "url": reverse("integrations:qb_select_tenant_import", kwargs={"fy_pk": fy.pk}),
        })

    if len(linked_options) == 1:
        return redirect(linked_options[0]["url"])
    if len(linked_options) > 1:
        return redirect("integrations:select_provider_import", fy_pk=fy_pk)

    xero_conn = XeroGlobalConnection.objects.filter(status="active").first()
    qb_conn = QBGlobalConnection.objects.filter(status="active").first()
    if xero_conn or qb_conn:
        messages.error(
            request,
            "This entity is not linked to accounting software yet. Open the Software tab on the entity and link Xero or QuickBooks first."
        )
        return redirect(f"{reverse('core:entity_detail', kwargs={'pk': entity.pk})}#software")

    messages.warning(
        request,
        "No accounting platform connected. Connect Xero or QuickBooks first."
    )
    return redirect(f"{reverse('core:entity_detail', kwargs={'pk': entity.pk})}#software")


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
        if abs(imbalance) > Decimal("0.01"):
            raise ValueError(
                f"{provider.display_name} trial balance does not balance for {as_at_date.isoformat()} "
                f"(difference: {imbalance})."
            )

        # Merge duplicate account codes before mapping
        from core.tb_dedup import merge_duplicate_accounts
        raw_lines, merge_warnings = merge_duplicate_accounts(raw_lines)
        for w in merge_warnings:
            messages.warning(request, w)
        staged_lines = _apply_learned_mappings(entity, raw_lines)
        request.session["staged_import"] = {
            "fy_pk": str(fy.pk),
            "connection_pk": str(connection_obj.pk) if connection_obj else "",
            "as_at_date": as_at_date.isoformat(),
            "provider_name": provider.display_name,
            "lines": staged_lines,
            "merge_warnings": merge_warnings,
            "import_mode": import_mode,
            "from_date": from_date.isoformat() if from_date else "",
            "to_date": to_date.isoformat() if to_date else "",
        }
        # Force session save to DB before redirect so the next
        # request (possibly handled by a different Gunicorn worker)
        # can read the staged data from the database backend.
        request.session.modified = True
        try:
            request.session.save()
        except DatabaseError:
            # Session row may not exist yet (race condition) — create fresh
            request.session.create()
        if connection_obj:
            from integrations.models import QBTenantConnection, XeroTenantConnection
            type(connection_obj).objects.filter(pk=connection_obj.pk).update(
                last_sync_at=timezone.now()
            )
        return redirect("integrations:review_import", fy_pk=fy.pk)
    except Exception as e:
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
        messages.error(request, f"Import failed: {str(e)}")
        return redirect("core:financial_year_detail", pk=fy.pk)



@login_required
def xero_select_tenant_import(request, fy_pk):
    """
    Show tenant selection for Xero import.
    User picks which Xero organisation and period to pull from.
    """
    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    entity = fy.entity

    linked_tenant = XeroTenant.objects.filter(entity=entity).select_related("connection").first()
    if not linked_tenant or not linked_tenant.connection_id:
        messages.error(request, "This entity is not linked to a Xero organisation yet. Open the Software tab and link Xero first.")
        return redirect(f"{reverse('core:entity_detail', kwargs={'pk': entity.pk})}#software")

    global_conn = linked_tenant.connection
    tenants = [linked_tenant]

    if request.method == "POST":
        tenant_id = linked_tenant.tenant_id
        import_mode = request.POST.get("import_mode", "period_movement")
        from_date_raw = request.POST.get("from_date", "").strip()
        to_date_raw = request.POST.get("to_date", "").strip()

        if not tenant_id:
            messages.error(request, "This entity's saved Xero link is missing the organisation identifier.")
            return redirect(f"{reverse('core:entity_detail', kwargs={'pk': entity.pk})}#software")

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
            tenant_id,
            None,
            import_mode=import_mode,
            from_date=from_date,
            to_date=to_date,
        )

    context = {
        "fy": fy,
        "tenants": tenants,
        "linked_tenant": linked_tenant,
        "lock_selected_tenant": True,
        "default_from_date": fy.start_date.isoformat() if fy.start_date else "",
        "default_to_date": fy.end_date.isoformat() if fy.end_date else "",
    }
    return render(request, "integrations/xero_select_tenant_import.html", context)


@login_required
def review_import(request, fy_pk):
    """
    Review page showing fetched trial balance lines with pre-populated
    account mappings from the learning system. Accountant can approve,
    adjust, or reject individual mappings before committing.
    """
    fy = get_object_or_404(FinancialYear, pk=fy_pk)
    staged = request.session.get("staged_import")

    if not staged or staged.get("fy_pk") != str(fy_pk):
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
    TOLERANCE = Decimal("0.02")
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
    staged = request.session.get("staged_import")

    if not staged or staged.get("fy_pk") != str(fy_pk):
        messages.error(request, "No staged import data found.")
        return redirect("core:financial_year_detail", pk=fy_pk)

    entity = fy.entity
    staged_lines = staged["lines"]

    import_mode = staged.get("import_mode", "trial_balance")

    # Server-side balance validation — only for true trial balance imports
    total_dr = sum(Decimal(str(l.get("debit", "0"))) for l in staged_lines)
    total_cr = sum(Decimal(str(l.get("credit", "0"))) for l in staged_lines)
    balance_diff = abs(total_dr - total_cr)
    TOLERANCE = Decimal("0.02")

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

    imported = 0
    unmapped = 0
    errors = []

    # ------------------------------------------------------------------
    # Snapshot existing comparative data BEFORE deleting lines.
    # Key = account_code.  Cloud providers (Xero, QB) don't return PY
    # data, so we must preserve it from rollover / prior imports.
    # ------------------------------------------------------------------
    prior_data = {}
    for line_obj in fy.trial_balance_lines.filter(is_adjustment=False).order_by("account_code"):
        if line_obj.account_code not in prior_data:
            prior_data[line_obj.account_code] = {
                "prior_debit": line_obj.prior_debit,
                "prior_credit": line_obj.prior_credit,
                "prior_closing_balance": line_obj.prior_closing_balance,
                "prior_balance_override": line_obj.prior_balance_override,
                "prior_mapped_line_item": line_obj.prior_mapped_line_item,
                "reclassified": line_obj.reclassified,
                "comparatives_locked": line_obj.comparatives_locked,
                "mapped_line_item": line_obj.mapped_line_item,
                "account_name": line_obj.account_name,
            }

    fy.trial_balance_lines.filter(is_adjustment=False).delete()

    uploaded_codes = set()

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
        if entity_acct_code and not mapped_item:
            try:
                ea = EntityChartOfAccount.objects.select_related("maps_to").get(
                    entity=entity, account_code=entity_acct_code
                )
                if ea.maps_to:
                    mapped_item = ea.maps_to
            except EntityChartOfAccount.DoesNotExist:
                pass

        try:
            opening = Decimal(str(line.get("opening_balance", "0")))
            debit = Decimal(str(line.get("debit", "0")))
            credit = Decimal(str(line.get("credit", "0")))
            closing = opening + debit - credit

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

            # Restore prior year comparatives from snapshot (matched by code)
            acct_code = line["account_code"]
            comp = prior_data.get(acct_code, {})
            py_debit = comp.get("prior_debit", Decimal("0"))
            py_credit = comp.get("prior_credit", Decimal("0"))

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
                description=description,
                prior_debit=py_debit,
                prior_credit=py_credit,
                prior_closing_balance=comp.get("prior_closing_balance", Decimal("0")),
                prior_balance_override=comp.get("prior_balance_override", False),
                prior_mapped_line_item=comp.get("prior_mapped_line_item"),
                reclassified=comp.get("reclassified", False),
                comparatives_locked=comp.get("comparatives_locked", False),
            )

            uploaded_codes.add(acct_code)

            # Update the learning system
            ClientAccountMapping.objects.update_or_create(
                entity=entity,
                client_account_code=acct_code,
                defaults={
                    "client_account_name": line["account_name"],
                    "mapped_line_item": mapped_item,
                },
            )

            imported += 1
            if not mapped_item:
                unmapped += 1

        except Exception as e:
            errors.append(f"Line {i + 1} ({line.get('account_code', '?')}): {str(e)}")

    # ------------------------------------------------------------------
    # Re-create comparative-only lines for accounts that existed in the
    # prior snapshot but were NOT in the cloud import.  These are
    # typically P&L accounts from the prior year that have no current-year
    # activity yet but must appear in the comparative column.
    # ------------------------------------------------------------------
    for code, comp in prior_data.items():
        if code in uploaded_codes:
            continue
        if comp["prior_debit"] == 0 and comp["prior_credit"] == 0:
            continue

        TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code=code,
            account_name=comp.get("account_name", ""),
            opening_balance=Decimal("0"),
            debit=Decimal("0"),
            credit=Decimal("0"),
            closing_balance=Decimal("0"),
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

    request.session.pop("staged_import", None)

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
# Learning system helpers
# ---------------------------------------------------------------------------

def _apply_learned_mappings(entity, raw_lines):
    """
    Look up existing ClientAccountMapping records for this entity and
    pre-populate the mapped_line_item for each raw line.
    Also pre-match against EntityChartOfAccount by code.
    """
    existing_mappings = {
        cam.client_account_code: cam
        for cam in ClientAccountMapping.objects.filter(entity=entity)
        .select_related("mapped_line_item")
    }

    # Build entity COA lookup by code
    entity_coa = {
        ea.account_code.lower(): ea
        for ea in EntityChartOfAccount.objects.filter(entity=entity)
        .select_related("maps_to")
    }

    staged = []
    for line in raw_lines:
        code = line["account_code"]
        cam = existing_mappings.get(code)

        staged_line = {
            "account_code": code,
            "account_name": line["account_name"],
            "opening_balance": str(line["opening_balance"]),
            "debit": str(line["debit"]),
            "credit": str(line["credit"]),
            "movement_amount": str(line.get("movement_amount", Decimal(str(line["debit"])) - Decimal(str(line["credit"])))),
            "mapped_id": "",
            "mapped_label": "",
            "confidence": "new",
            "entity_acct_code": "",
            "entity_acct_name": "",
        }

        if cam and cam.mapped_line_item:
            staged_line["mapped_id"] = str(cam.mapped_line_item.pk)
            staged_line["mapped_label"] = (
                f"{cam.mapped_line_item.standard_code} - "
                f"{cam.mapped_line_item.line_item_label}"
            )
            staged_line["confidence"] = "learned"

        # Try to match entity COA by code
        ea = entity_coa.get(code.lower())
        if ea:
            staged_line["entity_acct_code"] = ea.account_code
            staged_line["entity_acct_name"] = ea.account_name
            if staged_line["confidence"] == "new":
                staged_line["confidence"] = "matched"
            # If entity account has a maps_to and we don't have one yet, use it
            if ea.maps_to and not staged_line["mapped_id"]:
                staged_line["mapped_id"] = str(ea.maps_to.pk)
                staged_line["mapped_label"] = (
                    f"{ea.maps_to.standard_code} - {ea.maps_to.line_item_label}"
                )

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
        "access_type": "offline",
    }
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
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
        return redirect("integrations:xero_global_dashboard")

    if not code or state != expected_state:
        messages.error(request, "Xero connection failed: invalid state.")
        request.session.pop("xero_rapid_connect", None)
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

        # Delete old tenants and recreate
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
        "access_type": "offline",
    }
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
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
    """Refresh a QBTenant's access token if needed. Returns True if valid."""
    if not qb_tenant.needs_refresh:
        return True

    import base64
    client_id = getattr(settings, "QBO_CLIENT_ID", "")
    client_secret = getattr(settings, "QBO_CLIENT_SECRET", "")
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    try:
        resp = http_requests.post(
            "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": qb_tenant.refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        qb_tenant.access_token = data["access_token"]
        qb_tenant.refresh_token = data.get("refresh_token", qb_tenant.refresh_token)
        qb_tenant.token_expires_at = timezone.now() + timedelta(
            seconds=data.get("expires_in", 3600)
        )
        qb_tenant.save()
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
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
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

    if error:
        messages.error(request, f"QuickBooks connection failed: {error}")
        request.session.pop("qb_rapid_connect", None)
        return redirect("integrations:qb_global_dashboard")

    if not code or state != expected_state:
        messages.error(request, "QuickBooks connection failed: invalid state.")
        request.session.pop("qb_rapid_connect", None)
        return redirect("integrations:qb_global_dashboard")

    if not realm_id:
        messages.error(request, "QuickBooks connection failed: no company ID returned.")
        request.session.pop("qb_rapid_connect", None)
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

        entity_pk = request.session.get("oauth_entity_pk")
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
        request.session.pop("qb_rapid_connect", None)
        request.session.pop("qb_global_oauth_state", None)
        return redirect("integrations:qb_global_dashboard")
    entity_pk = request.session.get("oauth_entity_pk")
    provider_name = request.session.get("oauth_provider")
    request.session.pop("qb_global_oauth_state", None)
    if rapid_mode:
        return redirect(reverse("integrations:qb_global_connect") + "?rapid=1")
    if entity_pk and provider_name == "quickbooks":
        for key in ["oauth_state", "oauth_entity_pk", "oauth_provider", "oauth_tokens", "oauth_tenants"]:
            request.session.pop(key, None)
        return redirect("integrations:connection_manage", entity_pk=entity_pk)
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
        import_mode = request.POST.get("import_mode", "period_movement")
        from_date_raw = request.POST.get("from_date", "").strip()
        to_date_raw = request.POST.get("to_date", "").strip()

        if not realm_id:
            messages.error(request, "Please select a company.")
            return redirect("integrations:qb_select_tenant_import", fy_pk=fy_pk)

        tenant_obj = tenants.filter(realm_id=realm_id).first()
        if not tenant_obj:
            messages.error(request, "Company not found.")
            return redirect("integrations:qb_select_tenant_import", fy_pk=fy_pk)

        from_date = None
        to_date = None
        if import_mode == "period_movement":
            if not from_date_raw or not to_date_raw:
                messages.error(request, "Please choose both a from date and a to date.")
                return redirect("integrations:qb_select_tenant_import", fy_pk=fy_pk)
            try:
                from_date = timezone.datetime.fromisoformat(from_date_raw).date()
                to_date = timezone.datetime.fromisoformat(to_date_raw).date()
            except ValueError:
                messages.error(request, "Invalid import period. Please choose valid dates.")
                return redirect("integrations:qb_select_tenant_import", fy_pk=fy_pk)
            if from_date > to_date:
                messages.error(request, "The from date must be on or before the to date.")
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
