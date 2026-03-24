"""
StatementHub — Family Trust Election (FTE) Document Views

Provides an interactive form for creating and editing a Family Trust Election
internal working document.  Entity data (trust name, ABN/TFN, trustee, deed
date, etc.) is auto-filled from the Entity record; the accountant completes
the remaining fields and ticks the checklist items.
"""
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import Entity, FamilyTrustElectionDocument, FinancialYear
from core.views import _log_action

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _get_entity_for_user(request, entity_pk):
    """Return the entity, checking the user has access."""
    return get_object_or_404(Entity, pk=entity_pk)


# ---------------------------------------------------------------------------
# Main view: create / edit a Family Trust Election document
# ---------------------------------------------------------------------------
@login_required
def family_trust_election(request, entity_pk, doc_pk=None):
    """
    GET  — Render the FTE form pre-filled with entity data.
           If doc_pk is given, load an existing document for editing.
    POST — Save (create or update) the document.
    """
    entity = _get_entity_for_user(request, entity_pk)

    # Optional: load existing document
    doc = None
    if doc_pk:
        doc = get_object_or_404(FamilyTrustElectionDocument, pk=doc_pk, entity=entity)

    # Build financial year choices for this entity
    financial_years = entity.financial_years.order_by("-end_date")

    if request.method == "POST":
        data = request.POST

        # Resolve financial year
        fy_id = data.get("financial_year_id") or ""
        financial_year = None
        if fy_id:
            try:
                financial_year = FinancialYear.objects.get(pk=fy_id, entity=entity)
            except FinancialYear.DoesNotExist:
                pass

        if doc is None:
            doc = FamilyTrustElectionDocument(entity=entity, created_by=request.user)

        doc.financial_year = financial_year
        doc.election_type = data.get("election_type", "")
        doc.election_type_other = data.get("election_type_other", "")
        doc.income_year = data.get("income_year", "")
        doc.reason_for_election = data.get("reason_for_election", "")

        # Section 3
        doc.proposed_test_individual = data.get("proposed_test_individual", "")
        doc.test_individual_relationship = data.get("test_individual_relationship", "")
        doc.spouse_details = data.get("spouse_details", "")
        doc.expected_beneficiaries = data.get("expected_beneficiaries", "")
        doc.non_family_distributions = data.get("non_family_distributions", "")
        doc.non_family_distribution_details = data.get("non_family_distribution_details", "")

        # Section 4 — checklist (yes/no/na)
        doc.checklist_franked_distributions = data.get("checklist_franked_distributions", "")
        doc.checklist_deed_permits_distribution = data.get("checklist_deed_permits_distribution", "")
        doc.checklist_beneficiaries_within_family = data.get("checklist_beneficiaries_within_family", "")
        doc.checklist_no_excluded_distributions = data.get("checklist_no_excluded_distributions", "")
        doc.checklist_bucket_company_within_group = data.get("checklist_bucket_company_within_group", "")
        doc.checklist_franking_credit_streaming = data.get("checklist_franking_credit_streaming", "")
        doc.checklist_prior_elections_checked = data.get("checklist_prior_elections_checked", "")

        # Section 5
        date_raw = data.get("date_first_franked_dividend", "")
        doc.date_first_franked_dividend = date_raw if date_raw else None
        doc.distribution_minutes_prepared_by = data.get("distribution_minutes_prepared_by", "")
        doc.tax_return_prepared_by = data.get("tax_return_prepared_by", "")
        doc.election_lodgment_year_ended = data.get("election_lodgment_year_ended", "")
        doc.further_action_required = data.get("further_action_required", "")

        # Section 6
        doc.risk_notes = data.get("risk_notes", "")
        doc.deed_legal_issues = data.get("deed_legal_issues", "")
        doc.return_disclosure_references = data.get("return_disclosure_references", "")

        # Section 7 — adviser completion checklist (boolean checkboxes)
        doc.adv_trust_deed_reviewed = "adv_trust_deed_reviewed" in data
        doc.adv_election_year_confirmed = "adv_election_year_confirmed" in data
        doc.adv_test_individual_confirmed = "adv_test_individual_confirmed" in data
        doc.adv_family_group_verified = "adv_family_group_verified" in data
        doc.adv_iee_considered = "adv_iee_considered" in data
        doc.adv_workpaper_references_saved = "adv_workpaper_references_saved" in data
        doc.adv_client_authority_retained = "adv_client_authority_retained" in data
        doc.adv_reviewer_signoff = "adv_reviewer_signoff" in data

        doc.last_saved_by = request.user
        doc.save()

        action_label = "updated" if doc_pk else "created"
        _log_action(
            request, "generate",
            f"Family Trust Election document {action_label} for {entity.entity_name}",
            doc.financial_year,
        )
        messages.success(request, "Family Trust Election document saved successfully.")
        return redirect("core:family_trust_election_edit", entity_pk=entity.pk, doc_pk=doc.pk)

    # ── GET ────────────────────────────────────────────────────────────────
    # Auto-fill from entity
    prefill = {
        "trust_name": entity.entity_name,
        "trust_abn": entity.abn or "",
        "trust_tfn": entity.tfn or "",
        "trustee_name": entity.trustee_name or "",
        "deed_date": entity.deed_date.strftime("%d/%m/%Y") if entity.deed_date else "",
        "primary_adviser": (
            entity.primary_accountant.get_full_name()
            if entity.primary_accountant else ""
        ),
        "client_contact": entity.contact_email or "",
    }

    # Existing documents for this entity (for the sidebar list)
    existing_docs = entity.fte_documents.select_related("financial_year").order_by("-created_at")

    # Section 4 checklist items
    checklist_items = [
        {
            "field": "checklist_franked_distributions",
            "label": "Does the trust expect to receive franked dividends or other income requiring FTE access?",
            "value": getattr(doc, "checklist_franked_distributions", "") if doc else "",
        },
        {
            "field": "checklist_deed_permits_distribution",
            "label": "Does the trust deed permit distributions to all proposed beneficiaries?",
            "value": getattr(doc, "checklist_deed_permits_distribution", "") if doc else "",
        },
        {
            "field": "checklist_beneficiaries_within_family",
            "label": "Are all expected beneficiaries within the family group of the test individual?",
            "value": getattr(doc, "checklist_beneficiaries_within_family", "") if doc else "",
        },
        {
            "field": "checklist_no_excluded_distributions",
            "label": "Are there no distributions to excluded entities (s272-70 ITAA 1997)?",
            "value": getattr(doc, "checklist_no_excluded_distributions", "") if doc else "",
        },
        {
            "field": "checklist_bucket_company_within_group",
            "label": "If a bucket company is used, is it within the family group or has an IEE been made?",
            "value": getattr(doc, "checklist_bucket_company_within_group", "") if doc else "",
        },
        {
            "field": "checklist_franking_credit_streaming",
            "label": "Has franking credit streaming been considered and documented?",
            "value": getattr(doc, "checklist_franking_credit_streaming", "") if doc else "",
        },
        {
            "field": "checklist_prior_elections_checked",
            "label": "Have prior-year elections and ATO records been checked for consistency?",
            "value": getattr(doc, "checklist_prior_elections_checked", "") if doc else "",
        },
    ]

    # Section 7 adviser completion checklist
    adviser_checklist = [
        {
            "field": "adv_trust_deed_reviewed",
            "label": "Trust deed reviewed and distribution powers confirmed",
            "value": getattr(doc, "adv_trust_deed_reviewed", False) if doc else False,
        },
        {
            "field": "adv_election_year_confirmed",
            "label": "Election year confirmed as the first income year FTE is required",
            "value": getattr(doc, "adv_election_year_confirmed", False) if doc else False,
        },
        {
            "field": "adv_test_individual_confirmed",
            "label": "Test individual identified and relationship documented",
            "value": getattr(doc, "adv_test_individual_confirmed", False) if doc else False,
        },
        {
            "field": "adv_family_group_verified",
            "label": "Family group verified — all beneficiaries are within the group",
            "value": getattr(doc, "adv_family_group_verified", False) if doc else False,
        },
        {
            "field": "adv_iee_considered",
            "label": "IEE requirement considered for interposed entities (e.g. bucket company)",
            "value": getattr(doc, "adv_iee_considered", False) if doc else False,
        },
        {
            "field": "adv_workpaper_references_saved",
            "label": "Workpaper references saved and cross-referenced to return",
            "value": getattr(doc, "adv_workpaper_references_saved", False) if doc else False,
        },
        {
            "field": "adv_client_authority_retained",
            "label": "Client authority / instruction retained on file",
            "value": getattr(doc, "adv_client_authority_retained", False) if doc else False,
        },
        {
            "field": "adv_reviewer_signoff",
            "label": "Reviewed and signed off by senior accountant or partner",
            "value": getattr(doc, "adv_reviewer_signoff", False) if doc else False,
        },
    ]

    return render(request, "core/compliance/family_trust_election.html", {
        "entity": entity,
        "doc": doc,
        "prefill": prefill,
        "financial_years": financial_years,
        "existing_docs": existing_docs,
        "is_new": doc is None,
        "checklist_items": checklist_items,
        "adviser_checklist": adviser_checklist,
    })


# ---------------------------------------------------------------------------
# HTMX / API: toggle a single adviser checklist item
# ---------------------------------------------------------------------------
@login_required
@require_POST
def fte_toggle_checklist(request, doc_pk):
    """
    POST /api/fte/<doc_pk>/toggle-checklist/
    Body: { "field": "adv_trust_deed_reviewed", "value": true }
    Toggles a single boolean checklist field and returns the new state.
    """
    doc = get_object_or_404(FamilyTrustElectionDocument, pk=doc_pk)
    entity = doc.entity

    ALLOWED_FIELDS = {
        "adv_trust_deed_reviewed",
        "adv_election_year_confirmed",
        "adv_test_individual_confirmed",
        "adv_family_group_verified",
        "adv_iee_considered",
        "adv_workpaper_references_saved",
        "adv_client_authority_retained",
        "adv_reviewer_signoff",
    }

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    field = data.get("field")
    if field not in ALLOWED_FIELDS:
        return JsonResponse({"error": "Invalid field"}, status=400)

    new_value = bool(data.get("value", False))
    setattr(doc, field, new_value)
    doc.last_saved_by = request.user
    doc.save(update_fields=[field, "last_saved_by", "updated_at"])

    return JsonResponse({"status": "ok", "field": field, "value": new_value})


# ---------------------------------------------------------------------------
# Delete a document
# ---------------------------------------------------------------------------
@login_required
@require_POST
def fte_delete(request, doc_pk):
    """POST /api/fte/<doc_pk>/delete/"""
    doc = get_object_or_404(FamilyTrustElectionDocument, pk=doc_pk)
    entity = doc.entity
    doc.delete()
    messages.success(request, "Family Trust Election document deleted.")
    return redirect("core:family_trust_election_new", entity_pk=entity.pk)
