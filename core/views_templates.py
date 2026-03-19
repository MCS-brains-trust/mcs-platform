"""
StatementHub — Document Template Management Views

Admin UI for managing JSON-driven document templates:
  - List all templates (grouped by category)
  - Create new template
  - Edit template (JSON structure editor + metadata)
  - Preview template (render with sample data)
  - Version management (create new version, view history)
  - Delete template
"""
import copy
import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import DocumentTemplate, Entity, FinancialStatementTemplate, LegalDocumentTemplate, WorkPaperTemplate
from core.views import _log_action


# =============================================================================
# Template List
# =============================================================================
@login_required
def template_list(request):
    """List all document templates, organised into three tabs."""
    if not request.user.is_admin:
        messages.error(request, "Only administrators can manage document templates.")
        return redirect("core:entity_list")

    # -----------------------------------------------------------------------
    # TAB 1: Financial Statement Templates
    # -----------------------------------------------------------------------
    # FinancialStatementTemplate (.docx) — grouped by entity type
    fs_templates = FinancialStatementTemplate.objects.filter(
        is_active=True
    ).order_by("entity_type", "document_type")

    ENTITY_TYPE_LABELS = {
        "company": "Company",
        "trust": "Trust",
        "sole_trader": "Sole Trader",
        "partnership": "Partnership",
        "smsf": "SMSF",
        "individual": "Individual",
    }
    # FS doc types that are used in the annual client package bundle
    PACKAGE_FS_TYPES = {
        "COVER", "DETAILED_PL", "BALANCE_SHEET", "SUMMARY_PL",
        "NOTES", "DECLARATION", "COMPILATION", "DISTRIBUTION",
    }
    fs_by_entity = {}
    for tpl in fs_templates:
        label = ENTITY_TYPE_LABELS.get(tpl.entity_type, tpl.entity_type.replace("_", " ").title())
        if label not in fs_by_entity:
            fs_by_entity[label] = []
        tpl.in_package = tpl.document_type in PACKAGE_FS_TYPES
        fs_by_entity[label].append(tpl)

    # JSON-driven DocumentTemplates (distribution minutes, trust elections, etc.)
    json_templates = DocumentTemplate.objects.filter(
        is_active=True
    ).order_by("document_category", "entity_type", "-version")
    json_categories = {}
    for tpl in json_templates:
        cat = tpl.get_document_category_display()
        if cat not in json_categories:
            json_categories[cat] = []
        json_categories[cat].append(tpl)

    # -----------------------------------------------------------------------
    # TAB 2: Legal Templates
    # -----------------------------------------------------------------------
    legal_templates = LegalDocumentTemplate.objects.filter(
        is_active=True
    ).order_by("name")

    LEGAL_CATEGORY_ORDER = [
        "Compliance Documents",
        "Client Letters",
        "Trust Documents",
        "Trust Deeds",
        "Company Documents",
        "Partnership Documents",
        "Legal Agreements",
        "Other",
    ]
    LEGAL_CATEGORY_MAP = {
        "div7a_loan_agreement": "Legal Agreements",
        "trust_deed_change_trustee": "Trust Deeds",
        "trust_deed_add_beneficiary": "Trust Deeds",
        "trust_deed_remove_beneficiary": "Trust Deeds",
        "trust_deed_extend_vesting": "Trust Deeds",
        "trust_deed_update_distribution": "Trust Deeds",
        "discretionary_trust_deed": "Trust Deeds",
        "unit_trust_deed": "Trust Deeds",
        "unit_trust_deed_ancillaries": "Trust Deeds",
        "unit_transfer": "Trust Deeds",
        "company_constitution": "Company Documents",
        "company_constitution_special": "Company Documents",
        "company_establishment": "Company Documents",
        "partnership_agreement": "Partnership Documents",
        "partner_statement": "Partnership Documents",
        "partnership_tax_summary": "Partnership Documents",
        "dividend_statement": "Compliance Documents",
        "dividend_minutes": "Compliance Documents",
        "solvency_resolution": "Compliance Documents",
        "directors_declaration": "Compliance Documents",
        "directors_declaration_large": "Compliance Documents",
        "directors_declaration_gp": "Compliance Documents",
        "directors_report": "Compliance Documents",
        "shareholder_loan_ack": "Compliance Documents",
        "engagement_letter": "Client Letters",
        "management_rep_letter": "Client Letters",
        "management_rep_letter_trust": "Client Letters",
        "management_rep_letter_partnership": "Client Letters",
        "client_cover_letter": "Client Letters",
        "distribution_minutes": "Trust Documents",
        "section_100a_summary": "Trust Documents",
    }
    # Legal doc types used in the annual client package bundle
    PACKAGE_LEGAL_TYPES = {
        "solvency_resolution", "directors_declaration", "directors_declaration_large",
        "directors_declaration_gp", "directors_report", "shareholder_loan_ack",
        "dividend_statement", "dividend_minutes", "management_rep_letter",
        "management_rep_letter_trust", "management_rep_letter_partnership",
        "client_cover_letter", "distribution_minutes", "partner_statement",
        "partnership_tax_summary",
    }
    legal_categories_raw = {}
    for tpl in legal_templates:
        cat = LEGAL_CATEGORY_MAP.get(tpl.document_type, "Other")
        if cat not in legal_categories_raw:
            legal_categories_raw[cat] = []
        tpl.in_package = tpl.document_type in PACKAGE_LEGAL_TYPES
        legal_categories_raw[cat].append(tpl)
    # Apply canonical ordering
    legal_categories = {
        cat: legal_categories_raw[cat]
        for cat in LEGAL_CATEGORY_ORDER
        if cat in legal_categories_raw
    }
    for cat, tpls in legal_categories_raw.items():
        if cat not in legal_categories:
            legal_categories[cat] = tpls

    # -----------------------------------------------------------------------
    # TAB 3: Workpapers
    # -----------------------------------------------------------------------
    workpaper_templates = WorkPaperTemplate.objects.filter(
        is_active=True
    ).order_by("category", "sort_order", "name")
    workpaper_categories = {}
    for tpl in workpaper_templates:
        cat = tpl.get_category_display()
        if cat not in workpaper_categories:
            workpaper_categories[cat] = []
        workpaper_categories[cat].append(tpl)

    # -----------------------------------------------------------------------
    # Choices for upload modals
    # -----------------------------------------------------------------------
    legal_type_choices = LegalDocumentTemplate.DocumentType.choices
    workpaper_category_choices = WorkPaperTemplate.Category.choices
    entity_type_choices = [
        ("company", "Company"),
        ("trust", "Trust"),
        ("sole_trader", "Sole Trader"),
        ("partnership", "Partnership"),
        ("smsf", "SMSF"),
        ("individual", "Individual"),
    ]

    context = {
        # Tab 1 — Financial Statements
        "fs_by_entity": fs_by_entity,
        "fs_total_count": fs_templates.count(),
        "json_categories": json_categories,
        "json_total_count": json_templates.count(),
        # Tab 2 — Legal Templates
        "legal_categories": legal_categories,
        "legal_total_count": legal_templates.count(),
        "legal_type_choices": legal_type_choices,
        # Tab 3 — Workpapers
        "workpaper_categories": workpaper_categories,
        "workpaper_total_count": workpaper_templates.count(),
        "workpaper_category_choices": workpaper_category_choices,
        # Shared
        "entity_type_choices": entity_type_choices,
    }
    return render(request, "core/template_list.html", context)


# =============================================================================
# Template Create
# =============================================================================
@login_required
def template_create(request):
    """Create a new document template."""
    if not request.user.is_admin:
        messages.error(request, "Only administrators can create templates.")
        return redirect("core:template_list")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        document_category = request.POST.get("document_category", "")
        entity_type = request.POST.get("entity_type", "")
        description = request.POST.get("description", "")

        if not name or not document_category:
            messages.error(request, "Name and document category are required.")
            return redirect("core:template_create")

        # Default structure
        default_structure = _get_default_structure(document_category)

        tpl = DocumentTemplate.objects.create(
            name=name,
            document_category=document_category,
            entity_type=entity_type,
            description=description,
            structure=default_structure,
            version=1,
            is_active=True,
            created_by=request.user,
        )

        _log_action(request, "create", f"Created document template: {name}")
        messages.success(request, f"Template '{name}' created. Edit the structure below.")
        return redirect("core:template_edit", pk=tpl.pk)

    context = {
        "category_choices": DocumentTemplate.DocumentCategory.choices,
        "entity_type_choices": [("", "All Entity Types")] + list(Entity.EntityType.choices),
    }
    return render(request, "core/template_create.html", context)


# =============================================================================
# Template Edit
# =============================================================================
@login_required
def template_edit(request, pk):
    """Edit a document template's metadata and JSON structure."""
    if not request.user.is_admin:
        messages.error(request, "Only administrators can edit templates.")
        return redirect("core:template_list")

    tpl = get_object_or_404(DocumentTemplate, pk=pk)

    if request.method == "POST":
        # Update metadata
        tpl.name = request.POST.get("name", tpl.name).strip()
        tpl.description = request.POST.get("description", tpl.description)
        tpl.entity_type = request.POST.get("entity_type", tpl.entity_type)

        # Update structure from JSON editor
        structure_json = request.POST.get("structure", "")
        if structure_json:
            try:
                tpl.structure = json.loads(structure_json)
            except json.JSONDecodeError as e:
                messages.error(request, f"Invalid JSON: {e}")
                return redirect("core:template_edit", pk=pk)

        tpl.save()
        _log_action(request, "update", f"Updated document template: {tpl.name}")
        messages.success(request, f"Template '{tpl.name}' saved.")
        return redirect("core:template_edit", pk=pk)

    # Get merge fields used in this template
    merge_fields = tpl.get_merge_field_names()

    # Get version history
    history = DocumentTemplate.objects.filter(
        document_category=tpl.document_category,
        entity_type=tpl.entity_type,
    ).order_by("-version")

    # Available section types for the UI
    section_types = [
        {"type": "heading", "label": "Heading", "icon": "bi-type-h1",
         "description": "Section heading with configurable level and alignment"},
        {"type": "paragraph", "label": "Paragraph", "icon": "bi-text-paragraph",
         "description": "Text block with merge field support"},
        {"type": "paragraph_list", "label": "Paragraph List", "icon": "bi-list-ul",
         "description": "Multiple paragraphs in sequence"},
        {"type": "table", "label": "Data Table", "icon": "bi-table",
         "description": "Table with column definitions and data source"},
        {"type": "key_value_table", "label": "Key-Value Table", "icon": "bi-card-list",
         "description": "Two-column label/value table"},
        {"type": "conditional", "label": "Conditional Block", "icon": "bi-question-circle",
         "description": "Show/hide content based on a merge field"},
        {"type": "signature_block", "label": "Signature Block", "icon": "bi-pen",
         "description": "Signature line with name, title, and date"},
        {"type": "disclaimer", "label": "Disclaimer", "icon": "bi-exclamation-triangle",
         "description": "Italic disclaimer text"},
        {"type": "spacer", "label": "Spacer", "icon": "bi-arrows-expand",
         "description": "Vertical whitespace"},
        {"type": "page_break", "label": "Page Break", "icon": "bi-file-break",
         "description": "Insert page break"},
        {"type": "horizontal_rule", "label": "Horizontal Rule", "icon": "bi-dash-lg",
         "description": "Thin horizontal line"},
        {"type": "numbered_list", "label": "Numbered List", "icon": "bi-list-ol",
         "description": "Ordered list items"},
        {"type": "bullet_list", "label": "Bullet List", "icon": "bi-list-ul",
         "description": "Unordered list items"},
        {"type": "firm_header", "label": "Firm Header", "icon": "bi-building",
         "description": "Firm name and address block"},
    ]

    # Available merge fields for this category
    available_fields = _get_available_merge_fields(tpl.document_category)

    context = {
        "template": tpl,
        "structure_json": json.dumps(tpl.structure, indent=2, default=str),
        "merge_fields": merge_fields,
        "history": history,
        "section_types": section_types,
        "available_fields": available_fields,
        "entity_type_choices": [("", "All Entity Types")] + list(Entity.EntityType.choices),
    }
    return render(request, "core/template_edit.html", context)


# =============================================================================
# Template Preview
# =============================================================================
@login_required
def template_preview(request, pk):
    """Preview a template by rendering it with sample data."""
    if not request.user.is_admin:
        messages.error(request, "Only administrators can preview templates.")
        return redirect("core:template_list")

    tpl = get_object_or_404(DocumentTemplate, pk=pk)

    # Use sample data for preview
    context = _get_sample_context(tpl.document_category)

    from core.template_renderer import TemplateRenderer
    renderer = TemplateRenderer(tpl.structure, context)
    buffer = renderer.render()

    filename = f"PREVIEW_{tpl.name.replace(' ', '_')}.docx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# =============================================================================
# Template Version
# =============================================================================
@login_required
@require_POST
def template_new_version(request, pk):
    """Create a new version of a template."""
    if not request.user.is_admin:
        messages.error(request, "Only administrators can version templates.")
        return redirect("core:template_list")

    tpl = get_object_or_404(DocumentTemplate, pk=pk)
    new_tpl = tpl.create_new_version(user=request.user)

    _log_action(request, "create", f"Created new version (v{new_tpl.version}) of template: {tpl.name}")
    messages.success(request, f"New version v{new_tpl.version} created from v{tpl.version}.")
    return redirect("core:template_edit", pk=new_tpl.pk)


# =============================================================================
# Template Delete
# =============================================================================
@login_required
@require_POST
def template_delete(request, pk):
    """Delete a document template."""
    if not request.user.is_admin:
        messages.error(request, "Only administrators can delete templates.")
        return redirect("core:template_list")

    tpl = get_object_or_404(DocumentTemplate, pk=pk)
    name = tpl.name
    tpl.delete()

    _log_action(request, "delete", f"Deleted document template: {name}")
    messages.success(request, f"Template '{name}' deleted.")
    return redirect("core:template_list")


# =============================================================================
# Template Toggle Active
# =============================================================================
@login_required
@require_POST
def template_toggle_active(request, pk):
    """Toggle a template's active status."""
    if not request.user.is_admin:
        return JsonResponse({"error": "Permission denied."}, status=403)

    tpl = get_object_or_404(DocumentTemplate, pk=pk)

    if not tpl.is_active:
        # Deactivate other templates in the same category+entity_type
        DocumentTemplate.objects.filter(
            document_category=tpl.document_category,
            entity_type=tpl.entity_type,
            is_active=True,
        ).update(is_active=False)

    tpl.is_active = not tpl.is_active
    tpl.save(update_fields=["is_active", "updated_at"])

    status = "activated" if tpl.is_active else "deactivated"
    _log_action(request, "update", f"Template {status}: {tpl.name}")
    messages.success(request, f"Template '{tpl.name}' {status}.")
    return redirect("core:template_list")


# =============================================================================
# API: Update structure via AJAX
# =============================================================================
@login_required
@require_POST
def template_update_structure(request, pk):
    """AJAX endpoint to update template structure."""
    if not request.user.is_admin:
        return JsonResponse({"error": "Permission denied."}, status=403)

    tpl = get_object_or_404(DocumentTemplate, pk=pk)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError as e:
        return JsonResponse({"error": f"Invalid JSON: {e}"}, status=400)

    structure = body.get("structure")
    if structure is None:
        return JsonResponse({"error": "No structure provided."}, status=400)

    tpl.structure = structure
    tpl.save(update_fields=["structure", "updated_at"])

    return JsonResponse({
        "success": True,
        "merge_fields": tpl.get_merge_field_names(),
    })


# =============================================================================
# Helpers
# =============================================================================
def _get_default_structure(document_category):
    """Return a sensible default JSON structure for a new template."""
    return {
        "metadata": {
            "page_setup": {
                "orientation": "portrait",
                "margin_top": 2.54,
                "margin_bottom": 2.54,
                "margin_left": 2.54,
                "margin_right": 2.54,
            }
        },
        "styles": {
            "font_name": "Times New Roman",
            "font_size_body": 11,
            "font_size_heading": 14,
            "font_size_subheading": 12,
            "font_size_small": 9,
            "table_header_bg": "333333",
            "table_header_fg": "FFFFFF",
        },
        "sections": [
            {
                "type": "heading",
                "text": "Document Title",
                "level": 1,
                "alignment": "center",
                "bold": True,
            },
            {
                "type": "paragraph",
                "text": "Replace this with your document content. Use {{merge_fields}} for dynamic data.",
            },
        ],
    }


def _get_available_merge_fields(document_category):
    """Return the list of available merge fields for a document category."""
    fields = {
        "distribution_minutes": [
            {"name": "trust_name", "description": "Entity name of the trust"},
            {"name": "trustee_name", "description": "Formatted trustee name(s)"},
            {"name": "chairperson_name", "description": "Chairperson's full name"},
            {"name": "financial_year", "description": "Year digits (e.g. 2025)"},
            {"name": "financial_year_end", "description": "30 June YYYY"},
            {"name": "minutes_date", "description": "Date of the minutes (30 June YYYY)"},
            {"name": "beneficiary_rows", "description": "Table data: name, type, distribution, percentage"},
            {"name": "total_distributed", "description": "Formatted total distributions"},
            {"name": "has_beneficiaries", "description": "Boolean: true if beneficiaries exist"},
        ],
        "trust_election": [
            {"name": "trust_name", "description": "Entity name of the trust"},
            {"name": "trustee_name", "description": "Formatted trustee name(s)"},
            {"name": "chairperson_name", "description": "Chairperson's full name"},
            {"name": "resolution_date", "description": "30 June YYYY"},
            {"name": "financial_year_end", "description": "30 June YYYY"},
            {"name": "financial_year", "description": "Year digits (e.g. 2025)"},
            {"name": "distributable_income", "description": "Formatted distributable income"},
            {"name": "beneficiary_rows", "description": "Table data: name, type, distribution, net_tax, effective_rate, notes"},
            {"name": "streaming_rows", "description": "Table data: name, capital_gains, franked_dividends, franking_credits, other_income"},
            {"name": "has_streaming", "description": "Boolean: true if CGT or franked dividends exist"},
            {"name": "has_capital_gains", "description": "Boolean: true if capital gains exist"},
            {"name": "has_franked_dividends", "description": "Boolean: true if franked dividends exist"},
            {"name": "capital_gains_total", "description": "Formatted capital gains total"},
            {"name": "franked_dividends_total", "description": "Formatted franked dividends total"},
            {"name": "franking_credits_total", "description": "Formatted franking credits total"},
            {"name": "total_distributed", "description": "Formatted total distributions"},
            {"name": "total_tax_payable", "description": "Formatted total tax payable"},
        ],
        "tax_planning_summary": [
            {"name": "trust_name", "description": "Entity name of the trust"},
            {"name": "trustee_name", "description": "Formatted trustee name(s)"},
            {"name": "financial_year_end", "description": "30 June YYYY"},
            {"name": "financial_year", "description": "Year digits (e.g. 2025)"},
            {"name": "distributable_income", "description": "Formatted distributable income"},
            {"name": "non_deductible_expenses", "description": "Formatted non-deductible expenses"},
            {"name": "non_assessable_income", "description": "Formatted non-assessable income"},
            {"name": "capital_gains", "description": "Formatted capital gains"},
            {"name": "franked_dividends", "description": "Formatted franked dividends"},
            {"name": "franking_credits", "description": "Formatted franking credits"},
            {"name": "beneficiary_rows", "description": "Table data: name, type, distribution, net_tax, effective_rate, notes"},
            {"name": "total_distributed", "description": "Formatted total distributions"},
            {"name": "total_tax_payable", "description": "Formatted total tax payable"},
            {"name": "weighted_effective_rate", "description": "Weighted effective tax rate (e.g. 24.50%)"},
            {"name": "undistributed_balance", "description": "Formatted undistributed balance"},
            {"name": "scenario_name", "description": "Name of the active scenario"},
            {"name": "accountant_recommendation", "description": "Recommendation notes (plain text)"},
            {"name": "has_recommendation", "description": "Boolean: true if recommendation exists"},
            {"name": "summary_items", "description": "Key-value data for summary table"},
        ],
    }
    return fields.get(document_category, [])


def _get_sample_context(document_category):
    """Return sample data for template preview."""
    sample = {
        "trust_name": "Smith Family Trust",
        "trustee_name": "John Smith and Jane Smith",
        "trustee_names_list": ["John Smith", "Jane Smith"],
        "chairperson_name": "John Smith",
        "financial_year": "2025",
        "financial_year_end": "30 June 2025",
        "minutes_date": "30 June 2025",
        "resolution_date": "30 June 2025",
        "distributable_income": "$150,000.00",
        "distributable_income_raw": Decimal("150000"),
        "non_deductible_expenses": "$2,500.00",
        "non_assessable_income": "$1,000.00",
        "capital_gains": "$25,000.00",
        "franked_dividends": "$10,000.00",
        "franking_credits": "$4,285.71",
        "total_distributed": "$150,000.00",
        "total_distributed_raw": Decimal("150000"),
        "total_tax_payable": "$28,500.00",
        "total_tax_raw": Decimal("28500"),
        "undistributed_balance": "$0.00",
        "weighted_effective_rate": "19.00%",
        "scenario_name": "Optimal Split",
        "accountant_recommendation": "We recommend distributing income equally between John and Jane to minimise the overall tax burden. This achieves a weighted effective rate of 19.00%.",
        "has_recommendation": True,
        "has_beneficiaries": True,
        "has_streaming": True,
        "has_capital_gains": True,
        "has_franked_dividends": True,
        "capital_gains_total": "$25,000.00",
        "franked_dividends_total": "$10,000.00",
        "franking_credits_total": "$4,285.71",
        "beneficiary_rows": [
            {
                "name": "John Smith",
                "type": "Individual",
                "distribution": "$75,000.00",
                "distribution_raw": Decimal("75000"),
                "percentage": "50.00%",
                "net_tax": "$14,250.00",
                "effective_rate": "19.00%",
                "notes": "",
            },
            {
                "name": "Jane Smith",
                "type": "Individual",
                "distribution": "$75,000.00",
                "distribution_raw": Decimal("75000"),
                "percentage": "50.00%",
                "net_tax": "$14,250.00",
                "effective_rate": "19.00%",
                "notes": "",
            },
        ],
        "streaming_rows": [
            {
                "name": "John Smith",
                "capital_gains": "$12,500.00",
                "franked_dividends": "$5,000.00",
                "franking_credits": "$2,142.86",
                "other_income": "$57,500.00",
            },
            {
                "name": "Jane Smith",
                "capital_gains": "$12,500.00",
                "franked_dividends": "$5,000.00",
                "franking_credits": "$2,142.86",
                "other_income": "$57,500.00",
            },
        ],
        "summary_items": [
            {"label": "Total Distributable Income", "value": "$150,000.00"},
            {"label": "Total Proposed Distributions", "value": "$150,000.00"},
            {"label": "Undistributed Balance", "value": "$0.00"},
            {"label": "Total Estimated Tax", "value": "$28,500.00"},
            {"label": "Weighted Effective Tax Rate", "value": "19.00%"},
        ],
    }
    return sample


# =============================================================================
# Financial Statement Template — Download & Replace
# =============================================================================

@login_required
def fs_template_download(request, pk):
    """Download the .docx file for a FinancialStatementTemplate."""
    if not request.user.is_admin:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Admin access required.")
    tpl = get_object_or_404(FinancialStatementTemplate, pk=pk)
    if not tpl.template_file:
        return HttpResponse("No file attached to this template.", status=404)
    safe_name = f"{tpl.name.replace(' ', '_')}_v{tpl.version}.docx"
    try:
        file_data = tpl.template_file.read()
    except Exception as e:
        return HttpResponse(f"Could not read template file: {e}", status=500)
    response = HttpResponse(
        file_data,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return response


@login_required
@require_POST
def fs_template_replace(request, pk):
    """Upload a replacement .docx file for an existing FinancialStatementTemplate."""
    if not request.user.is_admin:
        return JsonResponse({"error": "Admin access required."}, status=403)
    tpl = get_object_or_404(FinancialStatementTemplate, pk=pk)
    file = request.FILES.get("template_file")
    if not file:
        return JsonResponse({"error": "No file uploaded."}, status=400)
    if not file.name.lower().endswith(".docx"):
        return JsonResponse({"error": "Only .docx files are accepted."}, status=400)
    # Deactivate the current version
    tpl.is_active = False
    tpl.save(update_fields=["is_active", "updated_at"])
    # Create the new version
    try:
        new_version = str(float(tpl.version) + 1) if tpl.version else "2.0"
    except (ValueError, TypeError):
        new_version = "2.0"
    new_tpl = FinancialStatementTemplate.objects.create(
        name=tpl.name,
        document_type=tpl.document_type,
        entity_type=tpl.entity_type,
        description=tpl.description,
        template_file=file,
        version=new_version,
        is_active=True,
    )
    _log_action(
        request, "update",
        f"Replaced FS template '{tpl.name}' ({tpl.entity_type}) — now v{new_tpl.version}",
    )
    return JsonResponse({
        "status": "ok",
        "template_id": str(new_tpl.pk),
        "version": new_tpl.version,
        "message": f"Template replaced successfully. Now at version {new_tpl.version}.",
    })


# =============================================================================
# Workpaper Template — Upload, Download, Replace, Delete
# =============================================================================

@login_required
@require_POST
def workpaper_template_upload(request):
    """Upload a new WorkPaperTemplate (.xlsx or .docx)."""
    if not request.user.is_admin:
        return JsonResponse({"error": "Admin access required."}, status=403)
    name = request.POST.get("name", "").strip()
    category = request.POST.get("category", "general")
    description = request.POST.get("description", "").strip()
    entity_types = request.POST.getlist("entity_types")
    file = request.FILES.get("template_file")
    if not name or not file:
        return JsonResponse({"error": "Name and file are required."}, status=400)
    ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
    if ext not in ("xlsx", "docx"):
        return JsonResponse({"error": "Only .xlsx or .docx files are accepted."}, status=400)
    tpl = WorkPaperTemplate.objects.create(
        name=name,
        category=category,
        description=description,
        entity_types=entity_types,
        template_file=file,
        file_format=ext,
        is_active=True,
        created_by=request.user,
    )
    _log_action(request, "create", f"Uploaded workpaper template: {name}")
    return JsonResponse({
        "status": "ok",
        "template_id": str(tpl.pk),
        "message": f"Workpaper template '{name}' uploaded successfully.",
    })


@login_required
def workpaper_template_download(request, pk):
    """Download the raw template file for a WorkPaperTemplate."""
    if not request.user.is_admin:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Admin access required.")
    tpl = get_object_or_404(WorkPaperTemplate, pk=pk)
    if not tpl.template_file:
        return HttpResponse("No file attached to this template.", status=404)
    ext = tpl.file_format.lower()
    content_type_map = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    content_type = content_type_map.get(ext, "application/octet-stream")
    safe_name = f"{tpl.name.replace(' ', '_')}.{ext}"
    try:
        file_data = tpl.template_file.read()
    except Exception as e:
        return HttpResponse(f"Could not read template file: {e}", status=500)
    response = HttpResponse(file_data, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return response


@login_required
@require_POST
def workpaper_template_replace(request, pk):
    """Upload a replacement file for an existing WorkPaperTemplate."""
    if not request.user.is_admin:
        return JsonResponse({"error": "Admin access required."}, status=403)
    tpl = get_object_or_404(WorkPaperTemplate, pk=pk)
    file = request.FILES.get("template_file")
    if not file:
        return JsonResponse({"error": "No file uploaded."}, status=400)
    ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
    if ext not in ("xlsx", "docx"):
        return JsonResponse({"error": "Only .xlsx or .docx files are accepted."}, status=400)
    # Replace the file in-place (workpapers are not versioned like legal templates)
    tpl.template_file = file
    tpl.file_format = ext
    tpl.save(update_fields=["template_file", "file_format", "updated_at"])
    _log_action(request, "update", f"Replaced workpaper template file: {tpl.name}")
    return JsonResponse({
        "status": "ok",
        "message": f"Template '{tpl.name}' replaced successfully.",
    })


@login_required
@require_POST
def workpaper_template_delete(request, pk):
    """Soft-delete (deactivate) a WorkPaperTemplate."""
    if not request.user.is_admin:
        return JsonResponse({"error": "Admin access required."}, status=403)
    tpl = get_object_or_404(WorkPaperTemplate, pk=pk)
    tpl.is_active = False
    tpl.save(update_fields=["is_active", "updated_at"])
    _log_action(request, "delete", f"Deactivated workpaper template: {tpl.name}")
    return JsonResponse({"status": "ok", "message": f"'{tpl.name}' deactivated."})
