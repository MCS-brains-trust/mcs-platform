"""
Package PDF Bundle Renderer
============================
Renders all LegalDocument records for a FinancialYear into individual PDFs
using weasyprint (from context_data), then merges them with the Financial
Statements PDF into a single client package bundle using pypdf.

Document order follows DOCUMENT_ORDER in package_service.py:
  1. Financial Statements (from GeneratedDocument.file)
  2. Solvency Resolution
  3. Director's Declaration
  4. Director's Report
  5. Management Representation Letter
  6. Cover Letter (Transmittal)
  7. Dividend Statements
  8. Loan Acknowledgment
"""
import io
import logging
import os
import subprocess
import tempfile
from datetime import date

from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

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
    "solvency_resolution",
    "directors_declaration",
    "directors_report",
    "management_representation_letter",
    "cover_letter",
    "dividend_statement",
    "shareholder_loan_acknowledgment",
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
    context.setdefault("is_final", True)
    context.setdefault("watermark_text", "")

    try:
        html_string = render_to_string(template_name, context)
        pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()
        return pdf_bytes
    except Exception as e:
        logger.error("Failed to render %s to PDF: %s", doc_type, e)
        return None


def _render_final_financial_statements_pdf_bytes(fy):
    """Generate fresh final financial statements PDF bytes for bundle assembly."""
    from core.docgen import generate_financial_statements

    buffer = generate_financial_statements(fy.pk, has_open_risks=False, is_final=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "financial_statements.docx")
        pdf_path = os.path.join(tmpdir, "financial_statements.pdf")
        lo_profile_dir = os.path.join(tmpdir, "libreoffice-profile")
        os.makedirs(lo_profile_dir, exist_ok=True)

        with open(docx_path, "wb") as f:
            f.write(buffer.getvalue())
        lo_bin = None
        for candidate in ["soffice", "libreoffice", "/usr/bin/soffice", "/usr/bin/libreoffice"]:
            try:
                subprocess.run([candidate, "--version"], capture_output=True, timeout=5)
                lo_bin = candidate
                break
            except Exception:
                continue
        if not lo_bin:
            raise RuntimeError("LibreOffice is required to generate final financial statements PDF for the client package.")
        result = subprocess.run(
            [
                lo_bin,
                "--headless",
                f"-env:UserInstallation=file://{lo_profile_dir}",
                "--convert-to",
                "pdf",
                "--outdir",
                tmpdir,
                docx_path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or not os.path.exists(pdf_path):
            raise RuntimeError(
                f"Failed to convert final financial statements to PDF: {result.stderr.strip() or result.stdout.strip() or 'unknown error'}"
            )

        with open(pdf_path, "rb") as f:
            return f.read()



def build_package_bundle(fy):
    """
    Build a single merged PDF bundle for the given FinancialYear.
    Returns (pdf_bytes, filename) or raises an exception.
    """
    from pypdf import PdfWriter, PdfReader
    from core.models import LegalDocument

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
            try:
                fs_pdf_bytes = _render_final_financial_statements_pdf_bytes(fy)
                reader = PdfReader(io.BytesIO(fs_pdf_bytes))
                for page in reader.pages:
                    writer.add_page(page)
                docs_added += 1
                logger.info("Added Financial Statements (%d pages)", len(reader.pages))
            except Exception as e:
                logger.warning("Could not add Financial Statements: %s", e)
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

    # Write to bytes
    output = io.BytesIO()
    writer.write(output)
    pdf_bytes = output.getvalue()

    safe_name = entity_name.replace(" ", "_").replace("/", "_")
    filename = f"Client_Package_{safe_name}_{fy_year}.pdf"

    logger.info(
        "Package bundle built for %s %s: %d documents, %d bytes",
        entity_name, fy_year, docs_added, len(pdf_bytes),
    )
    return pdf_bytes, filename
