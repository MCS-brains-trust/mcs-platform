"""
StatementHub — Risk Engine Automatic Triggers
==============================================
Django signals that fire Tier 1 and Tier 2 risk analysis automatically
whenever trial balance data changes.

Tier 1 (variance analysis) runs immediately — it's pure math.
Tier 2 (ATO compliance rules) is debounced using Django's cache framework
to avoid excessive recalculation during rapid sequential edits.

Trigger events:
  - TrialBalanceLine saved or deleted
  - AdjustingJournal posted (status change to 'posted')
  - Journal deleted

No Celery required — debounce uses threading.Timer with cache-based
deduplication keyed by FinancialYear ID.
"""

import logging
import hashlib
import threading
from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Debounce window in seconds for Tier 2
TIER2_DEBOUNCE_SECONDS = 5

# Cache key prefix for debounce tokens
_DEBOUNCE_PREFIX = "risk_t2_debounce_"

# In-memory lock to prevent race conditions on timer management
_timer_lock = threading.Lock()
_active_timers = {}  # fy_id -> threading.Timer


def _get_financial_year_from_instance(instance):
    """
    Extract the FinancialYear from a model instance, if applicable.
    Returns None if the instance doesn't relate to a financial year
    or if the year is finalised (locked).
    """
    from core.models import FinancialYear

    fy = None

    # TrialBalanceLine
    if hasattr(instance, "financial_year_id") and hasattr(instance, "account_code"):
        fy = instance.financial_year

    # AdjustingJournal
    elif hasattr(instance, "financial_year_id") and hasattr(instance, "reference_number"):
        fy = instance.financial_year

    # JournalLine — get FY via parent journal
    elif hasattr(instance, "journal_id"):
        if hasattr(instance, "journal") and instance.journal:
            fy = instance.journal.financial_year

    if fy and fy.status == FinancialYear.Status.FINALISED:
        return None  # Don't recalculate on finalised years

    return fy


def trigger_tier1(financial_year, trigger_source="auto"):
    """
    Run Tier 1 variance analysis immediately (synchronous).
    This is pure math and executes in milliseconds.
    """
    from core.risk_engine import run_risk_engine

    try:
        result = run_risk_engine(financial_year, tiers=[1])
        logger.info(
            "Tier 1 auto-run for FY %s (%s): %d flags created, %d auto-resolved",
            financial_year.id,
            trigger_source,
            result["flags_created"],
            result["flags_auto_resolved"],
        )
        return result
    except Exception:
        logger.exception("Tier 1 auto-run failed for FY %s", financial_year.id)
        return None


def trigger_tier2(financial_year, trigger_source="auto"):
    """
    Run Tier 2 ATO compliance rules.
    Called after the debounce window expires.
    """
    from core.risk_engine import run_risk_engine

    try:
        result = run_risk_engine(financial_year, tiers=[2])
        logger.info(
            "Tier 2 auto-run for FY %s (%s): %d flags created, %d auto-resolved",
            financial_year.id,
            trigger_source,
            result["flags_created"],
            result["flags_auto_resolved"],
        )

        # Log to Activity trail
        _log_auto_risk_run(financial_year, result, trigger_source)

        return result
    except Exception:
        logger.exception("Tier 2 auto-run failed for FY %s", financial_year.id)
        return None


def _log_auto_risk_run(financial_year, result, trigger_source):
    """Log the automatic risk engine run to the Activity trail."""
    from core.models import ActivityLog

    try:
        ActivityLog.objects.create(
            financial_year=financial_year,
            event_type="audit_run",
            title=f"Risk analysis ({trigger_source})",
            description=(
                f"Automatic risk analysis ({trigger_source}): "
                f"{result['flags_created']} flags raised, "
                f"{result['flags_auto_resolved']} auto-resolved"
            ),
        )
    except Exception:
        logger.exception("Failed to log auto risk run for FY %s", financial_year.id)

    # Trigger Eva proactive suggestion if HIGH/CRITICAL flags were raised
    _maybe_trigger_proactive_risk_suggestion(financial_year, result)

    # Trigger Div 7A assessment for company entities after Tier 2 completes
    _maybe_trigger_div7a_assessment(financial_year, trigger_source)


def _maybe_trigger_div7a_assessment(financial_year, trigger_source):
    """
    Queue a Div 7A assessment if the entity is a company.
    Runs asynchronously via Celery after each Tier 2 risk engine run.
    """
    if financial_year.entity.entity_type != "company":
        return

    try:
        from core.tasks import div7a_assessment
        div7a_assessment.delay(str(financial_year.pk), trigger_source)
        logger.info(
            "Queued Div 7A assessment for FY %s (%s)",
            financial_year.pk, trigger_source,
        )
    except Exception:
        # Celery not running — run in background thread as fallback
        import threading
        from core.eva_div7a import run_div7a_assessment
        threading.Thread(
            target=run_div7a_assessment,
            args=(str(financial_year.pk), trigger_source),
            daemon=True,
        ).start()
        logger.warning(
            "Div 7A assessment queued in thread (Celery may not be running) for FY %s",
            financial_year.pk,
        )


def schedule_tier2_debounced(financial_year, trigger_source="auto"):
    """
    Schedule a Tier 2 run with debounce.

    Uses a cache key per FinancialYear to deduplicate rapid triggers.
    If a new trigger arrives within the debounce window, the previous
    timer is cancelled and a new one is scheduled.
    """
    fy_id = str(financial_year.id)
    cache_key = f"{_DEBOUNCE_PREFIX}{fy_id}"

    with _timer_lock:
        # Cancel any existing timer for this FY
        if fy_id in _active_timers:
            _active_timers[fy_id].cancel()
            del _active_timers[fy_id]

        # Set a debounce token in cache
        cache.set(cache_key, "pending", timeout=TIER2_DEBOUNCE_SECONDS + 2)

        def _run_after_debounce():
            """Execute Tier 2 after debounce window expires."""
            with _timer_lock:
                _active_timers.pop(fy_id, None)

            # Re-fetch the FY to ensure it's still valid
            from core.models import FinancialYear as FY
            try:
                fy = FY.objects.get(pk=fy_id)
                if fy.status == FY.Status.FINALISED:
                    return
                trigger_tier2(fy, trigger_source)
            except FY.DoesNotExist:
                pass
            finally:
                cache.delete(cache_key)
                # Update last_run and clear pending for badge polling
                from django.utils import timezone as tz
                cache.set(f'risk_engine_last_run_{fy_id}', tz.now(), timeout=86400)
                cache.delete(f'risk_engine_pending_{fy_id}')

        timer = threading.Timer(TIER2_DEBOUNCE_SECONDS, _run_after_debounce)
        timer.daemon = True
        _active_timers[fy_id] = timer
        timer.start()


def trigger_risk_recalc(financial_year, trigger_source="auto", force=False):
    """
    Main entry point: trigger Tier 1 immediately and schedule Tier 2
    with debounce. Called from signals and from explicit trigger points
    in views.

    Args:
        financial_year: FinancialYear instance
        trigger_source: string describing what triggered the recalc
                       (e.g., "tb_import", "journal_post", "bank_push")
        force: if True, skip debounce and run Tier 2 immediately
    """
    if not financial_year:
        return
    if financial_year.status == "finalised":
        return

    # Set pending flag for badge polling
    cache.set(f'risk_engine_pending_{financial_year.pk}', True, timeout=60)

    # Tier 1: immediate (synchronous, fast)
    trigger_tier1(financial_year, trigger_source)

    if force:
        # Run Tier 2 immediately (no debounce) — used for milestone triggers
        trigger_tier2(financial_year, trigger_source)
        # Update last_run and clear pending
        from django.utils import timezone as tz
        cache.set(f'risk_engine_last_run_{financial_year.pk}', tz.now(), timeout=86400)
        cache.delete(f'risk_engine_pending_{financial_year.pk}')
    else:
        # Tier 2: debounced
        schedule_tier2_debounced(financial_year, trigger_source)


# ============================================================================
# SIGNAL RECEIVERS
# ============================================================================

@receiver(post_save, sender="core.TrialBalanceLine")
def on_tb_line_saved(sender, instance, created, **kwargs):
    """Trigger risk recalc when a trial balance line is saved."""
    # Skip if this is part of a bulk import (signalled by _skip_risk_signal attr)
    if getattr(instance, "_skip_risk_signal", False):
        return
    fy = _get_financial_year_from_instance(instance)
    if fy:
        trigger_risk_recalc(fy, "tb_line_edit" if not created else "tb_line_created")


@receiver(post_delete, sender="core.TrialBalanceLine")
def on_tb_line_deleted(sender, instance, **kwargs):
    """Trigger risk recalc when a trial balance line is deleted."""
    fy = _get_financial_year_from_instance(instance)
    if fy:
        trigger_risk_recalc(fy, "tb_line_deleted")


@receiver(post_save, sender="core.AdjustingJournal")
def on_journal_saved(sender, instance, **kwargs):
    """Trigger risk recalc when a journal is posted."""
    # Only trigger when journal is posted (not on draft save)
    if instance.status == "posted":
        fy = instance.financial_year
        if fy and fy.status != "finalised":
            trigger_risk_recalc(fy, "journal_posted")


@receiver(post_delete, sender="core.AdjustingJournal")
def on_journal_deleted(sender, instance, **kwargs):
    """Trigger risk recalc when a journal is deleted."""
    fy = instance.financial_year
    if fy and fy.status != "finalised":
        trigger_risk_recalc(fy, "journal_deleted")


# ============================================================================
# FINANCIAL YEAR STATUS TRANSITION SIGNALS (Phase 14)
# ============================================================================

@receiver(post_save, sender="core.FinancialYear")
def track_fy_status_change(sender, instance, created, **kwargs):
    """
    Handle FinancialYear status transitions:
    - DRAFT → IN_REVIEW: Log activity
    - IN_REVIEW → FINALISED: Trigger Eva Client Summary generation
    - FINALISED → REOPENED: Log activity
    """
    if created:
        return

    # Detect status change by comparing with cached old value
    old_status = getattr(instance, "_old_status", None)
    new_status = instance.status

    if old_status is None or old_status == new_status:
        return

    logger.info(
        "FY %s status transition: %s → %s",
        instance.pk, old_status, new_status,
    )

    # Log the status change as an activity
    try:
        from core.models import ActivityLog
        ActivityLog.objects.create(
            financial_year=instance,
            event_type="fy_status_changed",
            title="Status change",
            description=f"Status changed from {old_status} to {new_status}",
        )
    except Exception:
        logger.exception("Failed to log status change activity")

    # Trigger Eva Client Summary when year is finalised
    if new_status == "finalised" and old_status != "finalised":
        try:
            from core.tasks import eva_client_summary
            eva_client_summary.delay(str(instance.pk))
            logger.info("Queued Eva client summary for FY %s", instance.pk)
        except Exception:
            logger.warning(
                "Could not queue Eva client summary (Celery may not be running)"
            )


@receiver(post_save, sender="core.EvaReview")
def handle_eva_review_completion(sender, instance, **kwargs):
    """
    When an EvaReview is completed, log the event.
    FY stays in_review — the accountant must click Finalise manually
    after all findings are addressed.
    """
    if instance.status not in ("completed", "cleared", "findings_raised"):
        return

    fy = instance.financial_year
    logger.info(
        "Eva review %s completed for FY %s with status=%s",
        instance.pk, fy.pk, instance.status,
    )


@receiver(post_save, sender="core.LegalDocument")
def handle_legal_document_created(sender, instance, created, **kwargs):
    """Log legal document creation as an activity."""
    if not created:
        return

    try:
        from core.models import ActivityLog
        if instance.financial_year:
            ActivityLog.objects.create(
                financial_year=instance.financial_year,
                event_type="doc_generated",
                title="Document generated",
                description=f"Generated: {instance.get_document_type_display()} for {instance.entity.entity_name}",
            )
    except Exception:
        logger.exception("Failed to log document creation activity")


# ============================================================================
# GOVERNING DOCUMENT OCR TRIGGER (Phase 7)
# ============================================================================

@receiver(post_save, sender="core.GoverningDocument")
def handle_governing_document_upload(sender, instance, created, **kwargs):
    """
    When a GoverningDocument is uploaded, queue OCR text extraction.
    Only triggers on creation (new upload), not on subsequent saves.
    """
    if not created:
        return

    if instance.extraction_status != "pending":
        return

    try:
        from core.tasks import extract_governing_document
        extract_governing_document.delay(str(instance.pk))
        logger.info("Queued OCR extraction for GoverningDocument %s", instance.pk)
    except Exception:
        logger.warning(
            "Could not queue OCR extraction (Celery may not be running)"
        )


# ============================================================================
# DIVIDEND EVENT ACTIVITY LOGGING (Phase 11)
# ============================================================================

@receiver(post_save, sender="core.DividendEvent")
def handle_dividend_event_created(sender, instance, created, **kwargs):
    """Log dividend event creation as an activity."""
    if not created:
        return

    try:
        from core.models import ActivityLog
        if instance.financial_year:
            ActivityLog.objects.create(
                financial_year=instance.financial_year,
                event_type="general",
                title="Dividend declared",
                description=(
                    f"Dividend declared: {instance.get_dividend_type_display()} "
                    f"${instance.total_amount:,.2f} "
                    f"({instance.franking_percentage}% franked)"
                ),
            )
    except Exception:
        logger.exception("Failed to log dividend event activity")


# ============================================================================
# EVA CONVERSATION ACTIVITY LOGGING (Phase 3)
# ============================================================================

# ============================================================================
# EVA PROACTIVE SUGGESTION TRIGGERS
# ============================================================================

def _maybe_trigger_proactive_risk_suggestion(financial_year, result):
    """
    If the risk engine raised HIGH or CRITICAL flags, queue a proactive
    Eva suggestion to alert the accountant.
    """
    flags_created = result.get("flags_created", 0)
    if flags_created == 0:
        return

    # Check for HIGH/CRITICAL flags specifically
    from core.models import RiskFlag
    open_flags_qs = RiskFlag.objects.filter(
        financial_year=financial_year,
        status="open",
        severity__in=["HIGH", "CRITICAL"],
    ).order_by("-created_at")

    if not open_flags_qs.exists():
        return

    critical_count = open_flags_qs.filter(severity="CRITICAL").count()
    high_count = open_flags_qs.filter(severity="HIGH").count()
    recent_flags = open_flags_qs[:5]
    flag_details = "\n".join(
        f"- [{f.severity}] {f.title}: {f.description[:120]}"
        for f in recent_flags
    )

    try:
        from core.tasks import eva_proactive_suggestion
        eva_proactive_suggestion.delay(
            str(financial_year.pk),
            "risk_flags_raised",
            {
                "flag_count": critical_count + high_count,
                "critical_count": critical_count,
                "high_count": high_count,
                "flag_details": flag_details,
            },
        )
        logger.info(
            "Queued proactive risk suggestion for FY %s (%d critical, %d high)",
            financial_year.pk, critical_count, high_count,
        )
    except Exception:
        # Celery not running — generate in thread as fallback
        import threading
        from core.eva_proactive import generate_proactive_suggestion
        threading.Thread(
            target=generate_proactive_suggestion,
            args=(
                str(financial_year.pk),
                "risk_flags_raised",
                {
                    "flag_count": critical_count + high_count,
                    "critical_count": critical_count,
                    "high_count": high_count,
                    "flag_details": flag_details,
                },
            ),
            daemon=True,
        ).start()


def trigger_proactive_bank_classification(financial_year, classification_result):
    """
    Called after bank statement AI classification completes.
    Queues a proactive Eva suggestion summarising the results.

    Args:
        financial_year: FinancialYear instance
        classification_result: dict with total_transactions, auto_classified,
                              needs_review, new_accounts
    """
    total = classification_result.get("total_transactions", 0)
    if total == 0:
        return

    auto = classification_result.get("auto_classified", 0)
    auto_pct = round((auto / total) * 100) if total > 0 else 0

    context = {
        "total_transactions": total,
        "auto_classified": auto,
        "auto_pct": auto_pct,
        "needs_review": classification_result.get("needs_review", 0),
        "new_accounts": classification_result.get("new_accounts", 0),
    }

    try:
        from core.tasks import eva_proactive_suggestion
        eva_proactive_suggestion.delay(
            str(financial_year.pk),
            "bank_classification_complete",
            context,
        )
        logger.info("Queued proactive bank classification suggestion for FY %s", financial_year.pk)
    except Exception:
        import threading
        from core.eva_proactive import generate_proactive_suggestion
        threading.Thread(
            target=generate_proactive_suggestion,
            args=(str(financial_year.pk), "bank_classification_complete", context),
            daemon=True,
        ).start()


# ============================================================================
# OFFICER CAPITAL ACCOUNT PROVISIONING (Trust beneficiaries/unit holders)
# ============================================================================

@receiver(post_save, sender="core.EntityOfficer")
def handle_officer_saved(sender, instance, created, **kwargs):
    """
    When a new officer is created with a distribution role (unit_holder or
    beneficiary) on a trust entity, auto-provision capital accounts.
    When an existing officer is ceased, mark their accounts as ceased.

    Phase 2: also materialises per-officer 4xxx children
    (see core/beneficiary_account_service.py) and propagates name changes
    to existing auto-provisioned ECAs.
    """
    from core.models import EntityOfficer

    if created and instance.role in EntityOfficer.DISTRIBUTION_ROLES:
        # Existing 9000-series provisioning — DO NOT TOUCH (Phase 3 will migrate)
        try:
            from core.capital_account_service import provision_capital_accounts
            provision_capital_accounts(instance.pk)
        except Exception:
            logger.exception(
                "Failed to provision capital accounts for officer %s", instance.pk
            )
        # NEW: 4xxx beneficiary-account provisioning
        try:
            from core.beneficiary_account_service import provision_beneficiary_accounts
            provision_beneficiary_accounts(instance.pk)
        except Exception:
            logger.exception(
                "Failed to provision beneficiary accounts for officer %s", instance.pk
            )

    if not created:
        # Officer-saved name-change propagation. Idempotent: zero updates
        # when nothing changed.
        try:
            from core.beneficiary_account_service import sync_officer_account_names
            sync_officer_account_names(instance.pk)
        except Exception:
            logger.exception(
                "Failed to sync officer account names for officer %s", instance.pk
            )

        if instance.date_ceased:
            try:
                from core.capital_account_service import cease_officer_accounts
                cease_officer_accounts(instance.pk)
            except Exception:
                logger.exception(
                    "Failed to cease capital accounts for officer %s", instance.pk
                )


@receiver(pre_delete, sender="core.EntityOfficer")
def handle_officer_pre_delete(sender, instance, **kwargs):
    """Delete auto-provisioned ECAs whenever an officer is deleted, regardless
    of code path (UI view, admin, shell, cascade). Phase 1.6 flagged that
    cleanup previously lived only in entity_officer_delete (views.py:6531-6535)
    and would orphan rows for any other deletion path.
    """
    from core.models import EntityChartOfAccount

    try:
        EntityChartOfAccount.objects.filter(
            beneficiary_officer=instance,
            auto_provisioned=True,
        ).delete()
    except Exception:
        logger.exception(
            "Failed to clean up auto-provisioned ECAs for deleted officer %s",
            instance.pk,
        )


# ============================================================================
# TRUST CHART OF ACCOUNTS AUTO-SEED
# ============================================================================

@receiver(post_save, sender="core.Entity")
def handle_trust_entity_created(sender, instance, created, **kwargs):
    """Seed trust entity COA from the master template.

    Trust template was rebuilt on 2026-04-29 after the original was found
    to contain leaked client data. seed_from_template now refuses any
    template row whose name matches SUSPICIOUS_REGEX so a future template
    edit cannot reintroduce client/firm data.
    """
    if not created:
        return

    from core.models import Entity, EntityChartOfAccount

    if instance.entity_type != Entity.EntityType.TRUST:
        return

    try:
        result = EntityChartOfAccount.seed_from_template(instance)
        logger.info(
            "Trust COA seeded for entity %s: %s accounts created",
            instance.id, result,
        )
    except Exception:
        logger.exception(
            "Failed to seed trust COA for entity %s", instance.id,
        )


@receiver(post_save, sender="core.EvaMessage")
def handle_eva_message_created(sender, instance, created, **kwargs):
    """Log Eva chat messages to the activity trail."""
    if not created:
        return

    # Only log user messages (not assistant responses)
    if instance.role != "user":
        return

    try:
        from core.models import ActivityLog
        conversation = instance.conversation
        if conversation and conversation.financial_year:
            ActivityLog.objects.create(
                financial_year=conversation.financial_year,
                user=conversation.user,
                event_type="general",
                title="Eva chat",
                description=f"Eva chat message ({conversation.interaction_type})",
            )
    except Exception:
        logger.exception("Failed to log Eva message activity")
