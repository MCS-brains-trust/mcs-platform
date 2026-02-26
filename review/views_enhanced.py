"""
Enhanced Transaction Review Workflow — View Endpoints.

Provides API endpoints for:
1. Natural Language Search (client-side primary, server fallback for compound)
2. Transaction Splitting (inline split editor)
3. Entity-Specific Classification Rule Memory (CRUD + matching)
4. GST Treatment Controls (bulk set, toggle, undo)
5. GST Apportionment & Partial Credit (creditable %, overrides, RITC, LCT)
"""
import json
import logging
import re
import uuid as uuid_lib
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET

from config.authorization import get_review_job_for_user
from .models import (
    ClassificationRule,
    EntityGSTSetting,
    PendingTransaction,
    ReviewActivity,
    ReviewJob,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants for GST Apportionment
# ---------------------------------------------------------------------------

# RITC patterns — merchant fees, bank fees → 75% creditable (AC-APT-03)
RITC_PATTERNS = [
    "merchant fee", "eftpos fee", "bank fee", "bank charge",
    "account keeping fee", "monthly fee", "service fee",
    "card fee", "interchange fee", "terminal fee",
]

# Motor vehicle account code prefixes (AC-APT-04)
VEHICLE_ACCOUNT_CODES = ["7600", "7610", "7620", "7630", "7640", "7650"]

# Entertainment keywords (AC-APT-05)
ENTERTAINMENT_KEYWORDS = [
    "entertainment", "meal", "dining", "restaurant", "catering",
    "lunch", "dinner", "breakfast", "food & beverage",
]

# Default LCT threshold (AC-APT-06/07) — can be overridden by config
DEFAULT_LCT_THRESHOLD = Decimal("76950")  # 2024-25 threshold
DEFAULT_LCT_FUEL_EFFICIENT_THRESHOLD = Decimal("89332")


def _get_lct_threshold(entity=None):
    """Get the LCT threshold from configurable reference data or default."""
    # TODO: Pull from a ConfigurableReferenceData model when implemented
    return DEFAULT_LCT_THRESHOLD


# ---------------------------------------------------------------------------
# 1. Natural Language Search — Server-side fallback for compound queries
# ---------------------------------------------------------------------------

@login_required
@require_POST
def search_transactions(request, pk):
    """
    Server-side search endpoint for compound/ambiguous queries that
    the client-side parser cannot handle. Uses simple NLP parsing.

    POST /api/review/<pk>/search/
    Body: {"query": "bank fees over $50 in January"}

    Returns: {"status": "ok", "filters": {...}, "transaction_ids": [...]}
    """
    job = get_review_job_for_user(request, pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    query = data.get("query", "").strip()
    if not query:
        return JsonResponse({"status": "ok", "filters": {}, "transaction_ids": []})

    txns = job.transactions.filter(split_parent__isnull=True)
    filters_applied = {}

    # Parse amount thresholds: "over $X", "under $X", "above $X", "below $X"
    amount_over = re.search(r'(?:over|above|more than|greater than)\s*\$?([\d,.]+)', query, re.I)
    amount_under = re.search(r'(?:under|below|less than)\s*\$?([\d,.]+)', query, re.I)
    if amount_over:
        threshold = Decimal(amount_over.group(1).replace(",", ""))
        txns = txns.filter(amount__gte=threshold) | txns.filter(amount__lte=-threshold)
        filters_applied["amount_min"] = str(threshold)
    if amount_under:
        threshold = Decimal(amount_under.group(1).replace(",", ""))
        txns = txns.filter(amount__lte=threshold, amount__gte=-threshold)
        filters_applied["amount_max"] = str(threshold)

    # Parse approval status: "approved", "pending", "unconfirmed"
    if re.search(r'\b(approved|confirmed)\b', query, re.I):
        txns = txns.filter(is_confirmed=True)
        filters_applied["status"] = "confirmed"
    elif re.search(r'\b(pending|unconfirmed|unreviewed)\b', query, re.I):
        txns = txns.filter(is_confirmed=False)
        filters_applied["status"] = "pending"

    # Parse GST treatment: "taxable", "gst-free", "out of scope"
    gst_map = {
        r'\btaxable\b': 'taxable',
        r'\bgst[\s-]*free\b': 'gst_free',
        r'\binput[\s-]*taxed\b': 'input_taxed',
        r'\bout[\s-]*of[\s-]*scope\b': 'out_of_scope',
    }
    for pattern, treatment in gst_map.items():
        if re.search(pattern, query, re.I):
            txns = txns.filter(gst_treatment=treatment)
            filters_applied["gst_treatment"] = treatment
            break

    # Parse month names for date filtering
    months = {
        'january': '01', 'february': '02', 'march': '03', 'april': '04',
        'may': '05', 'june': '06', 'july': '07', 'august': '08',
        'september': '09', 'october': '10', 'november': '11', 'december': '12',
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
        'jun': '06', 'jul': '07', 'aug': '08', 'sep': '09',
        'oct': '10', 'nov': '11', 'dec': '12',
    }
    for name, num in months.items():
        if re.search(rf'\b{name}\b', query, re.I):
            txns = txns.filter(date__contains=f"/{num}/")
            filters_applied["month"] = name.capitalize()
            break

    # Description keyword search — remove parsed tokens and search remainder
    remainder = query
    for pattern in [
        r'(?:over|above|more than|greater than)\s*\$?[\d,.]+',
        r'(?:under|below|less than)\s*\$?[\d,.]+',
        r'\b(approved|confirmed|pending|unconfirmed|unreviewed)\b',
        r'\btaxable\b', r'\bgst[\s-]*free\b', r'\binput[\s-]*taxed\b',
        r'\bout[\s-]*of[\s-]*scope\b',
    ] + [rf'\b{m}\b' for m in months]:
        remainder = re.sub(pattern, '', remainder, flags=re.I)
    remainder = remainder.strip().strip(',').strip()

    if remainder:
        txns = txns.filter(description__icontains=remainder)
        filters_applied["description"] = remainder

    txn_ids = list(txns.values_list("pk", flat=True))
    return JsonResponse({
        "status": "ok",
        "filters": filters_applied,
        "transaction_ids": [str(tid) for tid in txn_ids],
        "count": len(txn_ids),
    })


# ---------------------------------------------------------------------------
# 2. Transaction Splitting
# ---------------------------------------------------------------------------

@login_required
@require_POST
def split_transaction(request, pk):
    """
    Split a transaction into multiple lines.

    POST /api/review/transaction/<pk>/split/
    Body: {
        "lines": [
            {"account_code": "4100", "account_name": "Sales", "amount": "500.00",
             "gst_treatment": "taxable", "creditable_percentage": "100"},
            {"account_code": "4200", "account_name": "Other Income", "amount": "200.00",
             "gst_treatment": "gst_free", "creditable_percentage": "100"}
        ]
    }
    """
    parent = get_object_or_404(PendingTransaction, pk=pk)

    # Cannot split an already-split child line
    if parent.split_parent is not None:
        return JsonResponse(
            {"status": "error", "message": "Cannot split a split line. Remove the existing split first."},
            status=400,
        )

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    lines = data.get("lines", [])
    if len(lines) < 2:
        return JsonResponse(
            {"status": "error", "message": "A split requires at least 2 lines."},
            status=400,
        )

    # Validate total equals original amount
    total = Decimal("0")
    for line in lines:
        try:
            amt = Decimal(str(line.get("amount", "0")))
        except (InvalidOperation, ValueError):
            return JsonResponse(
                {"status": "error", "message": f"Invalid amount: {line.get('amount')}"},
                status=400,
            )
        total += amt

    if total != parent.amount:
        return JsonResponse(
            {"status": "error", "message": f"Split lines total ${total} does not equal original amount ${parent.amount}."},
            status=400,
        )

    # Remove any existing split children
    parent.split_children.all().delete()

    # Create split lines
    created_lines = []
    for i, line in enumerate(lines, start=1):
        amt = Decimal(str(line.get("amount", "0")))
        gst_treatment = line.get("gst_treatment", parent.gst_treatment or "")
        cred_pct = Decimal(str(line.get("creditable_percentage", "100")))

        # Calculate GST for this split line
        abs_amt = abs(amt)
        if gst_treatment == "taxable" and parent.job.is_gst_registered:
            gst_amt = (abs_amt / Decimal("11")).quantize(Decimal("0.01"))
            net_amt = (abs_amt - gst_amt).quantize(Decimal("0.01"))
        else:
            gst_amt = Decimal("0.00")
            net_amt = abs_amt

        child = PendingTransaction.objects.create(
            job=parent.job,
            date=parent.date,
            description=f"{parent.description} [Split {i}/{len(lines)}]",
            amount=amt,
            gst_amount=gst_amt,
            net_amount=net_amt,
            ai_suggested_code=line.get("account_code", ""),
            ai_suggested_name=line.get("account_name", ""),
            ai_suggested_tax_type=parent.ai_suggested_tax_type,
            ai_confidence=parent.ai_confidence,
            confirmed_code=line.get("account_code", ""),
            confirmed_name=line.get("account_name", ""),
            gst_treatment=gst_treatment,
            creditable_percentage=cred_pct,
            is_confirmed=False,
            split_parent=parent,
            split_line_number=i,
        )
        created_lines.append({
            "id": str(child.pk),
            "line_number": i,
            "account_code": child.confirmed_code,
            "account_name": child.confirmed_name,
            "amount": str(child.amount),
            "gst_treatment": child.gst_treatment,
            "gst_amount": str(child.gst_amount),
            "net_amount": str(child.net_amount),
            "creditable_percentage": str(child.creditable_percentage),
        })

    # Mark parent as split
    parent.is_split = True
    parent.save(update_fields=["is_split"])

    # Log activity
    ReviewActivity.objects.create(
        activity_type="review_started",
        title="Transaction split",
        description=(
            f"Transaction '{parent.description[:60]}' (${parent.amount}) "
            f"split into {len(lines)} lines by {request.user.get_full_name() or request.user.username}"
        ),
    )

    return JsonResponse({
        "status": "ok",
        "parent_id": str(parent.pk),
        "lines": created_lines,
    })


@login_required
@require_POST
def unsplit_transaction(request, pk):
    """
    Remove a split and restore the original transaction.

    POST /api/review/transaction/<pk>/unsplit/
    """
    parent = get_object_or_404(PendingTransaction, pk=pk)

    if not parent.is_split:
        return JsonResponse(
            {"status": "error", "message": "This transaction is not split."},
            status=400,
        )

    # Delete all split children
    deleted_count = parent.split_children.all().delete()[0]

    # Restore parent
    parent.is_split = False
    parent.is_confirmed = False
    parent.save(update_fields=["is_split", "is_confirmed"])

    # Update job counts
    job = parent.job
    job.confirmed_count = job.transactions.filter(is_confirmed=True).count()
    job.flagged_count = job.transactions.filter(split_parent__isnull=True).count() + \
                        job.transactions.filter(split_parent__isnull=False).count()
    job.save()

    ReviewActivity.objects.create(
        activity_type="review_started",
        title="Split removed",
        description=(
            f"Split removed from '{parent.description[:60]}' — "
            f"{deleted_count} lines deleted by {request.user.get_full_name() or request.user.username}"
        ),
    )

    return JsonResponse({
        "status": "ok",
        "parent_id": str(parent.pk),
        "deleted_lines": deleted_count,
    })


# ---------------------------------------------------------------------------
# 3. Classification Rule Memory
# ---------------------------------------------------------------------------

@login_required
@require_POST
def create_classification_rule(request):
    """
    Create a new entity-scoped classification rule.

    POST /api/review/rules/create/
    Body: {
        "entity_id": <uuid>,
        "description_pattern": "WOOLWORTHS",
        "match_type": "contains",
        "account_code": "5100",
        "account_name": "Groceries",
        "gst_treatment": "taxable",
        "creditable_percentage": "100"
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    from core.models import Entity
    entity_id = data.get("entity_id")
    if not entity_id:
        return JsonResponse({"status": "error", "message": "entity_id is required"}, status=400)

    entity = get_object_or_404(Entity, pk=entity_id)

    pattern = data.get("description_pattern", "").strip()
    if not pattern:
        return JsonResponse({"status": "error", "message": "description_pattern is required"}, status=400)

    account_code = data.get("account_code", "").strip()
    if not account_code:
        return JsonResponse({"status": "error", "message": "account_code is required"}, status=400)

    cred_pct = Decimal(str(data.get("creditable_percentage", "100")))

    rule = ClassificationRule.objects.create(
        entity=entity,
        description_pattern=pattern,
        match_type=data.get("match_type", "contains"),
        account_code=account_code,
        account_name=data.get("account_name", ""),
        gst_treatment=data.get("gst_treatment", ""),
        creditable_percentage=cred_pct,
        is_active=True,
        created_by=request.user,
    )

    return JsonResponse({
        "status": "ok",
        "rule_id": str(rule.pk),
        "message": f"Rule created: '{pattern}' → {account_code}",
    })


@login_required
@require_POST
def update_classification_rule(request, pk):
    """
    Update an existing classification rule.

    POST /api/review/rules/<pk>/update/
    """
    rule = get_object_or_404(ClassificationRule, pk=pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    if "description_pattern" in data:
        rule.description_pattern = data["description_pattern"].strip()
    if "match_type" in data:
        rule.match_type = data["match_type"]
    if "account_code" in data:
        rule.account_code = data["account_code"].strip()
    if "account_name" in data:
        rule.account_name = data["account_name"]
    if "gst_treatment" in data:
        rule.gst_treatment = data["gst_treatment"]
    if "creditable_percentage" in data:
        rule.creditable_percentage = Decimal(str(data["creditable_percentage"]))
    if "is_active" in data:
        rule.is_active = bool(data["is_active"])

    rule.save()

    return JsonResponse({
        "status": "ok",
        "rule_id": str(rule.pk),
        "message": "Rule updated successfully.",
    })


@login_required
@require_POST
def delete_classification_rule(request, pk):
    """Delete a classification rule."""
    rule = get_object_or_404(ClassificationRule, pk=pk)
    rule.delete()
    return JsonResponse({"status": "ok", "message": "Rule deleted."})


@login_required
@require_POST
def toggle_classification_rule(request, pk):
    """Toggle a classification rule active/inactive."""
    rule = get_object_or_404(ClassificationRule, pk=pk)
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active", "updated_at"])
    return JsonResponse({
        "status": "ok",
        "is_active": rule.is_active,
        "message": f"Rule {'activated' if rule.is_active else 'deactivated'}.",
    })


@login_required
@require_GET
def list_classification_rules(request, entity_id):
    """
    List all classification rules for an entity.

    GET /api/review/rules/<entity_id>/
    """
    from core.models import Entity
    entity = get_object_or_404(Entity, pk=entity_id)
    rules = ClassificationRule.objects.filter(entity=entity)

    rules_data = []
    for r in rules:
        rules_data.append({
            "id": str(r.pk),
            "description_pattern": r.description_pattern,
            "match_type": r.match_type,
            "match_type_display": r.get_match_type_display(),
            "account_code": r.account_code,
            "account_name": r.account_name,
            "gst_treatment": r.gst_treatment,
            "creditable_percentage": str(r.creditable_percentage),
            "is_active": r.is_active,
            "created_by": r.created_by.get_full_name() if r.created_by else "",
            "created_at": r.created_at.strftime("%d/%m/%Y %H:%M") if r.created_at else "",
            "matched_count": r.matched_transactions.count(),
        })

    return JsonResponse({"status": "ok", "rules": rules_data, "count": len(rules_data)})


def apply_classification_rules(job):
    """
    Apply entity-specific classification rules to unconfirmed transactions.
    Rules run BEFORE the AI engine. Most recently created rule wins on conflict.

    Returns the number of transactions matched.
    """
    entity = job.entity
    if not entity:
        return 0

    rules = ClassificationRule.objects.filter(
        entity=entity, is_active=True
    ).order_by("-created_at")  # Most recent first

    if not rules.exists():
        return 0

    unconfirmed = job.transactions.filter(
        is_confirmed=False, split_parent__isnull=True, matched_rule__isnull=True
    )

    matched_count = 0
    for txn in unconfirmed:
        for rule in rules:
            if rule.matches(txn.description):
                txn.ai_suggested_code = rule.account_code
                txn.ai_suggested_name = rule.account_name
                txn.gst_treatment = rule.gst_treatment
                txn.creditable_percentage = rule.creditable_percentage
                txn.matched_rule = rule
                txn.from_learning = True  # Show "Rule applied" badge

                # Recalculate GST based on rule's treatment
                _recalculate_gst(txn, job.is_gst_registered)
                txn.save()
                matched_count += 1
                break  # First (most recent) matching rule wins

    return matched_count


# ---------------------------------------------------------------------------
# 4. GST Treatment Controls
# ---------------------------------------------------------------------------

@login_required
@require_POST
def set_gst_treatment(request, pk):
    """
    Set GST treatment for a single transaction.

    POST /api/review/transaction/<pk>/gst-treatment/
    Body: {"gst_treatment": "taxable", "is_manual": true}
    """
    txn = get_object_or_404(PendingTransaction, pk=pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    treatment = data.get("gst_treatment", "")
    valid_treatments = {"taxable", "gst_free", "input_taxed", "out_of_scope", "not_registered", ""}
    if treatment not in valid_treatments:
        return JsonResponse({"status": "error", "message": f"Invalid GST treatment: {treatment}"}, status=400)

    txn.gst_treatment = treatment
    txn.is_gst_manual = data.get("is_manual", True)

    # Map to legacy tax type for backward compatibility
    TREATMENT_TO_TAX_TYPE = {
        "taxable": "GST on Expenses" if txn.amount < 0 else "GST on Income",
        "gst_free": "GST Free Expenses" if txn.amount < 0 else "GST Free Income",
        "input_taxed": "Input Taxed",
        "out_of_scope": "BAS Excluded",
        "not_registered": "N-T",
    }
    if treatment:
        legacy_tax = TREATMENT_TO_TAX_TYPE.get(treatment, "")
        txn.confirmed_tax_type = legacy_tax
        if not txn.ai_suggested_tax_type:
            txn.ai_suggested_tax_type = legacy_tax

    # Reset creditable percentage for non-taxable treatments (AC-APT-01)
    if treatment in ("gst_free", "input_taxed", "out_of_scope", "not_registered"):
        txn.creditable_percentage = Decimal("0")

    _recalculate_gst(txn, txn.job.is_gst_registered)
    txn.save()

    return JsonResponse({
        "status": "ok",
        "gst_treatment": txn.gst_treatment,
        "gst_amount": str(txn.gst_amount),
        "net_amount": str(txn.net_amount),
        "creditable_percentage": str(txn.creditable_percentage),
        "is_gst_manual": txn.is_gst_manual,
    })


@login_required
@require_POST
def bulk_set_gst_treatment(request, pk):
    """
    Bulk-set GST treatment for multiple transactions.

    POST /api/review/<pk>/bulk-gst/
    Body: {
        "transaction_ids": ["uuid1", "uuid2"],
        "gst_treatment": "taxable"
    }
    """
    job = get_review_job_for_user(request, pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    txn_ids = data.get("transaction_ids", [])
    treatment = data.get("gst_treatment", "")

    valid_treatments = {"taxable", "gst_free", "input_taxed", "out_of_scope", "not_registered"}
    if treatment not in valid_treatments:
        return JsonResponse({"status": "error", "message": f"Invalid GST treatment: {treatment}"}, status=400)

    txns = job.transactions.filter(pk__in=txn_ids)
    updated_ids = []

    TREATMENT_TO_TAX_TYPE = {
        "taxable": lambda txn: "GST on Expenses" if txn.amount < 0 else "GST on Income",
        "gst_free": lambda txn: "GST Free Expenses" if txn.amount < 0 else "GST Free Income",
        "input_taxed": lambda txn: "Input Taxed",
        "out_of_scope": lambda txn: "BAS Excluded",
        "not_registered": lambda txn: "N-T",
    }

    for txn in txns:
        txn.gst_treatment = treatment
        txn.is_gst_manual = True
        txn.confirmed_tax_type = TREATMENT_TO_TAX_TYPE[treatment](txn)

        if treatment in ("gst_free", "input_taxed", "out_of_scope", "not_registered"):
            txn.creditable_percentage = Decimal("0")

        _recalculate_gst(txn, job.is_gst_registered)
        txn.save()
        updated_ids.append(str(txn.pk))

    return JsonResponse({
        "status": "ok",
        "updated_ids": updated_ids,
        "updated_count": len(updated_ids),
        "gst_treatment": treatment,
    })


@login_required
@require_POST
def undo_bulk_gst(request, pk):
    """
    Undo a bulk GST treatment change (within 5-second window).
    Restores previous GST treatment from the undo payload.

    POST /api/review/<pk>/undo-bulk-gst/
    Body: {
        "undo_data": [
            {"id": "uuid1", "gst_treatment": "taxable", "creditable_percentage": "100"},
            ...
        ]
    }
    """
    job = get_review_job_for_user(request, pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    undo_data = data.get("undo_data", [])
    restored_count = 0

    for item in undo_data:
        try:
            txn = job.transactions.get(pk=item["id"])
            txn.gst_treatment = item.get("gst_treatment", "")
            txn.creditable_percentage = Decimal(str(item.get("creditable_percentage", "100")))
            txn.is_gst_manual = item.get("is_gst_manual", False)
            _recalculate_gst(txn, job.is_gst_registered)
            txn.save()
            restored_count += 1
        except (PendingTransaction.DoesNotExist, KeyError):
            continue

    return JsonResponse({
        "status": "ok",
        "restored_count": restored_count,
    })


# ---------------------------------------------------------------------------
# 5. GST Apportionment & Partial Credit
# ---------------------------------------------------------------------------

@login_required
@require_POST
def set_creditable_percentage(request, pk):
    """
    Set the creditable percentage for a transaction.

    POST /api/review/transaction/<pk>/creditable-pct/
    Body: {"creditable_percentage": "75", "reason": "RITC - merchant fees"}
    """
    txn = get_object_or_404(PendingTransaction, pk=pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    try:
        pct = Decimal(str(data.get("creditable_percentage", "100")))
    except (InvalidOperation, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid percentage"}, status=400)

    if pct < 0 or pct > 100:
        return JsonResponse({"status": "error", "message": "Percentage must be 0-100"}, status=400)

    txn.creditable_percentage = pct
    _recalculate_gst(txn, txn.job.is_gst_registered)
    txn.save()

    # Offer to save as entity-level setting (AC-APT-08)
    save_setting = data.get("save_as_entity_setting", False)
    setting_type = data.get("setting_type", "")
    if save_setting and setting_type and txn.job.entity:
        EntityGSTSetting.objects.update_or_create(
            entity=txn.job.entity,
            financial_year=None,  # Applies to all FYs
            setting_type=setting_type,
            defaults={
                "value": str(pct),
                "created_by": request.user,
            },
        )

    return JsonResponse({
        "status": "ok",
        "creditable_percentage": str(txn.creditable_percentage),
        "gst_amount": str(txn.gst_amount),
        "net_amount": str(txn.net_amount),
        "itc_amount": str(_calculate_itc(txn)),
    })


@login_required
@require_POST
def set_gst_override(request, pk):
    """
    Set a direct GST amount override (AC-APT-10/11/12).

    POST /api/review/transaction/<pk>/gst-override/
    Body: {"gst_amount": "45.50", "reason": "Adjusted per client invoice"}
    """
    txn = get_object_or_404(PendingTransaction, pk=pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    reason = data.get("reason", "").strip()
    if not reason:
        return JsonResponse(
            {"status": "error", "message": "A reason is mandatory for GST amount overrides (AC-APT-12)."},
            status=400,
        )

    try:
        override_amount = Decimal(str(data.get("gst_amount", "0")))
    except (InvalidOperation, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid GST amount"}, status=400)

    txn.gst_amount_override = override_amount
    txn.gst_override_reason = reason
    txn.gst_amount = override_amount
    txn.net_amount = (abs(txn.amount) - override_amount).quantize(Decimal("0.01"))

    # Back-calculate implied creditable percentage (AC-APT-11)
    standard_gst = (abs(txn.amount) / Decimal("11")).quantize(Decimal("0.01"))
    if standard_gst > 0:
        implied_pct = (override_amount / standard_gst * Decimal("100")).quantize(Decimal("0.01"))
        txn.creditable_percentage = min(implied_pct, Decimal("100"))
    txn.save()

    # Log to activity
    ReviewActivity.objects.create(
        activity_type="review_started",
        title="GST amount overridden",
        description=(
            f"Transaction '{txn.description[:50]}': GST overridden to ${override_amount} "
            f"(reason: {reason[:100]}) by {request.user.get_full_name() or request.user.username}"
        ),
    )

    return JsonResponse({
        "status": "ok",
        "gst_amount": str(txn.gst_amount),
        "gst_amount_override": str(txn.gst_amount_override),
        "net_amount": str(txn.net_amount),
        "implied_creditable_percentage": str(txn.creditable_percentage),
        "has_override": True,
    })


def detect_apportionment(txn, entity=None):
    """
    AI-powered apportionment detection for a transaction.
    Returns a dict with suggested creditable_percentage and reason.

    Checks (in order):
    1. RITC patterns → 75% (AC-APT-03)
    2. Motor vehicle codes → prompt for business use % (AC-APT-04)
    3. Entertainment keywords → 50% default (AC-APT-05)
    4. Vehicle purchase above LCT → cap GST (AC-APT-06)
    5. Mid-year GST registration → Out of Scope (AC-APT-15)
    """
    result = {
        "creditable_percentage": Decimal("100"),
        "reason": "",
        "requires_confirmation": False,
        "apportionment_type": None,
    }

    desc_upper = (txn.description or "").upper()
    account_code = txn.ai_suggested_code or txn.confirmed_code or ""

    # AC-APT-15: Mid-year GST registration check
    if entity and entity.gst_registration_date and txn.date:
        try:
            from datetime import datetime
            # Parse date — handle common formats
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                try:
                    txn_date = datetime.strptime(txn.date, fmt).date()
                    break
                except ValueError:
                    continue
            else:
                txn_date = None

            if txn_date and txn_date < entity.gst_registration_date:
                result["creditable_percentage"] = Decimal("0")
                result["reason"] = "Transaction before GST registration date — auto-set to Out of Scope"
                result["apportionment_type"] = "pre_registration"
                return result
        except Exception:
            pass

    # AC-APT-03: RITC patterns → 75%
    for pattern in RITC_PATTERNS:
        if pattern.upper() in desc_upper:
            result["creditable_percentage"] = Decimal("75")
            result["reason"] = f"RITC detected ({pattern}) — 75% creditable"
            result["apportionment_type"] = "ritc"
            return result

    # AC-APT-04: Motor vehicle account codes → prompt
    for code_prefix in VEHICLE_ACCOUNT_CODES:
        if account_code.startswith(code_prefix):
            # Check if entity has a saved business use %
            if entity:
                saved = EntityGSTSetting.objects.filter(
                    entity=entity, setting_type="vehicle_business_use"
                ).first()
                if saved:
                    result["creditable_percentage"] = Decimal(saved.value)
                    result["reason"] = f"Vehicle business use: {saved.value}% (saved setting)"
                    result["apportionment_type"] = "vehicle"
                    return result

            result["creditable_percentage"] = Decimal("100")
            result["reason"] = "Motor vehicle expense — please confirm business use percentage"
            result["requires_confirmation"] = True
            result["apportionment_type"] = "vehicle"
            return result

    # AC-APT-05: Entertainment → 50% default
    for keyword in ENTERTAINMENT_KEYWORDS:
        if keyword.upper() in desc_upper:
            # Check entity FBT method
            if entity:
                fbt_setting = EntityGSTSetting.objects.filter(
                    entity=entity, setting_type="entertainment_method"
                ).first()
                if fbt_setting:
                    pct = Decimal(fbt_setting.value) if fbt_setting.value.isdigit() else Decimal("50")
                    result["creditable_percentage"] = pct
                    result["reason"] = f"Entertainment — {fbt_setting.label or 'entity FBT method'}: {pct}%"
                    result["apportionment_type"] = "entertainment"
                    return result

            result["creditable_percentage"] = Decimal("50")
            result["reason"] = "Meal entertainment — 50% creditable (50/50 method default)"
            result["apportionment_type"] = "entertainment"
            return result

    # AC-APT-06: Luxury car GST cap
    abs_amount = abs(txn.amount)
    if account_code.startswith("76") and abs_amount > _get_lct_threshold():
        lct = _get_lct_threshold()
        max_gst = (lct / Decimal("11")).quantize(Decimal("0.01"))
        actual_gst = (abs_amount / Decimal("11")).quantize(Decimal("0.01"))
        if actual_gst > max_gst:
            implied_pct = (max_gst / actual_gst * Decimal("100")).quantize(Decimal("0.01"))
            result["creditable_percentage"] = implied_pct
            result["reason"] = (
                f"Vehicle purchase above LCT threshold (${lct}). "
                f"GST capped at ${max_gst} (creditable: {implied_pct}%)"
            )
            result["apportionment_type"] = "lct_cap"
            return result

    return result


@login_required
@require_POST
def detect_apportionment_api(request, pk):
    """
    Detect apportionment for a transaction and return suggestion.

    POST /api/review/transaction/<pk>/detect-apportionment/
    """
    txn = get_object_or_404(PendingTransaction, pk=pk)
    entity = txn.job.entity
    result = detect_apportionment(txn, entity)

    return JsonResponse({
        "status": "ok",
        "creditable_percentage": str(result["creditable_percentage"]),
        "reason": result["reason"],
        "requires_confirmation": result["requires_confirmation"],
        "apportionment_type": result["apportionment_type"],
    })


@login_required
@require_POST
def save_entity_gst_setting(request):
    """
    Save an entity-level GST setting (AC-APT-08).

    POST /api/review/entity-gst-setting/
    Body: {
        "entity_id": <uuid>,
        "setting_type": "vehicle_business_use",
        "value": "65",
        "financial_year_id": <uuid or null>
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    from core.models import Entity, FinancialYear
    entity = get_object_or_404(Entity, pk=data.get("entity_id"))

    fy = None
    fy_id = data.get("financial_year_id")
    if fy_id:
        fy = get_object_or_404(FinancialYear, pk=fy_id)

    setting, created = EntityGSTSetting.objects.update_or_create(
        entity=entity,
        financial_year=fy,
        setting_type=data.get("setting_type", "custom"),
        defaults={
            "value": str(data.get("value", "")),
            "label": data.get("label", ""),
            "created_by": request.user,
        },
    )

    return JsonResponse({
        "status": "ok",
        "setting_id": str(setting.pk),
        "created": created,
        "message": f"Setting {'created' if created else 'updated'}: {setting.get_setting_type_display()} = {setting.value}",
    })


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _recalculate_gst(txn, is_gst_registered):
    """
    Recalculate GST amount and net amount for a transaction based on
    its current gst_treatment and creditable_percentage.

    If there is a gst_amount_override, that takes precedence.
    """
    if txn.gst_amount_override is not None:
        txn.gst_amount = txn.gst_amount_override
        txn.net_amount = (abs(txn.amount) - txn.gst_amount).quantize(Decimal("0.01"))
        return

    abs_amount = abs(txn.amount)
    treatment = txn.gst_treatment

    if not is_gst_registered or treatment in ("gst_free", "input_taxed", "out_of_scope", "not_registered", ""):
        txn.gst_amount = Decimal("0.00")
        txn.net_amount = abs_amount
    else:
        # Taxable: GST = gross / 11, then apply creditable percentage
        full_gst = (abs_amount / Decimal("11")).quantize(Decimal("0.01"))
        cred_pct = txn.creditable_percentage or Decimal("100")
        txn.gst_amount = (full_gst * cred_pct / Decimal("100")).quantize(Decimal("0.01"))
        txn.net_amount = (abs_amount - full_gst).quantize(Decimal("0.01"))


def _calculate_itc(txn):
    """
    Calculate the Input Tax Credit amount for BAS purposes.
    ITC = GST × Creditable Percentage (AC-APT-09)
    """
    if txn.gst_amount_override is not None:
        return txn.gst_amount_override

    abs_amount = abs(txn.amount)
    full_gst = (abs_amount / Decimal("11")).quantize(Decimal("0.01"))
    cred_pct = txn.creditable_percentage or Decimal("100")
    return (full_gst * cred_pct / Decimal("100")).quantize(Decimal("0.01"))
