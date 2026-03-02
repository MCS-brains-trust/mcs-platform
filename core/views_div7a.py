"""
Views for Division 7A Detection Module.

Provides:
  - div7a_dashboard: Overview of Div 7A assessment for a financial year
  - div7a_run_assessment: Trigger a Div 7A assessment manually
  - div7a_compliance_list: List compliance records for an entity
  - div7a_compliance_create: Create a new compliance record
  - div7a_compliance_edit: Edit an existing compliance record
  - div7a_assessment_api: JSON API for assessment data (HTMX/polling)
"""

import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from core.models import (
    Div7AAssessment,
    Div7ACompliance,
    Entity,
    FinancialYear,
    LegalDocument,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


def _get_fy(request, pk):
    """Get FinancialYear with permission check."""
    from core.views import get_financial_year_for_user
    return get_financial_year_for_user(request, pk)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
def div7a_dashboard(request, pk):
    """
    Div 7A assessment dashboard for a financial year.
    Shows the consolidated assessment card, compliance records,
    and action buttons.
    """
    fy = _get_fy(request, pk)
    entity = fy.entity

    # Only show for company entities
    if entity.entity_type != "company":
        return render(request, "core/div7a/not_applicable.html", {
            "financial_year": fy,
            "entity": entity,
            "reason": "Division 7A only applies to company entities.",
        })

    # Get assessment
    assessment = None
    try:
        assessment = Div7AAssessment.objects.get(financial_year=fy)
    except Div7AAssessment.DoesNotExist:
        pass

    # Get compliance records
    compliance_records = Div7ACompliance.objects.filter(entity=entity)

    # Get existing Div 7A loan agreements
    agreements = LegalDocument.objects.filter(
        entity=entity,
        document_type="div7a_loan_agreement",
    ).order_by("-created_at")

    # Build context
    context = {
        "financial_year": fy,
        "entity": entity,
        "assessment": assessment,
        "compliance_records": compliance_records,
        "agreements": agreements,
        "has_assessment": assessment is not None,
    }

    # Add severity badge info
    if assessment:
        context["severity_class"] = {
            "CRITICAL": "danger",
            "ADVISORY": "warning",
            "CLEAR": "success",
        }.get(assessment.overall_severity, "secondary")

        context["severity_icon"] = {
            "CRITICAL": "bi-exclamation-triangle-fill",
            "ADVISORY": "bi-exclamation-circle-fill",
            "CLEAR": "bi-check-circle-fill",
        }.get(assessment.overall_severity, "bi-question-circle")

        # Parse finding lines from the eva_finding
        if assessment.eva_finding:
            context["finding_explanation"] = assessment.eva_finding.plain_english_explanation
            context["finding_recommendation"] = assessment.eva_finding.recommendation
            context["finding_checklist"] = assessment.eva_finding.remediation_synthesis

    return render(request, "core/div7a/dashboard.html", context)


# ---------------------------------------------------------------------------
# Run Assessment
# ---------------------------------------------------------------------------
@login_required
@require_POST
def div7a_run_assessment(request, pk):
    """Manually trigger a Div 7A assessment."""
    fy = _get_fy(request, pk)

    if fy.entity.entity_type != "company":
        return JsonResponse({"error": "Div 7A only applies to company entities"}, status=400)

    try:
        from core.tasks import div7a_assessment
        div7a_assessment.delay(str(fy.pk), "manual")
        return JsonResponse({"status": "queued", "message": "Div 7A assessment queued."})
    except Exception:
        # Celery not running — run synchronously
        from core.eva_div7a import run_div7a_assessment
        result = run_div7a_assessment(str(fy.pk), "manual")
        return JsonResponse({"status": "complete", "result": result})


# ---------------------------------------------------------------------------
# Assessment API (for HTMX polling)
# ---------------------------------------------------------------------------
@login_required
@require_GET
def div7a_assessment_api(request, pk):
    """JSON API returning the current Div 7A assessment data."""
    fy = _get_fy(request, pk)

    try:
        assessment = Div7AAssessment.objects.get(financial_year=fy)
        data = {
            "exists": True,
            "overall_severity": assessment.overall_severity,
            "total_exposure": str(assessment.total_exposure),
            "direct_loan_balance": str(assessment.direct_loan_balance),
            "upe_exposure": str(assessment.upe_exposure),
            "s109e_payments": str(assessment.s109e_payments),
            "has_complying_agreement": assessment.has_complying_agreement,
            "agreement_covers_balance": assessment.agreement_covers_balance,
            "interest_compliant": assessment.interest_compliant,
            "expected_interest": str(assessment.expected_interest),
            "recorded_interest": str(assessment.recorded_interest),
            "expected_myr": str(assessment.expected_myr),
            "actual_repayments": str(assessment.actual_repayments) if assessment.actual_repayments else None,
            "myr_compliant": assessment.myr_compliant,
            "escalation_required": assessment.escalation_required,
            "rules_fired": assessment.rules_fired,
            "assessed_at": assessment.assessed_at.isoformat(),
            "direct_loan_accounts": assessment.direct_loan_accounts,
            "upe_details": assessment.upe_details,
            "s109e_details": assessment.s109e_details,
        }
        return JsonResponse(data)
    except Div7AAssessment.DoesNotExist:
        return JsonResponse({"exists": False})


# ---------------------------------------------------------------------------
# Compliance Records
# ---------------------------------------------------------------------------
@login_required
def div7a_compliance_list(request, pk):
    """List Div 7A compliance records for an entity."""
    entity = get_object_or_404(Entity, pk=pk)
    records = Div7ACompliance.objects.filter(entity=entity)

    return render(request, "core/div7a/compliance_list.html", {
        "entity": entity,
        "records": records,
    })


@login_required
def div7a_compliance_create(request, pk):
    """Create a new Div 7A compliance record."""
    fy = _get_fy(request, pk)
    entity = fy.entity

    if request.method == "POST":
        try:
            record = Div7ACompliance.objects.create(
                entity=entity,
                borrower_name=request.POST.get("borrower_name", ""),
                loan_amount=Decimal(request.POST.get("loan_amount", "0")),
                loan_start_date=request.POST.get("loan_start_date"),
                loan_start_year=int(request.POST.get("loan_start_year", "2025")),
                loan_term=int(request.POST.get("loan_term", "7")),
                is_secured=request.POST.get("is_secured") == "on",
                status=request.POST.get("status", "PENDING"),
                notes=request.POST.get("notes", ""),
            )

            # Link to agreement if provided
            agreement_id = request.POST.get("agreement_document")
            if agreement_id:
                try:
                    record.agreement_document = LegalDocument.objects.get(pk=agreement_id)
                    record.save(update_fields=["agreement_document"])
                except LegalDocument.DoesNotExist:
                    pass

            # Link borrower entity if provided
            borrower_entity_id = request.POST.get("borrower_entity")
            if borrower_entity_id:
                try:
                    record.borrower_entity = Entity.objects.get(pk=borrower_entity_id)
                    record.save(update_fields=["borrower_entity"])
                except Entity.DoesNotExist:
                    pass

            return redirect("core:div7a_dashboard", pk=fy.pk)
        except (ValueError, TypeError) as e:
            return render(request, "core/div7a/compliance_form.html", {
                "financial_year": fy,
                "entity": entity,
                "error": str(e),
            })

    # GET: show form
    agreements = LegalDocument.objects.filter(
        entity=entity,
        document_type="div7a_loan_agreement",
    )

    # Get related entities for borrower dropdown
    from core.models import EntityRelationship
    related_entities = Entity.objects.filter(
        pk__in=EntityRelationship.objects.filter(
            from_entity=entity,
        ).values_list("to_entity_id", flat=True),
    )

    return render(request, "core/div7a/compliance_form.html", {
        "financial_year": fy,
        "entity": entity,
        "agreements": agreements,
        "related_entities": related_entities,
    })


@login_required
def div7a_compliance_edit(request, pk):
    """Edit an existing Div 7A compliance record."""
    record = get_object_or_404(Div7ACompliance, pk=pk)
    entity = record.entity

    # Find the current FY for breadcrumb
    fy = FinancialYear.objects.filter(entity=entity).order_by("-end_date").first()

    if request.method == "POST":
        record.borrower_name = request.POST.get("borrower_name", record.borrower_name)
        record.loan_amount = Decimal(request.POST.get("loan_amount", str(record.loan_amount)))
        record.loan_start_date = request.POST.get("loan_start_date", record.loan_start_date)
        record.loan_start_year = int(request.POST.get("loan_start_year", record.loan_start_year))
        record.loan_term = int(request.POST.get("loan_term", record.loan_term))
        record.is_secured = request.POST.get("is_secured") == "on"
        record.status = request.POST.get("status", record.status)
        record.notes = request.POST.get("notes", record.notes)

        agreement_id = request.POST.get("agreement_document")
        if agreement_id:
            try:
                record.agreement_document = LegalDocument.objects.get(pk=agreement_id)
            except LegalDocument.DoesNotExist:
                pass
        else:
            record.agreement_document = None

        record.save()

        if fy:
            return redirect("core:div7a_dashboard", pk=fy.pk)
        return redirect("core:entity_detail", pk=entity.pk)

    # GET: show form
    agreements = LegalDocument.objects.filter(
        entity=entity,
        document_type="div7a_loan_agreement",
    )

    from core.models import EntityRelationship
    related_entities = Entity.objects.filter(
        pk__in=EntityRelationship.objects.filter(
            from_entity=entity,
        ).values_list("to_entity_id", flat=True),
    )

    return render(request, "core/div7a/compliance_form.html", {
        "financial_year": fy,
        "entity": entity,
        "record": record,
        "agreements": agreements,
        "related_entities": related_entities,
        "is_edit": True,
    })
