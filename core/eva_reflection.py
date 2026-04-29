"""
Eva Nightly Reflection Engine
==============================
Runs as a Celery Beat task at 2:00 AM daily.

Reads the last 24 hours of accountant interaction signals and uses Claude
Sonnet to extract generalised lessons, which are stored in EvaLearnedLesson
with vector embeddings for future RAG retrieval.

Signal Sources (in priority order):
  1. EvaClarification    — Accountant answered Eva's question about a finding
  2. EvaFindingSuppression — Accountant suppressed a finding (with reason)
  3. EvaMessage (user)   — Accountant sent a message correcting Eva
  4. ActivityLog         — General system events (account reclassifications, etc.)

The reflection prompt instructs Claude to:
  - Extract the RULE behind the action (not just the action itself)
  - Classify the rule (classification, compliance, workflow, etc.)
  - Determine if it applies to one entity or the whole firm
  - Assign a confidence score

Lessons with confidence < 0.6 are stored but not injected into retrieval
until they are reinforced by additional signals.

Usage (called by Celery):
    from core.eva_reflection import run_nightly_reflection
    run_nightly_reflection()
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum confidence to store a lesson
MIN_CONFIDENCE = 0.5
# Maximum signals to process per run (prevents runaway API costs)
MAX_SIGNALS_PER_RUN = 200


# ---------------------------------------------------------------------------
# Signal Extraction
# ---------------------------------------------------------------------------
def _extract_signals(since: datetime) -> list:
    """
    Gather all learning signals from the last N hours.
    Returns a list of signal dicts.
    """
    from core.models import (
        EvaClarification, EvaFindingSuppression,
        EvaMessage, ActivityLog,
    )

    signals = []

    # 1. EvaClarification — highest value signal
    for c in EvaClarification.objects.filter(
        created_at__gte=since,
        answer__isnull=False,
    ).select_related("finding__eva_review__financial_year__entity", "answered_by").order_by("-created_at")[:50]:
        entity = None
        try:
            entity = c.finding.eva_review.financial_year.entity
        except Exception:
            pass
        signals.append({
            "type": "EvaClarification",
            "id": str(c.id),
            "question": c.question,
            "answer": c.answer,
            "user": c.answered_by.get_full_name() if c.answered_by else None,
            "entity": entity.entity_name if entity else None,
            "entity_id": str(entity.id) if entity else None,
            "timestamp": str(c.created_at),
        })

    # 2. EvaFindingSuppression — explicit accountant overrides
    for s in EvaFindingSuppression.objects.filter(
        created_at__gte=since,
    ).select_related("suppressed_by", "entity").order_by("-created_at")[:50]:
        signals.append({
            "type": "EvaFindingSuppression",
            "id": str(s.id),
            "check_name": s.check_name,
            "reason": s.reason or "",
            "user": s.suppressed_by.get_full_name() if s.suppressed_by else None,
            "entity": s.entity.entity_name if s.entity else None,
            "entity_id": str(s.entity.id) if s.entity else None,
            "timestamp": str(s.created_at),
        })

    # 3. EvaMessage — user messages that correct Eva
    for m in EvaMessage.objects.filter(
        created_at__gte=since,
        role="user",
        is_proactive=False,
    ).select_related(
        "conversation__financial_year__entity",
        "conversation__user",
    ).order_by("-created_at")[:50]:
        content = m.content or ""
        # Only include messages that appear to be corrections or clarifications
        correction_keywords = [
            "actually", "no,", "that's wrong", "incorrect", "should be",
            "we always", "for this client", "don't flag", "ignore",
            "reclassify", "change to", "use", "prefer",
        ]
        if not any(kw in content.lower() for kw in correction_keywords):
            continue
        entity = None
        user = None
        try:
            entity = m.conversation.financial_year.entity
            user = m.conversation.user
        except Exception:
            pass
        signals.append({
            "type": "EvaMessage",
            "id": str(m.id),
            "content": content[:500],
            "user": user.get_full_name() if user else None,
            "entity": entity.entity_name if entity else None,
            "entity_id": str(entity.id) if entity else None,
            "timestamp": str(m.created_at),
        })

    # 4. ActivityLog — account reclassifications
    for a in ActivityLog.objects.filter(
        created_at__gte=since,
        action__in=["reclassify", "account_mapping_update", "journal_posted"],
    ).select_related("user", "entity").order_by("-created_at")[:50]:
        signals.append({
            "type": "ActivityLog",
            "id": str(a.id),
            "action": a.action,
            "description": a.description or "",
            "user": a.user.get_full_name() if a.user else None,
            "entity": a.entity.entity_name if hasattr(a, "entity") and a.entity else None,
            "entity_id": str(a.entity.id) if hasattr(a, "entity") and a.entity else None,
            "timestamp": str(a.created_at),
        })

    return signals[:MAX_SIGNALS_PER_RUN]


# ---------------------------------------------------------------------------
# Lesson Synthesis
# ---------------------------------------------------------------------------
REFLECTION_SYSTEM_PROMPT = """You are a meta-learning AI for an Australian accounting firm.
Your job is to analyse accountant behaviour signals and extract generalised rules that
an AI assistant (Eva) should learn and apply in the future.

For each batch of signals, extract 1-5 distinct, actionable lessons.

Each lesson must be:
- A clear, specific rule (not a vague observation)
- Written as a directive Eva can follow (e.g. "When classifying X, always use Y")
- Categorised as one of: classification, compliance, workflow, style, entity_specific, general
- Scoped to a specific entity (entity_id) if it only applies to one client, or null for firm-wide rules
- Scoped to a specific user (user_name) if it is a personal preference, or null for firm-wide

Return ONLY a JSON array. Example:
[
  {
    "lesson_text": "For Smith Family Trust, motor vehicle expenses should be classified under 'Vehicle Expenses - Trust' (account 6-2100), not the generic 'Motor Vehicle Expenses'.",
    "category": "classification",
    "entity_name": "Smith Family Trust",
    "user_name": null,
    "confidence": 0.9
  },
  {
    "lesson_text": "When Eva flags a Div7A issue for loans under $10,000, the firm prefers to suppress the finding with a note rather than raise it formally.",
    "category": "compliance",
    "entity_name": null,
    "user_name": null,
    "confidence": 0.75
  }
]

If no clear lessons can be extracted, return an empty array: []
"""


def _synthesise_lessons(signals: list) -> list:
    """
    Send signals to Claude Sonnet for lesson synthesis.
    Returns a list of lesson dicts.
    """
    if not signals:
        return []

    from core.ai_service import _call_llm

    signals_text = json.dumps(signals, indent=2, default=str)
    user_prompt = f"Analyse these accountant interaction signals and extract generalised lessons:\n\n{signals_text}"

    try:
        response = _call_llm(
            system_prompt=REFLECTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            tier="sonnet",
            temperature=0.2,
            max_tokens=2000,
        )
    except Exception as e:
        logger.error(f"Lesson synthesis LLM call failed: {e}")
        return []

    # Parse the response
    try:
        import re
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            lessons = json.loads(json_match.group())
            if isinstance(lessons, list):
                return lessons
    except Exception as e:
        logger.warning(f"Failed to parse lesson synthesis response: {e}")

    return []


# ---------------------------------------------------------------------------
# Lesson Storage
# ---------------------------------------------------------------------------
def _get_embedding(text: str) -> Optional[list]:
    """Get a vector embedding for the lesson text."""
    try:
        from core.eva_service import get_embedding
        return get_embedding(text)
    except Exception as e:
        logger.warning(f"Failed to get lesson embedding: {e}")
        return None


def _store_lessons(lessons: list, signals: list) -> int:
    """
    Store extracted lessons in EvaLearnedLesson.
    Returns the number of lessons stored.
    """
    from core.models import EvaLearnedLesson, Entity
    from django.contrib.auth import get_user_model
    User = get_user_model()

    stored = 0
    for lesson_data in lessons:
        confidence = float(lesson_data.get("confidence", 0.7))
        if confidence < MIN_CONFIDENCE:
            logger.debug(f"Skipping low-confidence lesson: {lesson_data.get('lesson_text', '')[:60]}")
            continue

        lesson_text = lesson_data.get("lesson_text", "").strip()
        if not lesson_text or len(lesson_text) < 20:
            continue

        # Deduplicate: skip if a very similar lesson already exists
        existing = EvaLearnedLesson.objects.filter(
            lesson_text__icontains=lesson_text[:50],
            is_active=True,
        ).first()
        if existing:
            # Boost the weight of the existing lesson instead
            existing.priority_weight = min(existing.priority_weight + 0.1, 3.0)
            existing.save(update_fields=["priority_weight", "updated_at"])
            logger.debug(f"Boosted existing lesson weight: {lesson_text[:60]}")
            continue

        # Resolve entity
        entity = None
        entity_name = lesson_data.get("entity_name")
        if entity_name:
            entity = Entity.objects.filter(entity_name__icontains=entity_name).first()

        # Resolve user
        source_user = None
        user_name = lesson_data.get("user_name")
        if user_name:
            name_parts = user_name.split()
            if len(name_parts) >= 2:
                source_user = User.objects.filter(
                    first_name__icontains=name_parts[0],
                    last_name__icontains=name_parts[-1],
                ).first()

        # Get embedding
        embedding = _get_embedding(lesson_text)

        # Calculate priority weight based on confidence
        priority_weight = 1.0 + confidence  # range: 1.5 to 2.0

        try:
            EvaLearnedLesson.objects.create(
                lesson_text=lesson_text,
                embedding_vector=embedding,
                category=lesson_data.get("category", "general"),
                priority_weight=priority_weight,
                source_user=source_user,
                source_entity=entity,
                source_signal_type="nightly_reflection",
                source_signal_id=f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                is_active=True,
            )
            stored += 1
            logger.info(f"Stored lesson: {lesson_text[:80]}")
        except Exception as e:
            logger.error(f"Failed to store lesson: {e}")

    return stored


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def run_nightly_reflection(hours_back: int = 24) -> dict:
    """
    Main entry point for the nightly reflection task.

    Args:
        hours_back: How many hours of signals to process (default 24).

    Returns:
        {
            "signals_processed": int,
            "lessons_extracted": int,
            "lessons_stored": int,
            "errors": list,
        }
    """
    logger.info("Eva Nightly Reflection Engine starting...")
    errors = []

    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    # Step 1: Extract signals
    try:
        signals = _extract_signals(since)
        logger.info(f"Extracted {len(signals)} signals since {since}")
    except Exception as e:
        logger.error(f"Signal extraction failed: {e}")
        errors.append(f"Signal extraction: {e}")
        signals = []

    if not signals:
        logger.info("No signals to process. Reflection complete.")
        return {
            "signals_processed": 0,
            "lessons_extracted": 0,
            "lessons_stored": 0,
            "errors": errors,
        }

    # Step 2: Synthesise lessons (process in batches of 30 signals)
    all_lessons = []
    batch_size = 30
    for i in range(0, len(signals), batch_size):
        batch = signals[i:i + batch_size]
        try:
            batch_lessons = _synthesise_lessons(batch)
            all_lessons.extend(batch_lessons)
            logger.info(f"Batch {i // batch_size + 1}: extracted {len(batch_lessons)} lessons")
        except Exception as e:
            logger.error(f"Lesson synthesis failed for batch {i}: {e}")
            errors.append(f"Synthesis batch {i}: {e}")

    # Step 3: Store lessons
    stored = 0
    try:
        stored = _store_lessons(all_lessons, signals)
    except Exception as e:
        logger.error(f"Lesson storage failed: {e}")
        errors.append(f"Storage: {e}")

    result = {
        "signals_processed": len(signals),
        "lessons_extracted": len(all_lessons),
        "lessons_stored": stored,
        "errors": errors,
    }

    logger.info(
        f"Eva Nightly Reflection complete: {len(signals)} signals → "
        f"{len(all_lessons)} lessons extracted → {stored} stored."
    )

    return result
