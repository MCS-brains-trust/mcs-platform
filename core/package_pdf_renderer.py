"""
Package PDF Bundle Renderer
============================
Renders all LegalDocument records for a FinancialYear into individual PDFs
using weasyprint (from context_data), then merges them with the Financial
Statements PDF into a single client package bundle using pypdf.

Document order:
  1. Financial Statements (Compilation Report, P&L, Balance Sheet, Notes)
  2. Director's Declaration (standalone)
  3. Solvency Resolution
  4. Director's Report
  5. Management Representation Letter
  6. Cover Letter (Transmittal)
  7. Dividend Statements
  8. Loan Acknowledgment
"""
import io
import logging
import os
from datetime import date

from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _format_acn(raw):
    """Format ACN as XXX XXX XXX (9 digits in groups of 3)."""
    digits = "".join(c for c in str(raw) if c.isdigit()) if raw else ""
    if len(digits) != 9:
        return digits  # return as-is if not standard length
    return f"{digits[:3]} {digits[3:6]} {digits[6:9]}"


def _format_abn(raw):
    """Format ABN as XX XXX XXX XXX (11 digits: 2-3-3-3)."""
    digits = "".join(c for c in str(raw) if c.isdigit()) if raw else ""
    if len(digits) != 11:
        return digits  # return as-is if not standard length
    return f"{digits[:2]} {digits[2:5]} {digits[5:8]} {digits[8:11]}"


def _build_acn_abn(acn, abn):
    """Build a combined 'ACN xxx / ABN xxx' display string."""
    parts = []
    if acn:
        parts.append(f"ACN: {_format_acn(acn)}")
    if abn:
        parts.append(f"ABN: {_format_abn(abn)}")
    return " / ".join(parts) if parts else ""


# Document type → (template_name, title)
LEGAL_DOC_TEMPLATES = {
    "solvency_resolution": ("core/pdf/solvency_resolution.html", "Solvency Resolution"),
    "directors_declaration": ("core/pdf/directors_declaration.html", "Director's Declaration"),
    "directors_report": ("core/pdf/directors_report.html", "Director's Report"),
    "management_representation_letter": ("core/pdf/management_rep_letter.html", "Management Representation Letter"),
    "cover_letter": ("core/pdf/cover_letter.html", "Cover Letter"),
    "shareholder_loan_acknowledgment": ("core/pdf/loan_acknowledgment.html", "Loan Acknowledgment"),
}

DOCUMENT_ORDER = [
    "financial_statements",
    "directors_declaration",
    "solvency_resolution",
    "directors_report",
    "management_representation_letter",
    # "cover_letter" excluded — transmittal letter is not part of the client package
    "dividend_statement",
    "shareholder_loan_acknowledgment",
    "compilation_report",  # Compilation Report last per APES 315
]


def render_legal_doc_to_pdf_bytes(doc):
    """
    Render a LegalDocument record to PDF bytes using weasyprint.
    Uses context_data stored on the record.
    Returns bytes or None on failure.

    This path is used for final client package rendering so the bundle is
    always generated from clean HTML templates rather than from any previously
    stored draft PDFs that may contain watermarks.
    """
    import weasyprint

    doc_type = doc.document_type
    template_name = LEGAL_DOC_TEMPLATES.get(doc_type)
    if not template_name:
        logger.warning("No PDF template for document type: %s", doc_type)
        return None

    if isinstance(template_name, tuple):
        template_name = template_name[0]

    context = dict(doc.context_data or {})
    context.setdefault("document_title", doc.title or doc.get_document_type_display())
    context.setdefault("generated_at", doc.generated_at.strftime("%d %B %Y") if doc.generated_at else "")
    context.setdefault("firm_name", "MC & S Chartered Accountants")
    # Always rebuild ACN/ABN from current entity data, formatted with spaces.
    if doc.entity:
        acn_raw = doc.entity.acn or context.get("acn", "")
        abn_raw = doc.entity.abn or context.get("abn", "")
        # Format ACN as XXX XXX XXX
        acn_digits = "".join(c for c in str(acn_raw) if c.isdigit())
        context["acn"] = (
            f"{acn_digits[:3]} {acn_digits[3:6]} {acn_digits[6:]}"
            if len(acn_digits) == 9 else acn_raw
        )
        # Format ABN as XX XXX XXX XXX
        abn_digits = "".join(c for c in str(abn_raw) if c.isdigit())
        context["abn"] = (
            f"{abn_digits[:2]} {abn_digits[2:5]} {abn_digits[5:8]} {abn_digits[8:]}"
            if len(abn_digits) == 11 else abn_raw
        )
    context["acn_abn"] = _build_acn_abn(context.get("acn", ""), context.get("abn", ""))
    context.setdefault("is_final", True)
    context.setdefault("watermark_text", "")
    if doc.financial_year and doc.financial_year.end_date:
        fy_end = doc.financial_year.end_date
        context["financial_year"] = str(fy_end.year)
        # Always override — stored context_data may contain stale ISO format
        context["financial_year_end"] = fy_end.strftime("%-d %B %Y")
        context.setdefault("resolution_date", fy_end.strftime("%-d %B %Y"))

    # Always rebuild signatories from current entity officers so stale
    # context_data (e.g. generated before directors were entered) is corrected.
    if doc.entity and doc.document_type in (
        "directors_declaration", "solvency_resolution", "management_representation_letter",
    ):
        from core.models import EntityOfficer
        officers = EntityOfficer.objects.filter(
            entity=doc.entity,
            role__in=["director", "director_shareholder", "trustee", "partner"],
            date_ceased__isnull=True,
        ).order_by("display_order", "full_name")
        if officers.exists():
            context["signatories"] = [
                {"name": o.full_name, "role": o.get_role_display()}
                for o in officers
            ]

    try:
        html_string = render_to_string(template_name, context)
        pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()
        return pdf_bytes
    except Exception as e:
        logger.error("Failed to render %s to PDF: %s", doc_type, e)
        return None


def build_package_bundle(fy):
    """
    Build a single merged PDF bundle for the given FinancialYear.
    Returns (pdf_bytes, filename) or raises an exception.
    """
    from pypdf import PdfWriter, PdfReader
    from core.models import GeneratedDocument, LegalDocument

    writer = PdfWriter()
    docs_added = 0

    # Build a map of existing LegalDocuments by type (most recent first)
    legal_docs_by_type = {}
    for doc in LegalDocument.objects.filter(
        financial_year=fy,
        status__in=["generated", "final", "executed", "signed"],
    ).order_by("-generated_at"):
        if doc.document_type not in legal_docs_by_type:
            legal_docs_by_type[doc.document_type] = doc

    entity = fy.entity
    entity_name = entity.entity_name
    fy_year = fy.end_date.year

    for doc_type in DOCUMENT_ORDER:
        if doc_type == "financial_statements":
            # Primary path: regenerate clean FS via the docxtpl template
            # service with include_watermark=False, then convert to PDF.
            fs_added = False
            try:
                from core.fs_template_service import generate_combined_pdf

                logger.info("Regenerating clean FS for package bundle FY %s", fy.pk)
                # Exclude DECLARATION (standalone legal doc) and COMPILATION
                # (appended last per APES 315 after all legal documents).
                pdf_buffer = generate_combined_pdf(
                    fy.pk, include_watermark=False,
                    exclude_types={"DECLARATION", "COMPILATION"},
                )

                reader = PdfReader(pdf_buffer)
                for page in reader.pages:
                    writer.add_page(page)
                docs_added += 1
                fs_added = True
                logger.info(
                    "Added Financial Statements from template render (%d pages)",
                    len(reader.pages),
                )
            except Exception as e:
                logger.error(
                    "FALLBACK TRIGGERED for FS in package bundle FY %s: %s",
                    fy.pk, str(e), exc_info=True,
                )

            # Fallback: use latest stored FS document.
            if not fs_added:
                logger.warning(
                    "Package bundle FS fallback: using stored PDF for FY %s "
                    "(may contain DRAFT watermarks)", fy.pk,
                )
                fs_doc = GeneratedDocument.objects.filter(
                    financial_year=fy,
                    document_type=GeneratedDocument.DocumentType.FINANCIAL_STATEMENTS,
                ).order_by("-generated_at").first()

                if fs_doc and fs_doc.file:
                    try:
                        fs_doc.file.seek(0)
                        reader = PdfReader(io.BytesIO(fs_doc.file.read()))
                        for page in reader.pages:
                            writer.add_page(page)
                        docs_added += 1
                        logger.warning(
                            "Added Financial Statements from STORED PDF fallback (%d pages)",
                            len(reader.pages),
                        )
                    except Exception as e2:
                        logger.error("Could not add stored FS PDF: %s", e2)
                else:
                    logger.error("No stored FS document found for fallback FY %s", fy.pk)
            continue

        # Compilation Report — docxtpl template, not an HTML legal doc.
        # Rendered separately so it appears after all legal documents.
        if doc_type == "compilation_report":
            logger.info("Generating Compilation Report for package FY %s", fy.pk)
            try:
                from core.fs_template_service import (
                    build_company_context, build_trust_context,
                    build_sole_trader_context, render_template,
                    _post_process_fs_doc,
                )
                from core.models import FinancialStatementTemplate
                from core.libreoffice_utils import convert_docx_to_pdf
                import tempfile as _tmpfile

                # Build context for this entity type
                ctx_builders = {
                    "company": build_company_context,
                    "trust": build_trust_context,
                    "sole_trader": build_sole_trader_context,
                }
                ctx_builder = ctx_builders.get(entity.entity_type, build_company_context)
                context = ctx_builder(fy, include_watermark=False)

                # Find the COMPILATION template
                comp_tmpl = FinancialStatementTemplate.objects.filter(
                    document_type="COMPILATION",
                    entity_type=entity.entity_type,
                    is_active=True,
                ).first()

                if comp_tmpl:
                    # Render from registered DB template
                    comp_buffer = render_template(comp_tmpl, context)
                else:
                    # Self-healing: build template on-the-fly if DB record missing
                    logger.warning(
                        "No COMPILATION template in DB for %s — building on-the-fly",
                        entity.entity_type,
                    )
                    from core.management.commands.generate_fs_templates import _build_compilation
                    from docxtpl import DocxTemplate

                    comp_doc = _build_compilation(entity.entity_type)
                    tmp_tmpl = io.BytesIO()
                    comp_doc.save(tmp_tmpl)
                    tmp_tmpl.seek(0)
                    tpl = DocxTemplate(tmp_tmpl)
                    tpl.render(context)
                    comp_buffer = io.BytesIO()
                    tpl.save(comp_buffer)
                    comp_buffer.seek(0)

                comp_buffer = _post_process_fs_doc(comp_buffer, "COMPILATION")

                # Convert .docx to PDF
                _tmpdir = _tmpfile.mkdtemp(prefix="shub_comp_")
                comp_docx = os.path.join(_tmpdir, "COMPILATION.docx")
                with open(comp_docx, "wb") as _f:
                    _f.write(comp_buffer.read())

                convert_docx_to_pdf(comp_docx, _tmpdir, timeout=60)
                comp_pdf = os.path.join(_tmpdir, "COMPILATION.pdf")

                if os.path.exists(comp_pdf):
                    comp_reader = PdfReader(comp_pdf)
                    for page in comp_reader.pages:
                        writer.add_page(page)
                    docs_added += 1
                    logger.info("Added Compilation Report (%d pages)", len(comp_reader.pages))
                else:
                    logger.error("Compilation Report PDF conversion produced no output")

                import shutil
                shutil.rmtree(_tmpdir, ignore_errors=True)

            except Exception as e:
                logger.error("Failed to add Compilation Report: %s", e, exc_info=True)
            continue

        # LegalDocument types
        doc = legal_docs_by_type.get(doc_type)
        if not doc:
            continue

        # Always re-render legal documents for the final client package so any
        # previously generated draft PDFs do not carry watermarks into the
        # bundle downloaded after Eva clearance.
        pdf_bytes = render_legal_doc_to_pdf_bytes(doc)

        # Fall back to stored bytes only if clean re-rendering is unavailable.
        if not pdf_bytes and doc.pdf_file:
            try:
                doc.pdf_file.seek(0)
                pdf_bytes = doc.pdf_file.read()
            except Exception:
                pdf_bytes = None

        if pdf_bytes:
            try:
                reader = PdfReader(io.BytesIO(pdf_bytes))
                for page in reader.pages:
                    writer.add_page(page)
                docs_added += 1
                logger.info("Added %s (%d pages)", doc_type, len(reader.pages))
            except Exception as e:
                logger.warning("Could not add %s: %s", doc_type, e)

    if docs_added == 0:
        raise ValueError("No documents could be added to the bundle.")

    # Write to bytes and stamp continuous page numbers
    output = io.BytesIO()
    writer.write(output)
    raw_bytes = output.getvalue()

    from core.fs_template_service import _stamp_page_numbers
    pdf_bytes = _stamp_page_numbers(raw_bytes)

    safe_name = entity_name.replace(" ", "_").replace("/", "_")
    filename = f"Client_Package_{safe_name}_{fy_year}.pdf"

    logger.info(
        "Package bundle built for %s %s: %d documents, %d bytes",
        entity_name, fy_year, docs_added, len(pdf_bytes),
    )
    return pdf_bytes, filename
