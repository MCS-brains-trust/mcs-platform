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
from django.db.models.signals import post_save, post_delete
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
            action_type="audit_run",
            description=(
                f"Automatic risk analysis ({trigger_source}): "
                f"{result['flags_created']} flags raised, "
                f"{result['flags_auto_resolved']} auto-resolved"
            ),
            metadata={
                "trigger": trigger_source,
                "automatic": True,
                "run_id": result.get("run_id", ""),
                "flags_created": result["flags_created"],
                "flags_auto_resolved": result["flags_auto_resolved"],
            },
        )
    except Exception:
        logger.exception("Failed to log auto risk run for FY %s", financial_year.id)


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
