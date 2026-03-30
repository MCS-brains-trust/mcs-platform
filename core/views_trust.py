"""
Trust Distribution Tab Views
=============================

Handles the 6-stage trust distribution workflow:
  Stage 1: Income Calculation
  Stage 2: Beneficiary Profiling
  Stage 3: Distribution Modelling
  Stage 4: Section 100A Assessment
  Stage 5: Trust Elections
  Stage 6: Documents
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import models as django_models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import (
    FinancialYear, TrustWorkspace, BeneficiaryProfile,
    DistributionScenario, Section100AAssessment, TrustElectionRecord,
    EntityOfficer, ActivityLog,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Workspace Management
# ---------------------------------------------------------------------------
@login_required
def trust_workspace_api(request, pk):
    """
    GET  /api/years/<pk>/trust-workspace/ — Get or create workspace
    POST /api/years/<pk>/trust-workspace/ — Update workspace fields
    """
    fy = get_object_or_404(FinancialYear, pk=pk)

    if request.method == "GET":
        workspace, created = TrustWorkspace.objects.get_or_create(
            financial_year=fy,
        )
        if created:
            # Auto-populate income streams from TB
            _auto_populate_income(workspace)
            # Auto-create beneficiary profiles from officers
            _auto_create_beneficiary_profiles(workspace)

        # Auto-skip Stage 4 for unit trusts / trusts with unitholders
        if _entity_has_unitholders(fy.entity):
            if workspace.stage_4_status != TrustWorkspace.StageStatus.COMPLETED:
                workspace.stage_4_status = TrustWorkspace.StageStatus.COMPLETED
                workspace.save(update_fields=["stage_4_status", "updated_at"])

        return JsonResponse(_serialize_workspace(workspace))

    elif request.method == "POST":
        workspace = get_object_or_404(TrustWorkspace, financial_year=fy)
        data = json.loads(request.body)

        # Update income streams if provided
        if "income_streams" in data:
            workspace.income_streams = data["income_streams"]
        if "net_distributable_income" in data:
            try:
                workspace.net_distributable_income = Decimal(str(data["net_distributable_income"]))
            except (InvalidOperation, ValueError):
                pass

        workspace.save()
        return JsonResponse(_serialize_workspace(workspace))


@login_required
def trust_stage_update(request, pk, stage_num):
    """
    POST /api/years/<pk>/trust-workspace/stage/<stage_num>/
    Update a specific stage's status.
    """
    fy = get_object_or_404(FinancialYear, pk=pk)
    workspace = get_object_or_404(TrustWorkspace, financial_year=fy)
    data = json.loads(request.body)
    new_status = data.get("status", "")

    if new_status not in dict(TrustWorkspace.StageStatus.choices):
        return JsonResponse({"error": "Invalid status"}, status=400)

    if stage_num < 1 or stage_num > 6:
        return JsonResponse({"error": "Invalid stage number"}, status=400)

    field_name = f"stage_{stage_num}_status"
    setattr(workspace, field_name, new_status)
    workspace.save(update_fields=[field_name, "updated_at"])

    stage_names = {
        1: "Income Calculation", 2: "Beneficiary Profiling",
        3: "Distribution Modelling", 4: "Section 100A Assessment",
        5: "Trust Elections", 6: "Documents",
    }

    ActivityLog.objects.create(
        user=request.user,
        event_type="trust_stage_update",
        title=f"Trust Stage {stage_num}: {stage_names.get(stage_num, '')} → {new_status}",
        description=f"Updated stage {stage_num} ({stage_names.get(stage_num, '')}) to {new_status}",
        entity=fy.entity,
        financial_year=fy,
        url=f"/entities/years/{fy.pk}/?tab=trust",
    )

    return JsonResponse({
        "status": "ok",
        "stage": stage_num,
        "new_status": new_status,
        "all_completed": workspace.all_stages_completed(),
    })


# ---------------------------------------------------------------------------
# Stage 2: Beneficiary Profiles
# ---------------------------------------------------------------------------
@login_required
def beneficiary_profiles_api(request, pk):
    """
    GET  — List all beneficiary profiles for this workspace
    POST — Update a beneficiary profile
    """
    fy = get_object_or_404(FinancialYear, pk=pk)
    workspace = get_object_or_404(TrustWorkspace, financial_year=fy)

    if request.method == "GET":
        profiles = workspace.beneficiary_profiles.select_related("beneficiary").all()
        return JsonResponse({
            "profiles": [_serialize_beneficiary_profile(p) for p in profiles],
        })

    elif request.method == "POST":
        data = json.loads(request.body)
        profile_id = data.get("id")
        if not profile_id:
            return JsonResponse({"error": "Profile ID required"}, status=400)

        profile = get_object_or_404(BeneficiaryProfile, pk=profile_id, trust_workspace=workspace)

        for field in ["beneficiary_type", "other_income", "marginal_rate",
                       "bracket_remaining", "franking_surplus", "include_in_distribution",
                       "exclusion_reason", "tax_residency"]:
            if field in data:
                val = data[field]
                if field in ("other_income", "marginal_rate", "bracket_remaining", "franking_surplus"):
                    try:
                        val = Decimal(str(val)) if val not in (None, "", "null") else None
                    except (InvalidOperation, ValueError):
                        val = None
                setattr(profile, field, val)

        profile.save()
        return JsonResponse(_serialize_beneficiary_profile(profile))


# ---------------------------------------------------------------------------
# Stage 3: Distribution Scenarios
# ---------------------------------------------------------------------------
@login_required
def distribution_scenarios_api(request, pk):
    """
    GET  — List all scenarios
    POST — Create or update a scenario
    """
    fy = get_object_or_404(FinancialYear, pk=pk)
    workspace = get_object_or_404(TrustWorkspace, financial_year=fy)

    if request.method == "GET":
        scenarios = workspace.scenarios.all()
        return JsonResponse({
            "scenarios": [_serialize_scenario(s) for s in scenarios],
        })

    elif request.method == "POST":
        data = json.loads(request.body)
        scenario_id = data.get("id")

        with transaction.atomic():
            if scenario_id:
                scenario = get_object_or_404(DistributionScenario, pk=scenario_id, trust_workspace=workspace)
            else:
                # Lock workspace row to prevent race condition on count check
                TrustWorkspace.objects.select_for_update().get(pk=workspace.pk)
                if workspace.scenarios.count() >= 3:
                    return JsonResponse({"error": "Maximum 3 scenarios allowed"}, status=400)
                scenario = DistributionScenario(trust_workspace=workspace)

            if "name" in data:
                scenario.name = data["name"]
            if "allocations" in data:
                scenario.allocations = data["allocations"]
            if "total_tax" in data:
                try:
                    scenario.total_tax = Decimal(str(data["total_tax"]))
                except (InvalidOperation, ValueError):
                    pass
            if "tax_saved_vs_equal" in data:
                try:
                    scenario.tax_saved_vs_equal = Decimal(str(data["tax_saved_vs_equal"]))
                except (InvalidOperation, ValueError):
                    pass

            scenario.save()
        return JsonResponse(_serialize_scenario(scenario))


@login_required
@require_POST
def confirm_scenario(request, pk, scenario_pk):
    """POST — Confirm a scenario as the final distribution."""
    fy = get_object_or_404(FinancialYear, pk=pk)

    with transaction.atomic():
        workspace = TrustWorkspace.objects.select_for_update().get(financial_year=fy)
        scenario = get_object_or_404(DistributionScenario, pk=scenario_pk, trust_workspace=workspace)

        # Unconfirm all others
        workspace.scenarios.select_for_update().update(is_confirmed=False)
        scenario.is_confirmed = True
        scenario.save(update_fields=["is_confirmed"])

        workspace.confirmed_scenario = scenario
        workspace.save(update_fields=["confirmed_scenario"])

    ActivityLog.objects.create(
        user=request.user,
        event_type="trust_scenario_confirmed",
        title=f"Distribution scenario '{scenario.name}' confirmed",
        description=f"Confirmed '{scenario.name}' as the final distribution for {fy.entity.entity_name}",
        entity=fy.entity,
        financial_year=fy,
        url=f"/entities/years/{fy.pk}/?tab=trust",
    )

    return JsonResponse({"status": "ok", "scenario_id": str(scenario.pk)})


@login_required
@require_POST
def delete_scenario(request, pk, scenario_pk):
    """POST — Delete a distribution scenario."""
    fy = get_object_or_404(FinancialYear, pk=pk)
    workspace = get_object_or_404(TrustWorkspace, financial_year=fy)
    scenario = get_object_or_404(DistributionScenario, pk=scenario_pk, trust_workspace=workspace)

    if scenario.is_confirmed:
        workspace.confirmed_scenario = None
        workspace.save(update_fields=["confirmed_scenario"])

    scenario.delete()
    return JsonResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Stage 4: Section 100A Assessments
# ---------------------------------------------------------------------------
@login_required
def section_100a_api(request, pk):
    """
    GET  — List all Section 100A assessments
    POST — Update an assessment
    """
    fy = get_object_or_404(FinancialYear, pk=pk)
    workspace = get_object_or_404(TrustWorkspace, financial_year=fy)

    if request.method == "GET":
        assessments = workspace.section_100a_assessments.select_related("beneficiary").all()
        return JsonResponse({
            "assessments": [_serialize_100a(a) for a in assessments],
        })

    elif request.method == "POST":
        data = json.loads(request.body)
        assessment_id = data.get("id")

        with transaction.atomic():
            if assessment_id:
                assessment = get_object_or_404(
                    Section100AAssessment, pk=assessment_id, trust_workspace=workspace
                )
            else:
                beneficiary_id = data.get("beneficiary_id")
                beneficiary = get_object_or_404(EntityOfficer, pk=beneficiary_id)
                assessment, _ = Section100AAssessment.objects.get_or_create(
                    trust_workspace=workspace, beneficiary=beneficiary,
                )

            for q in ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8"]:
                if q in data:
                    setattr(assessment, q, data[q])

            if "resolution_strategy" in data:
                assessment.resolution_strategy = data["resolution_strategy"]

            assessment.save()  # risk_rating calculated in save()

            # Update overall workspace risk (inside transaction for consistency)
            _update_overall_100a_risk(workspace)

        return JsonResponse(_serialize_100a(assessment))


# ---------------------------------------------------------------------------
# Stage 5: Trust Elections
# ---------------------------------------------------------------------------
@login_required
def trust_elections_api(request, pk):
    """
    GET  — List all election records
    POST — Update an election record
    """
    fy = get_object_or_404(FinancialYear, pk=pk)
    workspace = get_object_or_404(TrustWorkspace, financial_year=fy)

    if request.method == "GET":
        elections = workspace.election_records.select_related(
            "test_individual", "related_entity"
        ).all()
        return JsonResponse({
            "elections": [_serialize_election(e) for e in elections],
        })

    elif request.method == "POST":
        data = json.loads(request.body)
        election_id = data.get("id")

        if election_id:
            election = get_object_or_404(
                TrustElectionRecord, pk=election_id, trust_workspace=workspace
            )
        else:
            election = TrustElectionRecord(trust_workspace=workspace)

        if "election_type" in data:
            election.election_type = data["election_type"]
        if "status" in data:
            election.status = data["status"]
        if "effective_date" in data and data["effective_date"]:
            election.effective_date = data["effective_date"]
        if "test_individual_id" in data and data["test_individual_id"]:
            election.test_individual_id = data["test_individual_id"]
        if "related_entity_id" in data and data["related_entity_id"]:
            election.related_entity_id = data["related_entity_id"]

        election.save()
        return JsonResponse(_serialize_election(election))


@login_required
@require_POST
def confirm_election(request, pk, election_pk):
    """POST — Confirm an election record."""
    fy = get_object_or_404(FinancialYear, pk=pk)
    workspace = get_object_or_404(TrustWorkspace, financial_year=fy)
    election = get_object_or_404(
        TrustElectionRecord, pk=election_pk, trust_workspace=workspace
    )

    election.confirmed_by = request.user
    election.confirmed_at = timezone.now()
    election.save(update_fields=["confirmed_by", "confirmed_at"])

    return JsonResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Eva Context for Trust Tab
# ---------------------------------------------------------------------------
@login_required
def trust_eva_context(request, pk):
    """
    GET /api/years/<pk>/trust-workspace/eva-context/
    Provides trust-specific context for Eva's Finalisation Gate.
    """
    fy = get_object_or_404(FinancialYear, pk=pk)

    try:
        workspace = TrustWorkspace.objects.get(financial_year=fy)
    except TrustWorkspace.DoesNotExist:
        return JsonResponse({"error": "No trust workspace found"}, status=404)

    context = {
        "workspace_status": {
            "stage_1": workspace.stage_1_status,
            "stage_2": workspace.stage_2_status,
            "stage_3": workspace.stage_3_status,
            "stage_4": workspace.stage_4_status,
            "stage_5": workspace.stage_5_status,
            "stage_6": workspace.stage_6_status,
            "all_completed": workspace.all_stages_completed(),
        },
        "income": {
            "ndi": str(workspace.net_distributable_income or 0),
            "streams": workspace.income_streams,
        },
        "confirmed_scenario": None,
        "section_100a_risk": workspace.section_100a_overall_risk,
        "beneficiary_count": workspace.beneficiary_profiles.count(),
        "elections": [],
    }

    if workspace.confirmed_scenario:
        s = workspace.confirmed_scenario
        context["confirmed_scenario"] = {
            "name": s.name,
            "allocations": s.allocations,
            "total_tax": str(s.total_tax) if s.total_tax else None,
        }

    for e in workspace.election_records.all():
        context["elections"].append({
            "type": e.get_election_type_display(),
            "status": e.get_status_display(),
            "confirmed": e.confirmed_at is not None,
        })

    return JsonResponse(context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _auto_populate_income(workspace):
    """Auto-populate income streams from trial balance."""
    from core.eva_trust_planning import _calculate_income_streams
    income_data = _calculate_income_streams(workspace.financial_year)
    workspace.income_streams = income_data["income_streams"]
    workspace.net_distributable_income = Decimal(income_data["net_distributable_income"])
    workspace.save(update_fields=["income_streams", "net_distributable_income"])


def _auto_create_beneficiary_profiles(workspace):
    """Create beneficiary profiles from entity officers."""
    entity = workspace.financial_year.entity
    officers = EntityOfficer.objects.filter(entity=entity)

    for officer in officers:
        BeneficiaryProfile.objects.get_or_create(
            trust_workspace=workspace,
            beneficiary=officer,
            defaults={
                "beneficiary_type": _map_officer_to_beneficiary_type(officer),
                "tax_residency": getattr(officer, "tax_residency", "AU") or "AU",
            },
        )


def _map_officer_to_beneficiary_type(officer):
    """Map an EntityOfficer to a BeneficiaryProfile type."""
    bt = getattr(officer, "beneficiary_type", "")
    mapping = {
        "adult": BeneficiaryProfile.BeneficiaryType.ADULT,
        "minor": BeneficiaryProfile.BeneficiaryType.MINOR,
        "company": BeneficiaryProfile.BeneficiaryType.COMPANY,
        "trust": BeneficiaryProfile.BeneficiaryType.TRUST,
        "smsf": BeneficiaryProfile.BeneficiaryType.SMSF,
    }
    return mapping.get(bt, BeneficiaryProfile.BeneficiaryType.ADULT)


def _update_overall_100a_risk(workspace):
    """Update the workspace's overall Section 100A risk rating."""
    assessments = workspace.section_100a_assessments.all()
    if not assessments:
        workspace.section_100a_overall_risk = ""
        workspace.save(update_fields=["section_100a_overall_risk"])
        return

    ratings = [a.risk_rating for a in assessments]
    if "red" in ratings:
        overall = "red"
    elif "amber" in ratings:
        overall = "amber"
    else:
        overall = "green"

    workspace.section_100a_overall_risk = overall
    workspace.save(update_fields=["section_100a_overall_risk"])


def _entity_has_unitholders(entity):
    """Return True if the entity is a unit trust or has any unit holder officers."""
    if entity.entity_type == 'trust_unit':
        return True
    return entity.officers.filter(
        django_models.Q(role='unit_holder') | django_models.Q(roles__contains='unit_holder')
    ).exists()


def _serialize_workspace(workspace):
    """Serialize a TrustWorkspace to JSON."""
    entity = workspace.financial_year.entity
    has_unitholders = _entity_has_unitholders(entity)
    return {
        "id": str(workspace.pk),
        "financial_year_id": str(workspace.financial_year_id),
        "entity_type": entity.entity_type,
        "has_unitholders": has_unitholders,
        "stages": {
            "1": {"status": workspace.stage_1_status, "name": "Income Calculation"},
            "2": {"status": workspace.stage_2_status, "name": "Beneficiary Profiling"},
            "3": {"status": workspace.stage_3_status, "name": "Distribution Modelling"},
            "4": {"status": workspace.stage_4_status, "name": "Section 100A Assessment"},
            "5": {"status": workspace.stage_5_status, "name": "Trust Elections"},
            "6": {"status": workspace.stage_6_status, "name": "Documents"},
        },
        "all_completed": workspace.all_stages_completed(),
        "net_distributable_income": str(workspace.net_distributable_income or 0),
        "income_streams": workspace.income_streams,
        "section_100a_overall_risk": workspace.section_100a_overall_risk,
        "confirmed_scenario_id": str(workspace.confirmed_scenario_id) if workspace.confirmed_scenario_id else None,
    }


def _serialize_beneficiary_profile(profile):
    """Serialize a BeneficiaryProfile to JSON."""
    return {
        "id": str(profile.pk),
        "beneficiary_id": str(profile.beneficiary_id),
        "beneficiary_name": profile.beneficiary.full_name if hasattr(profile.beneficiary, 'full_name') else str(profile.beneficiary),
        "beneficiary_type": profile.beneficiary_type,
        "other_income": str(profile.other_income) if profile.other_income else None,
        "marginal_rate": str(profile.marginal_rate) if profile.marginal_rate else None,
        "bracket_remaining": str(profile.bracket_remaining) if profile.bracket_remaining else None,
        "franking_surplus": str(profile.franking_surplus) if profile.franking_surplus else None,
        "include_in_distribution": profile.include_in_distribution,
        "exclusion_reason": profile.exclusion_reason,
        "tax_residency": profile.tax_residency,
    }


def _serialize_scenario(scenario):
    """Serialize a DistributionScenario to JSON."""
    return {
        "id": str(scenario.pk),
        "name": scenario.name,
        "allocations": scenario.allocations,
        "total_tax": str(scenario.total_tax) if scenario.total_tax else None,
        "tax_saved_vs_equal": str(scenario.tax_saved_vs_equal) if scenario.tax_saved_vs_equal else None,
        "is_confirmed": scenario.is_confirmed,
        "created_at": scenario.created_at.isoformat(),
    }


def _serialize_100a(assessment):
    """Serialize a Section100AAssessment to JSON."""
    return {
        "id": str(assessment.pk),
        "beneficiary_id": str(assessment.beneficiary_id),
        "beneficiary_name": assessment.beneficiary.full_name if hasattr(assessment.beneficiary, 'full_name') else str(assessment.beneficiary),
        "q1": assessment.q1, "q2": assessment.q2, "q3": assessment.q3, "q4": assessment.q4,
        "q5": assessment.q5, "q6": assessment.q6, "q7": assessment.q7, "q8": assessment.q8,
        "risk_rating": assessment.risk_rating,
        "resolution_strategy": assessment.resolution_strategy,
        "reviewed_by": str(assessment.reviewed_by) if assessment.reviewed_by else None,
        "reviewed_at": assessment.reviewed_at.isoformat() if assessment.reviewed_at else None,
    }


def _serialize_election(election):
    """Serialize a TrustElectionRecord to JSON."""
    return {
        "id": str(election.pk),
        "election_type": election.election_type,
        "election_type_display": election.get_election_type_display(),
        "status": election.status,
        "status_display": election.get_status_display(),
        "effective_date": str(election.effective_date) if election.effective_date else None,
        "test_individual_id": str(election.test_individual_id) if election.test_individual_id else None,
        "test_individual_name": (
            election.test_individual.full_name
            if election.test_individual and hasattr(election.test_individual, 'full_name')
            else None
        ),
        "related_entity_id": str(election.related_entity_id) if election.related_entity_id else None,
        "confirmed_by": str(election.confirmed_by) if election.confirmed_by else None,
        "confirmed_at": election.confirmed_at.isoformat() if election.confirmed_at else None,
    }


# =============================================================================
# Stage 6 — Document Generation Views
# =============================================================================

def _get_confirmed_scenario_data(workspace):
    """
    Return a list of dicts describing each beneficiary's confirmed allocation.
    Each dict: {name, type, total, streams, percentage}
    Returns (rows, total_distributed, ndi)
    """
    confirmed = workspace.confirmed_scenario
    if not confirmed or not confirmed.allocations:
        return [], Decimal("0"), workspace.net_distributable_income or Decimal("0")

    profiles = {
        str(p.beneficiary_id): p
        for p in workspace.beneficiary_profiles.select_related("beneficiary").all()
    }

    rows = []
    total_distributed = Decimal("0")
    for ben_id, streams in confirmed.allocations.items():
        total_for_ben = sum(Decimal(str(v or 0)) for v in streams.values())
        if total_for_ben <= 0:
            continue
        profile = profiles.get(str(ben_id))
        name = profile.beneficiary.full_name if profile else f"Beneficiary {str(ben_id)[:8]}"
        ben_type = profile.get_beneficiary_type_display() if profile else ""
        rows.append({
            "name": name,
            "type": ben_type,
            "total": total_for_ben,
            "streams": {k: Decimal(str(v or 0)) for k, v in streams.items()},
            "percentage": Decimal("0"),
        })
        total_distributed += total_for_ben

    rows.sort(key=lambda r: r["name"])
    if total_distributed > 0:
        for r in rows:
            r["percentage"] = (r["total"] / total_distributed * 100).quantize(Decimal("0.01"))

    return rows, total_distributed, workspace.net_distributable_income or Decimal("0")


@login_required
def trust_generate_beneficiary_statements(request, pk):
    """
    GET /api/years/<pk>/trust-workspace/generate/beneficiary-statements/
    Generate a single DOCX containing all beneficiary statements from the
    confirmed distribution scenario.
    """
    import io
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from django.http import HttpResponse

    fy = get_object_or_404(FinancialYear, pk=pk)
    try:
        workspace = TrustWorkspace.objects.get(financial_year=fy)
    except TrustWorkspace.DoesNotExist:
        from django.http import JsonResponse as JR
        return JR({"error": "No trust workspace found."}, status=404)

    rows, total_distributed, ndi = _get_confirmed_scenario_data(workspace)
    entity = fy.entity
    fy_year = "".join(c for c in fy.year_label if c.isdigit()) or str(fy.end_date.year)
    fy_end = f"30 June {fy_year}"

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    STREAM_LABELS = {
        "ordinary": "Ordinary Income",
        "cgt_discount": "CGT Discount",
        "cgt_non_discount": "CGT Non-Discount",
        "franked_dividends": "Franked Dividends",
        "franking_credits": "Franking Credits",
        "tax_free": "Tax-Free Income",
    }

    for i, row in enumerate(rows):
        if i > 0:
            doc.add_page_break()

        h = doc.add_heading(f"Beneficiary Distribution Statement", level=1)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER

        doc.add_paragraph(f"Trust: {entity.entity_name}")
        doc.add_paragraph(f"Financial Year: {fy.year_label}")
        doc.add_paragraph(f"Year Ended: {fy_end}")
        doc.add_paragraph(f"Beneficiary: {row['name']}")
        if row["type"]:
            doc.add_paragraph(f"Beneficiary Type: {row['type']}")
        doc.add_paragraph(f"Share of Distribution: {row['percentage']}%")
        doc.add_paragraph("")

        table = doc.add_table(rows=1, cols=2)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "Income Stream"
        hdr[1].text = "Amount"
        for run in hdr[0].paragraphs[0].runs:
            run.bold = True
        for run in hdr[1].paragraphs[0].runs:
            run.bold = True

        for stream_key, amount in row["streams"].items():
            if amount and Decimal(str(amount)) > 0:
                r = table.add_row().cells
                r[0].text = STREAM_LABELS.get(stream_key, stream_key.replace("_", " ").title())
                r[1].text = f"${Decimal(str(amount)):,.2f}"

        total_row = table.add_row().cells
        total_row[0].text = "Total Distribution"
        total_row[1].text = f"${row['total']:,.2f}"
        for cell in total_row:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True

        doc.add_paragraph("")
        doc.add_paragraph(
            "This statement is prepared for income tax purposes and shows your "
            "entitlement to the net income of the trust for the year ended "
            f"{fy_end}. Please retain this statement for your tax records."
        )

    if not rows:
        doc.add_paragraph(
            "No confirmed distribution scenario found. Please complete Stage 3 "
            "(Distribution Modelling) and confirm a scenario before generating statements."
        )

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    entity_name = entity.entity_name.replace(" ", "_")
    filename = f"{entity_name}_Beneficiary_Statements_{fy.year_label}.docx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def trust_generate_distribution_summary(request, pk):
    """
    GET /api/years/<pk>/trust-workspace/generate/distribution-summary/
    Generate a DOCX distribution summary from the confirmed scenario.
    """
    import io
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from django.http import HttpResponse

    fy = get_object_or_404(FinancialYear, pk=pk)
    try:
        workspace = TrustWorkspace.objects.get(financial_year=fy)
    except TrustWorkspace.DoesNotExist:
        from django.http import JsonResponse as JR
        return JR({"error": "No trust workspace found."}, status=404)

    rows, total_distributed, ndi = _get_confirmed_scenario_data(workspace)
    entity = fy.entity
    fy_year = "".join(c for c in fy.year_label if c.isdigit()) or str(fy.end_date.year)
    fy_end = f"30 June {fy_year}"
    confirmed = workspace.confirmed_scenario

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    h = doc.add_heading("Trust Distribution Summary", level=1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"Trust: {entity.entity_name}")
    doc.add_paragraph(f"Financial Year: {fy.year_label}  |  Year Ended: {fy_end}")
    doc.add_paragraph(f"Scenario: {confirmed.name if confirmed else 'N/A'}")
    doc.add_paragraph(f"Net Distributable Income: ${ndi:,.2f}")
    doc.add_paragraph("")

    if rows:
        # Build column headers: Stream | Ben1 | Ben2 | ... | Total
        STREAM_LABELS = {
            "ordinary": "Ordinary",
            "cgt_discount": "CGT Discount",
            "cgt_non_discount": "CGT Non-Discount",
            "franked_dividends": "Franked Dividends",
            "franking_credits": "Franking Credits",
            "tax_free": "Tax-Free",
        }
        all_streams = list(STREAM_LABELS.keys())
        col_count = 1 + len(rows) + 1  # Stream + beneficiaries + Total

        table = doc.add_table(rows=1, cols=col_count)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "Income Stream"
        for i, row in enumerate(rows):
            hdr[i + 1].text = row["name"]
        hdr[-1].text = "Total"
        for cell in hdr:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True

        stream_totals = {s: Decimal("0") for s in all_streams}
        for stream_key in all_streams:
            label = STREAM_LABELS.get(stream_key, stream_key)
            r = table.add_row().cells
            r[0].text = label
            row_total = Decimal("0")
            for i, ben_row in enumerate(rows):
                amt = ben_row["streams"].get(stream_key, Decimal("0"))
                r[i + 1].text = f"${amt:,.2f}" if amt else "-"
                row_total += amt
                stream_totals[stream_key] += amt
            r[-1].text = f"${row_total:,.2f}"

        # Total row
        total_row = table.add_row().cells
        total_row[0].text = "TOTAL"
        for i, ben_row in enumerate(rows):
            total_row[i + 1].text = f"${ben_row['total']:,.2f}"
        total_row[-1].text = f"${total_distributed:,.2f}"
        for cell in total_row:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True

        doc.add_paragraph("")

        # Percentage summary
        doc.add_heading("Distribution Percentages", level=2)
        pct_table = doc.add_table(rows=1, cols=3)
        pct_table.style = "Table Grid"
        ph = pct_table.rows[0].cells
        ph[0].text = "Beneficiary"
        ph[1].text = "Amount"
        ph[2].text = "Percentage"
        for cell in ph:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True
        for ben_row in rows:
            pr = pct_table.add_row().cells
            pr[0].text = ben_row["name"]
            pr[1].text = f"${ben_row['total']:,.2f}"
            pr[2].text = f"{ben_row['percentage']}%"
    else:
        doc.add_paragraph(
            "No confirmed distribution scenario found. Please complete Stage 3 "
            "(Distribution Modelling) and confirm a scenario before generating this summary."
        )

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    entity_name = entity.entity_name.replace(" ", "_")
    filename = f"{entity_name}_Distribution_Summary_{fy.year_label}.docx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def trust_generate_100a_summary(request, pk):
    """
    GET /api/years/<pk>/trust-workspace/generate/100a-summary/
    Generate a DOCX Section 100A risk assessment summary.
    """
    import io
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from django.http import HttpResponse

    fy = get_object_or_404(FinancialYear, pk=pk)
    try:
        workspace = TrustWorkspace.objects.get(financial_year=fy)
    except TrustWorkspace.DoesNotExist:
        from django.http import JsonResponse as JR
        return JR({"error": "No trust workspace found."}, status=404)

    entity = fy.entity
    fy_year = "".join(c for c in fy.year_label if c.isdigit()) or str(fy.end_date.year)
    fy_end = f"30 June {fy_year}"

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    h = doc.add_heading("Section 100A Risk Assessment Summary", level=1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"Trust: {entity.entity_name}")
    doc.add_paragraph(f"Financial Year: {fy.year_label}  |  Year Ended: {fy_end}")
    overall = workspace.section_100a_overall_risk or "Not assessed"
    doc.add_paragraph(f"Overall Risk Rating: {overall.upper()}")
    doc.add_paragraph("")

    QUESTIONS = [
        ("Q1", "Was the distribution made under a reimbursement agreement?"),
        ("Q2", "Did the beneficiary receive the economic benefit of the distribution?"),
        ("Q3", "Was the distribution part of a pre-arranged plan?"),
        ("Q4", "Were funds redirected to another party?"),
        ("Q5", "Is the beneficiary a related party of the trustee?"),
        ("Q6", "Was there a tax benefit from the arrangement?"),
        ("Q7", "Is the arrangement consistent with an ordinary family dealing? (protective)"),
        ("Q8", "Does the arrangement fall within a safe harbour? (protective)"),
    ]

    assessments = workspace.section_100a_assessments.select_related("beneficiary").all()
    if assessments:
        for assessment in assessments:
            doc.add_heading(assessment.beneficiary.full_name, level=2)
            doc.add_paragraph(f"Risk Rating: {assessment.risk_rating.upper() if assessment.risk_rating else 'Not assessed'}")

            table = doc.add_table(rows=1, cols=3)
            table.style = "Table Grid"
            hdr = table.rows[0].cells
            hdr[0].text = "#"
            hdr[1].text = "Question"
            hdr[2].text = "Answer"
            for cell in hdr:
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.bold = True

            answers = [
                assessment.q1, assessment.q2, assessment.q3, assessment.q4,
                assessment.q5, assessment.q6, assessment.q7, assessment.q8,
            ]
            for (qnum, question), answer in zip(QUESTIONS, answers):
                r = table.add_row().cells
                r[0].text = qnum
                r[1].text = question
                r[2].text = (answer or "Not answered").title()

            if assessment.resolution_strategy:
                doc.add_paragraph(f"Resolution Strategy: {assessment.resolution_strategy}")
            doc.add_paragraph("")
    else:
        doc.add_paragraph(
            "No Section 100A assessments found. This stage may have been skipped "
            "(e.g. unit trust) or not yet completed."
        )

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    entity_name = entity.entity_name.replace(" ", "_")
    filename = f"{entity_name}_Section_100A_Summary_{fy.year_label}.docx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# =============================================================================
# Post Distribution — Create Journal Entries
# =============================================================================

@login_required
@require_POST
def trust_post_distribution(request, pk):
    """
    POST /api/years/<pk>/trust-workspace/post-distribution/
    Creates journal entries in the trial balance from the confirmed distribution scenario.

    For each beneficiary allocation:
      DR  Distribution Payable — <Beneficiary Name>  (liability account)
      CR  Net Income / Retained Earnings              (equity account)

    The journal is created as DRAFT so the accountant can review before posting.
    """
    from core.models import AdjustingJournal, JournalLine
    from django.utils import timezone as tz

    fy = get_object_or_404(FinancialYear, pk=pk)
    try:
        workspace = TrustWorkspace.objects.get(financial_year=fy)
    except TrustWorkspace.DoesNotExist:
        return JsonResponse({"error": "No trust workspace found."}, status=404)

    confirmed = workspace.confirmed_scenario
    if not confirmed or not confirmed.allocations:
        return JsonResponse(
            {"error": "No confirmed distribution scenario. Please confirm a scenario in Stage 3 first."},
            status=400,
        )

    rows, total_distributed, ndi = _get_confirmed_scenario_data(workspace)
    if not rows:
        return JsonResponse(
            {"error": "Confirmed scenario has no allocations with positive amounts."},
            status=400,
        )

    fy_year = "".join(c for c in fy.year_label if c.isdigit()) or str(fy.end_date.year)

    with transaction.atomic():
        journal = AdjustingJournal.objects.create(
            financial_year=fy,
            journal_type=AdjustingJournal.JournalType.YEAR_END,
            status=AdjustingJournal.JournalStatus.DRAFT,
            journal_date=fy.end_date,
            description=f"Trust Distribution — {fy.entity.entity_name} — FY{fy_year}",
            narration=(
                f"Distribution journal generated from confirmed scenario '{confirmed.name}'. "
                f"Total distributed: ${total_distributed:,.2f}. "
                f"Review and post when ready."
            ),
            created_by=request.user,
        )

        line_num = 1
        # CR side: Net Income / Retained Earnings account
        # Use account 4000 (Equity/Retained Earnings) as the default CR account
        JournalLine.objects.create(
            journal=journal,
            line_number=line_num,
            account_code="4000",
            account_name="Net Income — Trust Distribution",
            description=f"Distribution of net income for year ended 30 June {fy_year}",
            debit=Decimal("0"),
            credit=total_distributed,
        )
        line_num += 1

        # DR side: one line per beneficiary
        for row in rows:
            ben_name = row["name"]
            JournalLine.objects.create(
                journal=journal,
                line_number=line_num,
                account_code="3100",
                account_name=f"Distribution Payable — {ben_name}",
                description=f"{ben_name}: {row['percentage']}% = ${row['total']:,.2f}",
                debit=row["total"],
                credit=Decimal("0"),
            )
            line_num += 1

        journal.recalculate_totals()

    return JsonResponse({
        "success": True,
        "journal_id": str(journal.pk),
        "journal_reference": journal.reference_number,
        "total_distributed": str(total_distributed),
        "beneficiary_count": len(rows),
        "message": (
            f"Distribution journal {journal.reference_number} created as DRAFT. "
            f"Go to the Journals tab to review and post it."
        ),
    })
