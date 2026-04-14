"""
StatementHub — Template-Engine Document Generation

Unified entry point for generating documents using the JSON-driven
template engine. Replaces hardcoded python-docx generation.

Usage:
    from core.template_docgen import generate_from_template

    buffer = generate_from_template("trust_election", "trust", fy.pk)
    # buffer is a BytesIO containing the .docx
"""
import io
import logging

from core.models import DocumentTemplate
from core.template_renderer import TemplateRenderer
from core.template_resolvers import resolve_context

logger = logging.getLogger(__name__)


def generate_from_template(document_category: str, entity_type: str, financial_year_id) -> io.BytesIO:
    """
    Generate a Word document using the template engine.

    1. Loads the active DocumentTemplate for the given category + entity_type
    2. Resolves all merge fields via the appropriate resolver
    3. Renders the JSON structure into a .docx via TemplateRenderer

    Args:
        document_category: e.g. "distribution_minutes", "trust_election", "tax_planning_summary"
        entity_type: e.g. "trust", "company", "" (blank for generic)
        financial_year_id: UUID of the FinancialYear

    Returns:
        BytesIO buffer containing the generated .docx

    Raises:
        ValueError: If no active template found or resolver fails
    """
    # 1. Load template
    tpl = DocumentTemplate.get_active(document_category, entity_type)
    if not tpl:
        raise ValueError(
            f"No active document template found for category='{document_category}', "
            f"entity_type='{entity_type}'. Please create one in Admin → Document Templates."
        )

    logger.info(
        f"Generating {document_category} using template '{tpl.name}' v{tpl.version} "
        f"(pk={tpl.pk})"
    )

    # 2. Resolve merge fields
    context = resolve_context(document_category, financial_year_id)

    logger.info(
        "template_docgen context keys: %s | has_beneficiaries=%s beneficiary_rows=%d",
        list(context.keys()),
        context.get("has_beneficiaries"),
        len(context.get("beneficiary_rows", [])),
    )

    # 3. Render
    renderer = TemplateRenderer(tpl.structure, context)
    buffer = renderer.render()

    return buffer
