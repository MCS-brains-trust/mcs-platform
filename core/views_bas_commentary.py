"""
BAS Period Commentary Views.

Provides API endpoints for generating, managing, and exporting
AI-powered BAS period commentaries.

Endpoints:
  - generate_commentary: POST — Create and queue commentary generation
  - list_commentaries: GET — List all commentaries for a financial year
  - get_commentary: GET — Retrieve a single commentary
  - update_commentary: POST — Update commentary sections (accountant edits)
  - regenerate_commentary: POST — Re-generate an existing commentary
  - download_commentary: GET — Download commentary as Word document
  - commentary_status: GET — Poll generation status
  - mark_commentary_sent: POST — Mark commentary as sent to client
  - delete_commentary: POST — Delete a commentary
  - compare_commentaries: GET — Side-by-side comparison of two commentaries
"""
import json
import logging
import threading

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from config.authorization import get_financial_year_for_user
from .models import (
    BASPeriod, BASPeriodCommentary, FinancialYear,
    LegalDocument, ActivityLog,
)

logger = logging.getLogger(__name__)


# ── Helper: Activity Logging ─────────────────────────────────────────────────

def _log_commentary_activity(user, event_type, commentary, description=""):
    """Log a BAS commentary event to the ActivityLog."""
    try:
        entity = commentary.financial_year.entity
        ActivityLog.objects.create(
            user=user,
            event_type=event_type,
            title=f"BAS Commentary — {entity.entity_name}",
            description=description or f"{commentary.period_label} v{commentary.version}",
            entity=entity,
            financial_year=commentary.financial_year,
        )
    except Exception:
        logger.exception("Failed to log commentary activity: %s", event_type)


# ── Helper: Find Prior Commentary ────────────────────────────────────────────

def _find_prior_commentary(bas_period):
    """
    Find the most recent completed commentary from the immediately preceding
    BAS period for trend chaining.
    """
    if not bas_period:
        return None

    prior_period = BASPeriod.objects.filter(
        financial_year=bas_period.financial_year,
        period_type=bas_period.period_type,
        period_number=bas_period.period_number - 1,
    ).first()

    if not prior_period:
        # Check the prior financial year's last period
        fy = bas_period.financial_year
        if fy.prior_year:
            prior_period = BASPeriod.objects.filter(
                financial_year=fy.prior_year,
                period_type=bas_period.period_type,
            ).order_by("-period_number").first()

    if not prior_period:
        return None

    return BASPeriodCommentary.objects.filter(
        bas_period=prior_period,
        status__in=["draft", "reviewed", "sent"],
    ).order_by("-version").first()


# ── Generate Commentary ────────────────────────────────────────────────────

@login_required
@require_POST
def generate_commentary(request, pk):
    """
    Create a new BASPeriodCommentary record and queue generation.

    POST body (JSON):
      - period_number: int (required)
      - tone: str (optional, default "professional")
    """
    fy = get_financial_year_for_user(request, pk)
    if not fy:
        return JsonResponse({"error": "Financial year not found or access denied."}, status=404)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}

    period_number = data.get("period_number") or request.POST.get("period_number")
    tone = data.get("tone", "professional")

    if not period_number:
        return JsonResponse({"error": "period_number is required."}, status=400)

    try:
        period_number = int(period_number)
    except (TypeError, ValueError):
        return JsonResponse({"error": "period_number must be an integer."}, status=400)

    entity = fy.entity

    # Get or validate the BAS period
    period_type = entity.bas_frequency
    bas_period = BASPeriod.objects.filter(
        financial_year=fy,
        period_type=period_type,
        period_number=period_number,
    ).first()

    if not bas_period:
        return JsonResponse({"error": f"BAS period {period_number} not found."}, status=404)

    # Check for existing generating commentary
    existing = BASPeriodCommentary.objects.filter(
        bas_period=bas_period,
        status="generating",
    ).exists()
    if existing:
        return JsonResponse(
            {"error": "A commentary is already being generated for this period."},
            status=409,
        )

    # Determine version number
    latest = BASPeriodCommentary.objects.filter(
        bas_period=bas_period,
    ).order_by("-version").first()
    version = (latest.version + 1) if latest else 1

    # Find prior period commentary for trend chaining
    prior_commentary = _find_prior_commentary(bas_period)

    # Create the commentary record
    commentary = BASPeriodCommentary.objects.create(
        financial_year=fy,
        bas_period=bas_period,
        period_start=bas_period.period_start,
        period_end=bas_period.period_end,
        period_label=bas_period.label,
        tone=tone,
        version=version,
        generated_by=request.user,
        status="generating",
        prior_commentary=prior_commentary,
    )

    # Queue generation in background thread (or Celery if available)
    _queue_commentary_generation(str(commentary.pk), str(request.user.pk))

    # Log activity
    _log_commentary_activity(
        request.user,
        "bas_commentary_generated",
        commentary,
        f"Generated {commentary.period_label} v{version} ({tone} tone)",
    )

    return JsonResponse({
        "commentary_id": str(commentary.pk),
        "status": "generating",
        "version": version,
        "period_label": bas_period.label,
    }, status=201)


# ── List Commentaries ──────────────────────────────────────────────────────

@login_required
@require_GET
def list_commentaries(request, pk):
    """
    List all commentaries for a financial year.

    Optional query params:
      - period_number: filter by period
      - status: filter by status
    """
    fy = get_financial_year_for_user(request, pk)
    if not fy:
        return JsonResponse({"error": "Financial year not found or access denied."}, status=404)

    qs = BASPeriodCommentary.objects.filter(financial_year=fy)

    period_number = request.GET.get("period_number")
    if period_number:
        qs = qs.filter(bas_period__period_number=int(period_number))

    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)

    commentaries = []
    for c in qs.select_related("bas_period", "generated_by", "reviewed_by"):
        commentaries.append({
            "id": str(c.pk),
            "period_label": c.period_label,
            "period_number": c.bas_period.period_number if c.bas_period else None,
            "status": c.status,
            "status_display": c.get_status_display(),
            "tone": c.tone,
            "version": c.version,
            "section_count": c.section_count,
            "generated_by": c.generated_by.get_full_name() if c.generated_by else "",
            "generated_at": c.generated_at.isoformat() if c.generated_at else None,
            "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
            "sent_at": c.sent_at.isoformat() if c.sent_at else None,
        })

    return JsonResponse({"commentaries": commentaries})


# ── Get Commentary ─────────────────────────────────────────────────────────

@login_required
@require_GET
def get_commentary(request, pk):
    """Retrieve a single commentary with full content."""
    commentary = get_object_or_404(BASPeriodCommentary, pk=pk)

    # Access check via financial year
    fy = get_financial_year_for_user(request, commentary.financial_year.pk)
    if not fy:
        return JsonResponse({"error": "Access denied."}, status=403)

    return JsonResponse({
        "id": str(commentary.pk),
        "period_label": commentary.period_label,
        "period_start": str(commentary.period_start),
        "period_end": str(commentary.period_end),
        "status": commentary.status,
        "status_display": commentary.get_status_display(),
        "tone": commentary.tone,
        "version": commentary.version,
        "section_snapshot": commentary.section_snapshot,
        "section_revenue": commentary.section_revenue,
        "section_costs": commentary.section_costs,
        "section_watch_items": commentary.section_watch_items,
        "section_actions": commentary.section_actions,
        "full_content": commentary.full_content,
        "generated_by": commentary.generated_by.get_full_name() if commentary.generated_by else "",
        "generated_at": commentary.generated_at.isoformat() if commentary.generated_at else None,
        "reviewed_by": commentary.reviewed_by.get_full_name() if commentary.reviewed_by else "",
        "reviewed_at": commentary.reviewed_at.isoformat() if commentary.reviewed_at else None,
        "sent_at": commentary.sent_at.isoformat() if commentary.sent_at else None,
        "sent_to_email": commentary.sent_to_email,
        "is_editable": commentary.is_editable,
        "error_message": commentary.error_message,
        "prior_commentary_id": str(commentary.prior_commentary_id) if commentary.prior_commentary_id else None,
    })


# ── Update Commentary ──────────────────────────────────────────────────────

@login_required
@require_POST
def update_commentary(request, pk):
    """
    Update commentary sections (accountant edits before sending to client).

    POST body (JSON):
      - section_snapshot: str (optional)
      - section_revenue: str (optional)
      - section_costs: str (optional)
      - section_watch_items: str (optional)
      - section_actions: str (optional)
      - mark_reviewed: bool (optional)
    """
    commentary = get_object_or_404(BASPeriodCommentary, pk=pk)

    fy = get_financial_year_for_user(request, commentary.financial_year.pk)
    if not fy:
        return JsonResponse({"error": "Access denied."}, status=403)

    if not commentary.is_editable:
        return JsonResponse(
            {"error": f"Commentary in '{commentary.get_status_display()}' status cannot be edited."},
            status=400,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}

    # Update sections if provided
    updated_fields = []
    for field in ["section_snapshot", "section_revenue", "section_costs",
                   "section_watch_items", "section_actions"]:
        if field in data:
            setattr(commentary, field, data[field])
            updated_fields.append(field)

    # Rebuild full_content
    if updated_fields:
        full_sections = []
        section_map = {
            "section_snapshot": "Period Snapshot",
            "section_revenue": "Revenue Analysis",
            "section_costs": "Cost & Margin Analysis",
            "section_watch_items": "Items to Watch",
            "section_actions": "Recommended Actions",
        }
        for field_name, heading in section_map.items():
            content = getattr(commentary, field_name)
            if content and content.strip():
                full_sections.append(f"**{heading}**\n\n{content}")
        commentary.full_content = "\n\n---\n\n".join(full_sections)

    # Mark as reviewed if requested
    if data.get("mark_reviewed"):
        commentary.status = "reviewed"
        commentary.reviewed_by = request.user
        commentary.reviewed_at = timezone.now()

    commentary.save()

    # Log activity for edits
    if updated_fields:
        _log_commentary_activity(
            request.user,
            "bas_commentary_edited",
            commentary,
            f"Edited {commentary.period_label} v{commentary.version}: {', '.join(updated_fields)}",
        )

    return JsonResponse({
        "id": str(commentary.pk),
        "status": commentary.status,
        "status_display": commentary.get_status_display(),
        "updated_fields": updated_fields,
    })


# ── Regenerate Commentary ──────────────────────────────────────────────────

@login_required
@require_POST
def regenerate_commentary(request, pk):
    """
    Re-generate an existing commentary. Creates a new version.
    """
    commentary = get_object_or_404(BASPeriodCommentary, pk=pk)

    fy = get_financial_year_for_user(request, commentary.financial_year.pk)
    if not fy:
        return JsonResponse({"error": "Access denied."}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}

    tone = data.get("tone", commentary.tone)

    # Find prior period commentary for trend chaining
    prior_commentary = _find_prior_commentary(commentary.bas_period)

    # Create a new version
    new_commentary = BASPeriodCommentary.objects.create(
        financial_year=commentary.financial_year,
        bas_period=commentary.bas_period,
        period_start=commentary.period_start,
        period_end=commentary.period_end,
        period_label=commentary.period_label,
        tone=tone,
        version=commentary.version + 1,
        generated_by=request.user,
        status="generating",
        prior_commentary=prior_commentary,
    )

    _queue_commentary_generation(str(new_commentary.pk), str(request.user.pk))

    # Log activity
    _log_commentary_activity(
        request.user,
        "bas_commentary_regenerated",
        new_commentary,
        f"Regenerated {new_commentary.period_label} v{new_commentary.version} (from v{commentary.version})",
    )

    return JsonResponse({
        "commentary_id": str(new_commentary.pk),
        "status": "generating",
        "version": new_commentary.version,
    }, status=201)


# ── Download Commentary ────────────────────────────────────────────────────

@login_required
@require_GET
def download_commentary(request, pk):
    """Download commentary as a Word document."""
    commentary = get_object_or_404(BASPeriodCommentary, pk=pk)

    fy = get_financial_year_for_user(request, commentary.financial_year.pk)
    if not fy:
        return JsonResponse({"error": "Access denied."}, status=403)

    if commentary.status == "generating":
        return JsonResponse({"error": "Commentary is still being generated."}, status=400)

    if commentary.status == "error":
        return JsonResponse({"error": "Commentary generation failed."}, status=400)

    try:
        from .eva_bas_commentary import generate_commentary_docx
        filepath = generate_commentary_docx(commentary)

        entity = fy.entity
        filename = (
            f"BAS_Commentary_{entity.entity_name}_{commentary.period_label}_v{commentary.version}.docx"
        ).replace(" ", "_").replace("(", "").replace(")", "")

        response = FileResponse(
            open(filepath, "rb"),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        # Log the download
        _log_commentary_activity(
            request.user,
            "doc_generated",
            commentary,
            f"Downloaded {commentary.period_label} v{commentary.version} as Word document",
        )

        return response

    except ImportError:
        return JsonResponse(
            {"error": "python-docx is not installed. Run: pip install python-docx"},
            status=500,
        )
    except Exception as e:
        logger.error(f"Commentary download error: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)


# ── Commentary Status (Polling) ────────────────────────────────────────────

@login_required
@require_GET
def commentary_status(request, pk):
    """
    Poll the generation status of a commentary.
    Uses database-backed tracking fields instead of an in-memory dict,
    so status survives server restarts and works across multiple workers.
    """
    commentary = get_object_or_404(BASPeriodCommentary, pk=pk)

    fy = get_financial_year_for_user(request, commentary.financial_year.pk)
    if not fy:
        return JsonResponse({"error": "Access denied."}, status=403)

    response = {
        "id": str(commentary.pk),
        "status": commentary.status,
        "status_display": commentary.get_status_display(),
    }

    if commentary.celery_task_id:
        response["celery_task_id"] = commentary.celery_task_id

    if commentary.status == "generating":
        response["step"] = commentary.generation_step or "Processing..."
    elif commentary.status == "error":
        response["error_message"] = commentary.error_message
    elif commentary.status in ("draft", "reviewed", "sent"):
        response["section_count"] = commentary.section_count

    return JsonResponse(response)


# ── Mark as Sent ───────────────────────────────────────────────────────────

@login_required
@require_POST
def mark_commentary_sent(request, pk):
    """
    Mark a commentary as sent to the client.

    POST body (JSON):
      - sent_to_email: str (optional — email address the commentary was sent to)
    """
    commentary = get_object_or_404(BASPeriodCommentary, pk=pk)

    fy = get_financial_year_for_user(request, commentary.financial_year.pk)
    if not fy:
        return JsonResponse({"error": "Access denied."}, status=403)

    if commentary.status not in ("draft", "reviewed"):
        return JsonResponse(
            {"error": f"Cannot mark as sent — commentary is '{commentary.get_status_display()}'."},
            status=400,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}

    commentary.status = "sent"
    commentary.sent_at = timezone.now()
    commentary.sent_to_email = data.get("sent_to_email", "")
    commentary.save(update_fields=["status", "sent_at", "sent_to_email"])

    # Log activity
    sent_to = f" to {commentary.sent_to_email}" if commentary.sent_to_email else ""
    _log_commentary_activity(
        request.user,
        "bas_commentary_sent",
        commentary,
        f"Sent {commentary.period_label} v{commentary.version}{sent_to}",
    )

    return JsonResponse({
        "id": str(commentary.pk),
        "status": "sent",
        "status_display": "Sent to Client",
        "sent_at": commentary.sent_at.isoformat(),
    })


# ── Delete Commentary ──────────────────────────────────────────────────────

@login_required
@require_POST
def delete_commentary(request, pk):
    """
    Delete a commentary. Only draft/reviewed/error commentaries can be deleted.
    Sent commentaries are preserved for audit trail.
    """
    commentary = get_object_or_404(BASPeriodCommentary, pk=pk)

    fy = get_financial_year_for_user(request, commentary.financial_year.pk)
    if not fy:
        return JsonResponse({"error": "Access denied."}, status=403)

    if commentary.status == "sent":
        return JsonResponse(
            {"error": "Sent commentaries cannot be deleted (audit trail)."},
            status=400,
        )

    if commentary.status == "generating":
        return JsonResponse(
            {"error": "Cannot delete a commentary that is still being generated."},
            status=400,
        )

    # Capture info before deletion for logging
    period_label = commentary.period_label
    version = commentary.version
    entity = fy.entity

    # Log activity before deletion
    _log_commentary_activity(
        request.user,
        "bas_commentary_deleted",
        commentary,
        f"Deleted {period_label} v{version}",
    )

    commentary.delete()

    return JsonResponse({
        "status": "deleted",
        "message": f"Commentary {period_label} v{version} deleted.",
    })


# ── Compare Commentaries (Side-by-Side) ───────────────────────────────────

@login_required
@require_GET
def compare_commentaries(request, pk):
    """
    Compare two commentaries side-by-side.

    Query params:
      - left: UUID of the left (older) commentary
      - right: UUID of the right (newer) commentary

    If only one ID is provided via 'right', the system will automatically
    use its prior_commentary as the left side.
    """
    fy = get_financial_year_for_user(request, pk)
    if not fy:
        return JsonResponse({"error": "Financial year not found or access denied."}, status=404)

    left_id = request.GET.get("left")
    right_id = request.GET.get("right")

    if not right_id and not left_id:
        return JsonResponse({"error": "At least one commentary ID is required."}, status=400)

    # If only right is provided, auto-detect left from prior_commentary
    if right_id and not left_id:
        right = get_object_or_404(BASPeriodCommentary, pk=right_id, financial_year=fy)
        if right.prior_commentary:
            left = right.prior_commentary
        else:
            # Try to find the previous period's latest commentary
            left = _find_prior_commentary(right.bas_period)
            if not left:
                return JsonResponse({
                    "error": "No prior period commentary found for comparison.",
                    "right": _serialize_commentary_for_compare(right),
                    "left": None,
                })
    elif left_id and not right_id:
        left = get_object_or_404(BASPeriodCommentary, pk=left_id, financial_year=fy)
        # Find the next period's commentary
        if left.bas_period:
            next_period = BASPeriod.objects.filter(
                financial_year=fy,
                period_type=left.bas_period.period_type,
                period_number=left.bas_period.period_number + 1,
            ).first()
            if next_period:
                right = BASPeriodCommentary.objects.filter(
                    bas_period=next_period,
                    status__in=["draft", "reviewed", "sent"],
                ).order_by("-version").first()
            else:
                right = None
        else:
            right = None

        if not right:
            return JsonResponse({
                "error": "No subsequent period commentary found for comparison.",
                "left": _serialize_commentary_for_compare(left),
                "right": None,
            })
    else:
        left = get_object_or_404(BASPeriodCommentary, pk=left_id)
        right = get_object_or_404(BASPeriodCommentary, pk=right_id)

    # Verify both belong to the same entity (may span financial years)
    if left.financial_year.entity_id != right.financial_year.entity_id:
        return JsonResponse({"error": "Commentaries must belong to the same entity."}, status=400)

    return JsonResponse({
        "left": _serialize_commentary_for_compare(left),
        "right": _serialize_commentary_for_compare(right),
    })


def _serialize_commentary_for_compare(commentary):
    """Serialize a commentary for the comparison view."""
    return {
        "id": str(commentary.pk),
        "period_label": commentary.period_label,
        "period_start": str(commentary.period_start),
        "period_end": str(commentary.period_end),
        "status": commentary.status,
        "status_display": commentary.get_status_display(),
        "tone": commentary.tone,
        "version": commentary.version,
        "section_snapshot": commentary.section_snapshot,
        "section_revenue": commentary.section_revenue,
        "section_costs": commentary.section_costs,
        "section_watch_items": commentary.section_watch_items,
        "section_actions": commentary.section_actions,
        "generated_by": commentary.generated_by.get_full_name() if commentary.generated_by else "",
        "generated_at": commentary.generated_at.isoformat() if commentary.generated_at else None,
        "sent_at": commentary.sent_at.isoformat() if commentary.sent_at else None,
    }


# ── Helper: Queue Generation ──────────────────────────────────────────────

def _queue_commentary_generation(commentary_pk, user_pk):
    """
    Queue commentary generation. Uses Celery if available, otherwise
    falls back to a background thread. Stores the Celery task ID on
    the BASPeriodCommentary record for reliable status tracking.
    """
    try:
        from core.tasks import eva_bas_commentary
        result = eva_bas_commentary.delay(commentary_pk, user_pk)
        BASPeriodCommentary.objects.filter(pk=commentary_pk).update(
            celery_task_id=result.id,
        )
        logger.info("BAS commentary queued via Celery: %s (task=%s)", commentary_pk, result.id)
    except Exception:
        # Fallback to thread (dev/testing)
        from core.eva_bas_commentary import generate_bas_commentary
        thread = threading.Thread(
            target=generate_bas_commentary,
            args=(commentary_pk, user_pk),
            daemon=True,
        )
        thread.start()
        logger.info("BAS commentary queued via thread: %s", commentary_pk)
