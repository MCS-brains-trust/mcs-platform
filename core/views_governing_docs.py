"""Views for Governing Documents — upload, extract, archive, OCR pipeline."""
import io
import logging
import os
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Entity, GoverningDocument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
@login_required
@require_POST
def governing_doc_upload(request, pk):
    """Upload a governing document (primary or amendment)."""
    entity = get_object_or_404(Entity, pk=pk)
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


# ---------------------------------------------------------------------------
# Archive API
# ---------------------------------------------------------------------------
@login_required
@require_POST
def governing_doc_archive(request, doc_pk):
    """Archive a governing document."""
    doc = get_object_or_404(GoverningDocument, pk=doc_pk)
    doc.status = GoverningDocument.Status.ARCHIVED
    doc.archived_by = request.user
    doc.archived_at = timezone.now()
    doc.is_primary = False
    doc.save(update_fields=["status", "archived_by", "archived_at", "is_primary"])
    return JsonResponse({"status": "ok"})


# Governing document extraction is handled centrally by core.tasks.extract_governing_document
# and core.ocr_service so uploads, manual retries, and any background reprocessing all use
# the same pipeline.