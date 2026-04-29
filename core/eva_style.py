"""
Eva Per-Accountant Style Learning Engine
==========================================
Learns how each accountant writes by analysing the diffs between
AI-generated commentaries and the accountant's final edited versions.

Two components:

1. EditCapture (Django signal handler)
   - Fires whenever a BASPeriodCommentary or YearEndCommentary is saved.
   - Compares the current text to the AI-generated original.
   - If there is a meaningful diff, creates an EvaCommentaryEdit record.
   - Connect this signal in core/apps.py ready() method.

2. StyleUpdateEngine (weekly Celery task)
   - Aggregates all unprocessed EvaCommentaryEdit records per user.
   - Sends the diffs to Claude Sonnet to extract style rules.
   - Upserts the UserStyleProfile for each user.

The resulting UserStyleProfile.prompt_fragment is injected into the
system prompt when generating future commentaries for that user, ensuring
Eva's output matches the accountant's personal writing style.

Usage:
    # In core/apps.py ready():
    from core.eva_style import connect_edit_capture_signals
    connect_edit_capture_signals()

    # In Celery task:
    from core.eva_style import run_weekly_style_update
    run_weekly_style_update()
"""

import difflib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum character change to consider an edit meaningful
MIN_CHAR_DELTA = 20
# Minimum edits before building a style profile
MIN_EDITS_FOR_PROFILE = 3
# Maximum edits to analyse per user per run
MAX_EDITS_PER_USER = 50


# ---------------------------------------------------------------------------
# Edit Capture — Django Signal Handler
# ---------------------------------------------------------------------------
def _capture_bas_commentary_edit(sender, instance, created, **kwargs):
    """
    Signal handler for BASPeriodCommentary post_save.
    Captures meaningful edits to AI-generated BAS commentaries.
    """
    if created:
        return  # Only capture edits, not initial creation

    # Check if this commentary was AI-generated
    if not getattr(instance, "ai_generated", False):
        return

    # Get the current user from the request (stored on the instance by the view)
    user = getattr(instance, "_current_user", None)
    if not user or not user.is_authenticated:
        return

    # Compare sections that Eva generates
    sections_to_track = [
        ("section_snapshot", "section_snapshot"),
        ("section_revenue", "section_revenue"),
        ("section_expenses", "section_expenses"),
        ("section_gst", "section_gst"),
        ("section_outlook", "section_outlook"),
    ]

    for field_name, section_name in sections_to_track:
        original = getattr(instance, f"_original_{field_name}", None)
        current = getattr(instance, field_name, None)

        if not original or not current:
            continue

        char_delta = len(current) - len(original)
        if abs(char_delta) < MIN_CHAR_DELTA:
            continue

        _save_commentary_edit(
            user=user,
            document_type="bas_commentary",
            source_document_id=str(instance.id),
            entity=getattr(instance, "entity", None) or getattr(instance.bas_period, "entity", None),
            section_name=section_name,
            original_text=original,
            edited_text=current,
            char_delta=char_delta,
        )


def _capture_yearend_commentary_edit(sender, instance, created, **kwargs):
    """
    Signal handler for YearEndCommentary post_save.
    Captures meaningful edits to AI-generated year-end commentaries.
    """
    if created:
        return

    if not getattr(instance, "ai_generated", False):
        return

    user = getattr(instance, "_current_user", None)
    if not user or not user.is_authenticated:
        return

    sections_to_track = [
        ("section_overview", "section_overview"),
        ("section_performance", "section_performance"),
        ("section_position", "section_position"),
        ("section_outlook", "section_outlook"),
    ]

    for field_name, section_name in sections_to_track:
        original = getattr(instance, f"_original_{field_name}", None)
        current = getattr(instance, field_name, None)

        if not original or not current:
            continue

        char_delta = len(current) - len(original)
        if abs(char_delta) < MIN_CHAR_DELTA:
            continue

        _save_commentary_edit(
            user=user,
            document_type="yearend_commentary",
            source_document_id=str(instance.id),
            entity=getattr(instance, "entity", None),
            section_name=section_name,
            original_text=original,
            edited_text=current,
            char_delta=char_delta,
        )


def _save_commentary_edit(user, document_type, source_document_id, entity,
                           section_name, original_text, edited_text, char_delta):
    """Create an EvaCommentaryEdit record."""
    try:
        from core.models import EvaCommentaryEdit
        EvaCommentaryEdit.objects.create(
            user=user,
            document_type=document_type,
            source_document_id=source_document_id,
            entity=entity,
            section_name=section_name,
            original_text=original_text,
            edited_text=edited_text,
            char_delta=char_delta,
            processed_for_style=False,
        )
        logger.debug(f"Captured commentary edit for {user} on {document_type} section {section_name}")
    except Exception as e:
        logger.warning(f"Failed to save commentary edit: {e}")


def connect_edit_capture_signals():
    """
    Connect the edit capture signal handlers.
    Call this from core/apps.py ready() method.
    """
    try:
        from django.db.models.signals import post_save
        from core.models import BASPeriodCommentary, YearEndCommentary

        post_save.connect(
            _capture_bas_commentary_edit,
            sender=BASPeriodCommentary,
            dispatch_uid="eva_style_bas_commentary_capture",
        )
        post_save.connect(
            _capture_yearend_commentary_edit,
            sender=YearEndCommentary,
            dispatch_uid="eva_style_yearend_commentary_capture",
        )
        logger.info("Eva style learning signals connected.")
    except ImportError as e:
        logger.warning(f"Could not connect style learning signals (model not found): {e}")
    except Exception as e:
        logger.warning(f"Could not connect style learning signals: {e}")


# ---------------------------------------------------------------------------
# Diff Analysis
# ---------------------------------------------------------------------------
def _compute_diff_summary(original: str, edited: str) -> str:
    """
    Compute a human-readable diff summary between original and edited text.
    Returns a plain-English description of what changed.
    """
    # Use difflib to get the diff
    diff = list(difflib.unified_diff(
        original.splitlines(),
        edited.splitlines(),
        lineterm="",
        n=0,
    ))

    if not diff:
        return "No significant changes detected."

    added_lines = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
    removed_lines = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]

    parts = []
    if removed_lines:
        parts.append(f"Removed {len(removed_lines)} line(s)")
    if added_lines:
        parts.append(f"Added {len(added_lines)} line(s)")

    char_delta = len(edited) - len(original)
    if char_delta > 0:
        parts.append(f"expanded by {char_delta} characters")
    elif char_delta < 0:
        parts.append(f"shortened by {abs(char_delta)} characters")

    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Style Extraction
# ---------------------------------------------------------------------------
STYLE_EXTRACTION_PROMPT = """You are analysing how an accountant edits AI-generated financial commentaries.
Your job is to extract their personal writing style preferences.

Look for patterns in:
- Tone (formal/informal, first/third person)
- Structure (bullet points vs paragraphs, length preference)
- Terminology (specific accounting terms they prefer)
- Content emphasis (what they add or remove)
- Formatting (how they present numbers, percentages)

Return a JSON object with:
{
  "style_descriptor": "A 2-3 sentence description of this accountant's writing style",
  "prompt_fragment": "Concise instructions for an AI to match this style (max 150 words). Write as directives.",
  "confidence": 0.0-1.0
}

Example prompt_fragment:
"Write in a formal, professional tone. Use third person ('the entity', 'the company'). 
Present variances as percentages first, then dollar amounts in brackets. 
Keep sections concise — 2-3 sentences maximum. 
Use 'revenue' not 'income', 'expenditure' not 'expenses'."
"""


def _extract_style_from_edits(edits: list) -> Optional[dict]:
    """
    Send a batch of edits to Claude Sonnet to extract style rules.
    Returns a dict with style_descriptor, prompt_fragment, confidence.
    """
    if not edits:
        return None

    from core.ai_service import _call_llm

    # Build the edit examples
    examples = []
    for edit in edits[:20]:  # cap at 20 examples
        diff_text = _compute_diff_summary(edit["original_text"], edit["edited_text"])
        examples.append({
            "section": edit.get("section_name", "unknown"),
            "original": edit["original_text"][:300],
            "edited": edit["edited_text"][:300],
            "diff_summary": diff_text,
        })

    user_prompt = (
        f"Analyse these {len(examples)} commentary edits made by the same accountant "
        f"and extract their writing style:\n\n"
        + json.dumps(examples, indent=2)
    )

    try:
        response = _call_llm(
            system_prompt=STYLE_EXTRACTION_PROMPT,
            user_prompt=user_prompt,
            tier="sonnet",
            temperature=0.2,
            max_tokens=600,
        )
    except Exception as e:
        logger.error(f"Style extraction LLM call failed: {e}")
        return None

    try:
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            if "prompt_fragment" in result:
                return result
    except Exception as e:
        logger.warning(f"Failed to parse style extraction response: {e}")

    return None


# ---------------------------------------------------------------------------
# Style Profile Update
# ---------------------------------------------------------------------------
def _update_style_profile(user, style_data: dict, edit_counts: dict) -> bool:
    """
    Upsert the UserStyleProfile for a user.
    Returns True if successful.
    """
    try:
        from core.models import UserStyleProfile
        profile, created = UserStyleProfile.objects.get_or_create(user=user)

        profile.style_descriptor = style_data.get("style_descriptor", "")
        profile.prompt_fragment = style_data.get("prompt_fragment", "")
        profile.confidence_score = float(style_data.get("confidence", 0.5))
        profile.bas_commentary_edits_analysed = edit_counts.get("bas_commentary", 0)
        profile.yearend_commentary_edits_analysed = edit_counts.get("yearend_commentary", 0)
        profile.save()

        action = "Created" if created else "Updated"
        logger.info(
            f"{action} style profile for {user}: "
            f"confidence={profile.confidence_score:.2f}, "
            f"edits={sum(edit_counts.values())}"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to update style profile for {user}: {e}")
        return False


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def run_weekly_style_update() -> dict:
    """
    Main entry point for the weekly style update task.

    Processes all unprocessed EvaCommentaryEdit records and updates
    UserStyleProfile for each user who has enough edits.

    Returns:
        {
            "users_processed": int,
            "profiles_updated": int,
            "edits_consumed": int,
            "errors": list,
        }
    """
    logger.info("Eva Weekly Style Update starting...")

    from core.models import EvaCommentaryEdit
    from django.contrib.auth import get_user_model
    User = get_user_model()

    errors = []
    users_processed = 0
    profiles_updated = 0
    total_edits_consumed = 0

    # Get all users with unprocessed edits
    user_ids = (
        EvaCommentaryEdit.objects
        .filter(processed_for_style=False)
        .values_list("user_id", flat=True)
        .distinct()
    )

    for user_id in user_ids:
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            continue

        users_processed += 1

        # Get unprocessed edits for this user
        edits_qs = EvaCommentaryEdit.objects.filter(
            user=user,
            processed_for_style=False,
        ).order_by("-created_at")[:MAX_EDITS_PER_USER]

        edits = list(edits_qs.values(
            "id", "document_type", "section_name",
            "original_text", "edited_text", "char_delta",
        ))

        if len(edits) < MIN_EDITS_FOR_PROFILE:
            logger.debug(
                f"User {user} has only {len(edits)} edits (min {MIN_EDITS_FOR_PROFILE}). Skipping."
            )
            continue

        # Count edits by type
        edit_counts = {
            "bas_commentary": sum(1 for e in edits if e["document_type"] == "bas_commentary"),
            "yearend_commentary": sum(1 for e in edits if e["document_type"] == "yearend_commentary"),
        }

        # Extract style
        try:
            style_data = _extract_style_from_edits(edits)
        except Exception as e:
            logger.error(f"Style extraction failed for {user}: {e}")
            errors.append(f"Style extraction for {user}: {e}")
            continue

        if not style_data:
            logger.warning(f"No style data extracted for {user}")
            continue

        # Update profile
        success = _update_style_profile(user, style_data, edit_counts)
        if success:
            profiles_updated += 1
            total_edits_consumed += len(edits)

            # Mark edits as processed
            edit_ids = [e["id"] for e in edits]
            EvaCommentaryEdit.objects.filter(id__in=edit_ids).update(processed_for_style=True)

    result = {
        "users_processed": users_processed,
        "profiles_updated": profiles_updated,
        "edits_consumed": total_edits_consumed,
        "errors": errors,
    }

    logger.info(
        f"Eva Weekly Style Update complete: {users_processed} users, "
        f"{profiles_updated} profiles updated, {total_edits_consumed} edits consumed."
    )

    return result


# ---------------------------------------------------------------------------
# Style Profile Injection Helper
# ---------------------------------------------------------------------------
def get_style_prompt_fragment(user) -> str:
    """
    Get the style prompt fragment for a user, ready to inject into a system prompt.
    Returns an empty string if no usable profile exists.
    """
    if not user or not user.is_authenticated:
        return ""

    try:
        profile = user.eva_style_profile
        if profile.is_usable:
            return f"\n\n## Writing Style Instructions\n{profile.prompt_fragment}"
    except Exception:
        pass

    return ""
