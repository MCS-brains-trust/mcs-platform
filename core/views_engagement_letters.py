"""Views for Engagement Letters — upload, archive, delete."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import EngagementLetter, Entity, FinancialYear

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
@login_required
@require_POST
def engagement_letter_upload(request, pk):
    """Upload an engagement letter for a specific entity + financial year.

    The accountant MUST select the financial year via the form field
    ``financial_year_id``.  The upload is rejected if no year is selected.
    """
    entity = get_object_or_404(Entity, pk=pk)

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        messages.error(request, "No file selected. Please choose a PDF or DOCX file.")
        return redirect("core:entity_detail", pk=pk)

    fy_id = request.POST.get("financial_year_id")
    if not fy_id:
        messages.error(
            request,
            "You must select the financial year this engagement letter covers.",
        )
        return redirect("core:entity_detail", pk=pk)

    financial_year = get_object_or_404(FinancialYear, pk=fy_id, entity=entity)

    status = request.POST.get("status", EngagementLetter.Status.DRAFT)
    if status not in EngagementLetter.Status.values:
        status = EngagementLetter.Status.DRAFT

    notes = request.POST.get("notes", "")

    letter = EngagementLetter(
        entity=entity,
        financial_year=financial_year,
        file=uploaded_file,
        original_filename=uploaded_file.name,
        file_size_bytes=uploaded_file.size,
        status=status,
        notes=notes,
        is_current=True,
        uploaded_by=request.user,
    )
    letter.save()

    messages.success(
        request,
        f"Engagement letter for {financial_year.year_label} uploaded successfully.",
    )
    return redirect(f"{request.build_absolute_uri('/')[:-1]}/entities/{pk}/?tab=engagement_letters")


# ---------------------------------------------------------------------------
# Archive (soft-delete — marks is_current=False)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def engagement_letter_archive(request, letter_pk):
    """Mark an engagement letter as superseded / archived."""
    letter = get_object_or_404(EngagementLetter, pk=letter_pk)
    entity = letter.entity

    letter.is_current = False
    letter.status = EngagementLetter.Status.SUPERSEDED
    letter.save(update_fields=["is_current", "status"])

    return JsonResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Delete (hard-delete — for accidental uploads only)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def engagement_letter_delete(request, letter_pk):
    """Permanently delete an engagement letter (accidental upload recovery)."""
    letter = get_object_or_404(EngagementLetter, pk=letter_pk)
    entity_pk = letter.entity.pk

    try:
        letter.file.delete(save=False)
    except Exception:
        pass
    letter.delete()

    return JsonResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Status update (AJAX)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def engagement_letter_update_status(request, letter_pk):
    """Update the status of an engagement letter (e.g. draft → signed)."""
    import json

    letter = get_object_or_404(EngagementLetter, pk=letter_pk)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)

    new_status = data.get("status")
    if new_status not in EngagementLetter.Status.values:
        return JsonResponse({"status": "error", "error": "Invalid status"}, status=400)

    letter.status = new_status
    letter.save(update_fields=["status"])

    return JsonResponse({"status": "ok", "new_status": letter.get_status_display()})
