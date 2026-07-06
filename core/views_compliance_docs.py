"""Views for Company Compliance Documents — dividends, declarations, solvency, director's report."""
import json
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from config.authorization import get_financial_year_for_user
from core.models import (
    DividendEvent,
    DividendShareholderAllocation,
    Entity,
    EntityOfficer,
    FinancialYear,
    LegalDocument,
)

logger = logging.getLogger(__name__)


def _sanitise_context_for_storage(ctx):
    """
    Return a copy of ctx with all non-JSON-serialisable values removed.
    InlineImage objects (logo), date/Decimal types, etc. must not be
    persisted to the database — they are only needed at render time.
    """
    from datetime import date as _date, datetime as _datetime
    from decimal import Decimal as _Decimal

    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(i) for i in obj]
        if isinstance(obj, (_date, _datetime)):
            return obj.isoformat()
        if isinstance(obj, _Decimal):
            return float(obj)
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        # Anything else (InlineImage, etc.) — drop it
        return None

    return _clean(ctx)


def _format_acn_abn(acn, abn):
    """Build a combined 'ACN: xxx / ABN: xxx' display string."""
    parts = []
    if acn:
        d = "".join(c for c in str(acn) if c.isdigit())
        parts.append(f"ACN: {d[:3]} {d[3:6]} {d[6:9]}" if len(d) == 9 else f"ACN: {d}")
    if abn:
        d = "".join(c for c in str(abn) if c.isdigit())
        parts.append(f"ABN: {d[:2]} {d[2:5]} {d[5:8]} {d[8:11]}" if len(d) == 11 else f"ABN: {d}")
    return " / ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Dividend Wizard (covers 5.1 Dividend Statement + 5.2 Declaration Minutes)
# ---------------------------------------------------------------------------
@login_required
def dividend_wizard(request, pk):
    """Dividend wizard — create a dividend event with shareholder allocations."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    # Pre-populate shareholders from EntityOfficer
    shareholders = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director", "shareholder", "director_shareholder"],
    ).order_by("full_name")

    total_shares = entity.total_shares_on_issue or sum(
        s.shares_held or 0 for s in shareholders
    )

    existing_events = DividendEvent.objects.filter(financial_year=fy)

    return render(request, "core/compliance/dividend_wizard.html", {
        "fy": fy,
        "entity": entity,
        "shareholders": shareholders,
        "total_shares": total_shares,
        "existing_events": existing_events,
    })


@login_required
@require_POST
def dividend_create(request, pk):
    """Create a dividend event and auto-allocate to shareholders."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)

    # Validate every shareholder belongs to this entity up-front so a bad id
    # never leaves a half-created dividend event behind (prevents cross-entity
    # officer references / IDOR).
    allocations_data = data.get("allocations", [])
    shareholders_by_id = {}
    for alloc in allocations_data:
        shareholder_id = alloc.get("shareholder_id")
        try:
            shareholders_by_id[shareholder_id] = EntityOfficer.objects.get(
                pk=shareholder_id, entity=entity
            )
        except (EntityOfficer.DoesNotExist, ValueError, TypeError):
            return JsonResponse(
                {
                    "status": "error",
                    "error": f"Invalid shareholder for this entity: {shareholder_id}",
                },
                status=400,
            )

    try:
        with transaction.atomic():
            event = DividendEvent.objects.create(
                entity=entity,
                financial_year=fy,
                dividend_type=data.get("dividend_type", "final"),
                total_amount=Decimal(str(data.get("total_amount", 0))),
                franking_percentage=Decimal(str(data.get("franking_percentage", 100))),
                company_tax_rate=Decimal(str(data.get("company_tax_rate", 25))),
                record_date=data.get("record_date"),
                payment_date=data.get("payment_date"),
                declaration_date=data.get("declaration_date"),
                solvency_confirmed=data.get("solvency_confirmed", False),
                resolution_type=data.get("resolution_type", "board_resolution"),
                meeting_location=data.get("meeting_location", ""),
                franking_account_opening_balance=Decimal(str(data.get("franking_opening", 0))) if data.get("franking_opening") else None,
                created_by=request.user,
            )

            # Auto-allocate to shareholders
            total_shares = sum(a.get("shares_held", 0) for a in allocations_data) or 1

            for alloc in allocations_data:
                shareholder = shareholders_by_id[alloc.get("shareholder_id")]
                shares = alloc.get("shares_held", 0)
                proportion = Decimal(str(shares)) / Decimal(str(total_shares))
                dividend_amount = event.total_amount * proportion
                franking_credit = dividend_amount * Decimal(str(event.franking_credit_per_dollar))

                DividendShareholderAllocation.objects.create(
                    dividend_event=event,
                    shareholder=shareholder,
                    shares_held=shares,
                    dividend_amount=dividend_amount.quantize(Decimal("0.01")),
                    franking_credit=franking_credit.quantize(Decimal("0.01")),
                    withholding_tax=Decimal(str(alloc.get("withholding_tax", 0))),
                )

            # Calculate franking account closing balance
            total_franking_debits = sum(
                a.franking_credit for a in event.allocations.all()
            )
            if event.franking_account_opening_balance is not None:
                event.franking_account_closing_balance = (
                    event.franking_account_opening_balance - total_franking_debits
                )
                event.save(update_fields=["franking_account_closing_balance"])

        return JsonResponse({
            "status": "ok",
            "event_id": str(event.pk),
            "message": f"Dividend event created with {len(allocations_data)} allocations.",
            "franking_deficit": (
                event.franking_account_closing_balance is not None
                and event.franking_account_closing_balance < 0
            ),
        })

    except Exception as e:
        logger.exception("Dividend creation failed: %s", e)
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


@login_required
def dividend_detail(request, pk, event_pk):
    """View a dividend event with all allocations."""
    fy = get_financial_year_for_user(request, pk)
    event = get_object_or_404(DividendEvent, pk=event_pk, financial_year=fy)
    allocations = event.allocations.select_related("shareholder").all()

    return render(request, "core/compliance/dividend_detail.html", {
        "fy": fy,
        "entity": fy.entity,
        "event": event,
        "allocations": allocations,
    })


# ---------------------------------------------------------------------------
# 5.3 Solvency Resolution (Auto-generated)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def generate_solvency_resolution(request, pk):
    """Auto-generate a solvency resolution for a company FY."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    if entity.entity_type != "company":
        return JsonResponse({"status": "error", "error": "Solvency resolution is only for companies."}, status=400)

    directors = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director", "director_shareholder"],
    )

    acn = entity.acn or ""
    abn = entity.abn or ""
    fy_end_formatted = fy.end_date.strftime("%-d %B %Y") if fy.end_date else ""
    context = {
        "entity_name": entity.entity_name,
        "acn": acn,
        "abn": abn,
        "acn_abn": _format_acn_abn(acn, abn),
        "directors": [{"name": d.full_name} for d in directors],
        "signatories": [{"name": d.full_name, "role": "Director"} for d in directors],
        "financial_year": str(fy.end_date.year),
        "financial_year_end": fy_end_formatted,
        "resolution_date": fy_end_formatted,
    }

    doc = LegalDocument.objects.create(
        financial_year=fy,
        entity=entity,
        document_type="solvency_resolution",
        title=f"Solvency Resolution — {entity.entity_name} — {fy.end_date.year}",
        context_data=_sanitise_context_for_storage(context),
        generated_by=request.user,
        status="generated",
    )

    return JsonResponse({
        "status": "ok",
        "document_id": str(doc.pk),
        "message": "Solvency resolution generated.",
    })


# ---------------------------------------------------------------------------
# 5.4 Director's Declaration (Auto-generated)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def generate_directors_declaration(request, pk):
    """Auto-generate a director's declaration for a company FY."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    if entity.entity_type != "company":
        return JsonResponse({"status": "error", "error": "Director's declaration is only for companies."}, status=400)

    # Determine wording variant
    is_large = getattr(entity, "is_large_proprietary", False)
    framework = getattr(entity, "reporting_framework", "general_purpose")

    if is_large:
        variant = "large_proprietary"
    elif framework == "AASB1060":
        variant = "small_proprietary_aasb1060"
    else:
        variant = "small_proprietary_general"

    directors = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director", "director_shareholder"],
    )

    acn = entity.acn or ""
    abn = entity.abn or ""
    fy_end_formatted = fy.end_date.strftime("%-d %B %Y") if fy.end_date else ""
    context = {
        "entity_name": entity.entity_name,
        "acn": acn,
        "abn": abn,
        "acn_abn": _format_acn_abn(acn, abn),
        "variant": variant,
        "directors": [{"name": d.full_name} for d in directors],
        "signatories": [{"name": d.full_name, "role": "Director"} for d in directors],
        "financial_year": str(fy.end_date.year),
        "financial_year_end": fy_end_formatted,
        "signing_director": directors.first().full_name if directors.exists() else "",
    }

    doc = LegalDocument.objects.create(
        financial_year=fy,
        entity=entity,
        document_type="directors_declaration",
        title=f"Director's Declaration — {entity.entity_name} — {fy.end_date.year}",
        context_data=_sanitise_context_for_storage(context),
        generated_by=request.user,
        status="generated",
    )

    return JsonResponse({
        "status": "ok",
        "document_id": str(doc.pk),
        "message": "Director's declaration generated.",
    })


# ---------------------------------------------------------------------------
# 5.5 Director's Report (Hybrid — structured + Eva narrative)
# ---------------------------------------------------------------------------
@login_required
def directors_report_wizard(request, pk):
    """Wizard for Director's Report — structured sections + Eva drafting."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    directors = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director", "director_shareholder"],
    )

    return render(request, "core/compliance/directors_report_wizard.html", {
        "fy": fy,
        "entity": entity,
        "directors": directors,
    })


@login_required
@require_POST
def directors_report_draft_with_eva(request, pk):
    """Use Eva to draft narrative sections of the Director's Report."""
    import anthropic
    import os

    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)

    section = data.get("section", "principal_activities")
    existing_text = data.get("existing_text", "")

    section_prompts = {
        "principal_activities": "Write the 'Principal Activities' section describing what the company does.",
        "review_of_operations": "Write the 'Review of Operations' section summarising the financial performance for the year.",
        "significant_changes": "Write the 'Significant Changes in State of Affairs' section.",
        "events_after_reporting": "Write the 'Events After the Reporting Period' section.",
        "likely_developments": "Write the 'Likely Developments and Expected Results' section.",
        "environmental_regulation": "Write the 'Environmental Regulation' section.",
        "dividends": "Write the 'Dividends' section describing any dividends declared or paid.",
        "indemnification_insurance": "Write the 'Indemnification and Insurance of Officers' section.",
    }

    prompt_text = section_prompts.get(section, f"Write the '{section}' section.")

    try:
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        )

        try:
            from core.models import FirmSettings
            _firm_ai_name = FirmSettings.get().firm_name or "MC & S Chartered Accountants"
        except Exception:
            _firm_ai_name = "MC & S Chartered Accountants"
        system_prompt = (
            f"You are Eva, the AI assistant for {_firm_ai_name}. "
            "You are drafting a section of a Director's Report under the Corporations Act 2001 (Cth). "
            "Write professionally and concisely. Use Australian accounting terminology. "
            f"The company is {entity.entity_name} (ACN: {entity.acn or 'N/A'})."
        )

        user_prompt = f"{prompt_text}\n\n"
        if existing_text:
            user_prompt += f"The user has already drafted:\n{existing_text}\n\nPlease improve and expand this."

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        return JsonResponse({
            "status": "ok",
            "text": response.content[0].text,
            "section": section,
        })

    except Exception as e:
        logger.exception("Eva draft failed: %s", e)
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# 5.6 Shareholder Loan Acknowledgment
# ---------------------------------------------------------------------------
@login_required
@require_POST
def generate_loan_acknowledgment(request, pk):
    """Generate a shareholder loan acknowledgment."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)

    context = {
        "entity_name": entity.entity_name,
        "acn": entity.acn or "",
        "abn": entity.abn or "",
        "shareholder_name": data.get("shareholder_name", ""),
        "loan_balance": data.get("loan_balance", ""),
        "financial_year": str(fy.end_date.year),
        "financial_year_end": str(fy.end_date),
        "acknowledgment_date": data.get("acknowledgment_date", str(fy.end_date)),
        "loan_terms": data.get("loan_terms", ""),
    }

    doc = LegalDocument.objects.create(
        financial_year=fy,
        entity=entity,
        document_type="shareholder_loan_ack",
        title=f"Loan Acknowledgment — {data.get('shareholder_name', 'Unknown')} — {entity.entity_name}",
        context_data=_sanitise_context_for_storage(context),
        generated_by=request.user,
        status="generated",
    )

    return JsonResponse({
        "status": "ok",
        "document_id": str(doc.pk),
        "message": "Shareholder loan acknowledgment generated.",
    })


# ---------------------------------------------------------------------------
# 5.10 Management Representation Letter (Auto-generated)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def generate_management_rep_letter(request, pk):
    """Auto-generate a management representation letter."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    from django.db import models as _m

    directors = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director", "director_shareholder", "trustee", "partner"],
    )

    fy_end_formatted = fy.end_date.strftime("%-d %B %Y") if fy.end_date else ""
    context = {
        "entity_name": entity.entity_name,
        "entity_type": entity.entity_type,
        "abn": entity.abn or "",
        "acn": entity.acn or "",
        "financial_year": str(fy.end_date.year),
        "financial_year_end": fy_end_formatted,
        "signatories": [{"name": d.full_name, "role": d.get_role_display()} for d in directors],
    }

    # Trust corporate-trustee structure: build declaration_signatories
    # so the HTML template renders one block per individual director-signatory
    if entity.entity_type == "trust":
        trustee_officer = EntityOfficer.objects.filter(
            entity=entity, date_ceased__isnull=True,
        ).filter(
            _m.Q(role="trustee") | _m.Q(roles__contains="trustee")
        ).first()
        trustee_company = trustee_officer.full_name if trustee_officer else (
            getattr(entity, "trustee_name", "") or ""
        )
        signatory_officers = EntityOfficer.objects.filter(
            entity=entity, is_signatory=True, date_ceased__isnull=True,
        ).order_by("display_order", "full_name")
        context["declaration_signatories"] = [
            {
                "name": o.full_name,
                "trustee_company": trustee_company,
                "trust_name": entity.entity_name,
            }
            for o in signatory_officers
        ]

    # Enrich with DocumentContextBuilder
    try:
        from core.document_context_builder import DocumentContextBuilder
        dcb = DocumentContextBuilder(entity, financial_year=fy)
        enriched = dcb.build("management_rep_letter")
        for k, v in enriched.items():
            if k not in context or k.startswith("practice_"):
                context[k] = v
    except Exception as _e:
        logger.warning("DCB enrichment skipped for management_rep_letter: %s", _e)

    doc = LegalDocument.objects.create(
        financial_year=fy,
        entity=entity,
        document_type="management_rep_letter",
        title=f"Management Representation Letter — {entity.entity_name} — {fy.end_date.year}",
        context_data=_sanitise_context_for_storage(context),
        generated_by=request.user,
        status="generated",
    )

    return JsonResponse({
        "status": "ok",
        "document_id": str(doc.pk),
        "message": "Management representation letter generated.",
    })


# ---------------------------------------------------------------------------
# 5.11 Client Cover Letter / Transmittal (Auto-generated LAST)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def generate_cover_letter(request, pk):
    """Auto-generate a client cover letter listing all enclosed documents."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    # Build a clean, deduplicated list of enclosed document display names.
    # Only include standard package documents, in the correct order.
    from core.models import GeneratedDocument

    STANDARD_PACKAGE = [
        ("financial_statements", "Financial Statements"),
        ("directors_declaration", "Director's Declaration"),
        ("solvency_resolution", "Solvency Resolution"),
        ("management_rep_letter", "Management Representation Letter"),
    ]

    existing_docs = LegalDocument.objects.filter(
        financial_year=fy,
        status__in=["generated", "signed"],
    )
    existing_types = set(existing_docs.values_list("document_type", flat=True))

    fs_exists = GeneratedDocument.objects.filter(
        financial_year=fy,
        document_type=GeneratedDocument.DocumentType.FINANCIAL_STATEMENTS,
    ).exists()

    enclosed_list = []
    for doc_type, display_name in STANDARD_PACKAGE:
        if doc_type == "financial_statements":
            if fs_exists:
                enclosed_list.append(display_name)
        elif doc_type in existing_types:
            enclosed_list.append(display_name)

    context = {
        "entity_name": entity.entity_name,
        "entity_type": entity.entity_type,
        "abn": entity.abn or "",
        "financial_year": str(fy.end_date.year),
        "financial_year_end": str(fy.end_date),
        "enclosed_documents": enclosed_list,
        "document_count": len(enclosed_list),
        "date": str(fy.end_date),
    }

    # Enrich with DocumentContextBuilder
    try:
        from core.document_context_builder import DocumentContextBuilder
        dcb = DocumentContextBuilder(entity, financial_year=fy)
        enriched = dcb.build("client_cover_letter")
        for k, v in enriched.items():
            if k not in context or k.startswith("practice_"):
                context[k] = v
    except Exception as _e:
        logger.warning("DCB enrichment skipped for cover_letter: %s", _e)

    doc = LegalDocument.objects.create(
        financial_year=fy,
        entity=entity,
        document_type="client_cover_letter",
        title=f"Cover Letter — {entity.entity_name} — {fy.end_date.year}",
        context_data=_sanitise_context_for_storage(context),
        generated_by=request.user,
        status="generated",
    )

    return JsonResponse({
        "status": "ok",
        "document_id": str(doc.pk),
        "message": f"Cover letter generated with {len(enclosed_list)} enclosed documents.",
    })
