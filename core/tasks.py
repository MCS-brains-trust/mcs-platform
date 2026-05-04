"""
Celery tasks for StatementHub core application.

Task registry (Master Implementation Spec §7.10):
    - sync_knowledge_brain: SharePoint sync, chunk, embed
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


@shared_task(name="core.poll_stuck_textract_jobs")
def poll_stuck_textract_jobs():
    """
    Polling fallback for the SNS webhook. Scans every doc stuck in
    `ocr_pending` for >15 minutes, fetches Textract status, and either
    completes, fails, or re-triggers an expired job.
    """
    from django.core.management import call_command
    call_command("recover_stuck_ocr", "--all", "--min-age-minutes", "15")


@shared_task(name="core.verify_textract_sns_daily")
def verify_textract_sns_daily():
    """Daily check that the Textract SNS subscription is still confirmed."""
    from django.core.management import call_command
    try:
        call_command("verify_textract_sns")
    except SystemExit as exc:
        if getattr(exc, "code", 0):
            logger.warning("verify_textract_sns reported broken subscription (exit %s)", exc.code)


@shared_task(name="core.check_industry_data_freshness")
def check_industry_data_freshness():
    """
    Warn if the ATO BIC fixture is older than its expected refresh window.
    Reads the version markers in core.industry_codes.
    """
    from datetime import date
    from core import industry_codes

    try:
        last = date.fromisoformat(industry_codes.__last_checked__)
    except (AttributeError, ValueError):
        logger.warning(
            "ATO BIC freshness check: could not parse __last_checked__ "
            "in core.industry_codes — refresh required."
        )
        return

    age_days = (date.today() - last).days
    expected = getattr(industry_codes, "__expected_refresh_days__", 365)
    version = getattr(industry_codes, "__version__", "unknown")
    if age_days > expected:
        logger.warning(
            "ATO BIC fixture is stale. Last checked %s (%d days ago, "
            "threshold %d). Current dataset: %s. Refresh required.",
            last.isoformat(), age_days, expected, version,
        )


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
        # On final retry failure, ensure the DB record is marked as failed
        if self.request.retries >= self.max_retries:
            try:
                from django.utils import timezone as tz
                from core.models import BASPeriodCommentary
                BASPeriodCommentary.objects.filter(pk=commentary_id).update(
                    status="error",
                    error_message=f"Task failed after {self.max_retries + 1} attempts: {str(exc)[:900]}",
                    generation_completed_at=tz.now(),
                    generation_step="",
                )
            except Exception:
                logger.exception("Failed to mark commentary %s as error", commentary_id)
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


# ---------------------------------------------------------------------------
# Chart-of-accounts hygiene — guards against re-introduction of leaked data
# ---------------------------------------------------------------------------
@shared_task(name="core.check_template_hygiene")
def check_template_hygiene():
    """Monthly scan of master + per-entity COA rows for suspicious account names.

    Logs WARNING with sample hits — does not mutate. Use the warning log to
    decide whether the template needs another rebuild. Per-entity rows added
    by accountants (is_custom=True) are excluded from the per-entity scan
    because they are intentional client-specific labels and not template
    leakage.
    """
    from core.models import (
        ChartOfAccount, EntityChartOfAccount, SUSPICIOUS_NAME_REGEX,
    )

    suspect_template = []
    for a in ChartOfAccount.objects.all():
        if SUSPICIOUS_NAME_REGEX.search(a.account_name):
            suspect_template.append(f"{a.entity_type} | {a.account_code} | {a.account_name}")

    suspect_eca = []
    for a in EntityChartOfAccount.objects.filter(is_custom=False):
        if SUSPICIOUS_NAME_REGEX.search(a.account_name):
            suspect_eca.append(f"entity={a.entity_id} | {a.account_code} | {a.account_name}")

    if suspect_template or suspect_eca:
        logger.warning(
            "COA hygiene check found suspicious rows. "
            "Templates: %d, seeded ECAs: %d. "
            "Template hits (first 10): %s. "
            "ECA hits (first 10): %s.",
            len(suspect_template), len(suspect_eca),
            suspect_template[:10], suspect_eca[:10],
        )
    else:
        logger.info("COA hygiene check: clean.")

    return {
        "template_suspect": len(suspect_template),
        "eca_suspect": len(suspect_eca),
    }


# ===========================================================================
# Eva Intelligence Upgrade — Celery Tasks
# ===========================================================================

@shared_task(
    name="core.eva_nightly_reflection",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def eva_nightly_reflection(self):
    """
    Nightly Reflection Engine — runs at 2:00 AM daily.

    Reads the last 24 hours of accountant interaction signals and uses
    Claude Sonnet to extract generalised lessons stored in EvaLearnedLesson.
    """
    try:
        from core.eva_reflection import run_nightly_reflection
        result = run_nightly_reflection(hours_back=24)
        logger.info(
            "eva_nightly_reflection complete: %d signals → %d lessons stored",
            result.get("signals_processed", 0),
            result.get("lessons_stored", 0),
        )
        return result
    except Exception as exc:
        logger.error("eva_nightly_reflection failed: %s", exc)
        raise self.retry(exc=exc)


@shared_task(
    name="core.eva_weekly_style_update",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def eva_weekly_style_update(self):
    """
    Weekly Style Update Engine — runs every Monday at 6:00 AM.

    Analyses unprocessed EvaCommentaryEdit records and updates
    UserStyleProfile for each accountant.
    """
    try:
        from core.eva_style import run_weekly_style_update
        result = run_weekly_style_update()
        logger.info(
            "eva_weekly_style_update complete: %d users, %d profiles updated",
            result.get("users_processed", 0),
            result.get("profiles_updated", 0),
        )
        return result
    except Exception as exc:
        logger.error("eva_weekly_style_update failed: %s", exc)
        raise self.retry(exc=exc)


@shared_task(
    name="core.eva_daily_proactive_scan",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def eva_daily_proactive_scan(self):
    """
    Daily Proactive Scan — runs at 7:00 AM daily.

    Scans all active financial years for time-sensitive issues and
    generates proactive Eva messages using the Agent Loop.
    """
    try:
        from core.eva_proactive_v2 import run_daily_proactive_scan
        result = run_daily_proactive_scan()
        logger.info(
            "eva_daily_proactive_scan complete: %d years, %d issues, %d messages",
            result.get("years_scanned", 0),
            result.get("issues_found", 0),
            result.get("messages_generated", 0),
        )
        return result
    except Exception as exc:
        logger.error("eva_daily_proactive_scan failed: %s", exc)
        raise self.retry(exc=exc)
