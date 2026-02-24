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

from core.models import DocumentTemplate, Entity
from core.views import _log_action


# =============================================================================
# Template List
# =============================================================================
@login_required
def template_list(request):
    """List all document templates, grouped by category."""
    if not request.user.is_admin:
        messages.error(request, "Only administrators can manage document templates.")
        return redirect("core:entity_list")

    templates = DocumentTemplate.objects.all().order_by(
        "document_category", "entity_type", "-version"
    )

    # Group by category
    categories = {}
    for tpl in templates:
        cat = tpl.get_document_category_display()
        if cat not in categories:
            categories[cat] = {
                "key": tpl.document_category,
                "templates": [],
            }
        categories[cat]["templates"].append(tpl)

    context = {
        "categories": categories,
        "total_count": templates.count(),
        "active_count": templates.filter(is_active=True).count(),
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
