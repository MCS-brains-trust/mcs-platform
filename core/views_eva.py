"""
StatementHub — Eva AI Practice Intelligence Views
===================================================
Handles all Eva-related HTTP endpoints:
  - Chat API (send/receive messages)
  - Finalisation Gate (trigger review, resolve findings)
  - Knowledge Brain admin (manual sync trigger)
  - Amber indicators (computed in trial balance context)
"""
import json
import logging
import threading

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET

from core.models import FinancialYear, AuditLog, TrialBalanceLine

logger = logging.getLogger(__name__)


def _log_action(request, action, description, obj=None):
    """Helper to create an AuditLog entry."""
    AuditLog.objects.create(
        user=request.user,
        action=action,
        description=description,
        affected_object_type=type(obj).__name__ if obj else "",
        affected_object_id=str(obj.pk) if obj else "",
        ip_address=request.META.get("REMOTE_ADDR"),
    )


# ===========================================================================
# Eva Chat API (combined GET/POST)
# ===========================================================================
@login_required
def eva_chat_api(request, pk):
    """
    GET  /api/financial-years/<pk>/eva-chat/ — retrieve conversation history
    POST /api/financial-years/<pk>/eva-chat/ — send a message
    """
    fy = get_object_or_404(FinancialYear, pk=pk)

    if request.method == "GET":
        return _eva_chat_history(request, fy)
    elif request.method == "POST":
        return _eva_chat_send(request, fy)
    else:
        return JsonResponse({"error": "Method not allowed"}, status=405)


def _eva_chat_history(request, fy):
    """Retrieve the full conversation history for this financial year."""
    from core.models import EvaConversation

    conversation = EvaConversation.objects.filter(
        financial_year=fy, user=request.user
    ).first()

    if not conversation:
        return JsonResponse({"messages": []})

    messages = []
    for msg in conversation.messages.order_by("created_at"):
        messages.append({
            "id": str(msg.pk),
            "role": msg.role,
            "content": msg.content,
            "model_used": msg.model_used,
            "created_at": msg.created_at.isoformat(),
            "is_proactive": msg.is_proactive,
        })

    return JsonResponse({
        "conversation_id": str(conversation.pk),
        "message_count": conversation.message_count,
        "messages": messages,
    })


def _eva_chat_send(request, fy):
    """Send a chat message to Eva. Returns the assistant's response."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    message_text = data.get("message", "").strip()
    if not message_text:
        return JsonResponse({"error": "Message cannot be empty"}, status=400)

    opus_override = data.get("opus_override", False)
    interaction_type = data.get("interaction_type", "general")

    from core.eva_service import process_eva_chat
    try:
        assistant_msg = process_eva_chat(
            financial_year=fy,
            user=request.user,
            message_text=message_text,
            opus_override=opus_override,
            interaction_type=interaction_type,
        )
        # Build citation list from M2M
        citations = []
        for chunk in assistant_msg.knowledge_chunks_cited.select_related("document").all():
            citations.append({
                "chunk_id": str(chunk.id),
                "document_title": chunk.document.title,
                "category": chunk.document.get_category_display(),
            })
        return JsonResponse({
            "status": "ok",
            "message": {
                "id": str(assistant_msg.pk),
                "role": "assistant",
                "content": assistant_msg.content,
                "model_used": assistant_msg.model_used,
                "created_at": assistant_msg.created_at.isoformat(),
                "retrieved_chunks": assistant_msg.retrieved_chunk_ids,
                "citations": citations,
                "token_count_prompt": assistant_msg.token_count_prompt,
                "token_count_response": assistant_msg.token_count_response,
                "interaction_type": assistant_msg.interaction_type,
            },
        })
    except Exception as e:
        logger.error(f"Eva chat error: {e}")
        return JsonResponse({"error": str(e)}, status=500)


# ===========================================================================
# Eva Finalisation Gate API
# ===========================================================================
@login_required
@require_POST
def ask_eva_review(request, pk):
    """
    POST /api/financial-years/<pk>/ask-eva-review/
    Trigger Eva's Finalisation Gate compliance review.
    """
    fy = get_object_or_404(FinancialYear, pk=pk)

    # Pre-flight checks
    if fy.status != FinancialYear.Status.DRAFT:
        return JsonResponse({
            "error": "Eva review can only be triggered from Draft status.",
            "current_status": fy.get_status_display(),
        }, status=400)

    # Check for unmapped accounts
    unmapped = TrialBalanceLine.objects.filter(
        financial_year=fy,
        mapped_line_item__isnull=True,
        is_adjustment=False,
    ).count()
    if unmapped > 0:
        return JsonResponse({
            "error": f"Cannot submit for Eva review: {unmapped} account(s) are unmapped. "
                     f"Please map all accounts before requesting Eva's review.",
            "unmapped_count": unmapped,
        }, status=400)

    # Check trial balance is in balance
    from django.db.models import Sum
    totals = TrialBalanceLine.objects.filter(
        financial_year=fy
    ).aggregate(
        total_dr=Sum("debit"),
        total_cr=Sum("credit"),
    )
    total_dr = totals["total_dr"] or 0
    total_cr = totals["total_cr"] or 0
    if abs(total_dr - total_cr) > 1:  # Allow $1 rounding tolerance
        return JsonResponse({
            "error": "Trial balance is not in balance. Please resolve before requesting Eva's review.",
            "difference": str(total_dr - total_cr),
        }, status=400)

    # Check at least one TB line exists
    if not TrialBalanceLine.objects.filter(financial_year=fy).exists():
        return JsonResponse({
            "error": "No trial balance data exists. Import a trial balance first.",
        }, status=400)

    opus_override = False
    try:
        data = json.loads(request.body) if request.body else {}
        opus_override = data.get("opus_override", False)
    except json.JSONDecodeError:
        pass

    # Update status to PREPARED
    fy.status = FinancialYear.Status.PREPARED
    fy.save(update_fields=["status"])

    _log_action(
        request, AuditLog.Action.EVA_REVIEW,
        f"{request.user.get_full_name() or request.user.email} submitted this financial year for Eva review.",
        fy,
    )

    # Run the review in a background thread to avoid blocking
    user = request.user

    def _run_review():
        try:
            from core.eva_service import run_eva_review
            run_eva_review(fy, user, opus_override=opus_override)
        except Exception:
            import logging
            logging.getLogger("core.views_eva").exception(
                "Background Eva review failed for FY %s", fy.pk,
            )
            # Update review status so the UI doesn't hang
            from core.models import EvaReview
            EvaReview.objects.filter(
                financial_year=fy, status="running",
            ).update(status="error", error_message="Background review thread crashed")

    thread = threading.Thread(target=_run_review, daemon=True)
    thread.start()

    return JsonResponse({
        "status": "accepted",
        "message": "Eva is now reviewing this financial year. Check back shortly.",
    }, status=202)


@login_required
@require_GET
def eva_review_status(request, pk):
    """
    GET /api/financial-years/<pk>/eva-review/
    Retrieve the current Eva review status and findings.
    """
    from core.models import EvaReview

    fy = get_object_or_404(FinancialYear, pk=pk)
    review = EvaReview.objects.filter(financial_year=fy).order_by("-triggered_at").first()

    if not review:
        return JsonResponse({"review": None})

    # Order findings by severity (critical first), then status (open first)
    from django.db.models import Case, When, Value, IntegerField
    severity_order = Case(
        When(severity="critical", then=Value(0)),
        When(severity="advisory", then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    status_order = Case(
        When(status="open", then=Value(0)),
        When(status="reopened", then=Value(1)),
        When(status="addressed", then=Value(2)),
        When(status="closed", then=Value(3)),
        default=Value(4),
        output_field=IntegerField(),
    )
    ordered_findings = (
        review.findings
        .select_related("resolved_by")
        .annotate(_sev_order=severity_order, _status_order=status_order)
        .order_by("_sev_order", "_status_order", "check_name")
    )

    findings = []
    for f in ordered_findings:
        findings.append({
            "id": str(f.pk),
            "check_name": f.check_name,
            "check_display": f.get_check_name_display(),
            "severity": f.severity,
            "title": f.title,
            "explanation": f.explanation,
            "recommendation": f.recommendation,
            "legislation_reference": f.legislation_reference,
            "knowledge_brain_citation": f.knowledge_brain_citation,
            "confidence": f.confidence,
            "status": f.status,
            "resolution_note": f.resolution_note,
            "resolved_by": (f.resolved_by.get_full_name() or f.resolved_by.email) if f.resolved_by else None,
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
        })

    return JsonResponse({
        "review": {
            "id": str(review.pk),
            "status": review.status,
            "status_display": review.get_status_display(),
            "triggered_at": review.triggered_at.isoformat(),
            "completed_at": review.completed_at.isoformat() if review.completed_at else None,
            "model_used": review.model_used,
            "checks_completed": review.checks_completed,
            "checks_total": review.checks_total,
            "finding_count": review.finding_count,
            "open_finding_count": review.open_finding_count,
            "critical_finding_count": review.critical_finding_count,
            "error_message": review.error_message,
            "is_rerun": review.is_rerun,
        },
        "findings": findings,
        "fy_status": fy.status,
    })


@login_required
@require_POST
def eva_resolve_finding(request, pk):
    """
    POST /api/eva-findings/<pk>/resolve/
    Mark an Eva finding as addressed.
    Body (JSON): {"resolution_note": "..."}
    """
    from core.models import EvaFinding

    finding = get_object_or_404(EvaFinding, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    resolution_note = data.get("resolution_note", "").strip()
    if not resolution_note:
        return JsonResponse({
            "error": "Resolution note is mandatory. Please document your response to this finding.",
        }, status=400)

    from core.eva_service import resolve_eva_finding
    finding, should_rerun = resolve_eva_finding(finding, request.user, resolution_note)

    _log_action(
        request, AuditLog.Action.EVA_FINDING,
        f"{request.user.get_full_name() or request.user.email} addressed Eva finding: {finding.title}",
        finding,
    )

    response_data = {
        "status": "ok",
        "finding_status": finding.status,
        "should_rerun": should_rerun,
    }

    if should_rerun:
        # Trigger a re-run in background
        fy = finding.eva_review.financial_year
        user = request.user

        def _rerun():
            try:
                from core.eva_service import run_eva_review
                review = run_eva_review(fy, user)
                review.is_rerun = True
                review.save(update_fields=["is_rerun"])
            except Exception:
                import logging
                logging.getLogger("core.views_eva").exception(
                    "Background Eva re-run failed for FY %s", fy.pk,
                )

        thread = threading.Thread(target=_rerun, daemon=True)
        thread.start()
        response_data["message"] = "All findings addressed. Eva is re-running her compliance checks."

    return JsonResponse(response_data)


@login_required
@require_POST
def eva_rerun_review(request, pk):
    """
    POST /api/financial-years/<pk>/eva-rerun/
    Manually trigger a re-run of Eva's compliance review.
    """
    fy = get_object_or_404(FinancialYear, pk=pk)

    if fy.status not in [FinancialYear.Status.PREPARED, FinancialYear.Status.DRAFT]:
        return JsonResponse({
            "error": "Cannot re-run Eva review in current status.",
        }, status=400)

    # Reset to PREPARED for re-run
    fy.status = FinancialYear.Status.PREPARED
    fy.save(update_fields=["status"])

    user = request.user

    def _run_review():
        try:
            from core.eva_service import run_eva_review
            review = run_eva_review(fy, user)
            review.is_rerun = True
            review.save(update_fields=["is_rerun"])
        except Exception:
            import logging
            logging.getLogger("core.views_eva").exception(
                "Background Eva rerun review failed for FY %s", fy.pk,
            )

    thread = threading.Thread(target=_run_review, daemon=True)
    thread.start()

    return JsonResponse({
        "status": "accepted",
        "message": "Eva is re-running her compliance review.",
    }, status=202)


# ===========================================================================
# Eva Finalise Financial Year
# ===========================================================================
@login_required
@require_POST
def eva_finalise(request, pk):
    """
    POST /api/financial-years/<pk>/eva-finalise/
    Finalise the financial year. Only succeeds if status is EVA_CLEARED.
    """
    fy = get_object_or_404(FinancialYear, pk=pk)

    if fy.status != FinancialYear.Status.EVA_CLEARED:
        return JsonResponse({
            "error": "Financial year can only be finalised after Eva has cleared it.",
            "current_status": fy.get_status_display(),
        }, status=400)

    fy.status = FinancialYear.Status.LOCKED
    fy.finalised_at = timezone.now()
    fy.save(update_fields=["status", "finalised_at"])

    # Lock comparatives
    fy.trial_balance_lines.update(comparatives_locked=True)

    _log_action(
        request, AuditLog.Action.STATUS_CHANGE,
        f"{request.user.get_full_name() or request.user.email} locked this financial year. "
        f"Eva clearance on record. All documents and data locked.",
        fy,
    )

    # Trigger Eva Client Summary generation (async if Celery available)
    try:
        from core.tasks import generate_eva_client_summary
        generate_eva_client_summary.delay(str(fy.pk), str(request.user.pk))
    except Exception as e:
        logger.warning(f"Could not trigger client summary generation: {e}")

    return JsonResponse({
        "status": "ok",
        "message": "Financial year has been locked. Eva Client Summary is being generated.",
    })


# ===========================================================================
# Knowledge Brain Admin
# ===========================================================================
@login_required
def knowledge_brain_admin(request):
    """
    GET /admin/eva/knowledge-brain/
    Admin page showing Knowledge Brain documents and sync status.
    """
    if not request.user.is_staff:
        return redirect("core:entity_list")

    from core.models import KnowledgeDocument

    documents = KnowledgeDocument.objects.all().order_by("-updated_at")

    # Optional category filter
    category = request.GET.get("category")
    if category:
        documents = documents.filter(category=category)

    context = {
        "documents": documents[:200],
        "total_count": documents.count(),
        "synced_count": documents.filter(sync_status="synced").count(),
        "pending_count": documents.filter(sync_status="pending").count(),
        "error_count": documents.filter(sync_status="error").count(),
        "categories": KnowledgeDocument.Category.choices,
        "selected_category": category,
    }

    return render(request, "core/eva_knowledge_brain.html", context)


@login_required
@require_POST
def trigger_knowledge_sync(request):
    """
    POST /admin/eva/knowledge-brain/sync/
    Admin-triggered manual sync of the SharePoint Knowledge Brain library.
    """
    if not request.user.is_staff:
        return JsonResponse({"error": "Admin access required"}, status=403)

    def _sync():
        try:
            from core.eva_service import sync_knowledge_brain
            sync_knowledge_brain()
        except Exception:
            import logging
            logging.getLogger("core.views_eva").exception(
                "Background Knowledge Brain sync failed",
            )

    thread = threading.Thread(target=_sync, daemon=True)
    thread.start()

    _log_action(
        request, AuditLog.Action.EVA_SYNC,
        f"{request.user.get_full_name() or request.user.email} triggered a Knowledge Brain sync.",
    )

    return JsonResponse({
        "status": "accepted",
        "message": "Knowledge Brain sync has been triggered. Check the Activity tab for results.",
    }, status=202)


# ===========================================================================
# Amber Indicators Helper (called from financial_year_detail view context)
# ===========================================================================
def compute_amber_indicators_for_context(fy):
    """
    Compute amber indicators for the trial balance view context.
    Returns a dict keyed by account_code with a list of trigger dicts.

    Called from the financial_year_detail view to pass into the template.
    """
    from core.eva_service import compute_amber_indicators
    return compute_amber_indicators(fy)
