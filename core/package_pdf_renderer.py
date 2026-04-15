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
# Keys MUST match LegalDocumentTemplate.DocumentType values exactly.
LEGAL_DOC_TEMPLATES = {
    "solvency_resolution": ("core/pdf/solvency_resolution.html", "Solvency Resolution"),
    "directors_declaration": ("core/pdf/directors_declaration.html", "Director's Declaration"),
    "directors_report": ("core/pdf/directors_report.html", "Director's Report"),
    "management_rep_letter": ("core/pdf/management_rep_letter.html", "Management Representation Letter"),
    "management_rep_letter_trust": ("core/pdf/management_rep_letter.html", "Management Representation Letter"),
    "management_rep_letter_partnership": ("core/pdf/management_rep_letter.html", "Management Representation Letter"),
    "client_cover_letter": ("core/pdf/cover_letter.html", "Cover Letter"),
    "shareholder_loan_ack": ("core/pdf/loan_acknowledgment.html", "Loan Acknowledgment"),
    "partner_statement": ("core/pdf/partner_statement.html", "Partner Statement"),
    "partnership_tax_summary": ("core/pdf/partnership_tax_summary.html", "Partnership Tax Summary"),
    "distribution_minutes": ("core/pdf/distribution_minutes.html", "Trust Distribution Minutes"),
}

DOCUMENT_ORDER = [
    "financial_statements",
    # Company compliance
    "directors_declaration",
    "solvency_resolution",
    "directors_report",
    # distribution_minutes is a standalone legal doc — NOT part of the FS package
    # Partnership-specific
    "partner_statement",
    "partnership_tax_summary",
    # All entity types
    "management_rep_letter",
    "management_rep_letter_trust",
    "management_rep_letter_partnership",
    "dividend_statement",
    "shareholder_loan_ack",
    # Compilation Report last — matches Contents page order
    "compilation_report",
    # Cover letter intentionally excluded from bundle — transmittal only
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
    # Load firm branding from FirmSettings (white-label support)
    try:
        from core.models import FirmSettings
        _fs = FirmSettings.get()
        context.setdefault("firm_name", _fs.firm_name or "MC & S Chartered Accountants")
        context.setdefault("firm_address_1", _fs.firm_address_1 or "")
        context.setdefault("firm_address_2", _fs.firm_address_2 or "")
        context.setdefault("firm_phone", _fs.firm_phone or "")
        context.setdefault("firm_email", _fs.firm_email or "")
        context.setdefault("firm_logo_url", _fs.logo_url or "")
    except Exception:
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
    # Always override entity_name/trust_name from live entity data —
    # stored context_data may be stale or missing these entirely.
    if doc.entity:
        context["entity_name"] = doc.entity.entity_name
        context["trust_name"] = doc.entity.entity_name
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
        "directors_declaration", "solvency_resolution",
        "management_representation_letter", "management_rep_letter",
        "management_rep_letter_trust", "management_rep_letter_partnership",
    ):
        from core.models import EntityOfficer
        from django.db import models as _m

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

        # For trusts, also rebuild declaration_signatories (corporate-trustee
        # structure: per-director signature blocks) so stale context_data
        # frozen before the structured-signatories fix is corrected at render
        # time. Mirrors build_trust_context / _build_compliance_context logic.
        if doc.entity.entity_type == "trust":
            trustee_officer = EntityOfficer.objects.filter(
                entity=doc.entity, date_ceased__isnull=True,
            ).filter(
                _m.Q(role="trustee") | _m.Q(roles__contains="trustee")
            ).first()
            trustee_company = trustee_officer.full_name if trustee_officer else (
                getattr(doc.entity, "trustee_name", "") or ""
            )
            signatory_officers = EntityOfficer.objects.filter(
                entity=doc.entity, is_signatory=True, date_ceased__isnull=True,
            ).order_by("display_order", "full_name")
            context["declaration_signatories"] = [
                {
                    "name": o.full_name,
                    "trustee_company": trustee_company,
                    "trust_name": doc.entity.entity_name,
                }
                for o in signatory_officers
            ]

    try:
        html_string = render_to_string(template_name, context)
        pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()
        return pdf_bytes
    except Exception as e:
        logger.error("Failed to render %s to PDF: %s", doc_type, e)
        return None


def build_package_bundle(fy, include_types=None):
    """
    Build a single merged PDF bundle for the given FinancialYear.

    Args:
        fy: FinancialYear instance.
        include_types: Optional list/set of document type strings to include.
            When provided, only those types are rendered into the bundle.
            When None (default), all documents in DOCUMENT_ORDER are included.

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

    # If the caller specified a subset of documents, filter DOCUMENT_ORDER.
    # Always preserve the canonical ordering even when filtering.
    active_order = (
        [dt for dt in DOCUMENT_ORDER if dt in include_types]
        if include_types is not None
        else DOCUMENT_ORDER
    )

    for doc_type in active_order:
        if doc_type == "financial_statements":
            # Primary path: regenerate clean FS via the docxtpl template
            # service with include_watermark=False, then convert to PDF.
            fs_added = False
            try:
                from core.fs_template_service import generate_combined_pdf

                logger.info("Regenerating clean FS for package bundle FY %s", fy.pk)
                # COMPILATION included in the combined PDF for all entity types.
                # DECLARATION excluded for companies (standalone legal doc).
                # Trusts include DECLARATION in the FS bundle.
                _fs_exclude = set()
                if entity.entity_type == "company":
                    _fs_exclude.add("DECLARATION")
                pdf_buffer = generate_combined_pdf(
                    fy.pk, include_watermark=False,
                    exclude_types=_fs_exclude,
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

        # Compilation Report is included in the combined FS PDF via
        # generate_combined_pdf — skip to avoid duplication.
        if doc_type == "compilation_report":
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
