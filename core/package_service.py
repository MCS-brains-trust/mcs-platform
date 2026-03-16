"""
Package Assembly Service (Phase 13-14).

Provides two main entry points called by Celery tasks:
  - assemble_package(financial_year_id, assembled_by_id)
  - bulk_generate(entity_ids, triggered_by_id)

Workflow:
  1. Scan existing LegalDocuments for the FY
  2. Check against entity-type-specific checklist
  3. Generate any missing Category A (auto-generatable) documents
  4. Generate cover letter with dynamic document list
  5. Combine all PDFs into a single client package
  6. Prepare FuseSign signing bundle with correct tags

Spec reference: Master Implementation Spec §7.9, §7.10, §8.14
"""
import io
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package Contents by Entity Type (mirrors views_package_assembly.py)
# ---------------------------------------------------------------------------
PACKAGE_CONTENTS = {
    "company": [
        ("financial_statements", "Financial Statements", True, False),
        ("directors_declaration", "Director's Declaration", True, True),
        ("directors_report", "Director's Report", False, False),
        ("solvency_resolution", "Solvency Resolution", True, True),
        ("dividend_statement", "Dividend Statements", False, False),
        ("shareholder_loan_acknowledgment", "Loan Acknowledgment", False, False),
        ("management_representation_letter", "Management Representation Letter", True, True),
        ("engagement_letter", "Engagement Letter", True, True),
        ("cover_letter", "Cover Letter (Transmittal)", True, True),
    ],
    "trust": [
        ("financial_statements", "Financial Statements", True, False),
        ("trust_distribution_minutes", "Trust Distribution Minutes", True, False),
        ("management_representation_letter", "Management Representation Letter", True, True),
        ("engagement_letter", "Engagement Letter", True, True),
        ("cover_letter", "Cover Letter (Transmittal)", True, True),
    ],
    "partnership": [
        ("financial_statements", "Financial Statements", True, False),
        ("partner_statement", "Partner Statements", True, False),
        ("partnership_tax_summary", "Partnership Tax Summary", True, False),
        ("management_representation_letter", "Management Representation Letter", True, True),
        ("engagement_letter", "Engagement Letter", True, True),
        ("cover_letter", "Cover Letter (Transmittal)", True, True),
    ],
    "individual": [
        ("engagement_letter", "Engagement Letter", True, True),
        ("cover_letter", "Cover Letter (Transmittal)", True, True),
    ],
    "smsf": [
        ("financial_statements", "Financial Statements", True, False),
        ("management_representation_letter", "Management Representation Letter", True, True),
        ("engagement_letter", "Engagement Letter", True, True),
        ("cover_letter", "Cover Letter (Transmittal)", True, True),
    ],
}

# Document order for the combined PDF
DOCUMENT_ORDER = [
    "cover_letter",
    "engagement_letter",
    "financial_statements",
    "directors_declaration",
    "directors_report",
    "solvency_resolution",
    "dividend_statement",
    "trust_distribution_minutes",
    "partner_statement",
    "partnership_tax_summary",
    "shareholder_loan_acknowledgment",
    "management_representation_letter",
]


# ---------------------------------------------------------------------------
# Main entry points (called by Celery tasks)
# ---------------------------------------------------------------------------
def assemble_package(financial_year_id, assembled_by_id=None):
    """
    Assemble the client package for a financial year.

    Steps:
      1. Scan existing documents
      2. Generate any missing auto-generatable (Category A) documents
      3. Generate cover letter with dynamic document list
      4. Combine all PDFs in correct order
      5. Mark FY as assembled

    Returns dict with status and document counts.
    """
    from core.models import (
        DividendEvent,
        Entity,
        FinancialYear,
        GeneratedDocument,
        LegalDocument,
        TrialBalanceLine,
    )

    fy = FinancialYear.objects.select_related("entity").get(pk=financial_year_id)
    entity = fy.entity

    assembled_by = None
    if assembled_by_id:
        from accounts.models import User
        assembled_by = User.objects.filter(pk=assembled_by_id).first()

    # Step 1: Scan existing documents
    existing_legal_docs = LegalDocument.objects.filter(financial_year=fy)
    existing_types = set(existing_legal_docs.values_list("document_type", flat=True))

    has_fs = GeneratedDocument.objects.filter(
        financial_year=fy,
        document_type=GeneratedDocument.DocumentType.FINANCIAL_STATEMENTS,
    ).exists()
    if has_fs:
        existing_types.add("financial_statements")

    # Step 2: Build checklist and identify missing auto-generatable docs
    required_docs = PACKAGE_CONTENTS.get(entity.entity_type, PACKAGE_CONTENTS["individual"])
    missing_auto = []
    all_docs_present = True

    for doc_type, label, always_required, auto_gen in required_docs:
        is_required = always_required

        # Conditional requirements
        if doc_type == "directors_report" and not getattr(entity, "is_large_proprietary", False):
            is_required = False
        if doc_type == "dividend_statement":
            is_required = DividendEvent.objects.filter(financial_year=fy).exists()
        if doc_type == "shareholder_loan_acknowledgment":
            is_required = _has_director_loan_over_10k(fy)

        is_present = doc_type in existing_types

        if is_required and not is_present:
            all_docs_present = False
            if auto_gen:
                missing_auto.append((doc_type, label))

    # Step 3: Auto-generate missing Category A documents
    generated = []
    generation_errors = []
    for doc_type, label in missing_auto:
        try:
            result = _auto_generate_document(fy, entity, doc_type, assembled_by)
            if result:
                generated.append(doc_type)
                existing_types.add(doc_type)
            else:
                generation_errors.append({"doc_type": doc_type, "label": label, "error": "Generator returned None"})
        except Exception as e:
            logger.warning("Auto-generation failed for %s on FY %s: %s", doc_type, fy.pk, e)
            generation_errors.append({"doc_type": doc_type, "label": label, "error": str(e)})

    # Step 4: Combine PDFs
    combined_pdf_path = None
    try:
        combined_pdf_path = _combine_pdfs(fy, entity, existing_types)
    except Exception as e:
        logger.warning("PDF combination failed for FY %s: %s", fy.pk, e)

    # Step 5: Mark as assembled
    fy.package_assembled = True
    fy.package_assembled_at = timezone.now()
    if assembled_by:
        fy.package_assembled_by = assembled_by
    fy.save(update_fields=["package_assembled", "package_assembled_at", "package_assembled_by"])

    # Log activity
    _log_activity(
        fy,
        assembled_by,
        "package_assembled",
        f"Client package assembled: {len(existing_types)} documents, "
        f"{len(generated)} auto-generated",
    )

    status = "assembled"
    if generation_errors:
        status = "assembled_with_warnings"

    return {
        "status": status,
        "total_documents": len(existing_types),
        "auto_generated": generated,
        "generation_errors": generation_errors,
        "combined_pdf": combined_pdf_path,
    }


def bulk_generate(entity_ids, triggered_by_id=None):
    """
    Generate packages for multiple entities.

    For each entity:
      1. Find the current (latest) financial year
      2. Check readiness
      3. Queue individual assemble_client_package task

    Returns dict with results per entity.
    """
    from core.models import Entity, FinancialYear
    from core.tasks import assemble_client_package

    results = {
        "queued": [],
        "skipped": [],
        "errors": [],
    }

    for entity_id in entity_ids:
        try:
            entity = Entity.objects.get(pk=entity_id)

            # Find the latest FY
            fy = FinancialYear.objects.filter(
                entity=entity,
            ).order_by("-end_date").first()

            if not fy:
                results["skipped"].append({
                    "entity_id": str(entity_id),
                    "entity_name": entity.entity_name,
                    "reason": "No financial year found",
                })
                continue

            if fy.package_assembled:
                results["skipped"].append({
                    "entity_id": str(entity_id),
                    "entity_name": entity.entity_name,
                    "reason": "Package already assembled",
                })
                continue

            # Check minimum readiness: at least financial statements exist
            from core.models import GeneratedDocument
            has_fs = GeneratedDocument.objects.filter(
                financial_year=fy,
                document_type=GeneratedDocument.DocumentType.FINANCIAL_STATEMENTS,
            ).exists()
            if not has_fs:
                results["skipped"].append({
                    "entity_id": str(entity_id),
                    "entity_name": entity.entity_name,
                    "reason": "Financial statements not yet generated",
                })
                continue

            # Queue the assembly task
            assemble_client_package.delay(
                str(fy.pk),
                triggered_by_id,
            )
            results["queued"].append({
                "entity_id": str(entity_id),
                "entity_name": entity.entity_name,
                "fy_id": str(fy.pk),
            })

        except Entity.DoesNotExist:
            results["errors"].append({
                "entity_id": str(entity_id),
                "error": "Entity not found",
            })
        except Exception as e:
            results["errors"].append({
                "entity_id": str(entity_id),
                "error": str(e),
            })

    logger.info(
        "Bulk package generation: %d queued, %d skipped, %d errors",
        len(results["queued"]),
        len(results["skipped"]),
        len(results["errors"]),
    )
    return results


# ---------------------------------------------------------------------------
# Auto-generation of Category A documents
# ---------------------------------------------------------------------------
def _auto_generate_document(fy, entity, doc_type, user=None):
    """
    Auto-generate a single compliance document.
    Returns the created LegalDocument or None.
    """
    from core.models import LegalDocument

    generators = {
        "directors_declaration": _gen_directors_declaration,
        "solvency_resolution": _gen_solvency_resolution,
        "management_representation_letter": _gen_management_rep_letter,
        "engagement_letter": _gen_engagement_letter,
        "cover_letter": _gen_cover_letter,
    }

    generator = generators.get(doc_type)
    if not generator:
        logger.warning("No auto-generator for document type: %s", doc_type)
        return None

    return generator(fy, entity, user)


def _gen_directors_declaration(fy, entity, user=None):
    """Generate a Director's Declaration using the compliance docs view logic."""
    from core.views_compliance_docs import generate_directors_declaration
    return _invoke_compliance_generator(
        "directors_declaration", fy, entity, user,
    )


def _gen_solvency_resolution(fy, entity, user=None):
    """Generate a Solvency Resolution."""
    return _invoke_compliance_generator(
        "solvency_resolution", fy, entity, user,
    )


def _gen_management_rep_letter(fy, entity, user=None):
    """Generate a Management Representation Letter."""
    return _invoke_compliance_generator(
        "management_representation_letter", fy, entity, user,
    )


def _gen_engagement_letter(fy, entity, user=None):
    """Generate an Engagement Letter."""
    return _invoke_compliance_generator(
        "engagement_letter", fy, entity, user,
    )


def _gen_cover_letter(fy, entity, user=None):
    """Generate a Cover Letter with dynamic document list."""
    return _invoke_compliance_generator(
        "cover_letter", fy, entity, user,
    )


def _invoke_compliance_generator(doc_type, fy, entity, user=None):
    """
    Invoke the compliance document generation logic programmatically.
    Creates a LegalDocument record with context_data (matching the
    pattern used by views_compliance_docs.py). The actual rendering
    to PDF happens on download or during PDF combination.
    """
    from core.models import LegalDocument

    try:
        context = _build_compliance_context(doc_type, fy, entity)
        if not context:
            return None

        # Build a human-readable title
        type_labels = {
            "directors_declaration": "Director's Declaration",
            "solvency_resolution": "Solvency Resolution",
            "management_representation_letter": "Management Representation Letter",
            "engagement_letter": "Engagement Letter",
            "cover_letter": "Cover Letter",
        }
        label = type_labels.get(doc_type, doc_type.replace("_", " ").title())
        title = f"{label} \u2014 {entity.entity_name} \u2014 {fy.end_date.year}"

        # Create LegalDocument record
        doc = LegalDocument.objects.create(
            entity=entity,
            financial_year=fy,
            document_type=doc_type,
            title=title,
            status="generated",
            generated_by=user,
            context_data=context,
        )

        logger.info("Auto-generated %s for FY %s", doc_type, fy.pk)
        return doc

    except Exception as e:
        logger.exception("Failed to auto-generate %s for FY %s: %s", doc_type, fy.pk, e)
        return None


def _build_compliance_context(doc_type, fy, entity):
    """Build the context dict for a compliance document."""
    from core.models import EntityOfficer

    directors = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director", "trustee"],
        date_ceased__isnull=True,
    )

    context = {
        "entity_name": entity.entity_name,
        "entity_type": entity.entity_type,
        "acn": getattr(entity, "acn", ""),
        "abn": getattr(entity, "abn", ""),
        "registered_address": getattr(entity, "registered_address", ""),
        "financial_year_end": fy.end_date.strftime("%d %B %Y") if fy.end_date else "",
        "financial_year_start": fy.start_date.strftime("%d %B %Y") if fy.start_date else "",
        "directors": [
            {
                "name": d.full_name,
                "role": d.role,
            }
            for d in directors
        ],
        "date": timezone.now().strftime("%d %B %Y"),
    }

    # Add document-type-specific context
    if doc_type == "cover_letter":
        from core.models import LegalDocument
        existing_docs = LegalDocument.objects.filter(
            financial_year=fy,
            status__in=["generated", "approved", "executed"],
        ).values_list("document_type", flat=True)
        context["enclosed_documents"] = list(existing_docs)

    return context


# ---------------------------------------------------------------------------
# PDF combination
# ---------------------------------------------------------------------------
def _combine_pdfs(fy, entity, existing_types):
    """
    Combine all generated PDFs into a single client package PDF.
    Documents are ordered according to DOCUMENT_ORDER.
    Returns the path to the combined PDF or None.
    """
    from core.models import GeneratedDocument, LegalDocument

    try:
        from PyPDF2 import PdfMerger
    except ImportError:
        logger.warning("PyPDF2 not available, skipping PDF combination")
        return None

    merger = PdfMerger()
    docs_added = 0

    for doc_type in DOCUMENT_ORDER:
        if doc_type not in existing_types:
            continue

        if doc_type == "financial_statements":
            # Regenerate financial statements without watermarks for the
            # client package.  Package assembly only runs after Eva has
            # cleared the year, so DRAFT / AUDIT RISK watermarks must
            # never appear in the bundled PDF.
            fs_added = False
            try:
                fs_pdf_path = _regenerate_fs_for_package(fy)
                if fs_pdf_path and os.path.exists(fs_pdf_path):
                    merger.append(fs_pdf_path)
                    docs_added += 1
                    fs_added = True
                    logger.info("Package FS: regenerated clean PDF for FY %s", fy.pk)
            except Exception as e:
                logger.error(
                    "FALLBACK TRIGGERED in _combine_pdfs for FY %s: %s",
                    fy.pk, str(e), exc_info=True,
                )

            if not fs_added:
                # Fallback: use latest stored FS document.
                # NOTE: this copy may contain DRAFT watermarks if it was
                # generated before the FY was finalised.
                logger.warning(
                    "Package FS fallback (_combine_pdfs): using stored document for FY %s "
                    "(may contain watermarks)", fy.pk
                )
                fs_docs = GeneratedDocument.objects.filter(
                    financial_year=fy,
                    document_type=GeneratedDocument.DocumentType.FINANCIAL_STATEMENTS,
                ).order_by("-generated_at")
                for fs_doc in fs_docs[:1]:
                    if fs_doc.file and os.path.exists(fs_doc.file.path):
                        try:
                            merger.append(fs_doc.file.path)
                            docs_added += 1
                        except Exception as e2:
                            logger.warning("Failed to add stored FS PDF: %s", e2)
        else:
            # Other documents come from LegalDocument
            legal_docs = LegalDocument.objects.filter(
                financial_year=fy,
                document_type=doc_type,
            ).order_by("-created_at")

            for legal_doc in legal_docs[:1]:
                pdf_path = None
                if hasattr(legal_doc, "pdf_file") and legal_doc.pdf_file:
                    pdf_path = legal_doc.pdf_file.path
                elif hasattr(legal_doc, "generated_file") and legal_doc.generated_file:
                    pdf_path = legal_doc.generated_file.path

                if pdf_path and os.path.exists(pdf_path):
                    try:
                        merger.append(pdf_path)
                        docs_added += 1
                    except Exception as e:
                        logger.warning("Failed to add %s PDF: %s", doc_type, e)

    if docs_added == 0:
        return None

    # Save combined PDF
    from django.conf import settings
    output_dir = os.path.join(settings.MEDIA_ROOT, "packages", str(fy.pk))
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(
        output_dir,
        f"client_package_{entity.entity_name}_{fy.end_date.year}.pdf",
    )

    merger.write(output_path)
    merger.close()

    logger.info("Combined PDF created: %s (%d documents)", output_path, docs_added)
    return output_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _regenerate_fs_for_package(fy):
    """
    Regenerate financial statements as a clean PDF (no watermarks) for
    inclusion in the client package.  Returns the path to a temporary PDF
    file, or None on failure.
    """
    import tempfile

    from core.fs_template_service import generate_combined_pdf

    logger.info("_regenerate_fs_for_package: starting for FY %s", fy.pk)

    pdf_buffer = generate_combined_pdf(fy.pk, include_watermark=False)
    logger.info("_regenerate_fs_for_package: PDF generated, %d bytes",
                pdf_buffer.getbuffer().nbytes)

    # Write to temp PDF file (caller expects a file path)
    tmpdir = tempfile.mkdtemp(prefix="shub_pkg_")
    pdf_path = os.path.join(tmpdir, "fs.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_buffer.read())

    return pdf_path


def _has_director_loan_over_10k(fy):
    """Check if there's a director/shareholder loan balance exceeding $10,000."""
    from core.models import TrialBalanceLine

    loan_lines = TrialBalanceLine.objects.filter(
        financial_year=fy,
    ).select_related("mapped_line_item")

    for line in loan_lines:
        acct_name = (line.account_name or "").lower()

        if any(kw in acct_name for kw in [
            "director loan", "shareholder loan",
            "loan to director", "loan to shareholder",
        ]):
            balance = abs(float(line.closing_balance or 0))
            if balance > 10000:
                return True
    return False


def _log_activity(fy, user, action, description):
    """Log an activity entry for the financial year."""
    try:
        from core.models import ActivityLog
        ActivityLog.objects.create(
            financial_year=fy,
            entity=fy.entity,
            user=user,
            action=action,
            description=description,
        )
    except Exception as e:
        logger.warning("Failed to log activity: %s", e)
