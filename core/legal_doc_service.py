"""
Legal Document Generation — Rendering Service
===============================================
Handles the end-to-end rendering pipeline:
  1. Load LegalDocumentTemplate .docx file
  2. Merge context via docxtpl
  3. Convert to PDF via LibreOffice
  4. Save generated files to LegalDocument record
  5. Auto-save to Governing Documents tab
  6. Log activity

Called by:
  - views_legal_docs.py (synchronous generation)
  - core.tasks.generate_legal_document (Celery async)
"""
import io
import logging
import os
import subprocess
import tempfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from core.models import (
    ActivityLog,
    GoverningDocument,
    LegalDocument,
    LegalDocumentTemplate,
)

logger = logging.getLogger(__name__)


def render_legal_document(legal_document_id):
    """
    Full render pipeline for a LegalDocument record.
    Used by the Celery task and can also be called synchronously.

    Steps:
      1. Load template and context
      2. Render docx via docxtpl
      3. Convert to PDF via LibreOffice
      4. Save files to the LegalDocument record
      5. Auto-save to Governing Documents if applicable
      6. Return result dict

    Returns:
        dict with keys: status, docx_url, pdf_url, document_id
    """
    doc = LegalDocument.objects.select_related(
        "entity", "financial_year", "template"
    ).get(pk=legal_document_id)

    template = doc.template
    if not template or not template.template_file:
        raise ValueError(f"No template file found for document {doc.pk}")

    # Build the full context: prefer context_data (enriched by DCB) over
    # raw parameters (wizard-only fields). context_data contains selected_services,
    # show_service_* flags, and all DCB-generated variables.
    context = doc.context_data or doc.parameters or {}

    # Add standard fields — firm branding from FirmSettings (white-label support)
    try:
        from core.models import FirmSettings
        _fs = FirmSettings.get()
        _firm_name = _fs.firm_name or "MC & S Chartered Accountants"
        context.setdefault("firm_name", _firm_name)
        context.setdefault("firm_logo_url", _fs.logo_url or "")
        context.setdefault("firm_address_1", _fs.firm_address_1 or "")
        context.setdefault("firm_address_2", _fs.firm_address_2 or "")
        context.setdefault("firm_phone", _fs.firm_phone or "")
        context.setdefault("firm_email", _fs.firm_email or "")
    except Exception:
        context.setdefault("firm_name", "MC & S Chartered Accountants")
    context.setdefault("disclaimer", _get_disclaimer())
    context.setdefault("generation_date", timezone.now().strftime("%d %B %Y"))

    # Step 1: Render docx
    docx_bytes = _render_docx(template, context)

    # Step 2: Convert to PDF
    pdf_bytes = _convert_to_pdf(docx_bytes)

    # Step 3: Save files
    entity_name = doc.entity.entity_name if doc.entity else "Unknown"
    fy_year = str(doc.financial_year.end_date.year) if doc.financial_year else ""
    safe_name = entity_name.replace(" ", "_").replace("/", "_")

    docx_filename = f"{safe_name}_{doc.document_type}_{fy_year}.docx"
    doc.generated_file.save(docx_filename, ContentFile(docx_bytes))

    if pdf_bytes:
        pdf_filename = f"{safe_name}_{doc.document_type}_{fy_year}.pdf"
        doc.pdf_file.save(pdf_filename, ContentFile(pdf_bytes))

    doc.save()

    return {
        "status": "ok",
        "document_id": str(doc.pk),
        "docx_url": doc.generated_file.url if doc.generated_file else None,
        "pdf_url": doc.pdf_file.url if doc.pdf_file else None,
    }


def render_and_create_document(
    entity,
    financial_year,
    template,
    doc_type,
    context,
    params,
    user,
    disclaimer_acknowledged=True,
):
    """
    Create a LegalDocument record, render the template, and save files.
    This is the primary entry point for synchronous generation from views.

    Args:
        entity: Entity instance
        financial_year: FinancialYear instance (can be None)
        template: LegalDocumentTemplate instance
        doc_type: str document type key
        context: dict — the full template context from build_context()
        params: dict — the raw wizard parameters (stored for audit)
        user: User instance
        disclaimer_acknowledged: bool

    Returns:
        dict with keys: status, document_id, docx_url, pdf_url, error
    """
    try:
        # Step 1: Render docx from template
        docx_bytes = _render_docx(template, context)

        # Step 2: Convert to PDF
        pdf_bytes = _convert_to_pdf(docx_bytes)

        # Step 3: Create or update LegalDocument record
        # If a draft already exists for this entity + financial_year + doc_type, update it
        # instead of creating a duplicate.
        existing_draft = None
        if financial_year:
            existing_draft = LegalDocument.objects.filter(
                entity=entity,
                financial_year=financial_year,
                document_type=doc_type,
                status=LegalDocument.Status.DRAFT,
            ).order_by('-generated_at').first()

        if existing_draft:
            doc = existing_draft
            doc.template = template
            doc.parameters = params
            doc.generated_by = user
            doc.disclaimer_acknowledged = disclaimer_acknowledged
            doc.disclaimer_acknowledged_at = timezone.now() if disclaimer_acknowledged else None
            doc.save(update_fields=[
                'template', 'parameters', 'generated_by',
                'disclaimer_acknowledged', 'disclaimer_acknowledged_at',
            ])
        else:
            # Auto-increment version
            existing_count = LegalDocument.objects.filter(
                entity=entity,
                document_type=doc_type,
            ).count()

            doc = LegalDocument.objects.create(
                entity=entity,
                financial_year=financial_year,
                template=template,
                document_type=doc_type,
                version=existing_count + 1,
                status=LegalDocument.Status.DRAFT,
                parameters=params,
                generated_by=user,
                disclaimer_acknowledged=disclaimer_acknowledged,
                disclaimer_acknowledged_at=timezone.now() if disclaimer_acknowledged else None,
            )

        # Step 4: Save files (delete old files first if updating an existing draft)
        safe_name = entity.entity_name.replace(" ", "_").replace("/", "_")
        fy_year = str(financial_year.end_date.year) if financial_year else ""

        if existing_draft:
            # Delete old stored files before saving new ones
            if doc.generated_file:
                doc.generated_file.delete(save=False)
            if doc.pdf_file:
                doc.pdf_file.delete(save=False)

        docx_filename = f"{safe_name}_{doc_type}_{fy_year}_v{doc.version}.docx"
        doc.generated_file.save(docx_filename, ContentFile(docx_bytes))

        if pdf_bytes:
            pdf_filename = f"{safe_name}_{doc_type}_{fy_year}_v{doc.version}.pdf"
            doc.pdf_file.save(pdf_filename, ContentFile(pdf_bytes))

        # Step 5: Auto-save to Governing Documents
        _auto_save_governing_doc(doc, docx_bytes, pdf_bytes, user)

        # Step 6: Log activity
        _log_generation_activity(doc, user, params)

        return {
            "status": "ok",
            "document_id": str(doc.pk),
            "docx_url": doc.generated_file.url if doc.generated_file else None,
            "pdf_url": doc.pdf_file.url if doc.pdf_file else None,
        }

    except Exception as e:
        logger.exception("Document generation failed: %s", e)
        return {"status": "error", "error": str(e)}


def render_multi_document_set(
    entity,
    financial_year,
    doc_type,
    context,
    params,
    user,
    template_configs,
    disclaimer_acknowledged=True,
):
    """
    Render multiple documents from a single wizard submission.
    Used for Change of Trustee (3 docs), Fixed Unit Trust (5 docs),
    and Unit Transfer (7 docs).

    Args:
        template_configs: list of dicts, each with:
            - template_type: str (LegalDocumentTemplate.DocumentType value)
            - sub_label: str (e.g. "Deed", "Resolution", "Certificate")
            - context_override: dict (optional per-document context additions)

    Returns:
        dict with keys: status, documents (list of {document_id, docx_url, pdf_url, label})
    """
    documents = []

    for config in template_configs:
        template_type = config.get("template_type", doc_type)
        sub_label = config.get("sub_label", "")
        context_override = config.get("context_override", {})

        # Find the template for this sub-document type
        template = LegalDocumentTemplate.objects.filter(
            document_type=template_type,
            is_active=True,
        ).first()

        if not template:
            # Fall back to the main document type template
            template = LegalDocumentTemplate.objects.filter(
                document_type=doc_type,
                is_active=True,
            ).first()

        if not template:
            documents.append({
                "label": sub_label,
                "status": "error",
                "error": f"No template found for {template_type}",
            })
            continue

        # Merge context with overrides
        merged_context = {**context, **context_override}
        merged_context["sub_document_label"] = sub_label

        try:
            result = render_and_create_document(
                entity=entity,
                financial_year=financial_year,
                template=template,
                doc_type=template_type,
                context=merged_context,
                params=params,
                user=user,
                disclaimer_acknowledged=disclaimer_acknowledged,
            )
            result["label"] = sub_label
            documents.append(result)
        except Exception as e:
            logger.exception("Failed to generate sub-document %s: %s", sub_label, e)
            documents.append({
                "label": sub_label,
                "status": "error",
                "error": str(e),
            })

    all_ok = all(d.get("status") == "ok" for d in documents)
    return {
        "status": "ok" if all_ok else "partial",
        "documents": documents,
    }


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _render_docx(template, context):
    """Render a docxtpl template with the given context and return bytes."""
    from docxtpl import DocxTemplate
    from core.document_context_builder import get_jinja_env, DocumentContextBuilder
    from core.models import FirmSettings
    import tempfile as _tempfile
    import os as _os

    tpl = DocxTemplate(template.template_file.path)

    # Inject InlineImage logo for Word documents
    firm = FirmSettings.get()
    _temp_files = []
    if firm.logo:
        try:
            from docxtpl import InlineImage
            from docx.shared import Cm
            try:
                logo_path = firm.logo.path
                if _os.path.exists(logo_path):
                    context["practice_logo"] = InlineImage(tpl, logo_path, width=Cm(4.0))
                else:
                    context.setdefault("practice_logo", "")
            except NotImplementedError:
                # Object storage — download to temp file
                import requests as _req
                resp = _req.get(firm.logo.url, timeout=10)
                resp.raise_for_status()
                suffix = _os.path.splitext(firm.logo.name)[1] or ".png"
                with _tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(resp.content)
                    _temp_files.append(tmp.name)
                context["practice_logo"] = InlineImage(tpl, _temp_files[-1], width=Cm(4.0))
        except Exception as _exc:
            logger.warning("Logo injection failed in _render_docx: %s", _exc)
            context.setdefault("practice_logo", "")
    else:
        context.setdefault("practice_logo", "")

    jinja_env = get_jinja_env()

    # Escape & in string values only — docxtpl does not auto-escape context values.
    # Must NOT recurse into lists (breaks {%tr for %} row-repeat iteration).
    import html as _html
    safe_context = {}
    for k, v in context.items():
        if isinstance(v, str):
            safe_context[k] = _html.escape(v, quote=False)
        else:
            safe_context[k] = v  # lists, bools, ints, InlineImage — pass through raw
    tpl.render(safe_context, jinja_env=jinja_env)

    buffer = io.BytesIO()
    tpl.save(buffer)

    for _tmp in _temp_files:
        try:
            _os.unlink(_tmp)
        except Exception:
            pass

    return buffer.getvalue()


def _convert_to_pdf(docx_bytes):
    """Convert DOCX bytes to PDF using LibreOffice headless mode.

    Tries multiple LibreOffice binary names/paths for cross-platform
    compatibility. Returns PDF bytes on success, None on failure.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = os.path.join(tmpdir, "document.docx")
            with open(docx_path, "wb") as f:
                f.write(docx_bytes)

            from core.libreoffice_utils import convert_docx_to_pdf
            try:
                convert_docx_to_pdf(docx_path, tmpdir, timeout=120)
            except RuntimeError:
                logger.error(
                    "LibreOffice not installed — PDF conversion unavailable. "
                    "Install with: sudo apt-get install -y libreoffice-writer"
                )
                return None

            pdf_path = os.path.join(tmpdir, "document.pdf")
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    return f.read()
            else:
                stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
                logger.error(
                    "LibreOffice PDF conversion failed (exit code %s): %s",
                    result.returncode, stderr[:500],
                )
                return None
    except subprocess.TimeoutExpired:
        logger.error("LibreOffice PDF conversion timed out after 120s")
        return None
    except Exception as e:
        logger.error("PDF conversion failed: %s", e)
        return None


def _auto_save_governing_doc(doc, docx_bytes, pdf_bytes, user):
    """
    Auto-save the generated document to the entity's Governing Documents tab.
    - Div 7A → AMENDMENT
    - Change of Trustee → AMENDMENT
    - Fixed Unit Trust Deed → primary TRUST_DEED
    - Unit Transfer → AMENDMENT
    """
    from core.legal_doc_contexts import (
        GOVERNING_DOC_DESCRIPTIONS,
        GOVERNING_DOC_IS_PRIMARY,
        GOVERNING_DOC_TYPES,
    )

    doc_type = doc.document_type
    gov_doc_type = GOVERNING_DOC_TYPES.get(doc_type)
    if not gov_doc_type:
        return  # Not a document type that auto-saves

    description_fn = GOVERNING_DOC_DESCRIPTIONS.get(doc_type)
    description = description_fn(doc.parameters) if description_fn else doc.get_document_type_display()
    is_primary = GOVERNING_DOC_IS_PRIMARY.get(doc_type, False)

    # If this is a primary document, archive the existing primary
    if is_primary:
        GoverningDocument.objects.filter(
            entity=doc.entity,
            document_type=gov_doc_type,
            is_primary=True,
            status="active",
        ).update(
            is_primary=False,
            status="archived",
            archived_by=user,
            archived_at=timezone.now(),
        )

    # Save the file (prefer PDF, fall back to DOCX)
    file_bytes = pdf_bytes or docx_bytes
    file_ext = "pdf" if pdf_bytes else "docx"
    safe_name = doc.entity.entity_name.replace(" ", "_").replace("/", "_")
    filename = f"{safe_name}_{doc_type}.{file_ext}"

    gov_doc = GoverningDocument.objects.create(
        entity=doc.entity,
        document_type=gov_doc_type,
        is_primary=is_primary,
        original_filename=filename,
        file_size_bytes=len(file_bytes),
        document_date=timezone.now().date(),
        description=description,
        status="active",
        uploaded_by=user,
        extraction_status="completed",
    )
    gov_doc.file.save(filename, ContentFile(file_bytes))

    # Link back to the LegalDocument
    doc.governing_document = gov_doc
    doc.auto_saved_to_governing_docs = True
    doc.save(update_fields=["governing_document", "auto_saved_to_governing_docs"])


def _log_generation_activity(doc, user, params):
    """Log document generation to the Activity tab."""
    try:
        # Build a brief summary of wizard inputs
        summary_parts = []
        for key in ("borrower_type", "borrower_name", "effective_date", "trust_name",
                     "governing_state", "unit_class", "transfer_count"):
            val = params.get(key)
            if val:
                label = key.replace("_", " ").title()
                summary_parts.append(f"{label}: {val}")
        summary = "; ".join(summary_parts) if summary_parts else "Standard generation"

        user_name = user.get_full_name() if user else "System"
        entity_name = doc.entity.entity_name if doc.entity else "Unknown"
        doc_type_display = doc.get_document_type_display()

        ActivityLog.objects.create(
            user=user,
            event_type=ActivityLog.EventType.DOCUMENT_GENERATED,
            title=f"Generated {doc_type_display}",
            description=(
                f"{user_name} generated {doc_type_display} for {entity_name}. "
                f"Wizard inputs: {summary}. Document ID: {doc.pk}."
            ),
            entity=doc.entity,
            financial_year=doc.financial_year,
        )
    except Exception:
        logger.exception("Failed to log generation activity for document %s", doc.pk)


def log_fusesign_activity(doc, user, recipient_count, envelope_id):
    """Log FuseSign send event to the Activity tab."""
    try:
        user_name = user.get_full_name() if user else "System"
        entity_name = doc.entity.entity_name if doc.entity else "Unknown"
        doc_type_display = doc.get_document_type_display()

        ActivityLog.objects.create(
            user=user,
            event_type=ActivityLog.EventType.DOCUMENT_GENERATED,
            title=f"Sent {doc_type_display} to FuseSign",
            description=(
                f"{user_name} sent {doc_type_display} to FuseSign. "
                f"Recipients: {recipient_count}. Envelope ID: {envelope_id}."
            ),
            entity=doc.entity,
            financial_year=doc.financial_year,
        )
    except Exception:
        logger.exception("Failed to log FuseSign activity for document %s", doc.pk)


def log_fusesign_executed(doc):
    """Log FuseSign execution (all parties signed) to the Activity tab."""
    try:
        entity_name = doc.entity.entity_name if doc.entity else "Unknown"
        doc_type_display = doc.get_document_type_display()

        ActivityLog.objects.create(
            event_type=ActivityLog.EventType.DOCUMENT_GENERATED,
            title=f"{doc_type_display} Fully Executed",
            description=(
                f"FuseSign: {doc_type_display} for {entity_name} fully executed. "
                f"All parties signed."
            ),
            entity=doc.entity,
            financial_year=doc.financial_year,
        )
    except Exception:
        logger.exception("Failed to log FuseSign execution for document %s", doc.pk)


def _get_disclaimer():
    """Return the standard legal disclaimer text, using FirmSettings if available."""
    try:
        from core.models import FirmSettings
        fs = FirmSettings.get()
        if fs.document_disclaimer:
            return fs.document_disclaimer
        name = fs.firm_name or "MC & S Chartered Accountants"
    except Exception:
        name = "MC & S Chartered Accountants"
    return (
        f"This document has been generated by StatementHub on behalf of {name}. "
        "It is based on the information provided and the applicable template. "
        f"{name} recommends that all legal documents be reviewed by a qualified solicitor "
        f"before execution. {name} accepts no liability for any loss arising from the use "
        "of this document without independent legal advice."
    )
