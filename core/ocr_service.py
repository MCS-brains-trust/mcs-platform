"""
OCR Service for Governing Document text extraction (Phase 7).

Pipeline:
  1. Native text extraction (PyPDF2 / python-docx / pdfplumber)
  2. If extracted text < 100 chars → queue for AWS Textract
  3. Process Textract callback → assemble OCR text, store confidence scores

Spec reference: Master Implementation Spec §7.6, §7.10
"""
import io
import json
import logging
import os
import uuid
from pathlib import Path

from django.core.files.base import ContentFile
from django.utils import timezone

from core.governing_doc_text import normalize_governing_doc_text
from core.governing_doc_retrieval import refresh_governing_document_chunks

logger = logging.getLogger(__name__)

# Minimum character threshold before falling back to OCR
MIN_TEXT_THRESHOLD = 100

# Textract confidence threshold for flagging low-confidence pages
LOW_CONFIDENCE_THRESHOLD = 80.0


# ---------------------------------------------------------------------------
# Main entry points (called by Celery tasks)
# ---------------------------------------------------------------------------
def extract_document_text(governing_document_id):
    """
    Extract text from a governing document.
    Pipeline: native text extraction first → if < 100 chars, queue for Textract.

    Returns dict with status and extracted text length.
    """
    from core.models import GoverningDocument

    doc = GoverningDocument.objects.get(pk=governing_document_id)
    file_path = doc.file.path if doc.file else None

    if not file_path or not os.path.exists(file_path):
        doc.extraction_status = "failed"
        doc.save(update_fields=["extraction_status"])
        return {"status": "failed", "error": "File not found"}

    file_ext = Path(file_path).suffix.lower()

    # Step 1: Try native text extraction
    try:
        text = _extract_native_text(file_path, file_ext)
    except Exception as e:
        logger.warning("Native extraction failed for %s: %s", governing_document_id, e)
        text = ""

    # Step 2: Check if we got enough text
    normalized_text = normalize_governing_doc_text(text)

    if len(normalized_text) >= MIN_TEXT_THRESHOLD:
        doc.extracted_text = normalized_text
        doc.extraction_status = "completed"
        doc.save(update_fields=["extracted_text", "extraction_status"])
        chunk_count = refresh_governing_document_chunks(doc)
        doc.chunk_count = chunk_count
        doc.save(update_fields=["chunk_count"])
        logger.info(
            "Native extraction successful for %s: %d chars",
            governing_document_id, len(normalized_text),
        )
        return {"status": "completed", "chars": len(normalized_text), "method": "native"}

    # Step 3: Fall back to AWS Textract (async)
    logger.info(
        "Native extraction insufficient (%d chars) for %s, queuing Textract",
        len(text.strip()), governing_document_id,
    )
    return _queue_textract(doc, file_path)


def process_textract_callback(governing_document_id, textract_job_id):
    """
    Process completed AWS Textract result:
    - Assemble OCR text from Textract blocks
    - Store per-page confidence scores
    - Flag low-confidence pages

    Returns dict with status and extracted text length.
    """
    from core.models import GoverningDocument

    doc = GoverningDocument.objects.get(pk=governing_document_id)

    if doc.textract_job_id != textract_job_id:
        return {"status": "error", "error": "Job ID mismatch"}

    try:
        text, low_confidence_pages = _get_textract_result(textract_job_id)
        normalized_text = normalize_governing_doc_text(text)

        doc.extracted_text = normalized_text
        doc.low_confidence_pages = low_confidence_pages

        if low_confidence_pages:
            doc.extraction_status = "completed_with_warnings"
        else:
            doc.extraction_status = "completed"

        doc.save(update_fields=[
            "extracted_text", "extraction_status", "low_confidence_pages",
        ])
        chunk_count = refresh_governing_document_chunks(doc)
        doc.chunk_count = chunk_count
        doc.save(update_fields=["chunk_count"])

        logger.info(
            "Textract processing complete for %s: %d chars, %d low-confidence pages",
            governing_document_id, len(normalized_text), len(low_confidence_pages),
        )
        return {
            "status": doc.extraction_status,
            "chars": len(normalized_text),
            "low_confidence_pages": low_confidence_pages,
            "method": "textract",
        }

    except Exception as e:
        logger.exception("Textract processing failed for %s", governing_document_id)
        doc.extraction_status = "failed"
        doc.extraction_error = str(e)[:2000]
        doc.save(update_fields=["extraction_status", "extraction_error"])
        return {"status": "failed", "error": str(e)}


# ---------------------------------------------------------------------------
# Native text extraction
# ---------------------------------------------------------------------------
def _extract_native_text(file_path, file_ext):
    """Extract text using native Python libraries based on file type."""
    if file_ext == ".pdf":
        return _extract_pdf_text(file_path)
    elif file_ext in (".docx", ".doc"):
        return _extract_docx_text(file_path)
    elif file_ext == ".txt":
        return _extract_txt_text(file_path)
    else:
        logger.warning("Unsupported file type for native extraction: %s", file_ext)
        return ""


def _extract_pdf_text(file_path):
    """Extract text from PDF using pdfplumber (better than PyPDF2 for scanned docs)."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
        return "\n\n".join(text_parts)
    except ImportError:
        # Fallback to PyPDF2
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            text_parts = []
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
            return "\n\n".join(text_parts)
        except ImportError:
            logger.warning("Neither pdfplumber nor PyPDF2 available")
            return ""


def _extract_docx_text(file_path):
    """Extract text from Word document."""
    try:
        from docx import Document
        doc = Document(file_path)
        return "\n".join(para.text for para in doc.paragraphs if para.text.strip())
    except ImportError:
        logger.warning("python-docx not available")
        return ""


def _extract_txt_text(file_path):
    """Extract text from plain text file."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------------------
# AWS Textract integration
# ---------------------------------------------------------------------------
def _textract_s3_key(doc, file_path):
    return f"textract-input/{doc.pk}/{os.path.basename(file_path)}"


def _queue_textract(doc, file_path):
    """
    Upload document to S3 (if not already there) and start an async Textract job.
    Returns dict with job status.
    """
    import boto3
    from botocore.exceptions import ClientError

    aws_region = os.environ.get("AWS_REGION", "ap-southeast-2")
    s3_bucket = os.environ.get("TEXTRACT_S3_BUCKET", "")
    sns_topic_arn = os.environ.get("TEXTRACT_SNS_TOPIC_ARN", "")
    role_arn = os.environ.get("TEXTRACT_ROLE_ARN", "")

    if not s3_bucket:
        doc.extraction_status = "failed"
        doc.extraction_error = "TEXTRACT_S3_BUCKET not configured"
        doc.save(update_fields=["extraction_status", "extraction_error"])
        return {
            "status": "failed",
            "error": "TEXTRACT_S3_BUCKET not configured",
        }

    try:
        s3_client = boto3.client("s3", region_name=aws_region)
        s3_key = _textract_s3_key(doc, file_path)

        # Skip re-upload if the object already exists (recovery path)
        already_uploaded = False
        try:
            s3_client.head_object(Bucket=s3_bucket, Key=s3_key)
            already_uploaded = True
        except ClientError as head_err:
            code = head_err.response.get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchKey", "NotFound"):
                raise

        if not already_uploaded:
            s3_client.upload_file(file_path, s3_bucket, s3_key)

        textract_client = boto3.client("textract", region_name=aws_region)

        start_params = {
            "DocumentLocation": {
                "S3Object": {
                    "Bucket": s3_bucket,
                    "Name": s3_key,
                }
            },
            "FeatureTypes": ["TABLES", "FORMS"],
        }

        if sns_topic_arn and role_arn:
            start_params["NotificationChannel"] = {
                "SNSTopicArn": sns_topic_arn,
                "RoleArn": role_arn,
            }

        response = textract_client.start_document_analysis(**start_params)
        job_id = response["JobId"]

        doc.textract_job_id = job_id
        doc.extraction_status = "ocr_pending"
        doc.extraction_error = ""
        doc.save(update_fields=["textract_job_id", "extraction_status", "extraction_error"])

        logger.info("Textract job started for %s: job_id=%s", doc.pk, job_id)
        return {"status": "ocr_pending", "job_id": job_id}

    except Exception as e:
        logger.exception("Failed to queue Textract for %s", doc.pk)
        doc.extraction_status = "failed"
        doc.extraction_error = str(e)[:2000]
        doc.save(update_fields=["extraction_status", "extraction_error"])
        return {"status": "failed", "error": str(e)}


def retrigger_textract(doc):
    """
    Re-submit a Textract job for an existing GoverningDocument without
    requiring a re-upload. Used by recover_stuck_ocr when the original
    job_id has expired (>7d AWS retention).
    """
    file_path = doc.file.path if doc.file else None
    if not file_path or not os.path.exists(file_path):
        doc.extraction_status = "failed"
        doc.extraction_error = "Source file no longer present on disk"
        doc.save(update_fields=["extraction_status", "extraction_error"])
        return {"status": "failed", "error": "File not found"}
    return _queue_textract(doc, file_path)


def _get_textract_result(job_id):
    """
    Retrieve Textract results, assemble text, and identify low-confidence pages.
    Returns (text, low_confidence_pages).
    """
    import boto3

    aws_region = os.environ.get("AWS_REGION", "ap-southeast-2")
    textract_client = boto3.client("textract", region_name=aws_region)

    # Get all pages of results
    pages_text = {}
    page_confidences = {}
    next_token = None

    while True:
        params = {"JobId": job_id}
        if next_token:
            params["NextToken"] = next_token

        response = textract_client.get_document_analysis(**params)

        for block in response.get("Blocks", []):
            page_num = block.get("Page", 1)

            if block["BlockType"] == "LINE":
                if page_num not in pages_text:
                    pages_text[page_num] = []
                pages_text[page_num].append(block.get("Text", ""))

                # Track confidence per page
                confidence = block.get("Confidence", 100.0)
                if page_num not in page_confidences:
                    page_confidences[page_num] = []
                page_confidences[page_num].append(confidence)

        next_token = response.get("NextToken")
        if not next_token:
            break

    # Assemble full text
    full_text_parts = []
    for page_num in sorted(pages_text.keys()):
        full_text_parts.append(f"--- Page {page_num} ---")
        full_text_parts.extend(pages_text[page_num])
    full_text = "\n".join(full_text_parts)

    # Identify low-confidence pages
    low_confidence_pages = []
    for page_num, confidences in page_confidences.items():
        avg_confidence = sum(confidences) / len(confidences) if confidences else 100.0
        if avg_confidence < LOW_CONFIDENCE_THRESHOLD:
            low_confidence_pages.append({
                "page": page_num,
                "avg_confidence": round(avg_confidence, 1),
                "line_count": len(confidences),
            })

    return full_text, low_confidence_pages


# ---------------------------------------------------------------------------
# Admin batch OCR migration (one-time)
# ---------------------------------------------------------------------------
def batch_ocr_migration(limit=50):
    """
    Process existing governing documents that haven't been OCR'd yet.
    Called from admin endpoint or management command.
    """
    from core.models import GoverningDocument
    from core.tasks import extract_governing_document

    pending = GoverningDocument.objects.filter(
        extraction_status="pending",
    ).order_by("uploaded_at")[:limit]

    queued = 0
    for doc in pending:
        extract_governing_document.delay(str(doc.pk))
        queued += 1

    logger.info("Batch OCR migration: queued %d documents", queued)
    return {"queued": queued, "total_pending": pending.count()}
