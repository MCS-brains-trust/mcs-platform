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
    )

    # Queue generation in background thread (or Celery if available)
    _queue_commentary_generation(str(commentary.pk), str(request.user.pk))

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
        "is_editable": commentary.is_editable,
        "error_message": commentary.error_message,
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
    )

    _queue_commentary_generation(str(new_commentary.pk), str(request.user.pk))

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
        try:
            ActivityLog.objects.create(
                user=request.user,
                event_type="bas_commentary_downloaded",
                title=f"BAS Commentary Downloaded — {entity.entity_name}",
                description=f"Downloaded {commentary.period_label} v{commentary.version}",
                entity=entity,
                financial_year=fy,
            )
        except Exception:
            pass

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
    Used by the frontend to check when generation is complete.
    """
    commentary = get_object_or_404(BASPeriodCommentary, pk=pk)

    fy = get_financial_year_for_user(request, commentary.financial_year.pk)
    if not fy:
        return JsonResponse({"error": "Access denied."}, status=403)

    # Check in-memory task tracker
    from .eva_bas_commentary import _commentary_tasks
    task_info = _commentary_tasks.get(str(commentary.pk), {})

    response = {
        "id": str(commentary.pk),
        "status": commentary.status,
        "status_display": commentary.get_status_display(),
    }

    if task_info.get("status") == "running":
        response["step"] = task_info.get("step", "Processing...")
    elif commentary.status == "error":
        response["error_message"] = commentary.error_message
    elif commentary.status in ("draft", "reviewed", "sent"):
        response["section_count"] = commentary.section_count

    return JsonResponse(response)


# ── Helper: Queue Generation ──────────────────────────────────────────────

def _queue_commentary_generation(commentary_pk, user_pk):
    """
    Queue commentary generation. Uses Celery if available, otherwise
    falls back to a background thread.
    """
    try:
        from core.tasks import eva_bas_commentary
        eva_bas_commentary.delay(commentary_pk, user_pk)
        logger.info("BAS commentary queued via Celery: %s", commentary_pk)
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
