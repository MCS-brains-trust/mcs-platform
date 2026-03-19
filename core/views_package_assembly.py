"""Views for Client Package Assembly and Risk Engine Rules (T2-67 to T2-71)."""
import json
import logging
from datetime import datetime

from django.contrib import messages

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from core.models import (
    DividendEvent,
    Entity,
    FinancialYear,
    LegalDocument,
    TrialBalanceLine,
    YearEndCommentary,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package Contents by Entity Type
# ---------------------------------------------------------------------------
PACKAGE_CONTENTS = {
    # NOTE: Engagement letters are a pre-engagement document generated at job start
    # (Roll Forward), not a client package deliverable. They are excluded here.
    "company": [
        ("financial_statements", "Financial Statements", True),
        ("directors_declaration", "Director's Declaration", True),
        ("directors_report", "Director's Report", False),  # Only if large proprietary
        ("solvency_resolution", "Solvency Resolution", True),
        ("dividend_statement", "Dividend Statements", False),  # Only if dividend declared
        ("shareholder_loan_ack", "Loan Acknowledgment", False),  # Only if loan > $10K
        ("management_rep_letter", "Management Representation Letter", True),
        ("client_cover_letter", "Cover Letter (Transmittal)", True),
    ],
    "trust": [
        ("financial_statements", "Financial Statements", True),
        ("distribution_minutes", "Trust Distribution Minutes", True),
        ("management_rep_letter", "Management Representation Letter", True),
        ("client_cover_letter", "Cover Letter (Transmittal)", True),
    ],
    "partnership": [
        ("financial_statements", "Financial Statements", True),
        ("partner_statement", "Partner Statements", True),
        ("partnership_tax_summary", "Partnership Tax Summary", True),
        ("management_rep_letter", "Management Representation Letter", True),
        ("client_cover_letter", "Cover Letter (Transmittal)", True),
    ],
    "individual": [
        ("client_cover_letter", "Cover Letter (Transmittal)", True),
    ],
    "smsf": [
        ("financial_statements", "Financial Statements", True),
        ("management_rep_letter", "Management Representation Letter", True),
        ("client_cover_letter", "Cover Letter (Transmittal)", True),
    ],
}
# ---------------------------------------------------------------------------
# Step 1: Scan & Checklist
# ---------------------------------------------------------------------------
@login_required
def package_assembly(request, pk):
    """Package assembly wizard — 5-step workflow."""
    fy = get_object_or_404(FinancialYear, pk=pk)
    entity = fy.entity

    if not fy.can_assemble_package:
        messages.warning(
            request,
            "The client package is only available after Eva has cleared this financial year."
        )

    # Get the required documents for this entity type
    required_docs = PACKAGE_CONTENTS.get(entity.entity_type, PACKAGE_CONTENTS["individual"])

    # Check which documents exist
    existing_docs = LegalDocument.objects.filter(financial_year=fy)
    existing_types = set(existing_docs.values_list("document_type", flat=True))

    # Check for financial statements (from GeneratedDocument model)
    from core.models import GeneratedDocument
    has_fs = GeneratedDocument.objects.filter(
        financial_year=fy,
        document_type=GeneratedDocument.DocumentType.FINANCIAL_STATEMENTS,
    ).exists()
    if has_fs:
        existing_types.add("financial_statements")

    # Build checklist
    checklist = []
    for doc_type, label, always_required in required_docs:
        is_required = always_required
        is_present = doc_type in existing_types

        # Conditional requirements
        if doc_type == "directors_report" and not getattr(entity, "is_large_proprietary", False):
            is_required = False
        if doc_type == "dividend_statement":
            is_required = DividendEvent.objects.filter(financial_year=fy).exists()
        if doc_type == "shareholder_loan_ack":
            is_required = _has_director_loan_over_10k(fy)

        checklist.append({
            "doc_type": doc_type,
            "label": label,
            "required": is_required,
            "present": is_present,
            "status": "complete" if is_present else ("missing" if is_required else "optional"),
        })

    # Run risk engine rules
    risk_alerts = _evaluate_risk_rules(fy, entity, existing_types)

    all_required_present = all(
        item["present"] for item in checklist if item["required"]
    )

    # Year-end commentary (if exists)
    commentary = YearEndCommentary.objects.filter(financial_year=fy).first()

    return render(request, "core/compliance/package_assembly.html", {
        "fy": fy,
        "entity": entity,
        "checklist": checklist,
        "risk_alerts": risk_alerts,
        "all_required_present": all_required_present,
        "existing_docs": existing_docs,
        "commentary": commentary,
    })


# ---------------------------------------------------------------------------
# Step 5: Assemble & Send for Signing
# ---------------------------------------------------------------------------
@login_required
@require_POST
def package_assemble(request, pk):
    """Mark the package as assembled."""
    fy = get_object_or_404(FinancialYear, pk=pk)

    if not fy.can_assemble_package:
        return JsonResponse({
            "status": "error",
            "error": "Client package is only available after Eva has cleared this financial year.",
        }, status=400)

    fy.package_assembled = True
    fy.package_assembled_at = datetime.now()
    fy.package_assembled_by = request.user
    fy.save(update_fields=["package_assembled", "package_assembled_at", "package_assembled_by"])

    return JsonResponse({
        "status": "ok",
        "message": "Client package assembled successfully.",
    })


@login_required
def package_download_bundle(request, pk):
    """
    Download the full client package as a single merged PDF bundle.
    Combines Financial Statements + all LegalDocuments in the correct order.
    The accountant can then upload this bundle to FuseSign manually.

    Optional query param:
        ?include=financial_statements,directors_declaration,...
    When provided, only the listed document types are included in the bundle.
    When omitted, all available documents are included (default behaviour).
    """
    fy = get_object_or_404(FinancialYear, pk=pk)

    if not fy.can_assemble_package:
        return HttpResponse(
            "Client package is only available after Eva has cleared this financial year.",
            status=400,
            content_type="text/plain",
        )

    if not fy.package_assembled:
        return HttpResponse(
            "Package must be assembled before downloading.",
            status=400,
            content_type="text/plain",
        )

    # Parse optional ?include= query param (comma-separated doc type list)
    include_param = request.GET.get("include", "").strip()
    include_types = None
    if include_param:
        include_types = {t.strip() for t in include_param.split(",") if t.strip()}

    try:
        from core.package_pdf_renderer import build_package_bundle
        pdf_bytes, filename = build_package_bundle(fy, include_types=include_types)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.exception("Package bundle generation failed: %s", e)
        return HttpResponse(
            f"Bundle generation failed: {e}",
            status=500,
            content_type="text/plain",
        )


@login_required
@require_POST
def package_send_for_signing(request, pk):
    """
    Redirect to the bundle download — the accountant downloads the PDF
    and uploads it to FuseSign manually until the API integration is built.
    """
    from django.http import JsonResponse as JR
    fy = get_object_or_404(FinancialYear, pk=pk)
    if not fy.can_assemble_package:
        return JR({
            "status": "error",
            "error": "Client package is only available after Eva has cleared this financial year.",
        }, status=400)
    if not fy.package_assembled:
        return JR({"status": "error", "error": "Package must be assembled first."}, status=400)
    from django.urls import reverse
    download_url = reverse("core:package_download_bundle", kwargs={"pk": str(fy.pk)})
    return JR({"status": "ok", "download_url": download_url})


# ---------------------------------------------------------------------------
# Risk Engine Rules (T2-67 to T2-71)
# ---------------------------------------------------------------------------
def _evaluate_risk_rules(fy, entity, existing_types):
    """Evaluate the 5 new risk engine rules and return alerts."""
    alerts = []

    # T2-67: Director loan > $10K, no acknowledgment
    if entity.entity_type == "company" and _has_director_loan_over_10k(fy):
        if "shareholder_loan_ack" not in existing_types:
            alerts.append({
                "rule": "T2-67",
                "severity": "warning",
                "message": "Director/shareholder loan exceeds $10,000 but no loan acknowledgment has been generated.",
                "resolution": "generate_loan_acknowledgment",
                "resolution_label": "Generate Loan Acknowledgment",
            })

    # T2-68: Dividend declared, no statements
    if DividendEvent.objects.filter(financial_year=fy).exists():
        if "dividend_statement" not in existing_types:
            alerts.append({
                "rule": "T2-68",
                "severity": "warning",
                "message": "Dividend has been declared but no dividend statements have been generated.",
                "resolution": "dividend_wizard",
                "resolution_label": "Generate Dividend Statements",
            })

    # T2-69: FS generated, no solvency resolution (company only)
    if entity.entity_type == "company" and "financial_statements" in existing_types:
        if "solvency_resolution" not in existing_types:
            alerts.append({
                "rule": "T2-69",
                "severity": "warning",
                "message": "Financial statements generated but no solvency resolution exists.",
                "resolution": "generate_solvency_resolution",
                "resolution_label": "Generate Solvency Resolution",
            })

    # T2-70: FS generated, no director's declaration (company only)
    if entity.entity_type == "company" and "financial_statements" in existing_types:
        if "directors_declaration" not in existing_types:
            alerts.append({
                "rule": "T2-70",
                "severity": "warning",
                "message": "Financial statements generated but no director's declaration exists.",
                "resolution": "generate_directors_declaration",
                "resolution_label": "Generate Declaration",
            })

    # NOTE: T2-71 (engagement letter check) removed — engagement letters are
    # generated at job start (Roll Forward), not at package assembly time.

    # T2-72: No management representation letter
    if "management_rep_letter" not in existing_types:
        alerts.append({
            "rule": "T2-72",
            "severity": "warning",
            "message": "No management representation letter has been generated for this financial year.",
            "resolution": "generate_management_rep_letter",
            "resolution_label": "Generate Management Rep Letter",
        })

    # T2-73: No cover letter (generate last, after all other docs are present)
    if "client_cover_letter" not in existing_types:
        alerts.append({
            "rule": "T2-73",
            "severity": "info",
            "message": "No cover letter (transmittal) has been generated. Generate this last, after all other documents are ready.",
            "resolution": "generate_cover_letter",
            "resolution_label": "Generate Cover Letter",
        })

    return alerts


def _has_director_loan_over_10k(fy):
    """Check if there's a director/shareholder loan balance exceeding $10,000."""
    loan_lines = TrialBalanceLine.objects.filter(
        financial_year=fy,
    ).select_related("mapped_line_item")

    for line in loan_lines:
        acct_name = (line.account_name or "").lower()

        if any(kw in acct_name for kw in ["director loan", "shareholder loan", "loan to director", "loan to shareholder"]):
            balance = abs(float(line.closing_balance or 0))
            if balance > 10000:
                return True
    return False


# ---------------------------------------------------------------------------
# Year-End Commentary Views
# ---------------------------------------------------------------------------

@login_required
@require_POST
def yearend_commentary_generate(request, pk):
    """
    Kick off year-end commentary generation for a financial year.
    Runs synchronously (fast enough for a single LLM call).
    Returns JSON immediately after saving the GENERATING record,
    then the client polls yearend_commentary_poll until done.
    """
    fy = get_object_or_404(FinancialYear, pk=pk)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        body = {}
    tone = body.get("tone", "professional")

    try:
        from core.eva_yearend_commentary import generate_yearend_commentary
        # Run synchronously — LLM call is ~3-5s, acceptable for this use case
        generate_yearend_commentary(
            financial_year_id=str(fy.pk),
            generated_by_id=str(request.user.pk),
            tone=tone,
        )
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        logger.exception("Year-end commentary generation error: %s", exc)
        return JsonResponse({"status": "error", "error": str(exc)}, status=500)


@login_required
def yearend_commentary_poll(request, pk):
    """
    Poll the current status of the year-end commentary generation.
    Returns status, current step, and error if any.
    """
    fy = get_object_or_404(FinancialYear, pk=pk)
    commentary = YearEndCommentary.objects.filter(financial_year=fy).first()

    if not commentary:
        return JsonResponse({"status": "none"})

    return JsonResponse({
        "status": commentary.status,
        "step": commentary.generation_step or "",
        "error": commentary.error_message or "",
    })


@login_required
@require_POST
def yearend_commentary_save(request, pk):
    """
    Save manual edits to the commentary sections.
    Accepts JSON: {"sections": {"snapshot": "...", "revenue": "...", ...}}
    """
    fy = get_object_or_404(FinancialYear, pk=pk)
    commentary = get_object_or_404(YearEndCommentary, financial_year=fy)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)

    sections = body.get("sections", {})
    update_fields = []

    section_map = {
        "snapshot": "section_snapshot",
        "revenue": "section_revenue",
        "costs": "section_costs",
        "watch_items": "section_watch_items",
        "actions": "section_actions",
    }

    for key, field in section_map.items():
        if key in sections:
            setattr(commentary, field, sections[key])
            update_fields.append(field)

    if update_fields:
        # Rebuild full_content from sections
        commentary.full_content = "\n\n".join([
            f"## Year Snapshot\n{commentary.section_snapshot}",
            f"## Revenue & Income Analysis\n{commentary.section_revenue}",
            f"## Expense & Margin Analysis\n{commentary.section_costs}",
            f"## Key Observations\n{commentary.section_watch_items}",
            f"## Recommended Actions\n{commentary.section_actions}",
        ])
        update_fields.append("full_content")
        commentary.save(update_fields=update_fields)

    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def yearend_commentary_mark_reviewed(request, pk):
    """Mark the year-end commentary as reviewed by the current user."""
    from datetime import timezone as dt_timezone
    fy = get_object_or_404(FinancialYear, pk=pk)
    commentary = get_object_or_404(YearEndCommentary, financial_year=fy)

    commentary.status = YearEndCommentary.Status.REVIEWED
    commentary.reviewed_by = request.user
    commentary.reviewed_at = datetime.now(dt_timezone.utc)
    commentary.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    return JsonResponse({"status": "ok"})
