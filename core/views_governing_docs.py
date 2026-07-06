"""Views for Governing Documents — upload, extract, archive, OCR pipeline."""
import io
import logging
import os
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.db.models import ProtectedError
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.authorization import get_entity_for_user
from core.models import Entity, GoverningDocument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
@login_required
@require_POST
def governing_doc_upload(request, pk):
    """Upload a governing document (primary or amendment)."""
    entity = get_entity_for_user(request, pk)
    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return redirect("core:entity_detail", pk=pk)

    is_primary = request.POST.get("is_primary") == "true"

    # Determine document type
    if is_primary:
        entity_type = entity.entity_type or ""
        if "trust" in entity_type:
            doc_type = GoverningDocument.DocumentType.TRUST_DEED
        elif entity_type == "partnership":
            doc_type = GoverningDocument.DocumentType.PARTNERSHIP_AGREEMENT
        elif entity_type == "smsf":
            doc_type = GoverningDocument.DocumentType.SMSF_DEED
        else:
            doc_type = GoverningDocument.DocumentType.COMPANY_CONSTITUTION
    else:
        doc_type = request.POST.get(
            "document_type", GoverningDocument.DocumentType.AMENDMENT
        )

    # If uploading a new primary, archive the old one
    if is_primary:
        GoverningDocument.objects.filter(
            entity=entity, is_primary=True, status=GoverningDocument.Status.ACTIVE
        ).update(
            is_primary=False,
            status=GoverningDocument.Status.ARCHIVED,
            archived_by=request.user,
            archived_at=timezone.now(),
        )

    GoverningDocument.objects.create(
        entity=entity,
        document_type=doc_type,
        is_primary=is_primary,
        file=uploaded_file,
        original_filename=uploaded_file.name,
        file_size_bytes=uploaded_file.size,
        document_date=request.POST.get("document_date") or None,
        description=request.POST.get("description", ""),
        uploaded_by=request.user,
    )

    return redirect("core:entity_detail", pk=pk)


# ---------------------------------------------------------------------------
# Text Extraction API
# ---------------------------------------------------------------------------
@login_required
@require_POST
def governing_doc_extract(request, doc_pk):
    """Trigger text extraction for a governing document."""
    doc = get_object_or_404(GoverningDocument, pk=doc_pk)
    get_entity_for_user(request, doc.entity_id)
    try:
        from core.tasks import extract_governing_document
        doc.extraction_status = GoverningDocument.ExtractionStatus.PENDING
        doc.save(update_fields=["extraction_status"])
        extract_governing_document.delay(str(doc.pk))
        return JsonResponse(
            {"status": "ok", "message": "Text extraction started."}
        )
    except Exception as e:
        logger.exception("Text extraction failed for doc %s", doc_pk)
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


def _humanise_elapsed(delta):
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _governing_doc_progress_payload(doc):
    extraction_status = doc.extraction_status or GoverningDocument.ExtractionStatus.PENDING
    status_label_map = {
        GoverningDocument.ExtractionStatus.PENDING: "Queued for extraction",
        GoverningDocument.ExtractionStatus.OCR_PENDING: "Processing in AWS Textract",
        GoverningDocument.ExtractionStatus.COMPLETED: "Text extracted successfully",
        GoverningDocument.ExtractionStatus.COMPLETED_WITH_WARNINGS: "Extraction completed with warnings",
        GoverningDocument.ExtractionStatus.FAILED: "Extraction failed",
        GoverningDocument.ExtractionStatus.EXPIRED: "Textract result expired — re-upload required",
    }
    is_indeterminate = extraction_status in (
        GoverningDocument.ExtractionStatus.PENDING,
        GoverningDocument.ExtractionStatus.OCR_PENDING,
    )
    is_finished = extraction_status in (
        GoverningDocument.ExtractionStatus.COMPLETED,
        GoverningDocument.ExtractionStatus.COMPLETED_WITH_WARNINGS,
        GoverningDocument.ExtractionStatus.FAILED,
        GoverningDocument.ExtractionStatus.EXPIRED,
    )

    started_at = getattr(doc, "updated_at", None) or doc.uploaded_at
    elapsed_label = ""
    if is_indeterminate and started_at:
        elapsed_label = _humanise_elapsed(timezone.now() - started_at)

    return {
        "doc_id": str(doc.pk),
        "extraction_status": extraction_status,
        "status_label": status_label_map.get(extraction_status, "Processing"),
        "is_indeterminate": is_indeterminate,
        "is_finished": is_finished,
        "elapsed_label": elapsed_label,
        "extraction_error": getattr(doc, "extraction_error", "") or "",
        "has_text": bool((doc.extracted_text or "").strip()),
        "chunk_count": getattr(doc, "chunk_count", 0),
    }


@login_required
def governing_doc_status(request, doc_pk):
    """Return extraction progress/status for a governing document."""
    doc = get_object_or_404(GoverningDocument, pk=doc_pk)
    get_entity_for_user(request, doc.entity_id)
    return JsonResponse({"status": "ok", "document": _governing_doc_progress_payload(doc)})


# ---------------------------------------------------------------------------
# Archive API
# ---------------------------------------------------------------------------
@login_required
@require_POST
def governing_doc_archive(request, doc_pk):
    """Archive a governing document."""
    doc = get_object_or_404(GoverningDocument, pk=doc_pk)
    get_entity_for_user(request, doc.entity_id)
    doc.status = GoverningDocument.Status.ARCHIVED
    doc.archived_by = request.user
    doc.archived_at = timezone.now()
    doc.is_primary = False
    doc.save(update_fields=["status", "archived_by", "archived_at", "is_primary"])
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def governing_doc_delete(request, doc_pk):
    """Permanently delete a governing document."""
    doc = get_object_or_404(GoverningDocument, pk=doc_pk)
    get_entity_for_user(request, doc.entity_id)
    try:
        doc.delete()
        return JsonResponse({"status": "ok"})
    except ProtectedError:
        return JsonResponse(
            {
                "status": "error",
                "error": "This document is linked to other records and cannot be deleted.",
            },
            status=400,
        )
    except Exception as e:
        logger.exception("Failed deleting governing document %s", doc_pk)
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


# Governing document extraction is handled centrally by core.tasks.extract_governing_document
# and core.ocr_service so uploads, manual retries, and any background reprocessing all use
# the same pipeline.
