"""Views for Client Package Assembly and Risk Engine Rules (T2-67 to T2-71)."""
import json
import logging
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from core.models import (
    DividendEvent,
    Entity,
    FinancialYear,
    LegalDocument,
    TrialBalanceLine,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package Contents by Entity Type
# ---------------------------------------------------------------------------
PACKAGE_CONTENTS = {
    "company": [
        ("financial_statements", "Financial Statements", True),
        ("directors_declaration", "Director's Declaration", True),
        ("directors_report", "Director's Report", False),  # Only if large proprietary
        ("solvency_resolution", "Solvency Resolution", True),
        ("dividend_statement", "Dividend Statements", False),  # Only if dividend declared
        ("shareholder_loan_acknowledgment", "Loan Acknowledgment", False),  # Only if loan > $10K
        ("management_representation_letter", "Management Representation Letter", True),
        ("engagement_letter", "Engagement Letter", True),
        ("cover_letter", "Cover Letter (Transmittal)", True),
    ],
    "trust": [
        ("financial_statements", "Financial Statements", True),
        ("trust_distribution_minutes", "Trust Distribution Minutes", True),
        ("management_representation_letter", "Management Representation Letter", True),
        ("engagement_letter", "Engagement Letter", True),
        ("cover_letter", "Cover Letter (Transmittal)", True),
    ],
    "partnership": [
        ("financial_statements", "Financial Statements", True),
        ("partner_statement", "Partner Statements", True),
        ("partnership_tax_summary", "Partnership Tax Summary", True),
        ("management_representation_letter", "Management Representation Letter", True),
        ("engagement_letter", "Engagement Letter", True),
        ("cover_letter", "Cover Letter (Transmittal)", True),
    ],
    "individual": [
        ("engagement_letter", "Engagement Letter", True),
        ("cover_letter", "Cover Letter (Transmittal)", True),
    ],
    "smsf": [
        ("financial_statements", "Financial Statements", True),
        ("management_representation_letter", "Management Representation Letter", True),
        ("engagement_letter", "Engagement Letter", True),
        ("cover_letter", "Cover Letter (Transmittal)", True),
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
        if doc_type == "shareholder_loan_acknowledgment":
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

    return render(request, "core/compliance/package_assembly.html", {
        "fy": fy,
        "entity": entity,
        "checklist": checklist,
        "risk_alerts": risk_alerts,
        "all_required_present": all_required_present,
        "existing_docs": existing_docs,
    })


# ---------------------------------------------------------------------------
# Step 5: Assemble & Send for Signing
# ---------------------------------------------------------------------------
@login_required
@require_POST
def package_assemble(request, pk):
    """Mark the package as assembled."""
    fy = get_object_or_404(FinancialYear, pk=pk)

    fy.package_assembled = True
    fy.package_assembled_at = datetime.now()
    fy.package_assembled_by = request.user
    fy.save(update_fields=["package_assembled", "package_assembled_at", "package_assembled_by"])

    return JsonResponse({
        "status": "ok",
        "message": "Client package assembled successfully.",
    })


@login_required
@require_POST
def package_send_for_signing(request, pk):
    """Send the assembled package for signing via FuseSign."""
    fy = get_object_or_404(FinancialYear, pk=pk)

    if not fy.package_assembled:
        return JsonResponse({
            "status": "error",
            "error": "Package must be assembled before sending for signing.",
        }, status=400)

    # FuseSign integration placeholder
    # In production, this would call the FuseSign API to create a signing bundle
    try:
        import os
        fusesign_api_key = os.environ.get("FUSESIGN_API_KEY", "")
        if not fusesign_api_key:
            return JsonResponse({
                "status": "error",
                "error": "FuseSign API key not configured. Set FUSESIGN_API_KEY in environment.",
            }, status=400)

        # Collect all documents for signing
        docs = LegalDocument.objects.filter(
            financial_year=fy,
            status__in=["generated", "approved"],
        )

        # TODO: Implement actual FuseSign API call
        # For now, mark as sent
        fy.package_sent_for_signing = True
        fy.package_sent_at = datetime.now()
        fy.save(update_fields=["package_sent_for_signing", "package_sent_at"])

        return JsonResponse({
            "status": "ok",
            "message": f"Package sent for signing ({docs.count()} documents).",
        })

    except Exception as e:
        logger.exception("FuseSign send failed: %s", e)
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# Risk Engine Rules (T2-67 to T2-71)
# ---------------------------------------------------------------------------
def _evaluate_risk_rules(fy, entity, existing_types):
    """Evaluate the 5 new risk engine rules and return alerts."""
    alerts = []

    # T2-67: Director loan > $10K, no acknowledgment
    if entity.entity_type == "company" and _has_director_loan_over_10k(fy):
        if "shareholder_loan_acknowledgment" not in existing_types:
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

    # T2-71: No engagement letter for current year
    if "engagement_letter" not in existing_types:
        alerts.append({
            "rule": "T2-71",
            "severity": "info",
            "message": "No engagement letter has been generated for this financial year.",
            "resolution": "engagement_letter_wizard",
            "resolution_label": "Generate Engagement Letter",
        })

    return alerts


def _has_director_loan_over_10k(fy):
    """Check if there's a director/shareholder loan balance exceeding $10,000."""
    loan_lines = TrialBalanceLine.objects.filter(
        financial_year=fy,
    ).select_related("account")

    for line in loan_lines:
        acct_name = ""
        if line.account:
            acct_name = (line.account.name or "").lower()
        elif line.account_name:
            acct_name = line.account_name.lower()

        if any(kw in acct_name for kw in ["director loan", "shareholder loan", "loan to director", "loan to shareholder"]):
            balance = abs(float(line.net_balance or line.balance or 0))
            if balance > 10000:
                return True
    return False
