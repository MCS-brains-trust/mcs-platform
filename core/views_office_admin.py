"""
MCS Platform - Office Admin Views
Dashboard and management views for the reception/office admin role.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Sum, Count
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Entity
from .models_office_admin import (
    Correspondence, ASICReturn, NOARecord,
    DebtorRecord, PaymentPlan, DailyTask, DailyTaskCompletion,
)


def _get_greeting():
    """Return a time-based greeting."""
    hour = timezone.localtime(timezone.now()).hour
    if hour < 12:
        return "Good morning"
    elif hour < 17:
        return "Good afternoon"
    return "Good evening"


# ---------------------------------------------------------------------------
# Office Admin Dashboard
# ---------------------------------------------------------------------------
@login_required
def office_admin_dashboard(request):
    """
    Main dashboard for the Office Admin role.
    Shows summary stats, today's tasks, recent correspondence,
    ASIC/ATO action items, and overdue debtors.
    """
    today = timezone.now().date()
    seven_days = today + timedelta(days=7)

    # ── Correspondence stats ──
    recent_correspondence = Correspondence.objects.select_related("entity").order_by(
        "-date_received", "-created_at"
    )[:10]
    correspondence_total = Correspondence.objects.filter(
        date_received__gte=today - timedelta(days=30)
    ).count()
    correspondence_new = Correspondence.objects.filter(date_received=today).count()
    correspondence_awaiting = Correspondence.objects.filter(status="awaiting").count()

    # ── ASIC / ATO stats ──
    asic_returns = ASICReturn.objects.select_related("entity").exclude(
        status__in=["completed", "lodged"]
    )[:10]
    asic_burning = ASICReturn.objects.filter(
        status="burning"
    ).count() + ASICReturn.objects.filter(
        due_date__lte=seven_days,
        status__in=["pending", "overdue"],
    ).count()
    noa_records = NOARecord.objects.select_related("entity").filter(
        status="to_send"
    )[:10]
    asic_total = ASICReturn.objects.exclude(status__in=["completed", "lodged"]).count()
    noa_to_send = noa_records.count()

    # Combine ASIC + NOA for the action required panel
    asic_action_items = []
    for ret in asic_returns[:5]:
        asic_action_items.append({
            "entity_name": ret.entity.entity_name,
            "item": ret.get_return_type_display(),
            "due_date": ret.due_date,
            "status": ret.status,
            "status_display": ret.get_status_display(),
            "is_burning": ret.is_burning,
            "type": "asic",
            "pk": ret.pk,
        })
    for noa in noa_records[:5]:
        asic_action_items.append({
            "entity_name": noa.entity.entity_name,
            "item": f"NOA — {noa.get_noa_type_display()} ${noa.amount:,.0f}",
            "due_date": None,
            "status": noa.status,
            "status_display": noa.get_status_display(),
            "is_burning": False,
            "type": "noa",
            "pk": noa.pk,
        })

    # ── Debtors stats ──
    overdue_debtors = DebtorRecord.objects.select_related("entity").filter(
        status="overdue"
    ).order_by("-days_overdue")[:10]
    total_overdue_amount = overdue_debtors.aggregate(
        total=Sum("amount_outstanding")
    )["total"] or Decimal("0")
    overdue_count = DebtorRecord.objects.filter(status="overdue").count()

    # ── Today's tasks ──
    today_weekday = today.strftime("%A").lower()
    # Get daily tasks + weekly tasks if it's Monday + monthly tasks if 1st
    task_filters = Q(frequency="daily", is_active=True)
    if today.weekday() == 0:  # Monday
        task_filters |= Q(frequency="weekly", is_active=True)
    if today.day == 1:
        task_filters |= Q(frequency="monthly", is_active=True)
    task_filters |= Q(frequency="one_off", is_active=True)

    tasks = DailyTask.objects.filter(task_filters)
    completed_task_ids = set(
        DailyTaskCompletion.objects.filter(
            completed_date=today
        ).values_list("task_id", flat=True)
    )

    task_list = []
    for task in tasks:
        task_list.append({
            "pk": task.pk,
            "title": task.title,
            "frequency": task.frequency,
            "frequency_display": task.get_frequency_display(),
            "scheduled_time": task.scheduled_time,
            "is_completed": task.pk in completed_task_ids,
        })

    tasks_completed = len([t for t in task_list if t["is_completed"]])
    tasks_total = len(task_list)

    # ── Payment plans ──
    active_plans = PaymentPlan.objects.select_related("entity").filter(
        status="active"
    ).count()

    context = {
        "greeting": _get_greeting(),
        "today": today,
        # Correspondence
        "recent_correspondence": recent_correspondence,
        "correspondence_total": correspondence_total,
        "correspondence_new": correspondence_new,
        "correspondence_awaiting": correspondence_awaiting,
        # ASIC / ATO
        "asic_action_items": asic_action_items,
        "asic_total": asic_total + noa_to_send,
        "asic_burning": asic_burning,
        "noa_to_send": noa_to_send,
        # Debtors
        "overdue_debtors": overdue_debtors,
        "total_overdue_amount": total_overdue_amount,
        "overdue_count": overdue_count,
        # Tasks
        "task_list": task_list,
        "tasks_completed": tasks_completed,
        "tasks_total": tasks_total,
        # Payment plans
        "active_plans": active_plans,
    }
    return render(request, "office_admin/dashboard.html", context)


# ---------------------------------------------------------------------------
# Task Completion Toggle (AJAX)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def toggle_task_completion(request, pk):
    """Toggle a daily task as completed/uncompleted for today."""
    task = get_object_or_404(DailyTask, pk=pk)
    today = timezone.now().date()

    completion = DailyTaskCompletion.objects.filter(task=task, completed_date=today).first()
    if completion:
        completion.delete()
        is_completed = False
    else:
        DailyTaskCompletion.objects.create(
            task=task, completed_by=request.user, completed_date=today
        )
        is_completed = True

    return JsonResponse({"status": "ok", "is_completed": is_completed})


# ---------------------------------------------------------------------------
# Correspondence Views
# ---------------------------------------------------------------------------
@login_required
def correspondence_list(request):
    """Full list of all correspondence with filtering."""
    direction = request.GET.get("direction", "")
    status = request.GET.get("status", "")
    query = request.GET.get("q", "")

    items = Correspondence.objects.select_related("entity", "assigned_to", "logged_by").all()

    if direction:
        items = items.filter(direction=direction)
    if status:
        items = items.filter(status=status)
    if query:
        items = items.filter(
            Q(entity__entity_name__icontains=query)
            | Q(subject__icontains=query)
        )

    context = {
        "items": items[:100],
        "direction_filter": direction,
        "status_filter": status,
        "query": query,
        "direction_choices": Correspondence.Direction.choices,
        "status_choices": Correspondence.Status.choices,
        "page_title": "All Correspondence",
    }
    return render(request, "office_admin/correspondence_list.html", context)


@login_required
def correspondence_incoming(request):
    """Incoming correspondence only."""
    items = Correspondence.objects.select_related("entity").filter(
        direction="incoming"
    ).order_by("-date_received")[:100]

    context = {
        "items": items,
        "page_title": "Incoming Mail",
        "direction_choices": Correspondence.Direction.choices,
        "status_choices": Correspondence.Status.choices,
    }
    return render(request, "office_admin/correspondence_list.html", context)


@login_required
def correspondence_outgoing(request):
    """Outgoing correspondence only."""
    items = Correspondence.objects.select_related("entity").filter(
        direction="outgoing"
    ).order_by("-date_received")[:100]

    context = {
        "items": items,
        "page_title": "Outgoing Mail",
        "direction_choices": Correspondence.Direction.choices,
        "status_choices": Correspondence.Status.choices,
    }
    return render(request, "office_admin/correspondence_list.html", context)


@login_required
def correspondence_awaiting(request):
    """Correspondence awaiting reply."""
    items = Correspondence.objects.select_related("entity").filter(
        status="awaiting"
    ).order_by("-date_received")[:100]

    context = {
        "items": items,
        "page_title": "Awaiting Reply",
        "direction_choices": Correspondence.Direction.choices,
        "status_choices": Correspondence.Status.choices,
    }
    return render(request, "office_admin/correspondence_list.html", context)


@login_required
def correspondence_documents_in(request):
    """Documents received from clients."""
    items = Correspondence.objects.select_related("entity").filter(
        direction="incoming",
        correspondence_type__in=["tax_documents", "bank_statement", "other"],
    ).order_by("-date_received")[:100]

    context = {
        "items": items,
        "page_title": "Documents In",
        "direction_choices": Correspondence.Direction.choices,
        "status_choices": Correspondence.Status.choices,
    }
    return render(request, "office_admin/correspondence_list.html", context)


@login_required
def correspondence_create(request):
    """Create a new correspondence record."""
    if request.method == "POST":
        entity_id = request.POST.get("entity")
        entity = Entity.objects.filter(pk=entity_id).first() if entity_id else None

        corr = Correspondence.objects.create(
            entity=entity,
            direction=request.POST.get("direction", "incoming"),
            correspondence_type=request.POST.get("correspondence_type", "other"),
            subject=request.POST.get("subject", ""),
            status=request.POST.get("status", "pending"),
            notes=request.POST.get("notes", ""),
            logged_by=request.user,
            date_received=request.POST.get("date_received") or timezone.now().date(),
        )
        messages.success(request, f"Correspondence logged: {corr}")
        return redirect("office_admin:correspondence_list")

    entities = Entity.objects.filter(is_archived=False).order_by("entity_name")[:500]
    context = {
        "entities": entities,
        "direction_choices": Correspondence.Direction.choices,
        "type_choices": Correspondence.CorrespondenceType.choices,
        "status_choices": Correspondence.Status.choices,
    }
    return render(request, "office_admin/correspondence_form.html", context)


@login_required
@require_POST
def correspondence_update_status(request, pk):
    """Quick status update for a correspondence item (AJAX)."""
    corr = get_object_or_404(Correspondence, pk=pk)
    new_status = request.POST.get("status")
    if new_status in dict(Correspondence.Status.choices):
        corr.status = new_status
        if new_status in ("actioned", "filed"):
            corr.date_actioned = timezone.now().date()
        corr.save()
        return JsonResponse({"status": "ok", "new_status": corr.get_status_display()})
    return JsonResponse({"status": "error"}, status=400)


# ---------------------------------------------------------------------------
# ASIC / ATO Views
# ---------------------------------------------------------------------------
@login_required
def noa_tracker(request):
    """NOA records list."""
    items = NOARecord.objects.select_related("entity").all()[:100]
    context = {
        "items": items,
        "page_title": "NOA Tracker",
    }
    return render(request, "office_admin/noa_list.html", context)


@login_required
def asic_returns_list(request):
    """ASIC returns list."""
    items = ASICReturn.objects.select_related("entity").all()[:100]
    burning_count = items.filter(status="burning").count()
    context = {
        "items": items,
        "burning_count": burning_count,
        "page_title": "ASIC Returns",
    }
    return render(request, "office_admin/asic_list.html", context)


@login_required
def burning_list(request):
    """ASIC items that are burning (due within 7 days or overdue)."""
    today = timezone.now().date()
    seven_days = today + timedelta(days=7)

    items = ASICReturn.objects.select_related("entity").filter(
        Q(status="burning") | Q(due_date__lte=seven_days, status__in=["pending", "overdue"])
    ).order_by("due_date")

    context = {
        "items": items,
        "page_title": "Burning List",
    }
    return render(request, "office_admin/asic_list.html", context)


@login_required
def company_register(request):
    """Company entities register for ASIC tracking."""
    query = request.GET.get("q", "")
    entities = Entity.objects.filter(
        entity_type="company", is_archived=False
    ).order_by("entity_name")

    if query:
        entities = entities.filter(
            Q(entity_name__icontains=query) | Q(acn__icontains=query) | Q(abn__icontains=query)
        )

    context = {
        "entities": entities[:200],
        "query": query,
        "page_title": "Company Register",
    }
    return render(request, "office_admin/company_register.html", context)


# ---------------------------------------------------------------------------
# Debtors Views
# ---------------------------------------------------------------------------
@login_required
def aged_receivables(request):
    """Full aged receivables report."""
    items = DebtorRecord.objects.select_related("entity").exclude(
        status="paid"
    ).order_by("-days_overdue")[:200]

    total = items.aggregate(total=Sum("amount_outstanding"))["total"] or Decimal("0")

    context = {
        "items": items,
        "total": total,
        "page_title": "Aged Receivables",
    }
    return render(request, "office_admin/debtors_list.html", context)


@login_required
def statements_sent(request):
    """Debtors where statements have been sent."""
    items = DebtorRecord.objects.select_related("entity").filter(
        escalation_stage__in=["1st_statement", "2nd_statement"]
    ).order_by("-days_overdue")[:200]

    context = {
        "items": items,
        "page_title": "Statements Sent",
    }
    return render(request, "office_admin/debtors_list.html", context)


@login_required
def debtors_overdue(request):
    """Overdue debtors only."""
    items = DebtorRecord.objects.select_related("entity").filter(
        status="overdue"
    ).order_by("-days_overdue")[:200]

    total = items.aggregate(total=Sum("amount_outstanding"))["total"] or Decimal("0")

    context = {
        "items": items,
        "total": total,
        "page_title": "Overdue Debtors",
    }
    return render(request, "office_admin/debtors_list.html", context)


@login_required
def payment_plans_list(request):
    """Active payment plans."""
    items = PaymentPlan.objects.select_related("entity").filter(
        status="active"
    ).order_by("next_payment_date")[:200]

    context = {
        "items": items,
        "page_title": "Payment Plans",
    }
    return render(request, "office_admin/payment_plans.html", context)
