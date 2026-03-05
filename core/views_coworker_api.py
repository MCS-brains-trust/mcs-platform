"""
MCS Platform — Coworker Integration API
========================================
Read-only API endpoint that the MC & S Coworker desktop app calls
to retrieve an "outreach queue" — a list of entities/clients that
need follow-up emails, with enough context for Claude to draft
personalised outreach.

Authentication: Bearer token (WEBHOOK_SECRET env var).
"""

import logging
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from config.webhook_auth import verify_webhook
from core.models import Entity, FinancialYear
from core.models_office_admin import ASICReturn, DebtorRecord, NOARecord

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _entity_base(entity: Entity) -> dict:
    """Serialise the common entity fields used by every outreach item."""
    accountant = entity.primary_accountant or entity.assigned_accountant
    return {
        "entity_id": str(entity.pk),
        "entity_name": entity.entity_name,
        "entity_type": entity.entity_type,
        "contact_email": entity.contact_email or "",
        "contact_phone": entity.contact_phone or "",
        "abn": entity.abn or "",
        "trading_as": entity.trading_as or "",
        "assigned_accountant": accountant.email if accountant else "",
        "assigned_accountant_name": accountant.get_full_name() if accountant else "",
    }


def _stale_financial_years(accountant_email: str | None, days: int = 30) -> list[dict]:
    """
    Find financial years that have been sitting in Draft or In Review
    with no update for more than `days` days.
    """
    cutoff = timezone.now() - timedelta(days=days)
    qs = FinancialYear.objects.filter(
        status__in=["draft", "in_review"],
        updated_at__lt=cutoff,
        entity__is_archived=False,
    ).select_related("entity", "entity__primary_accountant", "entity__assigned_accountant")

    if accountant_email:
        qs = qs.filter(
            entity__primary_accountant__email=accountant_email
        ) | qs.filter(
            entity__assigned_accountant__email=accountant_email
        )

    items = []
    for fy in qs[:50]:
        entity = fy.entity
        if not entity.contact_email:
            continue
        days_inactive = (timezone.now() - fy.updated_at).days
        item = _entity_base(entity)
        item.update({
            "outreach_reason": "stale_financial_year",
            "reason_detail": (
                f"{fy.year_label} has been in '{fy.get_status_display()}' status "
                f"for {days_inactive} days with no activity."
            ),
            "priority": "high" if days_inactive > 60 else "medium",
            "context": {
                "financial_year": fy.year_label,
                "status": fy.status,
                "status_display": fy.get_status_display(),
                "days_inactive": days_inactive,
                "start_date": str(fy.start_date),
                "end_date": str(fy.end_date),
                "last_updated": fy.updated_at.isoformat(),
            },
        })
        items.append(item)
    return items


def _documents_needed(accountant_email: str | None) -> list[dict]:
    """
    Find financial years that have been created but have no trial balance
    lines uploaded yet — the client likely hasn't sent their records.
    """
    cutoff = timezone.now() - timedelta(days=14)
    qs = FinancialYear.objects.filter(
        status="draft",
        created_at__lt=cutoff,
        entity__is_archived=False,
    ).select_related("entity", "entity__primary_accountant", "entity__assigned_accountant")

    if accountant_email:
        qs = qs.filter(
            entity__primary_accountant__email=accountant_email
        ) | qs.filter(
            entity__assigned_accountant__email=accountant_email
        )

    items = []
    for fy in qs[:50]:
        # Check if there are any trial balance lines
        tb_count = fy.trial_balance_lines.count()
        if tb_count > 0:
            continue
        entity = fy.entity
        if not entity.contact_email:
            continue
        days_waiting = (timezone.now() - fy.created_at).days
        item = _entity_base(entity)
        item.update({
            "outreach_reason": "documents_needed",
            "reason_detail": (
                f"{fy.year_label} was created {days_waiting} days ago but no "
                f"trial balance or records have been uploaded yet."
            ),
            "priority": "high" if days_waiting > 30 else "medium",
            "context": {
                "financial_year": fy.year_label,
                "days_waiting": days_waiting,
                "start_date": str(fy.start_date),
                "end_date": str(fy.end_date),
            },
        })
        items.append(item)
    return items


def _asic_items(accountant_email: str | None) -> list[dict]:
    """
    Find ASIC returns that are overdue or due within 14 days.
    """
    today = timezone.now().date()
    upcoming_cutoff = today + timedelta(days=14)

    qs = ASICReturn.objects.filter(
        status__in=["pending", "burning", "overdue"],
        entity__is_archived=False,
    ).select_related("entity", "entity__primary_accountant", "entity__assigned_accountant")

    if accountant_email:
        qs = qs.filter(
            entity__primary_accountant__email=accountant_email
        ) | qs.filter(
            entity__assigned_accountant__email=accountant_email
        )

    items = []
    for ret in qs[:50]:
        entity = ret.entity
        if not entity.contact_email:
            continue
        if not ret.due_date:
            continue

        is_overdue = ret.due_date < today
        is_upcoming = ret.due_date <= upcoming_cutoff

        if not is_overdue and not is_upcoming:
            continue

        days_until = (ret.due_date - today).days
        item = _entity_base(entity)
        item.update({
            "outreach_reason": "asic_overdue" if is_overdue else "asic_upcoming",
            "reason_detail": (
                f"ASIC {ret.get_return_type_display()} is "
                f"{'overdue by ' + str(abs(days_until)) + ' days' if is_overdue else 'due in ' + str(days_until) + ' days'}."
            ),
            "priority": "critical" if is_overdue else "medium",
            "context": {
                "return_type": ret.return_type,
                "return_type_display": ret.get_return_type_display(),
                "due_date": str(ret.due_date),
                "days_until_due": days_until,
                "amount": str(ret.amount) if ret.amount else None,
                "status": ret.status,
            },
        })
        items.append(item)
    return items


def _debtor_items(accountant_email: str | None) -> list[dict]:
    """
    Find outstanding debtor records that need follow-up.
    """
    qs = DebtorRecord.objects.filter(
        status__in=["overdue", "payment_plan"],
        days_overdue__gte=45,
        entity__is_archived=False,
    ).select_related("entity", "entity__primary_accountant", "entity__assigned_accountant")

    if accountant_email:
        qs = qs.filter(
            entity__primary_accountant__email=accountant_email
        ) | qs.filter(
            entity__assigned_accountant__email=accountant_email
        )

    items = []
    for debt in qs[:50]:
        entity = debt.entity
        if not entity.contact_email:
            continue

        is_escalation = debt.days_overdue >= 90
        item = _entity_base(entity)
        item.update({
            "outreach_reason": "debtor_escalation" if is_escalation else "debtor_followup",
            "reason_detail": (
                f"Invoice {debt.invoice_number or '(no number)'} for "
                f"${debt.amount_outstanding:,.2f} is {debt.days_overdue} days overdue. "
                f"Current escalation stage: {debt.get_escalation_stage_display()}."
            ),
            "priority": "critical" if is_escalation else "high",
            "context": {
                "invoice_number": debt.invoice_number or "",
                "amount_outstanding": str(debt.amount_outstanding),
                "days_overdue": debt.days_overdue,
                "escalation_stage": debt.escalation_stage,
                "escalation_stage_display": debt.get_escalation_stage_display(),
                "last_contact_date": str(debt.last_contact_date) if debt.last_contact_date else None,
                "status": debt.status,
            },
        })
        items.append(item)
    return items


def _general_checkins(accountant_email: str | None, months: int = 6) -> list[dict]:
    """
    Find entities with no financial year activity in the last N months.
    Good for proactive "how are things going?" outreach.
    """
    cutoff = timezone.now() - timedelta(days=months * 30)

    # Entities whose most recent FY was updated before the cutoff
    qs = Entity.objects.filter(
        is_archived=False,
        contact_email__gt="",  # must have an email
    ).select_related("primary_accountant", "assigned_accountant")

    if accountant_email:
        qs = qs.filter(
            primary_accountant__email=accountant_email
        ) | qs.filter(
            assigned_accountant__email=accountant_email
        )

    items = []
    for entity in qs[:100]:
        latest_fy = entity.financial_years.order_by("-updated_at").first()
        if latest_fy and latest_fy.updated_at > cutoff:
            continue  # Recent activity — skip
        if latest_fy is None:
            continue  # No FYs at all — probably a new entity

        days_since = (timezone.now() - latest_fy.updated_at).days
        item = _entity_base(entity)
        item.update({
            "outreach_reason": "general_checkin",
            "reason_detail": (
                f"No activity on this entity for {days_since} days. "
                f"Last financial year: {latest_fy.year_label} ({latest_fy.get_status_display()})."
            ),
            "priority": "low",
            "context": {
                "last_financial_year": latest_fy.year_label,
                "last_fy_status": latest_fy.status,
                "days_since_activity": days_since,
                "last_updated": latest_fy.updated_at.isoformat(),
            },
        })
        items.append(item)
    return items


# ── Main API View ────────────────────────────────────────────────────────────

@require_GET
def outreach_queue(request):
    """
    GET /api/coworker/outreach-queue/
    Returns a JSON list of entities/items that need outreach emails.

    Query params:
        accountant_email  — filter to a specific accountant's entities
        reasons           — comma-separated list of reason types to include
                            (default: all)
        limit             — max items to return (default: 100)
    """
    # Authenticate
    ok, error_response = verify_webhook(request)
    if not ok:
        return error_response

    # Parse query params
    accountant_email = request.GET.get("accountant_email", "").strip() or None
    requested_reasons = request.GET.get("reasons", "").strip()
    limit = min(int(request.GET.get("limit", "100")), 200)

    if requested_reasons:
        reason_set = set(requested_reasons.split(","))
    else:
        reason_set = {
            "stale_financial_year", "documents_needed",
            "asic_overdue", "asic_upcoming",
            "debtor_followup", "debtor_escalation",
            "general_checkin",
        }

    # Collect items from each source
    all_items = []

    try:
        if "stale_financial_year" in reason_set:
            all_items.extend(_stale_financial_years(accountant_email))

        if "documents_needed" in reason_set:
            all_items.extend(_documents_needed(accountant_email))

        if reason_set & {"asic_overdue", "asic_upcoming"}:
            asic_items = _asic_items(accountant_email)
            all_items.extend(
                i for i in asic_items if i["outreach_reason"] in reason_set
            )

        if reason_set & {"debtor_followup", "debtor_escalation"}:
            debtor_items = _debtor_items(accountant_email)
            all_items.extend(
                i for i in debtor_items if i["outreach_reason"] in reason_set
            )

        if "general_checkin" in reason_set:
            all_items.extend(_general_checkins(accountant_email))

    except Exception as e:
        logger.exception("Error building outreach queue")
        return JsonResponse(
            {"status": "error", "message": str(e)},
            status=500,
        )

    # Sort by priority (critical > high > medium > low)
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_items.sort(key=lambda x: priority_order.get(x.get("priority", "low"), 99))

    # Apply limit
    all_items = all_items[:limit]

    # De-duplicate by entity + reason (keep highest priority)
    seen = set()
    unique_items = []
    for item in all_items:
        key = (item["entity_id"], item["outreach_reason"])
        if key not in seen:
            seen.add(key)
            unique_items.append(item)

    return JsonResponse({
        "status": "ok",
        "generated_at": timezone.now().isoformat(),
        "count": len(unique_items),
        "items": unique_items,
    })


@require_GET
def entity_detail(request, entity_id):
    """
    GET /api/coworker/entity/<uuid>/
    Returns detailed information about a single entity for richer email drafting.
    """
    ok, error_response = verify_webhook(request)
    if not ok:
        return error_response

    try:
        entity = Entity.objects.select_related(
            "primary_accountant", "assigned_accountant", "client",
        ).get(pk=entity_id)
    except Entity.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Entity not found"}, status=404)

    accountant = entity.primary_accountant or entity.assigned_accountant

    # Financial years summary
    fys = []
    for fy in entity.financial_years.order_by("-end_date")[:5]:
        fys.append({
            "year_label": fy.year_label,
            "status": fy.status,
            "status_display": fy.get_status_display(),
            "start_date": str(fy.start_date),
            "end_date": str(fy.end_date),
            "package_assembled": fy.package_assembled,
            "package_sent_for_signing": fy.package_sent_for_signing,
        })

    # Officers
    officers = []
    for officer in entity.officers.all()[:10]:
        officers.append({
            "name": officer.full_name,
            "role": officer.get_role_display(),
            "email": officer.email or "",
        })

    # Engagement config
    engagement = None
    try:
        ec = entity.engagement_letter_config
        engagement = {
            "services": ec.services_engaged,
            "fee_amount": str(ec.fee_amount) if ec.fee_amount else None,
            "fee_basis": ec.get_fee_basis_display(),
        }
    except Exception:
        pass

    data = _entity_base(entity)
    data.update({
        "address": ", ".join(filter(None, [
            entity.address_line_1, entity.address_line_2,
            entity.suburb, entity.state, entity.postcode,
        ])),
        "financial_year_end": entity.financial_year_end,
        "is_gst_registered": entity.is_gst_registered,
        "financial_years": fys,
        "officers": officers,
        "engagement": engagement,
        "client_name": entity.client.name if entity.client else "",
    })

    return JsonResponse({"status": "ok", "entity": data})
