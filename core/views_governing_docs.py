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

    doc = GoverningDocument.objects.create(
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

    # Auto-trigger text extraction
    _extract_text_async(doc)

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
        _extract_text_async(doc)
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


# ---------------------------------------------------------------------------
# Text Extraction Pipeline
# ---------------------------------------------------------------------------
def _extract_text_async(doc):
    """
    Extract text from a governing document.
    Pipeline: native text extraction first → if < 100 chars, queue for Textract.
    """
    try:
        file_path = doc.file.path if hasattr(doc.file, "path") else None
        file_name = doc.original_filename.lower()

        extracted = ""

        # Step 1: Try native text extraction
        if file_name.endswith(".pdf"):
            extracted = _extract_pdf_text(doc.file)
        elif file_name.endswith(".docx"):
            extracted = _extract_docx_text(doc.file)

        # Step 2: If insufficient text, queue for OCR
        if len(extracted.strip()) < 100:
            doc.extraction_status = GoverningDocument.ExtractionStatus.OCR_PENDING
            doc.save(update_fields=["extraction_status"])
            _queue_textract(doc)
            return

        doc.extracted_text = extracted
        doc.extraction_status = GoverningDocument.ExtractionStatus.COMPLETED
        doc.save(update_fields=["extracted_text", "extraction_status"])

    except Exception as e:
        logger.exception("Text extraction failed for doc %s", doc.pk)
        doc.extraction_status = GoverningDocument.ExtractionStatus.FAILED
        doc.save(update_fields=["extraction_status"])


def _extract_pdf_text(file_field):
    """Extract text from a PDF using PyPDF2."""
    try:
        import PyPDF2

        file_field.seek(0)
        reader = PyPDF2.PdfReader(file_field)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except ImportError:
        # Fallback to pdfplumber
        try:
            import pdfplumber

            file_field.seek(0)
            with pdfplumber.open(file_field) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages)
        except ImportError:
            logger.warning("Neither PyPDF2 nor pdfplumber installed")
            return ""
    except Exception as e:
        logger.warning("PDF text extraction failed: %s", e)
        return ""


def _extract_docx_text(file_field):
    """Extract text from a DOCX file."""
    try:
        import docx

        file_field.seek(0)
        doc = docx.Document(file_field)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except ImportError:
        logger.warning("python-docx not installed")
        return ""
    except Exception as e:
        logger.warning("DOCX text extraction failed: %s", e)
        return ""


def _queue_textract(doc):
    """
    Queue document for AWS Textract OCR processing.
    Falls back to marking as failed if AWS is not configured.
    """
    try:
        import boto3

        aws_key = getattr(settings, "AWS_ACCESS_KEY_ID", None)
        aws_secret = getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
        aws_region = getattr(settings, "AWS_REGION", "ap-southeast-2")

        if not aws_key or not aws_secret:
            logger.warning("AWS credentials not configured — cannot run Textract")
            doc.extraction_status = GoverningDocument.ExtractionStatus.FAILED
            doc.save(update_fields=["extraction_status"])
            return

        # Read file bytes
        doc.file.seek(0)
        file_bytes = doc.file.read()

        client = boto3.client(
            "textract",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region,
        )

        # For documents under 5MB, use synchronous DetectDocumentText
        if len(file_bytes) < 5 * 1024 * 1024:
            response = client.detect_document_text(
                Document={"Bytes": file_bytes}
            )
            lines = []
            low_confidence = set()
            for block in response.get("Blocks", []):
                if block["BlockType"] == "LINE":
                    lines.append(block.get("Text", ""))
                    confidence = block.get("Confidence", 100)
                    if confidence < 80:
                        page = block.get("Page", 0)
                        low_confidence.add(page)

            doc.extracted_text = "\n".join(lines)
            doc.low_confidence_pages = sorted(low_confidence)
            if low_confidence:
                doc.extraction_status = (
                    GoverningDocument.ExtractionStatus.COMPLETED_WITH_WARNINGS
                )
            else:
                doc.extraction_status = GoverningDocument.ExtractionStatus.COMPLETED
            doc.save(
                update_fields=[
                    "extracted_text",
                    "extraction_status",
                    "low_confidence_pages",
                ]
            )
        else:
            # For larger documents, use async StartDocumentTextDetection
            # This requires S3 upload — mark as pending for now
            logger.info(
                "Document %s is too large for sync Textract, needs S3 pipeline",
                doc.pk,
            )
            doc.extraction_status = GoverningDocument.ExtractionStatus.FAILED
            doc.save(update_fields=["extraction_status"])

    except ImportError:
        logger.warning("boto3 not installed — cannot run Textract")
        doc.extraction_status = GoverningDocument.ExtractionStatus.FAILED
        doc.save(update_fields=["extraction_status"])
    except Exception as e:
        logger.exception("Textract OCR failed for doc %s: %s", doc.pk, e)
        doc.extraction_status = GoverningDocument.ExtractionStatus.FAILED
        doc.save(update_fields=["extraction_status"])
