"""
Learning Feedback Loop for Bank Statement Transaction Coding.

When an accountant confirms or corrects a transaction classification,
the learning system stores the pattern so future identical/similar
transactions are automatically coded with high confidence.

Patterns are stored per-entity (client-specific) for maximum accuracy.
"""
import logging
import re

from django.utils import timezone

from .models import TransactionPattern

logger = logging.getLogger(__name__)


def normalise_description(desc):
    """
    Normalise a transaction description for pattern matching.
    Removes dates, long reference numbers, and extra whitespace.
    """
    if not desc:
        return ""
    desc = re.sub(r"\d{2}/\d{2}/\d{2,4}", "", desc)
    desc = re.sub(r"\b\d{6,}\b", "", desc)
    desc = re.sub(r"\s+", " ", desc).strip().upper()
    return desc


def save_pattern(transaction, entity=None):
    """
    Save or update a transaction pattern in the learning database.
    Called when an accountant confirms a transaction classification.

    Args:
        transaction: PendingTransaction instance (must be confirmed)
        entity: Entity instance (optional, for client-specific patterns)
    """
    if not transaction.is_confirmed:
        return

    desc_normalised = normalise_description(transaction.description)
    if not desc_normalised:
        return

    code = transaction.confirmed_code or transaction.ai_suggested_code
    name = transaction.confirmed_name or transaction.ai_suggested_name
    tax_type = transaction.confirmed_tax_type or transaction.ai_suggested_tax_type

    if not code:
        return

    # Update or create entity-specific pattern
    pattern, created = TransactionPattern.objects.update_or_create(
        entity=entity,
        description_pattern=desc_normalised,
        defaults={
            "account_code": code,
            "account_name": name,
            "tax_type": tax_type,
        },
    )

    if not created:
        pattern.usage_count += 1
        pattern.save(update_fields=["usage_count", "last_used"])

    logger.info(
        f"{'Created' if created else 'Updated'} pattern: "
        f"'{desc_normalised[:50]}' -> {code} ({pattern.usage_count}x)"
    )

    return pattern


def save_patterns_from_job(job):
    """
    Save patterns for all confirmed transactions in a review job.
    Called when a review job is submitted/completed.
    """
    entity = job.entity
    confirmed_txns = job.transactions.filter(is_confirmed=True)
    patterns_saved = 0

    for txn in confirmed_txns:
        pattern = save_pattern(txn, entity=entity)
        if pattern:
            patterns_saved += 1

    logger.info(
        f"Saved {patterns_saved} patterns from job {job.id} "
        f"({job.client_name})"
    )

    return patterns_saved


def get_pattern_stats(entity=None):
    """
    Get statistics about the learning database.
    """
    qs = TransactionPattern.objects.all()
    if entity:
        qs = qs.filter(entity=entity)

    total = qs.count()
    total_usage = sum(p.usage_count for p in qs)
    top_patterns = qs.order_by("-usage_count")[:10]

    return {
        "total_patterns": total,
        "total_usage": total_usage,
        "top_patterns": [
            {
                "description": p.description_pattern[:60],
                "account_code": p.account_code,
                "account_name": p.account_name,
                "tax_type": p.tax_type,
                "usage_count": p.usage_count,
            }
            for p in top_patterns
        ],
    }


def find_similar_unconfirmed(transaction, job):
    """
    Find unconfirmed transactions in the same job whose normalised
    description matches the given transaction's normalised description.

    Uses the same normalisation logic that strips dates and long reference
    numbers, so "PAYMENT TO GOCARDLESS JHAVTECHST-F2BV8MK" on two
    different dates will match.

    Returns a QuerySet of PendingTransaction objects (excluding the
    source transaction itself and any already-confirmed rows).
    """
    from .models import PendingTransaction

    source_norm = normalise_description(transaction.description)
    if not source_norm:
        return PendingTransaction.objects.none()

    # Extract significant tokens (words with 3+ chars) for matching
    tokens = [t for t in source_norm.split() if len(t) >= 3]
    if not tokens:
        return PendingTransaction.objects.none()

    # Get all unconfirmed transactions in the same job (excl. source)
    candidates = (
        PendingTransaction.objects
        .filter(job=job, is_confirmed=False)
        .exclude(pk=transaction.pk)
    )

    matched_ids = []
    for txn in candidates:
        cand_norm = normalise_description(txn.description)
        if not cand_norm:
            continue
        # Match if the normalised descriptions are identical, OR if
        # the candidate contains all significant tokens from the source
        if cand_norm == source_norm:
            matched_ids.append(txn.pk)
        elif all(token in cand_norm for token in tokens):
            matched_ids.append(txn.pk)

    return PendingTransaction.objects.filter(pk__in=matched_ids)


def push_pattern_to_airtable(pattern):
    """
    Optionally push a confirmed pattern to the Airtable Learning Database
    for the n8n workflow to also benefit from.
    """
    import requests
    from django.conf import settings

    api_key = getattr(settings, "AIRTABLE_API_KEY", None)
    if not api_key:
        return

    AIRTABLE_BASE_ID = "appCIoC2AVqJ3axMG"
    AIRTABLE_LEARNING_TABLE = "tblDO2k2wB3OSFBxa"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        requests.post(
            f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_LEARNING_TABLE}",
            headers=headers,
            json={
                "fields": {
                    "Vendor Pattern": pattern.description_pattern,
                    "Account Code": pattern.account_code,
                    "Account Name": pattern.account_name,
                    "Confidence Score": "5",
                    "Notes": f"Tax Type: {pattern.tax_type}",
                },
            },
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Failed to push pattern to Airtable: {e}")
