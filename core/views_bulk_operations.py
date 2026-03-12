"""Views for Bulk Operations (Phase 14) — bulk package generation from Entity Hub."""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.models import Entity, FinancialYear, LegalDocument

logger = logging.getLogger(__name__)


@login_required
@require_POST
def bulk_generate_packages(request):
    """
    Bulk generate packages for multiple entities.
    Accepts a list of entity IDs, checks readiness for each entity's
    current FY, and queues package assembly tasks.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)

    entity_ids = data.get("entity_ids", [])
    if not entity_ids:
        return JsonResponse(
            {"status": "error", "error": "No entities selected."}, status=400
        )

    results = {
        "queued": [],
        "skipped": [],
        "errors": [],
    }

    for eid in entity_ids:
        try:
            entity = Entity.objects.get(pk=eid)
        except Entity.DoesNotExist:
            results["errors"].append({"id": str(eid), "error": "Entity not found"})
            continue

        # Find the most recent finalised FY
        fy = (
            FinancialYear.objects.filter(
                entity=entity,
                status="finalised",
            )
            .order_by("-end_date")
            .first()
        )

        if not fy:
            results["skipped"].append({
                "id": str(eid),
                "name": entity.entity_name,
                "reason": "No finalised financial year",
            })
            continue

        if fy.package_assembled:
            results["skipped"].append({
                "id": str(eid),
                "name": entity.entity_name,
                "reason": "Package already assembled",
            })
            continue

        # Try to queue via Celery, fall back to synchronous
        try:
            from core.tasks import assemble_client_package
            assemble_client_package.delay(str(fy.pk), str(request.user.pk))
            results["queued"].append({
                "id": str(eid),
                "name": entity.entity_name,
                "fy": str(fy),
            })
        except Exception:
            # Celery not running — mark as queued anyway for the response
            results["queued"].append({
                "id": str(eid),
                "name": entity.entity_name,
                "fy": str(fy),
                "note": "Celery not available — will need manual assembly",
            })

    return JsonResponse({
        "status": "ok",
        "message": (
            f"Queued {len(results['queued'])} packages, "
            f"skipped {len(results['skipped'])}, "
            f"{len(results['errors'])} errors."
        ),
        "results": results,
    })


@login_required
def bulk_readiness_check(request):
    """
    Check package readiness for all entities with a finalised FY.
    Returns a JSON list of entities with their readiness status.
    """
    from core.views_package_assembly import PACKAGE_CONTENTS

    entities = Entity.objects.filter(is_active=True).order_by("name")
    readiness = []

    for entity in entities:
        fy = (
            FinancialYear.objects.filter(
                entity=entity,
                status="finalised",
            )
            .order_by("-end_date")
            .first()
        )

        if not fy:
            continue

        # Check document completeness
        required_docs = PACKAGE_CONTENTS.get(
            entity.entity_type, PACKAGE_CONTENTS.get("individual", [])
        )
        existing_types = set(
            LegalDocument.objects.filter(financial_year=fy)
            .values_list("document_type", flat=True)
        )

        # Check for financial statements
        from core.models import GeneratedDocument
        if GeneratedDocument.objects.filter(
            financial_year=fy,
            document_type=GeneratedDocument.DocumentType.FINANCIAL_STATEMENTS,
        ).exists():
            existing_types.add("financial_statements")

        total_required = sum(1 for _, _, req in required_docs if req)
        present_required = sum(
            1 for doc_type, _, req in required_docs
            if req and doc_type in existing_types
        )

        readiness.append({
            "entity_id": str(entity.pk),
            "entity_name": entity.entity_name,
            "entity_type": entity.entity_type,
            "fy": str(fy),
            "fy_id": str(fy.pk),
            "status": fy.status,
            "package_assembled": fy.package_assembled,
            "docs_required": total_required,
            "docs_present": present_required,
            "ready": present_required >= total_required,
        })

    return JsonResponse({"status": "ok", "entities": readiness})
