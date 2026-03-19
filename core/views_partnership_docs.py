"""Views for Partnership Documents and Cross-Entity Documents (Engagement Letters)."""
import json
import logging
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from django.core.files.base import ContentFile
from core.legal_doc_service import render_and_create_document, render_legal_document
from core.models import (
    EngagementLetter,
    EngagementLetterConfig,
    Entity,
    EntityOfficer,
    FinancialYear,
    LegalDocument,
    LegalDocumentTemplate,
    TrialBalanceLine,
)

logger = logging.getLogger(__name__)


def _build_next_financial_year_option(entity, financial_years):
    if financial_years:
        latest_fy = max(financial_years, key=lambda fy: fy.end_date)
        start_date = latest_fy.end_date + timedelta(days=1)
        duration_days = (latest_fy.end_date - latest_fy.start_date).days
        end_date = start_date + timedelta(days=duration_days)
        year_label = str(end_date.year)
        prior_year_id = latest_fy.pk
    else:
        return None
    return {
        "pk": f"future:{start_date.isoformat()}:{end_date.isoformat()}",
        "year_label": year_label,
        "start_date": start_date,
        "end_date": end_date,
        "is_future_option": True,
        "prior_year_id": str(prior_year_id),
    }


def _get_or_create_selected_financial_year(entity, fy_id):
    if fy_id.startswith("future:"):
        _, start_raw, end_raw = fy_id.split(":", 2)
        start_date = date.fromisoformat(start_raw)
        end_date = date.fromisoformat(end_raw)
        prior_year = entity.financial_years.filter(end_date=start_date - timedelta(days=1)).order_by("-end_date").first()
        fy, _ = FinancialYear.objects.get_or_create(
            entity=entity,
            start_date=start_date,
            end_date=end_date,
            defaults={
                "year_label": str(end_date.year),
                "period_type": FinancialYear.PeriodType.ANNUAL,
                "status": FinancialYear.Status.DRAFT,
                "prior_year": prior_year,
            },
        )
        return fy
    return get_object_or_404(FinancialYear, pk=fy_id, entity=entity)


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
    all_financial_years = list(entity.financial_years.all().order_by("end_date"))
    selectable_financial_years = [fy for fy in all_financial_years if fy.status != "finalised"]
    next_financial_year_option = _build_next_financial_year_option(entity, all_financial_years)
    financial_years = list(selectable_financial_years)
    if next_financial_year_option:
        financial_years.append(next_financial_year_option)
    elif not financial_years:
        financial_years = list(all_financial_years)
    default_financial_year = next_financial_year_option or (financial_years[0] if financial_years else None)
    draft_id = request.GET.get("draft")
    draft_doc = None
    initial = {
        "services": config.services_engaged or [],
        "fee_amount": config.fee_amount,
        "fee_basis": config.fee_basis,
        "additional_terms": config.additional_terms,
        "date": "",
        "financial_year_id": str(config.last_generated_fy_id) if config.last_generated_fy_id else (
            str(default_financial_year["pk"] if isinstance(default_financial_year, dict) else default_financial_year.pk)
            if default_financial_year else ""
        ),
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

    fy = _get_or_create_selected_financial_year(entity, fy_id)
    if fy.status == "finalised":
        return JsonResponse({
            "status": "error",
            "error": "Engagement letters can only be created for a non-finalised financial year. Please choose the next open year.",
        }, status=400)

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

    # Enrich with DocumentContextBuilder (practice_* namespace + Jinja2 filters)
    try:
        from core.document_context_builder import DocumentContextBuilder
        _wizard = {
            "services": data.get("services", []),
            "fee_amount": data.get("fee_amount", ""),
            "fee_basis": data.get("fee_basis", "fixed"),
            "additional_terms": data.get("additional_terms", ""),
            "date": data.get("date", ""),
        }
        dcb = DocumentContextBuilder(entity, financial_year=fy, wizard_data=_wizard)
        enriched = dcb.build("engagement_letter")
        for k, v in enriched.items():
            if k not in context or k.startswith("practice_"):
                context[k] = v
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning("DCB enrichment skipped for engagement_letter: %s", _e)

    template = LegalDocumentTemplate.objects.filter(
        document_type=LegalDocumentTemplate.DocumentType.ENGAGEMENT_LETTER,
        is_active=True,
    ).first()
    if not template:
        return JsonResponse({
            "status": "error",
            "error": "No active engagement letter template is configured. Please activate a Client Engagement Letter template in Document Templates before saving.",
        }, status=400)

    draft_id = data.get("draft_id")
    if draft_id:
        # Explicit draft_id supplied — update that specific record
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
        try:
            render_result = render_legal_document(doc.pk)
        except Exception as exc:
            logger.exception("Engagement letter draft re-render failed: %s", exc)
            return JsonResponse({
                "status": "error",
                "error": f"Could not save the engagement letter draft: {exc}",
            }, status=400)
        if render_result.get("status") != "ok":
            return JsonResponse(render_result, status=400)
        document_id = str(doc.pk)
        docx_url = doc.generated_file.url if doc.generated_file else None
        pdf_url = doc.pdf_file.url if doc.pdf_file else None
    else:
        # No draft_id — render_and_create_document will upsert (update existing draft
        # for same entity + financial_year + doc_type, or create a new one).
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
        docx_url = result.get("docx_url")
        pdf_url = result.get("pdf_url")

    # Auto-satisfy roll-forward gate
    _auto_create_engagement_letter(doc, entity, fy, request.user)

    return JsonResponse({
        "status": "ok",
        "document_id": document_id,
        "message": "Engagement letter generated and saved. The roll-forward gate for this year is now satisfied.",
        "redirect_url": f"/entities/{entity.pk}/?tab=engagement_letters",
        "docx_url": docx_url,
        "pdf_url": pdf_url,
    })


def _auto_create_engagement_letter(legal_doc, entity, financial_year, user):
    """
    After generating a LegalDocument of type engagement_letter, create (or
    replace) an EngagementLetter record so the roll-forward gate is satisfied
    automatically without requiring a manual upload.
    """
    try:
        if not legal_doc.generated_file:
            return
        legal_doc.generated_file.seek(0)
        docx_bytes = legal_doc.generated_file.read()
        safe_name = entity.entity_name.replace(" ", "_").replace("/", "_")
        filename = f"{safe_name}_engagement_letter_{financial_year.year_label}.docx"
        # Demote any existing current letters for this entity+year
        EngagementLetter.objects.filter(
            entity=entity,
            financial_year=financial_year,
            is_current=True,
        ).update(is_current=False)
        el = EngagementLetter(
            entity=entity,
            financial_year=financial_year,
            original_filename=filename,
            file_size_bytes=len(docx_bytes),
            status=EngagementLetter.Status.DRAFT,
            is_current=True,
            notes=f"Auto-generated from LegalDocument {legal_doc.pk}",
            uploaded_by=user,
        )
        el.file.save(filename, ContentFile(docx_bytes), save=True)
    except Exception as exc:
        logger.warning("Could not auto-create EngagementLetter record: %s", exc)


@login_required
@require_POST
def engagement_letter_quick_generate(request, pk):
    """
    One-click engagement letter generation using the saved EngagementLetterConfig.
    No wizard required — uses stored services, fee, and fee_basis with today's date.
    Satisfies the roll-forward gate automatically.
    """
    entity = get_object_or_404(Entity, pk=pk)
    if not request.user.can_do_accounting:
        return JsonResponse({"status": "error", "error": "Permission denied."}, status=403)

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        data = {}

    fy_id = data.get("financial_year_id") or request.POST.get("financial_year_id")
    if fy_id:
        fy = get_object_or_404(FinancialYear, pk=fy_id, entity=entity)
    else:
        fy = (
            entity.financial_years.filter(status__in=["draft", "in_progress", "review"])
            .order_by("-end_date").first()
            or entity.financial_years.order_by("-end_date").first()
        )
    if not fy:
        return JsonResponse({"status": "error", "error": "No financial year found for this entity."}, status=400)

    config, _ = EngagementLetterConfig.objects.get_or_create(entity=entity)
    if not config.services_engaged:
        return JsonResponse({
            "status": "error",
            "error": "No services configured for this entity. Please use the full wizard first to set up services and fees.",
        }, status=400)

    template = LegalDocumentTemplate.objects.filter(
        document_type=LegalDocumentTemplate.DocumentType.ENGAGEMENT_LETTER,
        is_active=True,
    ).first()
    if not template:
        return JsonResponse({
            "status": "error",
            "error": "No active engagement letter template found. Please upload one in Document Templates.",
        }, status=400)

    address_parts = list(filter(None, [
        entity.address_line_1,
        entity.address_line_2,
        " ".join(filter(None, [entity.suburb, entity.state, entity.postcode])),
    ]))
    registered_address = ", ".join(address_parts)

    fee_display = ""
    if config.fee_amount:
        try:
            fee_display = f"${float(config.fee_amount):,.2f} + GST"
        except (ValueError, TypeError):
            fee_display = str(config.fee_amount)

    signatories = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director", "director_shareholder", "trustee", "partner", "individual", "public_officer"],
    ).order_by("full_name")

    today_str = date.today().strftime("%d %B %Y")
    params = {
        "services": config.services_engaged,
        "fee_amount": str(config.fee_amount) if config.fee_amount else "",
        "fee_basis": config.fee_basis,
        "additional_terms": config.additional_terms,
        "date": today_str,
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
        "date": today_str,
    }

    try:
        from core.document_context_builder import DocumentContextBuilder
        _wizard = {
            "services": config.services_engaged,
            "fee_amount": str(config.fee_amount) if config.fee_amount else "",
            "fee_basis": config.fee_basis,
            "additional_terms": config.additional_terms,
            "date": today_str,
        }
        dcb = DocumentContextBuilder(entity, financial_year=fy, wizard_data=_wizard)
        enriched = dcb.build("engagement_letter")
        for k, v in enriched.items():
            if k not in context or k.startswith("practice_"):
                context[k] = v
    except Exception as _e:
        logger.warning("DCB enrichment skipped for quick engagement letter: %s", _e)

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
    doc.title = f"Engagement Letter \u2014 {entity.entity_name} \u2014 {fy.year_label}"
    doc.context_data = context
    doc.status = LegalDocument.Status.DRAFT
    doc.save(update_fields=["title", "context_data", "status"])

    config.last_generated_fy = fy
    config.save(update_fields=["last_generated_fy"])

    _auto_create_engagement_letter(doc, entity, fy, request.user)

    return JsonResponse({
        "status": "ok",
        "document_id": document_id,
        "message": f"Engagement letter generated for {fy.year_label}. Roll-forward gate satisfied.",
        "redirect_url": f"/entities/{entity.pk}/?tab=engagement_letters",
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
