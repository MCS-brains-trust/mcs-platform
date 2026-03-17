"""Views for Partnership Documents and Cross-Entity Documents (Engagement Letters)."""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from core.legal_doc_service import render_and_create_document, render_legal_document
from core.models import (
    EngagementLetterConfig,
    Entity,
    EntityOfficer,
    FinancialYear,
    LegalDocument,
    TrialBalanceLine,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 5.7 Partner Statement (one per active partner)
# ---------------------------------------------------------------------------
@login_required
def partner_statements(request, pk):
    """View and generate partner statements for a partnership FY."""
    fy = get_object_or_404(FinancialYear, pk=pk)
    entity = fy.entity

    if entity.entity_type != "partnership":
        return render(request, "core/compliance/partner_statements.html", {
            "fy": fy,
            "entity": entity,
            "error": "Partner statements are only available for partnership entities.",
        })

    partners = EntityOfficer.objects.filter(
        entity=entity,
        role="partner",
    ).order_by("name")

    # Get profit allocation from TB
    profit_lines = TrialBalanceLine.objects.filter(
        financial_year=fy,
    ).select_related("mapped_line_item")

    total_revenue = 0
    total_expenses = 0
    for line in profit_lines:
        balance = float(line.net_balance or line.balance or 0)
        acct_type = getattr(line.account, "account_type", "") if line.account else ""
        if acct_type in ("revenue", "income"):
            total_revenue += balance
        elif acct_type in ("expense", "cost_of_sales"):
            total_expenses += balance

    net_profit = total_revenue - total_expenses

    # Existing partner statements
    existing_docs = LegalDocument.objects.filter(
        financial_year=fy,
        document_type="partner_statement",
    )

    return render(request, "core/compliance/partner_statements.html", {
        "fy": fy,
        "entity": entity,
        "partners": partners,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "existing_docs": existing_docs,
    })


@login_required
@require_POST
def generate_partner_statements(request, pk):
    """Generate partner statements for all active partners."""
    fy = get_object_or_404(FinancialYear, pk=pk)
    entity = fy.entity

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)

    allocations = data.get("allocations", [])

    # Validate total = 100%
    total_pct = sum(float(a.get("percentage", 0)) for a in allocations)
    if abs(total_pct - 100) > 0.01:
        return JsonResponse({
            "status": "error",
            "error": f"Profit allocation must total 100%. Currently: {total_pct:.1f}%",
        }, status=400)

    docs_created = 0
    for alloc in allocations:
        partner_id = alloc.get("partner_id")
        percentage = float(alloc.get("percentage", 0))
        partner_share = float(alloc.get("partner_share", 0))

        try:
            partner = EntityOfficer.objects.get(pk=partner_id)
        except EntityOfficer.DoesNotExist:
            continue

        context = {
            "entity_name": entity.entity_name,
            "abn": entity.abn or "",
            "partner_name": partner.full_name,
            "partner_tfn": "",  # Not stored for privacy
            "financial_year_end": str(fy.end_date),
            "profit_share_percentage": percentage,
            "profit_share_amount": partner_share,
            "capital_account": {
                "opening": float(alloc.get("capital_opening", 0)),
                "drawings": float(alloc.get("drawings", 0)),
                "contributions": float(alloc.get("contributions", 0)),
                "profit_share": partner_share,
                "closing": (
                    float(alloc.get("capital_opening", 0))
                    + float(alloc.get("contributions", 0))
                    + partner_share
                    - float(alloc.get("drawings", 0))
                ),
            },
            "income_categories": alloc.get("income_categories", {}),
        }

        LegalDocument.objects.create(
            financial_year=fy,
            entity=entity,
            document_type="partner_statement",
            title=f"Partner Statement — {partner.full_name} — {entity.entity_name} {fy.end_date.year}",
            context_data=context,
            generated_by=request.user,
            status="generated",
        )
        docs_created += 1

    return JsonResponse({
        "status": "ok",
        "message": f"Generated {docs_created} partner statement(s).",
    })


# ---------------------------------------------------------------------------
# 5.8 Partnership Tax Summary (internal one-page summary)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def generate_partnership_tax_summary(request, pk):
    """Generate an internal partnership tax summary."""
    fy = get_object_or_404(FinancialYear, pk=pk)
    entity = fy.entity

    partners = EntityOfficer.objects.filter(entity=entity, role="partner")

    context = {
        "entity_name": entity.entity_name,
        "abn": entity.abn or "",
        "tfn": entity.tfn or "",
        "financial_year_end": str(fy.end_date),
        "partners": [{"name": p.name, "tfn": ""} for p in partners],
        "document_type": "internal_summary",
    }

    doc = LegalDocument.objects.create(
        financial_year=fy,
        entity=entity,
        document_type="partnership_tax_summary",
        title=f"Partnership Tax Summary — {entity.entity_name} — {fy.end_date.year}",
        context_data=context,
        generated_by=request.user,
        status="generated",
    )

    return JsonResponse({
        "status": "ok",
        "document_id": str(doc.pk),
        "message": "Partnership tax summary generated.",
    })


# ---------------------------------------------------------------------------
# 5.9 Client Engagement Letter (All entity types, APES 305)
# ---------------------------------------------------------------------------
@login_required
def engagement_letter_wizard(request, pk):
    """Engagement letter wizard — entity-level, APES 305 compliant."""
    entity = get_object_or_404(Entity, pk=pk)

    config, _ = EngagementLetterConfig.objects.get_or_create(entity=entity)
    service_options = _get_service_options(entity.entity_type)
    financial_years = entity.financial_years.all().order_by("-end_date")

    draft_id = request.GET.get("draft")
    draft_doc = None
    initial = {
        "services": config.services_engaged or [],
        "fee_amount": config.fee_amount,
        "fee_basis": config.fee_basis,
        "additional_terms": config.additional_terms,
        "date": "",
        "financial_year_id": str(config.last_generated_fy_id) if config.last_generated_fy_id else "",
    }

    if draft_id:
        draft_doc = get_object_or_404(
            LegalDocument,
            pk=draft_id,
            entity=entity,
            document_type="engagement_letter",
        )
        params = draft_doc.parameters or {}
        initial.update({
            "services": params.get("services", initial["services"]),
            "fee_amount": params.get("fee_amount", initial["fee_amount"]),
            "fee_basis": params.get("fee_basis", initial["fee_basis"]),
            "additional_terms": params.get("additional_terms", initial["additional_terms"]),
            "date": params.get("date", initial["date"]),
            "financial_year_id": str(draft_doc.financial_year_id) if draft_doc.financial_year_id else initial["financial_year_id"],
        })

    return render(request, "core/compliance/engagement_letter_wizard.html", {
        "entity": entity,
        "config": config,
        "service_options": service_options,
        "financial_years": financial_years,
        "draft_doc": draft_doc,
        "initial": initial,
    })


@login_required
@require_POST
def engagement_letter_generate(request, pk):
    """Generate or update an engagement letter draft for an entity."""
    entity = get_object_or_404(Entity, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)

    fy_id = data.get("financial_year_id")
    if not fy_id:
        return JsonResponse({"status": "error", "error": "Please select the financial year this engagement letter covers."}, status=400)

    fy = get_object_or_404(FinancialYear, pk=fy_id, entity=entity)

    config, _ = EngagementLetterConfig.objects.get_or_create(entity=entity)
    config.services_engaged = data.get("services", [])
    config.fee_amount = data.get("fee_amount") or None
    config.fee_basis = data.get("fee_basis", "fixed")
    config.additional_terms = data.get("additional_terms", "")
    config.last_generated_fy = fy
    config.save()

    signatories = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director", "director_shareholder", "trustee", "partner", "individual", "public_officer"],
    )

    address_parts = filter(None, [
        entity.address_line_1,
        entity.address_line_2,
        " ".join(filter(None, [entity.suburb, entity.state, entity.postcode])),
    ])
    registered_address = ", ".join(address_parts)

    if config.fee_amount:
        try:
            fee_display = f"${float(config.fee_amount):,.2f} + GST"
        except (ValueError, TypeError):
            fee_display = f"{config.fee_amount} + GST"
    else:
        fee_display = ""

    params = {
        "services": data.get("services", []),
        "fee_amount": data.get("fee_amount") or "",
        "fee_basis": data.get("fee_basis", "fixed"),
        "additional_terms": data.get("additional_terms", ""),
        "date": data.get("date", ""),
        "financial_year_id": str(fy.pk),
    }

    context = {
        "entity_name": entity.entity_name,
        "entity_type": entity.entity_type,
        "abn": entity.abn or "",
        "acn": entity.acn or "",
        "registered_address": registered_address,
        "services_engaged": config.services_engaged,
        "fee_amount": fee_display,
        "fee_basis": config.fee_basis,
        "additional_terms": config.additional_terms,
        "signatories": [{"name": s.full_name, "role": getattr(s, "display_role", s.get_role_display())} for s in signatories],
        "date": data.get("date", ""),
    }

    template = LegalDocumentTemplate.objects.filter(
        document_type=LegalDocumentTemplate.DocumentType.ENGAGEMENT_LETTER,
        is_active=True,
    ).first()
    if not template:
        return JsonResponse({"status": "error", "error": "No active engagement letter template is configured."}, status=400)

    draft_id = data.get("draft_id")
    if draft_id:
        doc = get_object_or_404(
            LegalDocument,
            pk=draft_id,
            entity=entity,
            document_type="engagement_letter",
        )
        doc.financial_year = fy
        doc.template = template
        doc.title = f"Engagement Letter — {entity.entity_name} — {fy.year_label}"
        doc.parameters = params
        doc.context_data = context
        doc.status = LegalDocument.Status.DRAFT
        doc.generated_by = request.user
        doc.save(update_fields=["financial_year", "template", "title", "parameters", "context_data", "status", "generated_by"])
        result = render_legal_document(doc.pk)
        if result.get("status") != "ok":
            return JsonResponse(result, status=400)
        document_id = str(doc.pk)
    else:
        result = render_and_create_document(
            entity=entity,
            financial_year=fy,
            template=template,
            doc_type=LegalDocumentTemplate.DocumentType.ENGAGEMENT_LETTER,
            context=context,
            params=params,
            user=request.user,
            disclaimer_acknowledged=True,
        )
        if result.get("status") != "ok":
            return JsonResponse(result, status=400)
        document_id = result.get("document_id")
        doc = LegalDocument.objects.get(pk=document_id)
        doc.title = f"Engagement Letter — {entity.entity_name} — {fy.year_label}"
        doc.context_data = context
        doc.status = LegalDocument.Status.DRAFT
        doc.save(update_fields=["title", "context_data", "status"])

    return JsonResponse({
        "status": "ok",
        "document_id": document_id,
        "message": "Engagement letter saved. It is now available in the Engagement Letters tab for editing and download.",
        "redirect_url": f"/core/entities/{entity.pk}/?tab=engagement_letters",
        "docx_url": result.get("docx_url"),
        "pdf_url": result.get("pdf_url"),
    })


def _get_service_options(entity_type):
    """Return service options based on entity type."""
    base_services = [
        ("tax_return", "Tax Return Preparation"),
        ("financial_statements", "Financial Statements"),
        ("bas", "BAS Preparation & Lodgement"),
        ("bookkeeping", "Bookkeeping"),
        ("tax_planning", "Tax Planning & Advisory"),
        ("payroll", "Payroll Services"),
    ]

    if entity_type == "company":
        base_services.extend([
            ("asic_compliance", "ASIC Annual Review & Compliance"),
            ("dividend_management", "Dividend Management"),
            ("directors_report", "Director's Report"),
        ])
    elif entity_type == "trust":
        base_services.extend([
            ("trust_distribution", "Trust Distribution Planning"),
            ("trust_deed_review", "Trust Deed Review"),
        ])
    elif entity_type == "partnership":
        base_services.extend([
            ("partner_statements", "Partner Statements"),
            ("partnership_agreement_review", "Partnership Agreement Review"),
        ])
    elif entity_type == "smsf":
        base_services.extend([
            ("smsf_audit", "SMSF Audit Coordination"),
            ("smsf_compliance", "SMSF Compliance"),
            ("member_statements", "Member Statements"),
        ])

    return base_services
