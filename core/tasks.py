"""
Celery tasks for StatementHub core application.

Task registry (Master Implementation Spec §7.10):
    - sync_knowledge_brain: SharePoint sync, chunk, embed
    - eva_chat_response: Build context, RAG search, call Sonnet/Opus
    - eva_finalisation_review: 8 compliance checks, create findings
    - eva_client_summary: Generate bullet + narrative summaries
    - extract_governing_document: Native text → Textract if scanned
    - process_textract_result: Assemble OCR text, store confidence
    - generate_legal_document: docxtpl render, LibreOffice PDF
    - assemble_client_package: Combine all PDFs, cover letter
    - bulk_package_generation: Iterate entities, check readiness
    - div7a_assessment: Coordinated 8-rule Div 7A detection per FY
    - div7a_batch_assessment: Batch Div 7A across multiple entities
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 2 — Knowledge Brain
# ---------------------------------------------------------------------------
@shared_task(name="core.sync_knowledge_brain", bind=True, max_retries=3)
def sync_knowledge_brain(self):
    """Sync Knowledge Brain from SharePoint: download, extract, chunk, embed."""
    from core.eva_service import sync_knowledge_brain as _sync
    try:
        result = _sync()
        logger.info("Knowledge Brain sync complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("Knowledge Brain sync failed")
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# Phase 3 — Eva Chat
# ---------------------------------------------------------------------------
@shared_task(name="core.eva_chat_response", bind=True, max_retries=2)
def eva_chat_response(self, conversation_id, user_message, model_tier="sonnet"):
    """
    Process an Eva chat message: build context, RAG search, call Claude.
    Returns the assistant message ID.
    """
    from core.eva_chat import process_chat_message
    try:
        result = process_chat_message(conversation_id, user_message, model_tier)
        return result
    except Exception as exc:
        logger.exception("Eva chat response failed for conversation %s", conversation_id)
        raise self.retry(exc=exc, countdown=10)


# ---------------------------------------------------------------------------
# Phase 9 — Eva Finalisation Review
# ---------------------------------------------------------------------------
@shared_task(name="core.eva_finalisation_review", bind=True, max_retries=1)
def eva_finalisation_review(self, financial_year_id, triggered_by_id=None):
    """
    Run 8 compliance checks on a financial year, create EvaReview + EvaFindings.
    """
    from core.eva_engine import run_finalisation_review
    try:
        result = run_finalisation_review(financial_year_id, triggered_by_id)
        logger.info("Finalisation review complete for FY %s: %s", financial_year_id, result)
        return result
    except Exception as exc:
        logger.exception("Finalisation review failed for FY %s", financial_year_id)
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Phase 10 — Eva Client Summary
# ---------------------------------------------------------------------------
@shared_task(name="core.eva_client_summary", bind=True, max_retries=2)
def eva_client_summary(self, financial_year_id):
    """
    Generate bullet-point and narrative client summaries when a year is locked.
    Five sections: Key Financial Highlights, Compliance Status, Tax Position,
    Recommendations, Year-on-Year Comparison.
    """
    from core.eva_summary import generate_client_summary
    try:
        result = generate_client_summary(financial_year_id)
        logger.info("Client summary generated for FY %s", financial_year_id)
        return result
    except Exception as exc:
        logger.exception("Client summary generation failed for FY %s", financial_year_id)
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Phase 7 — Governing Document OCR
# ---------------------------------------------------------------------------
@shared_task(name="core.extract_governing_document", bind=True, max_retries=2)
def extract_governing_document(self, governing_document_id):
    """
    Extract text from a governing document.
    Pipeline: native text extraction first → if <100 chars, queue for Textract.
    """
    from core.ocr_service import extract_document_text
    try:
        result = extract_document_text(governing_document_id)
        logger.info("Document extraction complete for %s: %s", governing_document_id, result)
        return result
    except Exception as exc:
        logger.exception("Document extraction failed for %s", governing_document_id)
        raise self.retry(exc=exc, countdown=60)


@shared_task(name="core.process_textract_result", bind=True, max_retries=3)
def process_textract_result(self, governing_document_id, textract_job_id):
    """
    Process completed AWS Textract result: assemble OCR text, store confidence scores.
    """
    from core.ocr_service import process_textract_callback
    try:
        result = process_textract_callback(governing_document_id, textract_job_id)
        logger.info("Textract processing complete for %s", governing_document_id)
        return result
    except Exception as exc:
        logger.exception("Textract processing failed for %s", governing_document_id)
        raise self.retry(exc=exc, countdown=120)


# ---------------------------------------------------------------------------
# Phase 8 — Legal Document Generation
# ---------------------------------------------------------------------------
@shared_task(name="core.generate_legal_document", bind=True, max_retries=2)
def generate_legal_document(self, legal_document_id):
    """
    Render a legal document using docxtpl, then convert to PDF via LibreOffice.
    """
    from core.legal_doc_service import render_legal_document
    try:
        result = render_legal_document(legal_document_id)
        logger.info("Legal document generated: %s", legal_document_id)
        return result
    except Exception as exc:
        logger.exception("Legal document generation failed for %s", legal_document_id)
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Phase 13 — Package Assembly
# ---------------------------------------------------------------------------
@shared_task(name="core.assemble_client_package", bind=True, max_retries=2)
def assemble_client_package(self, financial_year_id, assembled_by_id=None):
    """
    Assemble the client package for a financial year:
    scan docs → checklist → generate missing → combine PDFs → prepare for FuseSign.
    """
    from core.package_service import assemble_package
    try:
        result = assemble_package(financial_year_id, assembled_by_id)
        logger.info("Package assembled for FY %s", financial_year_id)
        return result
    except Exception as exc:
        logger.exception("Package assembly failed for FY %s", financial_year_id)
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# Phase 14 — Bulk Package Generation
# ---------------------------------------------------------------------------
@shared_task(name="core.bulk_package_generation", bind=True, max_retries=1)
def bulk_package_generation(self, entity_ids, triggered_by_id=None):
    """
    Generate packages for multiple entities. Iterates each entity's current FY,
    checks readiness, and queues individual assemble_client_package tasks.
    """
    from core.package_service import bulk_generate
    try:
        result = bulk_generate(entity_ids, triggered_by_id)
        logger.info("Bulk package generation complete: %s entities", len(entity_ids))
        return result
    except Exception as exc:
        logger.exception("Bulk package generation failed for %s entities", len(entity_ids))
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# BAS Period Commentary
# ---------------------------------------------------------------------------
@shared_task(name="core.eva_bas_commentary", bind=True, max_retries=1)
def eva_bas_commentary(self, commentary_id, user_id):
    """
    Generate AI-powered BAS period commentary.
    Transforms raw BAS/GST compliance data into client-ready advisory insights
    with a five-section structure.
    """
    from core.eva_bas_commentary import generate_bas_commentary
    try:
        result = generate_bas_commentary(commentary_id, user_id)
        logger.info("BAS commentary generated for %s", commentary_id)
        return result
    except Exception as exc:
        logger.exception("BAS commentary generation failed for %s", commentary_id)
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Division 7A Assessment
# ---------------------------------------------------------------------------
@shared_task(name="core.div7a_assessment", bind=True, max_retries=1)
def div7a_assessment(self, financial_year_id, triggered_by=None):
    """
    Run the coordinated 8-rule Division 7A assessment for a financial year.

    Produces one Div7AAssessment record per entity per FY and one
    consolidated EvaFinding card.  Only runs on company entities.
    """
    from core.eva_div7a import run_div7a_assessment
    try:
        result = run_div7a_assessment(financial_year_id, triggered_by)
        logger.info("Div 7A assessment complete for FY %s: %s", financial_year_id, result)
        return result
    except Exception as exc:
        logger.exception("Div 7A assessment failed for FY %s", financial_year_id)
        raise self.retry(exc=exc, countdown=30)


@shared_task(name="core.div7a_batch_assessment", bind=True, max_retries=1)
def div7a_batch_assessment(self, entity_ids=None, year_label=None):
    """
    Run Div 7A assessment across multiple company entities.
    """
    from core.eva_div7a import run_batch_div7a_assessment
    try:
        result = run_batch_div7a_assessment(entity_ids, year_label)
        logger.info("Batch Div 7A assessment complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("Batch Div 7A assessment failed")
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# Eva Proactive Suggestions
# ---------------------------------------------------------------------------
@shared_task(name="core.eva_proactive_suggestion", bind=True, max_retries=1)
def eva_proactive_suggestion(self, financial_year_id, trigger_type, trigger_context=None):
    """
    Generate a proactive Eva suggestion based on a system event.

    Trigger types:
      - risk_flags_raised: HIGH/CRITICAL risk flags detected
      - bank_classification_complete: Bank statement AI classification finished
      - significant_variance: Large variance detected during TB import

    The suggestion is stored as an EvaMessage with is_proactive=True.
    """
    from core.eva_proactive import generate_proactive_suggestion
    try:
        result = generate_proactive_suggestion(
            financial_year_id, trigger_type, trigger_context or {}
        )
        logger.info(
            "Proactive suggestion generated for FY %s (trigger: %s)",
            financial_year_id, trigger_type,
        )
        return result
    except Exception as exc:
        logger.exception(
            "Proactive suggestion failed for FY %s (trigger: %s)",
            financial_year_id, trigger_type,
        )
        raise self.retry(exc=exc, countdown=30)
