"""
Bank Statement Review views.

Provides the dashboard (pending reviews + activity feed), the transaction
review page, and API endpoints for the n8n webhook and transaction submission.

Enhanced with GST handling, learning feedback, and bank statement upload.
"""
import json
import logging
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from config.webhook_auth import verify_webhook
from config.authorization import get_review_job_for_user
from .models import PendingTransaction, ReviewActivity, ReviewJob, TransactionPattern

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Airtable configuration
# ---------------------------------------------------------------------------

AIRTABLE_BASE_ID = getattr(settings, "AIRTABLE_BASE_ID", "")
AIRTABLE_PENDING_TABLE = getattr(settings, "AIRTABLE_PENDING_TABLE", "")
AIRTABLE_JOBS_TABLE = getattr(settings, "AIRTABLE_JOBS_TABLE", "")
AIRTABLE_LEARNING_TABLE = getattr(settings, "AIRTABLE_LEARNING_TABLE", "")


def _get_airtable_headers():
    """Return headers for Airtable API calls, or None if not configured."""
    api_key = getattr(settings, "AIRTABLE_API_KEY", None)
    if not api_key:
        return None
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _airtable_configured():
    return bool(getattr(settings, "AIRTABLE_API_KEY", None))


# ---------------------------------------------------------------------------
# Airtable sync — pull Pending Review records, group into jobs
# ---------------------------------------------------------------------------

def _sync_from_airtable():
    """
    Pull all records from the Pending Review table in Airtable.
    Group by (Batch ID prefix, Client Name) to create/update ReviewJobs.
    Each transaction becomes a PendingTransaction linked to its job.
    """
    import requests

    headers = _get_airtable_headers()
    if not headers:
        return False

    base_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"

    try:
        # Fetch ALL records from Pending Review (paginated)
        all_records = []
        offset = None
        while True:
            params = {"pageSize": 100}
            if offset:
                params["offset"] = offset
            resp = requests.get(
                f"{base_url}/{AIRTABLE_PENDING_TABLE}",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            all_records.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset:
                break

        if not all_records:
            return True

        # Group records by Client Name (each client = one review job)
        grouped = defaultdict(list)
        for rec in all_records:
            fields = rec.get("fields", {})
            client = fields.get("Client Name", "Unknown")
            grouped[client].append(rec)

        for client_name, records in grouped.items():
            # Determine job status based on transaction statuses
            statuses = [r["fields"].get("Status", "Pending") for r in records]
            all_confirmed = all(s == "Confirmed" for s in statuses)
            any_confirmed = any(s == "Confirmed" for s in statuses)

            if all_confirmed:
                job_status = "completed"
            elif any_confirmed:
                job_status = "in_progress"
            else:
                job_status = "awaiting_review"

            first_fields = records[0].get("fields", {})
            accountant_email = first_fields.get("Accountant Email", "")

            confirmed_count = sum(1 for s in statuses if s == "Confirmed")

            # Try to match entity for GST status
            from core.models import Entity
            entity = None
            is_gst = True
            try:
                entity = Entity.objects.filter(
                    entity_name__icontains=client_name
                ).first()
                if entity:
                    is_gst = entity.is_gst_registered
            except Exception:
                pass

            # Create or update the ReviewJob
            existing_job = ReviewJob.objects.filter(
                client_name=client_name,
                status__in=["awaiting_review", "in_progress"],
            ).first()
            defaults = {
                "entity": entity,
                "file_name": f"Bank Statement — {len(records)} transactions",
                "submitted_by": accountant_email or "Via bankstatements@mcands.com.au",
                "source": "airtable",
                "total_transactions": len(records),
                "auto_coded_count": len(records),
                "flagged_count": len(records),
                "confirmed_count": confirmed_count,
                "status": job_status,
                "is_gst_registered": is_gst,
            }
            if existing_job:
                for key, value in defaults.items():
                    setattr(existing_job, key, value)
                existing_job.save()
                job, created = existing_job, False
            else:
                job = ReviewJob.objects.create(client_name=client_name, **defaults)
                created = True

            if created:
                ReviewActivity.objects.create(
                    activity_type="new_statement",
                    title="New statement received",
                    description=f"{client_name} — {len(records)} transactions to review",
                )

            # Sync individual transactions
            for rec in records:
                fields = rec.get("fields", {})
                is_confirmed = fields.get("Status") == "Confirmed"
                amount = Decimal(str(fields.get("Amount", 0)))
                tax_type = fields.get("AI Suggested Tax Type", "")

                # Calculate GST
                abs_amount = abs(amount)
                if is_gst and tax_type in ("GST on Income", "GST on Expenses"):
                    gst_amount = (abs_amount / Decimal("11")).quantize(Decimal("0.01"))
                    net_amount = (abs_amount - gst_amount).quantize(Decimal("0.01"))
                else:
                    gst_amount = Decimal("0.00")
                    net_amount = abs_amount

                PendingTransaction.objects.update_or_create(
                    airtable_record_id=rec["id"],
                    defaults={
                        "job": job,
                        "date": fields.get("Transaction Date", ""),
                        "description": fields.get("Description", ""),
                        "amount": amount,
                        "gst_amount": gst_amount,
                        "net_amount": net_amount,
                        "ai_suggested_code": fields.get("AI Suggested Code", ""),
                        "ai_suggested_name": fields.get("AI Suggested Account", ""),
                        "ai_confidence": fields.get("AI Confidence", 0),
                        "ai_suggested_tax_type": fields.get("AI Suggested Tax Type", ""),
                        "confirmed_code": fields.get("Confirmed Code", ""),
                        "confirmed_name": fields.get("Confirmed Account", ""),
                        "confirmed_tax_type": fields.get("Confirmed Tax Type", ""),
                        "is_confirmed": is_confirmed,
                    },
                )

        return True

    except Exception as e:
        logger.error(f"Airtable sync failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@login_required
def review_dashboard(request):
    """
    Personalised dashboard showing staff-specific workload:
    My Entities, My Open Flags, My Pending Reviews, practice-wide metrics,
    and recent activity.
    """
    # Redirect Office Admin users to their dedicated dashboard
    if getattr(request.user, 'is_office_admin', False):
        return redirect('office_admin:dashboard')

    from core.models import (
        Client, Entity, FinancialYear, RiskFlag, AuditLog,
    )
    from django.db.models import Q, Count
    from django.contrib.auth import get_user_model
    User = get_user_model()

    # Sync from Airtable if configured
    if _airtable_configured():
        _sync_from_airtable()

    user = request.user

    # --- Time-based greeting (Melbourne time) ---
    from datetime import datetime
    import zoneinfo
    now = datetime.now(zoneinfo.ZoneInfo("Australia/Melbourne"))
    hour = now.hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    first_name = request.user.first_name or request.user.username.capitalize()

    # --- MY ENTITIES (assigned as primary_accountant or reviewer) ---
    my_entities = Entity.objects.filter(
        is_archived=False
    ).filter(
        Q(primary_accountant=user) | Q(reviewer=user)
    ).select_related("primary_accountant", "reviewer").distinct()

    my_entity_count = my_entities.count()
    my_primary_count = Entity.objects.filter(
        is_archived=False, primary_accountant=user
    ).count()
    my_review_count = Entity.objects.filter(
        is_archived=False, reviewer=user
    ).count()

    # --- MY UNFINALISED FINANCIAL YEARS ---
    my_unfinalised_years = (
        FinancialYear.objects.filter(
            entity__in=my_entities,
            status__in=["draft", "in_review", "reviewed"],
        )
        .select_related("entity", "entity__primary_accountant", "entity__reviewer")
        .order_by("-end_date")[:15]
    )

    # --- MY OPEN RISK FLAGS ---
    my_open_flags = (
        RiskFlag.objects.filter(
            financial_year__entity__in=my_entities,
            status__in=["open", "reviewed"],
        )
        .select_related("financial_year", "financial_year__entity")
        .order_by("-severity", "-created_at")[:20]
    )

    # Group risk flags by entity for display
    risk_alerts = []
    flagged_years = (
        FinancialYear.objects.filter(
            entity__in=my_entities,
            risk_flags__status="open",
        )
        .select_related("entity")
        .distinct()
    )
    for fy in flagged_years:
        open_flags = fy.risk_flags.filter(status="open")
        critical_count = open_flags.filter(severity="CRITICAL").count()
        high_count = open_flags.filter(severity__in=["HIGH", "CRITICAL"]).count()
        medium_count = open_flags.filter(severity="MEDIUM").count()
        low_count = open_flags.filter(severity="LOW").count()
        total_flags = open_flags.count()
        if total_flags > 0:
            risk_alerts.append({
                "entity_name": fy.entity.entity_name,
                "year_label": fy.year_label,
                "financial_year": fy,
                "critical_count": critical_count,
                "high_count": high_count,
                "medium_count": medium_count,
                "low_count": low_count,
                "total_flags": total_flags,
            })
    risk_alerts.sort(key=lambda x: (x["critical_count"], x["high_count"], x["total_flags"]), reverse=True)

    # --- PENDING BANK STATEMENT REVIEWS ---
    pending_jobs = ReviewJob.objects.filter(
        status__in=["awaiting_review", "in_progress"]
    ).order_by("-received_at")
    if not user.is_admin:
        pending_jobs = pending_jobs.filter(
            Q(entity__primary_accountant=user) | Q(entity__reviewer=user)
        )

    completed_jobs = ReviewJob.objects.filter(
        status="completed"
    ).order_by("-completed_at")[:5]

    activities = ReviewActivity.objects.all().order_by("-created_at")[:10]
    total_patterns = TransactionPattern.objects.count()

    # --- PRACTICE-WIDE METRICS (admin only) ---
    practice_metrics = None
    if user.is_admin:
        total_entities = Entity.objects.filter(is_archived=False).count()
        total_unfinalised = FinancialYear.objects.filter(
            status__in=["draft", "in_review", "reviewed"]
        ).count()
        total_open_flags = RiskFlag.objects.filter(status="open").count()
        total_critical_flags = RiskFlag.objects.filter(
            status="open", severity="CRITICAL"
        ).count()
        total_pending_reviews = ReviewJob.objects.filter(
            status__in=["awaiting_review", "in_progress"]
        ).count()
        unassigned_entities = Entity.objects.filter(
            is_archived=False, primary_accountant__isnull=True
        ).count()

        # Staff workload summary
        staff_workload = []
        for staff in User.objects.filter(is_active=True):
            p_count = Entity.objects.filter(
                is_archived=False, primary_accountant=staff
            ).count()
            r_count = Entity.objects.filter(
                is_archived=False, reviewer=staff
            ).count()
            open_flags_count = RiskFlag.objects.filter(
                financial_year__entity__primary_accountant=staff,
                status="open"
            ).count()
            if p_count > 0 or r_count > 0:
                staff_workload.append({
                    "name": staff.get_full_name() or staff.username,
                    "primary": p_count,
                    "review": r_count,
                    "open_flags": open_flags_count,
                })
        staff_workload.sort(key=lambda x: x["primary"], reverse=True)

        practice_metrics = {
            "total_entities": total_entities,
            "total_unfinalised": total_unfinalised,
            "total_open_flags": total_open_flags,
            "total_critical_flags": total_critical_flags,
            "total_pending_reviews": total_pending_reviews,
            "unassigned_entities": unassigned_entities,
            "staff_workload": staff_workload,
        }

    # --- RECENT ACTIVITY (from AuditLog) ---
    recent_audit_logs = AuditLog.objects.all().order_by("-timestamp")[:10]

    # For admins, also show all unfinalised years (not just mine)
    if user.is_admin:
        all_unfinalised_years = (
            FinancialYear.objects.filter(
                status__in=["draft", "in_review", "reviewed"],
            )
            .select_related("entity", "entity__primary_accountant", "entity__reviewer")
            .order_by("-end_date")[:15]
        )
    else:
        all_unfinalised_years = my_unfinalised_years

    # --- OFFICE ADMIN DASHBOARD ---
    if user.is_office_admin:
        from core.models import MeetingNote
        all_entities = Entity.objects.filter(is_archived=False)
        total_entity_count = all_entities.count()
        recently_added = all_entities.order_by("-created_at")[:5]
        unassigned_count = all_entities.filter(primary_accountant__isnull=True).count()

        # Entity type breakdown
        entity_type_counts = {}
        for et_val, et_label in Entity.EntityType.choices:
            c = all_entities.filter(entity_type=et_val).count()
            if c > 0:
                entity_type_counts[et_label] = c

        # Upcoming follow-ups across all entities
        from django.utils import timezone as tz
        upcoming_followups = (
            MeetingNote.objects.filter(
                follow_up_completed=False,
                follow_up_date__isnull=False,
                follow_up_date__gte=tz.now().date(),
            )
            .select_related("entity", "created_by")
            .order_by("follow_up_date")[:15]
        )
        overdue_followups = (
            MeetingNote.objects.filter(
                follow_up_completed=False,
                follow_up_date__isnull=False,
                follow_up_date__lt=tz.now().date(),
            )
            .select_related("entity", "created_by")
            .order_by("follow_up_date")[:10]
        )

        office_context = {
            "greeting": greeting,
            "first_name": first_name,
            "total_entity_count": total_entity_count,
            "recently_added": recently_added,
            "unassigned_count": unassigned_count,
            "entity_type_counts": entity_type_counts,
            "upcoming_followups": upcoming_followups,
            "overdue_followups": overdue_followups,
        }
        return render(request, "review/dashboard_office_admin.html", office_context)

    context = {
        "greeting": greeting,
        "first_name": first_name,
        # My workload
        "my_entity_count": my_entity_count,
        "my_primary_count": my_primary_count,
        "my_review_count": my_review_count,
        "my_unfinalised_years": my_unfinalised_years,
        "my_open_flags_count": my_open_flags.count(),
        # Risk alerts
        "risk_alerts": risk_alerts,
        # Bank statement reviews
        "pending_jobs": pending_jobs,
        "completed_jobs": completed_jobs,
        "activities": activities,
        "total_patterns": total_patterns,
        # Unfinalised files
        "unfinalised_years": all_unfinalised_years,
        # Practice metrics (admin only)
        "practice_metrics": practice_metrics,
        # Recent activity
        "recent_audit_logs": recent_audit_logs,
    }
    return render(request, "review/dashboard.html", context)


@login_required
def review_detail(request, pk):
    """
    Transaction review page for a specific job.
    Shows all flagged transactions with account picker, tax type dropdowns,
    and GST breakdown columns for GST-registered entities.
    """
    job = get_review_job_for_user(request, pk)

    # Apply classification rules before rendering (rules run BEFORE AI)
    from .views_enhanced import apply_classification_rules
    try:
        apply_classification_rules(job)
    except Exception as e:
        logger.warning(f"Error applying classification rules: {e}")

    # Get transactions — parents first, then split children grouped under parents
    transactions = job.transactions.all().order_by("date", "description")

    # Get entity-type-specific chart of accounts for the picker
    from core.models import ChartOfAccount
    entity_type = job.entity.entity_type if job.entity else "company"
    coa_qs = ChartOfAccount.objects.filter(
        entity_type=entity_type, is_active=True
    ).order_by("section", "display_order")
    accounts = [
        {
            "code": a.account_code,
            "name": a.account_name,
            "section": a.get_section_display(),
            "tax": a.tax_code,
        }
        for a in coa_qs
    ]

    # Count classification rules for this entity
    from .models import ClassificationRule
    rules_count = 0
    if job.entity:
        rules_count = ClassificationRule.objects.filter(
            entity=job.entity, is_active=True
        ).count()

    context = {
        "job": job,
        "transactions": transactions,
        "accounts_json": json.dumps(accounts),
        "rules_count": rules_count,
    }
    return render(request, "review/review_detail.html", context)


@login_required
@require_POST
def confirm_transaction(request, pk):
    """
    AJAX endpoint to confirm a single transaction.
    Recalculates GST based on confirmed tax type.
    Updates the local record and pushes to Airtable.
    """
    txn = get_object_or_404(PendingTransaction, pk=pk)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    # Validate input lengths and content
    confirmed_code = data.get("confirmed_code", "")[:20]
    confirmed_name = data.get("confirmed_name", "")[:255]
    confirmed_tax_type = data.get("confirmed_tax_type", "")[:50]

    VALID_TAX_TYPES = {
        "", "GST on Income", "GST on Expenses", "Input Taxed", "N-T",
        "GST Free Income", "GST Free Expenses", "BAS Excluded",
    }
    if confirmed_tax_type and confirmed_tax_type not in VALID_TAX_TYPES:
        return JsonResponse({"status": "error", "message": "Invalid tax type"}, status=400)

    txn.confirmed_code = confirmed_code
    txn.confirmed_name = confirmed_name
    txn.confirmed_tax_type = confirmed_tax_type
    txn.is_confirmed = True

    # Recalculate GST based on confirmed tax type
    is_gst = txn.job.is_gst_registered
    txn.calculate_gst(tax_type=txn.confirmed_tax_type, is_gst_registered=is_gst)
    txn.save()

    # Update job confirmed count
    job = txn.job
    job.confirmed_count = job.transactions.filter(is_confirmed=True).count()
    if job.confirmed_count > 0 and job.status == "awaiting_review":
        job.status = "in_progress"
    job.save()

    # Push to Airtable
    headers = _get_airtable_headers()
    if headers and txn.airtable_record_id:
        import requests as http_requests
        try:
            base_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
            http_requests.patch(
                f"{base_url}/{AIRTABLE_PENDING_TABLE}/{txn.airtable_record_id}",
                headers=headers,
                json={
                    "fields": {
                        "Confirmed Code": txn.confirmed_code,
                        "Confirmed Account": txn.confirmed_name,
                        "Confirmed Tax Type": txn.confirmed_tax_type,
                        "Status": "Confirmed",
                    }
                },
                timeout=10,
            )
        except Exception as e:
            logger.error(f"Failed to update Airtable: {e}")

    return JsonResponse({
        "status": "ok",
        "confirmed_count": job.confirmed_count,
        "flagged_count": job.flagged_count,
        "progress_percent": job.progress_percent,
        "gst_amount": str(txn.gst_amount),
        "net_amount": str(txn.net_amount),
    })


@login_required
@require_POST
def submit_review(request, pk):
    """
    Submit all confirmed transactions for a job.
    Saves patterns to the learning database.
    Marks the job as completed and batch-updates Airtable.
    """
    job = get_object_or_404(ReviewJob, pk=pk)

    # Check all transactions are confirmed
    unconfirmed = job.transactions.filter(is_confirmed=False).count()
    if unconfirmed > 0:
        return JsonResponse(
            {"status": "error", "message": f"{unconfirmed} transactions still need confirmation."},
            status=400,
        )

    # Save patterns to learning database
    from .learning import save_patterns_from_job, push_pattern_to_airtable
    patterns_saved = save_patterns_from_job(job)

    # Batch update Airtable
    headers = _get_airtable_headers()
    if headers:
        import requests as http_requests
        base_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"

        confirmed_txns = list(job.transactions.filter(
            is_confirmed=True
        ).exclude(airtable_record_id="").exclude(airtable_record_id__isnull=True))

        for i in range(0, len(confirmed_txns), 10):
            batch = confirmed_txns[i:i + 10]
            records = []
            for txn in batch:
                records.append({
                    "id": txn.airtable_record_id,
                    "fields": {
                        "Confirmed Code": txn.confirmed_code,
                        "Confirmed Account": txn.confirmed_name,
                        "Confirmed Tax Type": txn.confirmed_tax_type,
                        "Status": "Confirmed",
                    },
                })
            try:
                http_requests.patch(
                    f"{base_url}/{AIRTABLE_PENDING_TABLE}",
                    headers=headers,
                    json={"records": records},
                    timeout=15,
                )
            except Exception as e:
                logger.error(f"Airtable batch update failed: {e}")

    # Update local job
    job.status = "completed"
    job.completed_at = timezone.now()
    job.save()

    # Log activity
    ReviewActivity.objects.create(
        activity_type="review_completed",
        title="Review completed",
        description=(
            f"{job.client_name} — {job.confirmed_count} transactions confirmed, "
            f"{patterns_saved} patterns learned"
        ),
    )

    return JsonResponse({"status": "ok", "redirect": "/"})


@login_required
@require_POST
def accept_all_suggestions(request, pk):
    """
    Accept all AI suggestions for unconfirmed transactions in a job.
    Calculates GST based on the suggested tax type.
    """
    job = get_object_or_404(ReviewJob, pk=pk)
    unconfirmed = job.transactions.filter(is_confirmed=False)
    is_gst = job.is_gst_registered

    for txn in unconfirmed:
        txn.confirmed_code = txn.ai_suggested_code
        txn.confirmed_name = txn.ai_suggested_name
        # Use AI suggested tax type if available
        if txn.ai_suggested_tax_type:
            txn.confirmed_tax_type = txn.ai_suggested_tax_type
        elif not is_gst:
            txn.confirmed_tax_type = "BAS Excluded"
        elif txn.amount > 0:
            txn.confirmed_tax_type = "GST on Income"
        else:
            txn.confirmed_tax_type = "GST on Expenses"

        # Recalculate GST
        txn.calculate_gst(tax_type=txn.confirmed_tax_type, is_gst_registered=is_gst)
        txn.is_confirmed = True
        txn.save()

    job.confirmed_count = job.transactions.filter(is_confirmed=True).count()
    if job.status == "awaiting_review":
        job.status = "in_progress"
    job.save()

    return JsonResponse({
        "status": "ok",
        "confirmed_count": job.confirmed_count,
        "flagged_count": job.flagged_count,
    })


# ---------------------------------------------------------------------------
# Bulk Approve by Group (account code)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def bulk_approve_group(request, pk):
    """
    AJAX endpoint to bulk-approve a group of transactions that share
    the same AI-suggested account code.

    POST /api/review/<pk>/bulk-approve-group/
    Body: {"account_code": "1510", "transaction_ids": ["uuid1", "uuid2", ...]}

    If transaction_ids is provided, only those transactions are approved.
    If only account_code is provided, all unconfirmed transactions with
    that AI-suggested code are approved.
    """
    job = get_review_job_for_user(request, pk)
    is_gst = job.is_gst_registered

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    account_code = data.get("account_code", "")
    txn_ids = data.get("transaction_ids", [])

    if txn_ids:
        # Approve specific transactions
        txns = job.transactions.filter(pk__in=txn_ids, is_confirmed=False)
    elif account_code:
        # Approve all unconfirmed with this AI-suggested code
        txns = job.transactions.filter(
            ai_suggested_code=account_code, is_confirmed=False
        )
    else:
        return JsonResponse({"status": "error", "message": "Provide account_code or transaction_ids"}, status=400)

    approved_ids = []
    for txn in txns:
        txn.confirmed_code = txn.ai_suggested_code
        txn.confirmed_name = txn.ai_suggested_name
        if txn.ai_suggested_tax_type:
            txn.confirmed_tax_type = txn.ai_suggested_tax_type
        elif not is_gst:
            txn.confirmed_tax_type = "BAS Excluded"
        elif txn.amount > 0:
            txn.confirmed_tax_type = "GST on Income"
        else:
            txn.confirmed_tax_type = "GST on Expenses"
        txn.calculate_gst(tax_type=txn.confirmed_tax_type, is_gst_registered=is_gst)
        txn.is_confirmed = True
        txn.save()
        approved_ids.append(str(txn.pk))

    job.confirmed_count = job.transactions.filter(is_confirmed=True).count()
    if job.status == "awaiting_review":
        job.status = "in_progress"
    job.save()

    return JsonResponse({
        "status": "ok",
        "approved_ids": approved_ids,
        "approved_count": len(approved_ids),
        "confirmed_count": job.confirmed_count,
        "flagged_count": job.flagged_count,
        "progress_percent": job.progress_percent,
    })


# ---------------------------------------------------------------------------
# Bank Statement Upload (manual PDF/Excel upload)
# ---------------------------------------------------------------------------

@login_required
def upload_bank_statement(request):
    """
    Handle manual bank statement upload (PDF or Excel).
    Accepts period_start and period_end to filter out-of-period transactions.
    Supports multiple files via 'files' field.
    """
    from datetime import datetime as dt
    from .pdf_parsers import extract_transactions_from_pdf_direct

    # Support both single 'file' and multiple 'files' field names
    uploaded_files = request.FILES.getlist("files")
    if not uploaded_files:
        single = request.FILES.get("file")
        if single:
            uploaded_files = [single]

    entity_id = request.POST.get("entity_id")
    client_name = request.POST.get("client_name", "Unknown")
    period_start_str = request.POST.get("period_start", "")
    period_end_str = request.POST.get("period_end", "")
    user_opening_balance_str = request.POST.get("opening_balance", "")
    financial_year_id = request.POST.get("financial_year_id", "")

    if not uploaded_files:
        return JsonResponse({"status": "error", "message": "No files uploaded"}, status=400)
    # Parse period dates
    period_start_date = None
    period_end_date = None
    try:
        if period_start_str:
            period_start_date = dt.strptime(period_start_str, "%Y-%m-%d").date()
        if period_end_str:
            period_end_date = dt.strptime(period_end_str, "%Y-%m-%d").date()
    except ValueError:
        pass

    # Get entity for GST status
    entity = None
    is_gst = True
    if entity_id:
        from core.models import Entity
        try:
            entity = Entity.objects.get(pk=entity_id)
            is_gst = entity.is_gst_registered
            client_name = entity.entity_name
        except Exception:
            pass

    # Process each uploaded file
    created_jobs = []
    errors = []

    for uploaded_file in uploaded_files:
        content = uploaded_file.read()
        filename = uploaded_file.name
        logger.info(f"Processing file ({len(content)} bytes, entity_id={entity_id})")

        # Validate file content matches extension (magic bytes)
        is_pdf_content = content[:5] == b'%PDF-'
        is_xlsx_content = content[:4] == b'PK\x03\x04'
        if filename.lower().endswith(".pdf") and not is_pdf_content:
            errors.append(f"{filename}: File content does not match PDF format")
            continue
        if filename.lower().endswith(".xlsx") and not is_xlsx_content:
            errors.append(f"{filename}: File content does not match Excel format")
            continue

        try:
            if filename.lower().endswith(".pdf"):
                extracted = extract_transactions_from_pdf_direct(content, filename)
            else:
                extracted = _parse_excel_bank_statement(content, filename)
        except Exception as exc:
            logger.error(f"Extraction exception (entity_id={entity_id}): {exc}", exc_info=True)
            errors.append(f"{filename}: Extraction error — {exc}")
            continue

        logger.info(f"Extraction result (entity_id={entity_id}): txn_count={len(extracted.get('transactions', [])) if isinstance(extracted, dict) else 0}")

        if not extracted or not extracted.get("transactions"):
            errors.append(f"{filename}: No transactions could be extracted")
            continue

        transactions = extracted["transactions"]

        # Filter transactions by period if dates were provided
        if period_start_date or period_end_date:
            filtered = []
            excluded_count = 0
            for txn in transactions:
                txn_date_str = txn.get("date", "")
                txn_date = None
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"):
                    try:
                        txn_date = dt.strptime(txn_date_str.strip(), fmt).date()
                        break
                    except (ValueError, AttributeError):
                        continue

                if txn_date:
                    if period_start_date and txn_date < period_start_date:
                        excluded_count += 1
                        continue
                    if period_end_date and txn_date > period_end_date:
                        excluded_count += 1
                        continue

                filtered.append(txn)

            logger.info(
                f"Period filter ({filename}): {len(transactions)} total, {len(filtered)} in period, "
                f"{excluded_count} excluded ({period_start_str} to {period_end_str})"
            )
            transactions = filtered

        if not transactions:
            errors.append(f"{filename}: No transactions within the selected period")
            continue

        # Save transactions WITHOUT classification (fast)
        # Classification happens asynchronously on the review page
        logger.info(f"Creating ReviewJob for {filename} with {len(transactions)} transactions (no classification)")

        # Create ReviewJob
        # Determine opening balance: user-entered takes priority, then extracted from file
        extracted_opening = Decimal(str(extracted.get("opening_balance", 0)))
        if user_opening_balance_str:
            try:
                user_opening_balance = Decimal(user_opening_balance_str)
            except Exception:
                user_opening_balance = extracted_opening
        else:
            user_opening_balance = extracted_opening

        job = ReviewJob.objects.create(
            entity=entity,
            client_name=client_name,
            file_name=filename,
            submitted_by=request.user.get_full_name() or request.user.username,
            source="upload",
            total_transactions=len(transactions),
            auto_coded_count=0,
            flagged_count=len(transactions),
            confirmed_count=0,
            is_gst_registered=is_gst,
            bank_account_name=extracted.get("account_name", ""),
            bsb=extracted.get("bsb", ""),
            account_number=extracted.get("account_number", ""),
            period_start=period_start_str or extracted.get("period_start", ""),
            period_end=period_end_str or extracted.get("period_end", ""),
            opening_balance=user_opening_balance,
            closing_balance=Decimal(str(extracted.get("closing_balance", 0))),
        )

        # Create PendingTransactions - unclassified (bulk for speed)
        pending_objs = []
        for txn in transactions:
            amount = Decimal(str(txn.get("amount", 0)))
            abs_amount = abs(amount)
            pending_objs.append(PendingTransaction(
                job=job,
                date=txn.get("date", ""),
                description=txn.get("description", ""),
                amount=amount,
                gst_amount=Decimal("0.00"),
                net_amount=abs_amount,
                ai_suggested_code="",
                ai_suggested_name="",
                ai_suggested_tax_type="",
                ai_confidence=0,
                ai_reasoning="",
                from_learning=False,
                is_confirmed=False,
                confirmed_code="",
                confirmed_name="",
                confirmed_tax_type="",
            ))
        PendingTransaction.objects.bulk_create(pending_objs)
        logger.info(f"Created {len(pending_objs)} PendingTransactions for job {job.pk}")

        # Cross-check opening balance against trial balance
        balance_warning = None
        if user_opening_balance and user_opening_balance != Decimal('0') and entity and financial_year_id:
            try:
                from core.models import FinancialYear, TrialBalanceLine
                fy = FinancialYear.objects.get(pk=financial_year_id)
                # Look for bank account lines in the trial balance (common codes: 1-xxxx for bank accounts)
                bank_tb_lines = TrialBalanceLine.objects.filter(
                    financial_year=fy,
                    account_code__regex=r'^1-[0-9]+',  # Bank accounts typically start with 1-
                )
                if bank_tb_lines.exists():
                    tb_bank_total = sum(line.net_movement for line in bank_tb_lines)
                    difference = abs(user_opening_balance - tb_bank_total)
                    if difference > Decimal('0.01'):
                        balance_warning = (
                            f"BALANCE MISMATCH: Opening balance entered (${user_opening_balance:,.2f}) "
                            f"differs from trial balance bank total (${tb_bank_total:,.2f}) "
                            f"by ${difference:,.2f}. Please investigate — you may have missed a bank statement."
                        )
                        logger.warning(f"Balance mismatch for entity_id={entity_id}, fy_id={financial_year_id}: difference=${difference:,.2f}")
                        # Create a risk flag if the model exists
                        try:
                            from core.models import RiskFlag
                            RiskFlag.objects.create(
                                financial_year=fy,
                                severity='MEDIUM',
                                category='bank_reconciliation',
                                title='Bank balance mismatch detected',
                                description=balance_warning,
                                status='open',
                            )
                        except Exception as e:
                            logger.error(f"Could not create risk flag: {e}")
                else:
                    # No bank lines in TB yet — store the opening balance as the reference point
                    logger.info(f"No bank lines in TB for entity_id={entity_id}. Opening balance stored as reference.")
            except Exception as e:
                logger.error(f"Balance cross-check error: {e}")

        # Log activity
        activity_desc = (
            f"{client_name} — {len(transactions)} transactions from {filename}, "
            f"{job.confirmed_count} auto-confirmed"
            f"{'  (GST registered)' if is_gst else ' (not GST registered)'}"
        )
        if balance_warning:
            activity_desc += f" | WARNING: {balance_warning}"

        # Attach warning to job so it can be collected after the loop
        job._balance_warning = balance_warning

        ReviewActivity.objects.create(
            activity_type="new_statement",
            title="Bank statement uploaded",
            description=activity_desc,
        )
        # Dashboard activity log
        try:
            from core.models import ActivityLog
            ActivityLog.objects.create(
                user=request.user,
                event_type="bank_upload",
                title=f"Bank statement uploaded: {filename}",
                description=f"{client_name} — {len(transactions)} transactions extracted",
                url=f"/review/{job.pk}/",
            )
        except Exception:
            pass
        created_jobs.append(job)

    # Handle response
    logger.info(f"Upload complete: {len(created_jobs)} jobs created, {len(errors)} errors. Sending response.")
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if not created_jobs:
        error_msg = "; ".join(errors) if errors else "No transactions could be extracted from any file"
        if is_ajax:
            return JsonResponse({"status": "error", "message": error_msg}, status=400)
        from django.contrib import messages as django_messages
        django_messages.error(request, error_msg)
        return redirect("review:dashboard")

    # Build redirect URL — go back to the financial year page (review tab) so user stays in context
    from django.urls import reverse
    from django.contrib import messages as django_messages

    # Collect any balance warnings from all jobs
    all_balance_warnings = []
    for j in created_jobs:
        if hasattr(j, '_balance_warning') and j._balance_warning:
            all_balance_warnings.append(j._balance_warning)

    if financial_year_id:
        redirect_url = reverse("core:financial_year_detail", kwargs={"pk": financial_year_id}) + "#tab-review"
    elif len(created_jobs) == 1:
        redirect_url = reverse("review:review_detail", kwargs={"pk": created_jobs[0].pk})
    else:
        redirect_url = reverse("review:dashboard")

    success_msg = (
        f"Successfully processed {len(created_jobs)} bank statement(s) "
        f"({sum(j.total_transactions for j in created_jobs)} total transactions)."
    )
    if errors:
        success_msg += f" Errors: {'; '.join(errors)}"
    django_messages.success(request, success_msg)

    # Show each balance warning as a separate warning message
    for warning in all_balance_warnings:
        django_messages.warning(request, warning)

    if is_ajax:
        return JsonResponse({
            "status": "success",
            "redirect": redirect_url,
            "jobs_created": len(created_jobs),
            "total_transactions": sum(j.total_transactions for j in created_jobs),
            "errors": errors,
            "balance_warnings": all_balance_warnings,
        })
    return redirect(redirect_url)


def _parse_excel_bank_statement(content, filename):
    """Parse an Excel/CSV bank statement into the standard transaction format."""
    import io
    try:
        import pandas as pd
    except ImportError:
        return None

    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content))

        # Try to identify columns
        col_map = {}
        for col in df.columns:
            col_lower = str(col).lower()
            if "date" in col_lower:
                col_map["date"] = col
            elif "desc" in col_lower or "narr" in col_lower or "particular" in col_lower:
                col_map["description"] = col
            elif "amount" in col_lower or "value" in col_lower:
                col_map["amount"] = col
            elif "debit" in col_lower:
                col_map["debit"] = col
            elif "credit" in col_lower:
                col_map["credit"] = col

        transactions = []
        for _, row in df.iterrows():
            date = str(row.get(col_map.get("date", ""), ""))
            desc = str(row.get(col_map.get("description", ""), ""))

            if "amount" in col_map:
                amount = float(row.get(col_map["amount"], 0) or 0)
            elif "debit" in col_map and "credit" in col_map:
                debit = float(row.get(col_map["debit"], 0) or 0)
                credit = float(row.get(col_map["credit"], 0) or 0)
                amount = credit - debit
            else:
                continue

            if desc and desc != "nan":
                transactions.append({
                    "date": date,
                    "description": desc,
                    "amount": amount,
                })

        return {
            "transactions": transactions,
            "opening_balance": 0,
            "closing_balance": 0,
            "account_name": "",
            "bsb": "",
            "account_number": "",
            "period_start": "",
            "period_end": "",
        }
    except Exception as e:
        logger.error(f"Excel parsing failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Webhook endpoint (called by n8n)
# ---------------------------------------------------------------------------

@ratelimit(key='ip', rate='5/m', method='POST')
@csrf_exempt
@require_POST
def notify_new_review_job(request):
    """
    Webhook endpoint for n8n to notify StatementHub of a new review job.
    POST /api/notify/new-review-job

    Authentication: HMAC-SHA256 signature or Bearer token (via WEBHOOK_SECRET).
    """
    # Verify webhook authentication
    is_valid, error_response = verify_webhook(request)
    if not is_valid:
        return error_response

    try:
        data = json.loads(request.body)

        # Try to match entity
        from core.models import Entity
        entity = None
        is_gst = True
        client_name = data.get("client_name", "Unknown")
        try:
            entity = Entity.objects.filter(
                entity_name__icontains=client_name
            ).first()
            if entity:
                is_gst = entity.is_gst_registered
        except Exception:
            pass

        job = ReviewJob.objects.create(
            airtable_record_id=data.get("airtable_record_id"),
            entity=entity,
            client_name=client_name,
            file_name=data.get("file_name", ""),
            submitted_by=data.get("submitted_by", ""),
            source="airtable",
            total_transactions=data.get("total_transactions", 0),
            auto_coded_count=data.get("auto_coded_count", 0),
            flagged_count=data.get("flagged_count", 0),
            is_gst_registered=is_gst,
            status="awaiting_review",
        )

        ReviewActivity.objects.create(
            activity_type="new_statement",
            title="New statement received",
            description=f"{job.client_name} — {job.flagged_count} transactions flagged",
        )

        return JsonResponse({"status": "ok", "job_id": str(job.id)})
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return JsonResponse({"status": "error", "message": "An internal error occurred"}, status=400)


# ---------------------------------------------------------------------------
# Async Classification (AJAX endpoint)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def classify_batch(request, pk):
    """
    AJAX endpoint to classify a batch of unclassified transactions for a job.
    Called repeatedly from the review page to classify in small batches.

    POST /api/review/<pk>/classify-batch/
    Body: {"batch_size": 15}  (optional, defaults to 15)

    Returns:
    {
        "status": "ok",
        "classified": [...],  // list of {txn_id, code, name, tax_type, confidence, reasoning}
        "remaining": int,     // how many unclassified transactions remain
        "total": int,
        "auto_coded_count": int,
    }
    """
    from .email_ingestion import classify_transactions

    job = get_review_job_for_user(request, pk)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = {}

    try:
        batch_size = min(int(data.get("batch_size", 15)), 30)  # Cap at 30
    except (ValueError, TypeError):
        batch_size = 15

    # Get unclassified transactions (ai_confidence == 0 and no ai_suggested_code)
    unclassified = list(
        job.transactions.filter(ai_confidence=0, ai_suggested_code="")
        .order_by("date", "description")[:batch_size]
    )

    if not unclassified:
        return JsonResponse({
            "status": "ok",
            "classified": [],
            "remaining": 0,
            "total": job.total_transactions,
            "auto_coded_count": job.auto_coded_count,
        })

    # Build transaction dicts for the classifier
    txn_dicts = [
        {
            "date": txn.date,
            "description": txn.description,
            "amount": float(txn.amount),
        }
        for txn in unclassified
    ]

    entity = job.entity
    is_gst = job.is_gst_registered

    try:
        classifications = classify_transactions(
            txn_dicts, entity=entity, is_gst_registered=is_gst
        )
    except Exception as exc:
        logger.error(f"classify_batch failed for job {pk}: {exc}", exc_info=True)
        return JsonResponse({
            "status": "error",
            "message": f"Classification error: {str(exc)}",
        }, status=500)

    # Update each transaction with its classification
    classified_results = []
    for txn, cls in zip(unclassified, classifications):
        if cls is None:
            cls = {
                "account_code": "0000",
                "account_name": "Suspense",
                "tax_type": "N-T",
                "confidence": 1,
                "reasoning": "",
                "from_learning": False,
            }

        txn.ai_suggested_code = cls.get("account_code", "0000")
        txn.ai_suggested_name = cls.get("account_name", "Suspense")
        txn.ai_suggested_tax_type = cls.get("tax_type", "")
        txn.ai_confidence = cls.get("confidence", 1)
        txn.ai_reasoning = cls.get("reasoning", "")
        txn.from_learning = cls.get("from_learning", False)

        # Calculate GST
        tax_type = cls.get("tax_type", "")
        abs_amount = abs(txn.amount)
        if is_gst and tax_type in ("GST on Income", "GST on Expenses"):
            txn.gst_amount = (abs_amount / Decimal("11")).quantize(Decimal("0.01"))
            txn.net_amount = (abs_amount - txn.gst_amount).quantize(Decimal("0.01"))
        else:
            txn.gst_amount = Decimal("0.00")
            txn.net_amount = abs_amount

        # Auto-confirm if from learning with high confidence
        is_auto_confirmed = (
            cls.get("from_learning", False) and cls.get("confidence", 0) >= 5
        )
        if is_auto_confirmed:
            txn.is_confirmed = True
            txn.confirmed_code = txn.ai_suggested_code
            txn.confirmed_name = txn.ai_suggested_name
            txn.confirmed_tax_type = tax_type

        txn.save()

        classified_results.append({
            "txn_id": str(txn.pk),
            "code": txn.ai_suggested_code,
            "name": txn.ai_suggested_name,
            "tax_type": txn.ai_suggested_tax_type,
            "confidence": txn.ai_confidence,
            "reasoning": txn.ai_reasoning,
            "from_learning": txn.from_learning,
            "gst_amount": str(txn.gst_amount),
            "net_amount": str(txn.net_amount),
            "is_confirmed": txn.is_confirmed,
        })

    # Update job counts
    job.auto_coded_count = job.transactions.exclude(
        ai_confidence=0, ai_suggested_code=""
    ).count()
    job.confirmed_count = job.transactions.filter(is_confirmed=True).count()
    if job.confirmed_count > 0 and job.status == "awaiting_review":
        job.status = "in_progress"
    job.save()

    remaining = job.transactions.filter(
        ai_confidence=0, ai_suggested_code=""
    ).count()

    # Log activity when ALL classification is complete (remaining == 0)
    if remaining == 0:
        try:
            from core.models import ActivityLog
            client_name = job.entity.entity_name if job.entity else job.client_name
            ActivityLog.objects.create(
                user=request.user,
                event_type="classify_complete",
                title=f"AI classification complete: {client_name}",
                description=f"{job.total_transactions} transactions classified, {job.confirmed_count} auto-confirmed",
                url=f"/review/{job.pk}/",
            )
        except Exception:
            pass

    return JsonResponse({
        "status": "ok",
        "classified": classified_results,
        "remaining": remaining,
        "total": job.total_transactions,
        "auto_coded_count": job.auto_coded_count,
        "confirmed_count": job.confirmed_count,
    })



# ---------------------------------------------------------------------------
# Parse-only endpoint (returns JSON, does NOT save to DB)
# ---------------------------------------------------------------------------
@ratelimit(key='user', rate='10/m', method='POST')
@login_required
@require_POST
def parse_statement(request):
    """
    Parse a single bank statement file and return extracted data as JSON.
    Does NOT create ReviewJob or PendingTransaction records.
    Used by the preview flow: upload → preview → confirm.
    """
    import time
    import base64
    start_time = time.time()
    from datetime import datetime as dt
    from .pdf_parsers import extract_transactions_from_pdf_direct

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'status': 'error', 'message': 'No file provided'}, status=400)

    entity_id = request.POST.get('entity_id')
    period_start_str = request.POST.get('period_start', '')
    period_end_str = request.POST.get('period_end', '')

    # Parse period dates
    period_start_date = None
    period_end_date = None
    try:
        if period_start_str:
            period_start_date = dt.strptime(period_start_str, '%Y-%m-%d').date()
        if period_end_str:
            period_end_date = dt.strptime(period_end_str, '%Y-%m-%d').date()
    except ValueError:
        pass

    # Validate file size (20MB limit)
    if uploaded_file.size > 20 * 1024 * 1024:
        return JsonResponse(
            {"status": "error", "message": "File too large. Maximum size is 20MB."},
            status=400,
        )

    # Validate file type
    allowed_extensions = {".pdf", ".xlsx", ".xls", ".csv"}
    import os as _os
    file_ext = _os.path.splitext(uploaded_file.name)[1].lower()
    if file_ext not in allowed_extensions:
        return JsonResponse(
            {"status": "error", "message": f"Unsupported file type: {file_ext}. Allowed: PDF, Excel, CSV."},
            status=400,
        )

    content = uploaded_file.read()
    filename = uploaded_file.name
    logger.info(f'[parse-statement] Parsing: {filename} ({len(content)} bytes)')

    # Extract transactions using direct parser first, fall back to Claude Vision for scanned PDFs
    extracted = None
    used_vision = False
    try:
        if filename.lower().endswith('.pdf'):
            try:
                extracted = extract_transactions_from_pdf_direct(content, filename)
            except ValueError as ve:
                # Unsupported bank or scanned PDF — try Claude Vision OCR fallback
                logger.info(f'[parse-statement] Direct parse failed for {filename}: {ve}. Trying Claude Vision OCR...')
                try:
                    from .email_ingestion import extract_transactions_from_pdf as claude_extract
                    pdf_b64 = base64.b64encode(content).decode('utf-8')
                    extracted = claude_extract(pdf_b64, filename)
                    used_vision = True
                    logger.info(f'[parse-statement] Claude Vision OCR succeeded for {filename}')
                except Exception as vision_err:
                    logger.error(f'[parse-statement] Claude Vision OCR also failed for {filename}: {vision_err}')
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Could not parse {filename}. Direct parser: {ve}. Vision OCR: {vision_err}'
                    }, status=400)
        else:
            extracted = _parse_excel_bank_statement(content, filename)
    except Exception as exc:
        logger.error(f'[parse-statement] Extraction error for {filename}: {exc}', exc_info=True)
        return JsonResponse({'status': 'error', 'message': f'Extraction error: {exc}'}, status=400)

    if not extracted or not extracted.get('transactions'):
        return JsonResponse({'status': 'error', 'message': f'No transactions could be extracted from {filename}'}, status=400)

    transactions = extracted['transactions']
    total_before_filter = len(transactions)

    # Filter by period
    if period_start_date or period_end_date:
        filtered = []
        for txn in transactions:
            txn_date_str = txn.get('date', '')
            txn_date = None
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y', '%d %B %Y', '%m/%d/%Y'):
                try:
                    txn_date = dt.strptime(txn_date_str.strip(), fmt).date()
                    break
                except (ValueError, AttributeError):
                    continue
            if txn_date:
                if period_start_date and txn_date < period_start_date:
                    continue
                if period_end_date and txn_date > period_end_date:
                    continue
            filtered.append(txn)
        transactions = filtered

    if not transactions:
        return JsonResponse({'status': 'error', 'message': f'{filename}: No transactions within the selected period'}, status=400)

    # Normalize transaction amounts and compute running balance
    txn_list = []
    running_balance = float(extracted.get('opening_balance', 0))
    for txn in transactions:
        amount = float(txn.get('amount', 0))
        balance = txn.get('balance')
        if balance is not None:
            running_balance = float(balance)
        else:
            running_balance += amount
        txn_list.append({
            'date': txn.get('date', ''),
            'description': txn.get('description', ''),
            'amount': round(amount, 2),
            'balance': round(running_balance, 2),
        })

    # Auto-detect bank account info
    account_key = ''
    bsb = extracted.get('bsb', '')
    account_number = extracted.get('account_number', '')
    if bsb and account_number:
        account_key = f'{bsb}_{account_number}'
    elif account_number:
        account_key = account_number

    # Check if we already know this bank account
    bank_account_info = None
    if entity_id and (bsb or account_number):
        try:
            from core.models import BankAccount
            ba = BankAccount.objects.filter(
                entity_id=entity_id,
                bsb=bsb,
                account_number=account_number,
            ).first()
            if ba:
                bank_account_info = {
                    'id': str(ba.pk),
                    'nickname': ba.nickname,
                    'account_type': ba.account_type,
                    'tb_account_code': ba.tb_account_code,
                    'tb_account_name': ba.tb_account_name,
                }
        except Exception:
            pass

    elapsed = round(time.time() - start_time, 2)
    logger.info(f'[parse-statement] {filename}: {len(txn_list)} txns parsed in {elapsed}s (vision={used_vision})')

    response_data = {
        'status': 'success',
        'filename': filename,
        'bank': extracted.get('bank', 'Unknown'),
        'account_name': extracted.get('account_name', ''),
        'bsb': bsb,
        'account_number': account_number,
        'account_key': account_key,
        'opening_balance': float(extracted.get('opening_balance', 0)),
        'closing_balance': float(extracted.get('closing_balance', 0)),
        'period_start': extracted.get('period_start', ''),
        'period_end': extracted.get('period_end', ''),
        'transactions': txn_list,
        'total_before_filter': total_before_filter,
        'excluded': total_before_filter - len(txn_list),
        'bank_account_info': bank_account_info,
        'elapsed_seconds': elapsed,
        'used_vision_ocr': used_vision,
    }

    # Store parsed data in Django session (reliable fallback for sessionStorage)
    if request.POST.get('clear_session') == '1':
        request.session.pop('mcs_parsed_statements', None)

    parsed_list = request.session.get('mcs_parsed_statements', [])
    parsed_list.append(response_data)
    request.session['mcs_parsed_statements'] = parsed_list
    request.session.modified = True

    return JsonResponse(response_data)


# ---------------------------------------------------------------------------
# Upload Preview page
# ---------------------------------------------------------------------------
@login_required
def upload_preview(request):
    """
    Render the preview page. Transaction data is passed via Django session
    (primary, reliable) with sessionStorage as a browser-side fallback.
    """
    financial_year_id = request.GET.get('fy', '')
    entity_id = request.GET.get('entity', '')

    fy = None
    entity = None
    if financial_year_id:
        try:
            from core.models import FinancialYear
            fy = FinancialYear.objects.select_related('entity').get(pk=financial_year_id)
            entity = fy.entity
        except Exception:
            pass
    if not entity and entity_id:
        try:
            from core.models import Entity
            entity = Entity.objects.get(pk=entity_id)
        except Exception:
            pass

    # Read parsed data from Django session (stored by parse_statement)
    parsed_data = request.session.get('mcs_parsed_statements', [])
    # Don't clear yet — allow page refresh. Cleared on confirm_import.

    # Sort statements in chronological order by period_start date
    from datetime import datetime as _dt
    def _stmt_sort_key(stmt):
        """Return a date for sorting. Uses period_start, falling back to first transaction date."""
        for date_str in [stmt.get('period_start', ''), (stmt.get('transactions') or [{}])[0].get('date', '') if stmt.get('transactions') else '']:
            if not date_str:
                continue
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y', '%d %B %Y'):
                try:
                    return _dt.strptime(date_str.strip(), fmt).date()
                except (ValueError, AttributeError):
                    continue
        return _dt.max.date()  # Unknown dates go last

    parsed_data.sort(key=_stmt_sort_key)

    return render(request, 'review/upload_preview.html', {
        'fy': fy,
        'entity': entity,
        'parsed_data_json': json.dumps(parsed_data),
    })


# ---------------------------------------------------------------------------
# Confirm Import (saves verified transactions to DB)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def confirm_import(request):
    """
    Receive the verified/edited transaction data from the preview page
    and create ReviewJob + PendingTransaction records.
    """
    import json as json_mod
    from datetime import datetime as dt

    try:
        data = json_mod.loads(request.body)
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)

    financial_year_id = data.get('financial_year_id', '')
    entity_id = data.get('entity_id', '')
    statements = data.get('statements', [])

    if not statements:
        return JsonResponse({'status': 'error', 'message': 'No statements to import'}, status=400)

    # Validate: cap number of statements to prevent abuse
    if len(statements) > 50:
        return JsonResponse({'status': 'error', 'message': 'Too many statements (max 50)'}, status=400)

    entity = None
    is_gst = True
    client_name = 'Unknown'
    if entity_id:
        try:
            from core.models import Entity
            entity = Entity.objects.get(pk=entity_id)
            is_gst = entity.is_gst_registered
            client_name = entity.entity_name
        except Exception:
            pass

    # Validate financial year ownership if provided
    fy = None
    if financial_year_id:
        try:
            from core.models import FinancialYear
            fy = FinancialYear.objects.select_related('entity').get(pk=financial_year_id)
            # Ensure entity matches the financial year's entity
            if entity and fy.entity_id != entity.pk:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Entity does not match the selected financial year'
                }, status=400)
        except Exception:
            pass

    total_imported = 0
    job_ids = []
    validation_warnings = []

    for stmt in statements:
        filename = stmt.get('filename', 'Unknown')
        transactions = stmt.get('transactions', [])
        if not transactions:
            continue

        # Validate individual transactions
        valid_txns = []
        for txn in transactions:
            # Must have date and description
            txn_date = (txn.get('date') or '').strip()
            txn_desc = (txn.get('description') or '').strip()
            try:
                txn_amount = float(txn.get('amount', 0))
            except (ValueError, TypeError):
                continue

            if not txn_date or not txn_desc:
                continue

            # Validate date format (must be parseable)
            parsed_date = None
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y', '%d %B %Y'):
                try:
                    parsed_date = dt.strptime(txn_date, fmt).date()
                    break
                except (ValueError, AttributeError):
                    continue

            if not parsed_date:
                continue

            # Enforce: transaction must be within financial year period
            if fy:
                if parsed_date < fy.start_date or parsed_date > fy.end_date:
                    validation_warnings.append(
                        f'{filename}: Transaction on {txn_date} excluded — outside financial year period '
                        f'({fy.start_date.strftime("%d/%m/%Y")} to {fy.end_date.strftime("%d/%m/%Y")})'
                    )
                    continue  # Skip this transaction

            # Validate amount is not absurdly large (data integrity)
            if abs(txn_amount) > 999_999_999:
                continue

            valid_txns.append({
                'date': txn_date,
                'description': txn_desc,
                'amount': txn_amount,
            })

        transactions = valid_txns
        if not transactions:
            continue

        try:
            opening_balance = Decimal(str(stmt.get('opening_balance', 0)))
            closing_balance = Decimal(str(stmt.get('closing_balance', 0)))
        except Exception:
            opening_balance = Decimal('0')
            closing_balance = Decimal('0')
        bsb = stmt.get('bsb', '')
        account_number = stmt.get('account_number', '')
        account_name = stmt.get('account_name', '')
        bank = stmt.get('bank', '')
        account_nickname = stmt.get('account_nickname', '')
        account_type = stmt.get('account_type', 'cheque')

        # Auto-create or update BankAccount record
        if entity and (bsb or account_number):
            try:
                from core.models import BankAccount
                ba, created = BankAccount.objects.get_or_create(
                    entity=entity,
                    bsb=bsb,
                    account_number=account_number,
                    defaults={
                        'account_name': account_name,
                        'bank_name': bank,
                        'account_type': account_type,
                        'nickname': account_nickname,
                    }
                )
                if not created and not ba.account_name and account_name:
                    ba.account_name = account_name
                    ba.save(update_fields=['account_name'])
            except Exception as e:
                logger.error(f'[confirm-import] BankAccount error: {e}')

        # Create ReviewJob
        job = ReviewJob.objects.create(
            entity=entity,
            client_name=client_name,
            file_name=filename,
            submitted_by=request.user.get_full_name() or request.user.username,
            source='upload',
            total_transactions=len(transactions),
            auto_coded_count=0,
            flagged_count=len(transactions),
            confirmed_count=0,
            is_gst_registered=is_gst,
            bank_account_name=account_name,
            bsb=bsb,
            account_number=account_number,
            period_start=stmt.get('period_start', ''),
            period_end=stmt.get('period_end', ''),
            opening_balance=opening_balance,
            closing_balance=closing_balance,
        )
        job_ids.append(str(job.pk))

        # Bulk create transactions
        pending_objs = []
        for txn in transactions:
            amount = Decimal(str(txn.get('amount', 0)))
            pending_objs.append(PendingTransaction(
                job=job,
                date=txn.get('date', ''),
                description=txn.get('description', ''),
                amount=amount,
                gst_amount=Decimal('0.00'),
                net_amount=abs(amount),
                ai_suggested_code='',
                ai_suggested_name='',
                ai_suggested_tax_type='',
                ai_confidence=0,
                ai_reasoning='',
                from_learning=False,
                is_confirmed=False,
                confirmed_code='',
                confirmed_name='',
                confirmed_tax_type='',
            ))
        PendingTransaction.objects.bulk_create(pending_objs)
        total_imported += len(transactions)

        # Log activity
        ReviewActivity.objects.create(
            activity_type='new_statement',
            title='Bank statement imported (verified)',
            description=f'{client_name} — {len(transactions)} transactions from {filename}',
        )

        # Dashboard activity log
        try:
            from core.models import ActivityLog
            ActivityLog.objects.create(
                user=request.user,
                event_type='bank_upload',
                title=f'Bank statement imported: {filename}',
                description=f'{client_name} — {len(transactions)} transactions (verified & imported)',
                url=f'/review/{job.pk}/',
            )
        except Exception:
            pass

    # Build redirect URL
    redirect_url = '/'
    if financial_year_id:
        from django.urls import reverse
        try:
            redirect_url = reverse('core:financial_year_detail', kwargs={'pk': financial_year_id}) + '?tab=review'
        except Exception:
            redirect_url = f'/years/{financial_year_id}/?tab=review'

    # Clear session data now that import is complete
    request.session.pop('mcs_parsed_statements', None)
    request.session.modified = True

    logger.info(f'[confirm-import] {len(statements)} statements, {total_imported} transactions imported')

    return JsonResponse({
        'status': 'success',
        'total_statements': len(statements),
        'total_transactions': total_imported,
        'job_ids': job_ids,
        'redirect': redirect_url,
        'validation_warnings': validation_warnings,
    })
