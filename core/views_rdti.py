"""
R&D Tax Incentive (RDTI) Drafter — Views
Spec: R&DTI Drafter MVP v0.3

URL structure (all under years/<uuid:pk>/rdti/):
  GET  /                    → rdti_dashboard (tab content)
  POST /create/             → rdti_application_create
  GET  /intake/phase1/      → rdti_intake_phase1
  POST /intake/phase1/      → rdti_intake_phase1_save
  GET  /intake/phase2/<activity_pk>/  → rdti_intake_phase2
  POST /intake/phase2/<activity_pk>/  → rdti_intake_phase2_save
  POST /projects/create/    → rdti_project_create
  POST /activities/create/  → rdti_core_activity_create
  POST /activities/<activity_pk>/draft-all/  → rdti_draft_all_fields
  POST /activities/<activity_pk>/draft-field/ → rdti_draft_single_field
  POST /activities/<activity_pk>/save-field/  → rdti_save_field
  POST /activities/<activity_pk>/validate/    → rdti_validate_activity
  POST /supporting/create/  → rdti_supporting_activity_create
  GET  /export/docx/        → rdti_export_docx
  POST /status/update/      → rdti_status_update
"""
import json
import logging
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods
from django.utils import timezone

from core.models import FinancialYear
from core.models_rdti import (
    RdtiApplication, RdtiProject, RdtiCoreActivity,
    RdtiSupportingActivity, RdtiExpenditureYear,
    RdtiDraftVersion, RdtiFlag,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Narrative field metadata: (field_name, label, help_text, char_limit)
# ---------------------------------------------------------------------------
NARRATIVE_FIELD_META = [
    ("description", "Description of Core R&D Activity",
     "Describe the activity in plain language. What was being developed or researched?", 4000),
    ("outcome_not_known_in_advance", "How Outcome Could Not Be Known in Advance",
     "Explain why the outcome could not be determined without experimentation.", 4000),
    ("competent_professional", "Why a Competent Professional Could Not Have Known",
     "Explain why even a leading expert in the field could not have determined the outcome.", 4000),
    ("hypothesis", "Hypothesis",
     "State the specific hypothesis being tested.", 4000),
    ("experiment", "Experiment",
     "Describe the experimental activities, methods, and iterations.", 4000),
    ("evaluation_method", "Evaluation Method",
     "How were results measured, assessed, and compared against the hypothesis?", 4000),
    ("conclusions", "Conclusions",
     "What conclusions were drawn from the experimental results?", 4000),
    ("new_knowledge", "New Knowledge Produced",
     "What new knowledge was generated as a result of this activity?", 4000),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_fy(request, pk):
    """Get financial year with access control."""
    from core.views import get_financial_year_for_user
    return get_financial_year_for_user(request, pk)


def _save_flags(application, target_type, target_id, field_name, flags):
    """Persist validator flags, replacing existing ones for this field."""
    RdtiFlag.objects.filter(
        application=application,
        target_type=target_type,
        target_id=target_id,
        field_name=field_name,
    ).delete()

    for flag in flags:
        if flag.get("severity") == "green":
            continue  # Don't persist green flags — they're implicit
        RdtiFlag.objects.create(
            application=application,
            target_type=target_type,
            target_id=target_id,
            field_name=field_name,
            severity=flag["severity"],
            flag_type=flag["flag_type"],
            message=flag["message"],
            suggestion=flag.get("suggestion", ""),
        )


def _save_draft_version(application, target_type, target_id, field_name,
                        content, user, prompt_version="rdti-v1.0"):
    """Save a new draft version and mark previous versions as not current."""
    # Get next version number
    last_version = RdtiDraftVersion.objects.filter(
        application=application,
        target_type=target_type,
        target_id=target_id,
        field_name=field_name,
    ).order_by("-version_number").first()

    version_number = (last_version.version_number + 1) if last_version else 1

    # Mark previous as not current
    RdtiDraftVersion.objects.filter(
        application=application,
        target_type=target_type,
        target_id=target_id,
        field_name=field_name,
        is_current=True,
    ).update(is_current=False)

    return RdtiDraftVersion.objects.create(
        application=application,
        target_type=target_type,
        target_id=target_id,
        field_name=field_name,
        version_number=version_number,
        content=content,
        char_count=len(content),
        prompt_version=prompt_version,
        generated_by=user,
        is_current=True,
    )


# ---------------------------------------------------------------------------
# Dashboard (tab content)
# ---------------------------------------------------------------------------

@login_required
def rdti_dashboard(request, pk):
    """Main R&D tab content for a financial year."""
    fy = _get_fy(request, pk)
    entity = fy.entity

    # Get or create application
    application = getattr(fy, 'rdti_application', None)

    context = {
        "fy": fy,
        "entity": entity,
        "application": application,
        "has_application": application is not None,
    }

    if application:
        projects = application.projects.prefetch_related(
            'core_activities__supporting_activities',
            'core_activities__expenditure_years',
        ).all()
        flag_counts = application.flag_counts
        context.update({
            "projects": projects,
            "flag_counts": flag_counts,
            "red_flags": application.flags.filter(severity="red", is_resolved=False).count(),
            "amber_flags": application.flags.filter(severity="amber", is_resolved=False).count(),
        })

    return render(request, "core/rdti/dashboard.html", context)


# ---------------------------------------------------------------------------
# Application create
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_application_create(request, pk):
    """Create a new RDTI application for this financial year."""
    fy = _get_fy(request, pk)

    if not request.user.is_admin:
        messages.error(request, "Only administrators can edit R&DTI applications.")
        return redirect("core:rdti_dashboard", pk=fy.pk)

    if hasattr(fy, 'rdti_application'):
        return JsonResponse({"error": "Application already exists for this financial year."}, status=400)

    # Pre-populate from entity
    entity = fy.entity
    application = RdtiApplication.objects.create(
        financial_year=fy,
        company_name=entity.entity_name,
        abn=entity.abn or "",
        acn=entity.acn or "",
        created_by=request.user,
        status=RdtiApplication.Status.INTAKE,
    )

    return redirect("core:rdti_intake_phase1", pk=pk)


# ---------------------------------------------------------------------------
# Intake Phase 1 — Project Framing
# ---------------------------------------------------------------------------

@login_required
def rdti_intake_phase1(request, pk):
    """Phase 1 intake: project framing."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)

    if not request.user.is_admin:
        messages.error(request, "Only administrators can edit R&DTI applications.")
        return redirect("core:rdti_dashboard", pk=fy.pk)

    if request.method == "POST":
        # Save application-level fields
        application.company_name = request.POST.get("company_name", application.company_name)
        application.abn = request.POST.get("abn", application.abn)
        application.acn = request.POST.get("acn", application.acn)
        application.contact_name = request.POST.get("contact_name", "")
        application.contact_email = request.POST.get("contact_email", "")
        application.contact_phone = request.POST.get("contact_phone", "")
        application.anzsic_division = request.POST.get("anzsic_division", "")
        application.anzsic_code = request.POST.get("anzsic_code", "")

        turnover = request.POST.get("aggregated_turnover", "")
        if turnover:
            try:
                application.aggregated_turnover = float(turnover.replace(",", ""))
            except ValueError:
                pass

        emp = request.POST.get("employee_count", "")
        if emp:
            try:
                application.employee_count = int(emp)
            except ValueError:
                pass

        application.ip_owned_by_entity = request.POST.get("ip_owned_by_entity") == "yes"
        application.entity_bears_financial_burden = request.POST.get("entity_bears_financial_burden") == "yes"
        application.entity_controls_activities = request.POST.get("entity_controls_activities") == "yes"
        application.save()

        # Create or update project
        project_id = request.POST.get("project_id")
        if project_id:
            project = get_object_or_404(RdtiProject, id=project_id, application=application)
        else:
            project = RdtiProject(application=application)

        project.project_title = request.POST.get("project_title", "")
        project.intake_business_problem = request.POST.get("business_problem", "")
        project.intake_existing_knowledge = request.POST.get("existing_knowledge", "")
        project.intake_uncertainty = request.POST.get("uncertainty", "")
        project.intake_who_could_have_known = request.POST.get("who_could_have_known", "")
        project.anzsrc_division = request.POST.get("anzsrc_division", "")

        expenditure = request.POST.get("expenditure_estimate", "")
        if expenditure:
            try:
                project.intake_expenditure_estimate = float(expenditure.replace(",", ""))
            except ValueError:
                pass

        start_date = request.POST.get("project_start_date")
        if start_date:
            try:
                project.project_start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                pass

        project.save()

        if application.status == RdtiApplication.Status.INTAKE:
            application.status = RdtiApplication.Status.DRAFTING
            application.save()

        return redirect("core:rdti_dashboard", pk=pk)

    # GET
    projects = application.projects.all()
    from core.models_rdti import ANZSIC_DIVISIONS, ANZSRC_DIVISIONS
    context = {
        "fy": fy,
        "entity": fy.entity,
        "application": application,
        "projects": projects,
        "anzsic_divisions": ANZSIC_DIVISIONS,
        "anzsrc_divisions": ANZSRC_DIVISIONS,
    }
    return render(request, "core/rdti/intake_phase1.html", context)


# ---------------------------------------------------------------------------
# Intake Phase 2 — Activity Capture
# ---------------------------------------------------------------------------

@login_required
def rdti_intake_phase2(request, pk, project_pk):
    """Phase 2 intake: activity capture for a specific project."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    project = get_object_or_404(RdtiProject, id=project_pk, application=application)

    if not request.user.is_admin:
        messages.error(request, "Only administrators can edit R&DTI applications.")
        return redirect("core:rdti_dashboard", pk=fy.pk)

    if request.method == "POST":
        activity_id = request.POST.get("activity_id")
        if activity_id:
            activity = get_object_or_404(RdtiCoreActivity, id=activity_id, project=project)
        else:
            activity = RdtiCoreActivity(project=project, application=application)

        activity.activity_title = request.POST.get("activity_title", "")
        activity.intake_technical_question = request.POST.get("technical_question", "")
        activity.intake_prior_search = request.POST.get("prior_search", "")
        activity.intake_why_unpredictable = request.POST.get("why_unpredictable", "")
        activity.intake_hypothesis_raw = request.POST.get("hypothesis_raw", "")
        activity.intake_experiments_run = request.POST.get("experiments_run", "")
        activity.intake_measurement = request.POST.get("measurement", "")
        activity.intake_learnings = request.POST.get("learnings", "")
        activity.intake_records_kept = request.POST.get("records_kept", "")

        # Multi-select fields
        activity.sources_investigated = request.POST.getlist("sources_investigated")
        activity.evidence_kept = request.POST.getlist("evidence_kept")

        start_date = request.POST.get("activity_start_date")
        if start_date:
            try:
                activity.activity_start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                pass

        end_date = request.POST.get("activity_end_date")
        if end_date:
            try:
                activity.activity_end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                pass

        activity.performed_by = request.POST.get("performed_by", "entity")
        activity.save()

        return redirect("core:rdti_dashboard", pk=pk)

    # GET
    activities = project.core_activities.all()
    from core.models_rdti import RdtiCoreActivity as CA
    context = {
        "fy": fy,
        "entity": fy.entity,
        "application": application,
        "project": project,
        "activities": activities,
        "evidence_choices": CA.EVIDENCE_CHOICES,
        "sources_choices": CA.SOURCES_CHOICES,
        "performed_by_choices": CA.PerformedBy.choices,
    }
    return render(request, "core/rdti/intake_phase2.html", context)


# ---------------------------------------------------------------------------
# AI Drafting — Single Field
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_draft_single_field(request, pk, activity_pk):
    """Draft a single narrative field using AI."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    activity = get_object_or_404(RdtiCoreActivity, id=activity_pk, application=application)
    project = activity.project

    if not request.user.is_admin:
        return JsonResponse(
            {"error": "R&DTI editing is restricted to administrators."},
            status=403,
        )

    field_name = request.POST.get("field_name")
    if not field_name:
        return JsonResponse({"error": "field_name is required"}, status=400)

    from core.rdti_ai_service import draft_field, validate_field, FIELD_PROMPTS

    if field_name not in FIELD_PROMPTS:
        return JsonResponse({"error": f"Unknown field: {field_name}"}, status=400)

    # Build context
    context = {
        "project_title": project.project_title,
        "business_problem": project.intake_business_problem,
        "existing_knowledge": project.intake_existing_knowledge,
        "uncertainty": project.intake_uncertainty,
        "who_could_have_known": project.intake_who_could_have_known,
        "activity_title": activity.activity_title,
        "technical_question": activity.intake_technical_question,
        "prior_search": activity.intake_prior_search,
        "why_unpredictable": activity.intake_why_unpredictable,
        "hypothesis_raw": activity.intake_hypothesis_raw,
        "experiments_run": activity.intake_experiments_run,
        "measurement": activity.intake_measurement,
        "learnings": activity.intake_learnings,
        "records_kept": activity.intake_records_kept,
        "sources_investigated": ", ".join(activity.sources_investigated) if activity.sources_investigated else "",
        "evidence_kept": ", ".join(activity.evidence_kept) if activity.evidence_kept else "",
        # Include previously drafted fields as context
        "hypothesis": activity.hypothesis or "",
        "conclusions": activity.conclusions or "",
    }

    result = draft_field(field_name, context)

    if result["error"]:
        return JsonResponse({"error": result["error"]}, status=500)

    # Save to model
    setattr(activity, field_name, result["content"])
    activity.save()

    # Save draft version
    _save_draft_version(
        application=application,
        target_type="core_activity",
        target_id=activity.id,
        field_name=field_name,
        content=result["content"],
        user=request.user,
        prompt_version=result["prompt_version"],
    )

    # Run validator
    flags = validate_field(field_name, result["content"], context)
    _save_flags(application, "core_activity", activity.id, field_name, flags)

    # Return HTML field card partial for HTMX
    activity.refresh_from_db()
    current_value = getattr(activity, field_name, "") or ""
    char_count = len(current_value)
    field_flags = RdtiFlag.objects.filter(
        application=application, target_type="core_activity",
        target_id=activity.id, field_name=field_name, is_resolved=False,
    )
    # Find field meta
    field_meta = next((m for m in NARRATIVE_FIELD_META if m[0] == field_name), None)
    field_label = field_meta[1] if field_meta else field_name
    field_help = field_meta[2] if field_meta else ""
    char_limit = field_meta[3] if field_meta else 4000
    return render(request, "core/rdti/field_card.html", {
        "fy": fy,
        "entity": fy.entity,
        "application": application,
        "activity": activity,
        "project": activity.project,
        "field_name": field_name,
        "field_label": field_label,
        "field_help": field_help,
        "char_limit": char_limit,
        "current_value": current_value,
        "char_count": char_count,
        "field_flags": field_flags,
    })


# ---------------------------------------------------------------------------
# AI Drafting — All Fields for an Activity
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_draft_all_fields(request, pk, activity_pk):
    """Draft all 8 narrative fields for a Core Activity."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    activity = get_object_or_404(RdtiCoreActivity, id=activity_pk, application=application)
    project = activity.project

    if not request.user.is_admin:
        return JsonResponse(
            {"error": "R&DTI editing is restricted to administrators."},
            status=403,
        )

    from core.rdti_ai_service import draft_all_core_activity_fields, validate_field

    results = draft_all_core_activity_fields(activity, project, application)

    # Save draft versions and run validators for each field
    for field_name, result in results.items():
        if result.get("content") and not result.get("error"):
            _save_draft_version(
                application=application,
                target_type="core_activity",
                target_id=activity.id,
                field_name=field_name,
                content=result["content"],
                user=request.user,
                prompt_version=result.get("prompt_version", "rdti-v1.0"),
            )
            flags = validate_field(field_name, result["content"])
            _save_flags(application, "core_activity", activity.id, field_name, flags)

    # Run cross-field consistency check
    from core.rdti_ai_service import check_cross_field_consistency
    activity.refresh_from_db()
    cross_flags = check_cross_field_consistency(activity)
    for flag in cross_flags:
        if flag.get("severity") != "green":
            RdtiFlag.objects.create(
                application=application,
                target_type="core_activity",
                target_id=activity.id,
                field_name="cross_field",
                severity=flag["severity"],
                flag_type=flag["flag_type"],
                message=flag["message"],
                suggestion=flag.get("suggestion", ""),
            )

    # Mark activity as draft complete if no red flags
    red_flag_count = RdtiFlag.objects.filter(
        application=application,
        target_type="core_activity",
        target_id=activity.id,
        severity="red",
        is_resolved=False,
    ).count()
    activity.draft_complete = (red_flag_count == 0)
    activity.save()
    # Return HTML partial for HTMX
    activity.refresh_from_db()
    raw_fields = activity.get_narrative_fields()
    field_values = {name: value for name, label, value in raw_fields}
    char_counts = {name: len(value or "") for name, label, value in raw_fields}
    flags_by_field = {}
    for flag in RdtiFlag.objects.filter(
        application=application, target_type="core_activity",
        target_id=activity.id, is_resolved=False,
    ):
        flags_by_field.setdefault(flag.field_name, []).append(flag)
    narrative_fields = [
        (name, label, help_text, char_limit)
        for name, label, help_text, char_limit in NARRATIVE_FIELD_META
    ]
    return render(request, "core/rdti/all_fields.html", {
        "fy": fy,
        "entity": fy.entity,
        "application": application,
        "activity": activity,
        "project": activity.project,
        "narrative_fields": narrative_fields,
        "field_values": field_values,
        "char_counts": char_counts,
        "char_limit": 4000,
        "flags_by_field": flags_by_field,
    })


# ---------------------------------------------------------------------------
# Save field manually (consultant edits)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_save_field(request, pk, activity_pk):
    """Save a manually edited narrative field."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    activity = get_object_or_404(RdtiCoreActivity, id=activity_pk, application=application)

    if not request.user.is_admin:
        return JsonResponse(
            {"error": "R&DTI editing is restricted to administrators."},
            status=403,
        )

    field_name = request.POST.get("field_name")
    content = request.POST.get("content", "")

    if not field_name:
        return JsonResponse({"error": "field_name is required"}, status=400)

    # Validate field exists on model
    valid_fields = [f for f, _, _ in activity.get_narrative_fields()]
    if field_name not in valid_fields:
        return JsonResponse({"error": f"Invalid field: {field_name}"}, status=400)

    setattr(activity, field_name, content)
    activity.save()

    # Save version
    _save_draft_version(
        application=application,
        target_type="core_activity",
        target_id=activity.id,
        field_name=field_name,
        content=content,
        user=request.user,
        prompt_version="manual",
    )

    # Re-run validator
    from core.rdti_ai_service import validate_field
    flags = validate_field(field_name, content)
    _save_flags(application, "core_activity", activity.id, field_name, flags)

    # Return HTML field card partial for HTMX
    activity.refresh_from_db()
    current_value = getattr(activity, field_name, "") or ""
    char_count = len(current_value)
    field_flags = list(RdtiFlag.objects.filter(
        application=application, target_type="core_activity",
        target_id=activity.id, field_name=field_name, is_resolved=False,
    ))
    field_meta = next((m for m in NARRATIVE_FIELD_META if m[0] == field_name), None)
    field_label = field_meta[1] if field_meta else field_name
    field_help = field_meta[2] if field_meta else ""
    char_limit = field_meta[3] if field_meta else 4000
    return render(request, "core/rdti/field_card.html", {
        "fy": fy,
        "entity": fy.entity,
        "application": application,
        "activity": activity,
        "project": activity.project,
        "field_name": field_name,
        "field_label": field_label,
        "field_help": field_help,
        "char_limit": char_limit,
        "current_value": current_value,
        "char_count": char_count,
        "field_flags": field_flags,
    })


# ---------------------------------------------------------------------------
# Draft project-level fields
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_draft_project_fields(request, pk, project_pk):
    """Draft all project-level narrative fields."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    project = get_object_or_404(RdtiProject, id=project_pk, application=application)

    if not request.user.is_admin:
        return JsonResponse(
            {"error": "R&DTI editing is restricted to administrators."},
            status=403,
        )

    from core.rdti_ai_service import draft_project_fields
    results = draft_project_fields(project, application)

    for field_name, result in results.items():
        if result.get("content") and not result.get("error"):
            _save_draft_version(
                application=application,
                target_type="project",
                target_id=project.id,
                field_name=field_name,
                content=result["content"],
                user=request.user,
                prompt_version=result.get("prompt_version", "rdti-v1.0"),
            )

    return JsonResponse({
        "success": True,
        "fields_drafted": len([r for r in results.values() if r.get("content")]),
    })


# ---------------------------------------------------------------------------
# Validate a full activity
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_validate_activity(request, pk, activity_pk):
    """Run all validators on a Core Activity."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    activity = get_object_or_404(RdtiCoreActivity, id=activity_pk, application=application)

    if not request.user.is_admin:
        return JsonResponse(
            {"error": "R&DTI editing is restricted to administrators."},
            status=403,
        )

    from core.rdti_ai_service import validate_field, check_cross_field_consistency

    all_flags = {}
    for field_name, label, content in activity.get_narrative_fields():
        if content:
            flags = validate_field(field_name, content)
            _save_flags(application, "core_activity", activity.id, field_name, flags)
            all_flags[field_name] = flags

    # Cross-field check
    cross_flags = check_cross_field_consistency(activity)
    if cross_flags:
        all_flags["cross_field"] = cross_flags
        for flag in cross_flags:
            if flag.get("severity") != "green":
                RdtiFlag.objects.create(
                    application=application,
                    target_type="core_activity",
                    target_id=activity.id,
                    field_name="cross_field",
                    severity=flag["severity"],
                    flag_type=flag["flag_type"],
                    message=flag["message"],
                    suggestion=flag.get("suggestion", ""),
                )

    red_count = sum(1 for flags in all_flags.values() for f in flags if f.get("severity") == "red")
    amber_count = sum(1 for flags in all_flags.values() for f in flags if f.get("severity") == "amber")

    activity.draft_complete = (red_count == 0)
    activity.save()

    # Return HTML validation results partial for HTMX
    all_flag_objects = list(RdtiFlag.objects.filter(
        application=application, target_type="core_activity",
        target_id=activity.id, is_resolved=False,
    ).order_by("severity", "field_name"))
    return render(request, "core/rdti/validation_results.html", {
        "fy": fy,
        "entity": fy.entity,
        "application": application,
        "activity": activity,
        "flags": all_flag_objects,
        "red_count": red_count,
        "amber_count": amber_count,
    })


# ---------------------------------------------------------------------------
# Export to .docx
# ---------------------------------------------------------------------------

@login_required
def rdti_export_docx(request, pk):
    """Export the RDTI application to a formatted .docx file."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)

    if not request.user.is_admin:
        messages.error(request, "Only administrators can export R&DTI applications.")
        return redirect("core:rdti_dashboard", pk=fy.pk)

    try:
        from core.rdti_docx_export import generate_rdti_docx
        docx_bytes = generate_rdti_docx(application)

        filename = f"RDTI_{fy.entity.entity_name}_{fy.year_label}.docx".replace(" ", "_")
        response = HttpResponse(
            docx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        logger.error(f"RDTI docx export failed: {e}")
        return JsonResponse({"error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# Status update
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_status_update(request, pk):
    """Update the application status."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)

    if not request.user.is_admin:
        return JsonResponse(
            {"error": "R&DTI editing is restricted to administrators."},
            status=403,
        )

    new_status = request.POST.get("status")
    valid_statuses = [s[0] for s in RdtiApplication.Status.choices]

    if new_status not in valid_statuses:
        return JsonResponse({"error": "Invalid status"}, status=400)

    # Check: can't mark as ready if there are unresolved red flags
    if new_status == "ready":
        red_flags = application.flags.filter(severity="red", is_resolved=False).count()
        if red_flags > 0:
            return JsonResponse({
                "error": f"Cannot mark as Ready to Lodge: {red_flags} unresolved red flag(s) must be resolved first."
            }, status=400)

    # Validate transition against whitelist
    if not application.can_transition_to(new_status):
        allowed = application.VALID_TRANSITIONS.get(application.status, [])
        return JsonResponse({
            "error": (
                f"Cannot transition from {application.get_status_display()} to '{new_status}'. "
                f"Allowed transitions: {allowed}"
            )
        }, status=400)

    if new_status == "lodged":
        application.lodged_at = timezone.now()

    application.status = new_status
    application.save()

    return JsonResponse({"success": True, "status": application.get_status_display()})


# ---------------------------------------------------------------------------
# Supporting Activity CRUD
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_supporting_activity_create(request, pk, activity_pk):
    """Create a supporting activity for a core activity."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    core_activity = get_object_or_404(RdtiCoreActivity, id=activity_pk, application=application)

    if not request.user.is_admin:
        return JsonResponse(
            {"error": "R&DTI editing is restricted to administrators."},
            status=403,
        )

    sa = RdtiSupportingActivity.objects.create(
        core_activity=core_activity,
        application=application,
        activity_title=request.POST.get("activity_title", ""),
        intake_description=request.POST.get("description", ""),
        intake_relation=request.POST.get("relation", ""),
    )

    return JsonResponse({
        "success": True,
        "id": str(sa.id),
        "title": sa.activity_title,
    })


# ---------------------------------------------------------------------------
# Flag resolution
# ---------------------------------------------------------------------------

@login_required
@require_POST
def rdti_resolve_flag(request, pk, flag_pk):
    """Mark a flag as resolved."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    flag = get_object_or_404(RdtiFlag, id=flag_pk, application=application)

    if not request.user.is_admin:
        return JsonResponse(
            {"error": "R&DTI editing is restricted to administrators."},
            status=403,
        )

    flag.is_resolved = True
    flag.resolved_at = timezone.now()
    flag.save()

    return JsonResponse({"success": True})


# ---------------------------------------------------------------------------
# Activity detail view (full field-by-field review)
# ---------------------------------------------------------------------------

@login_required
def rdti_activity_detail(request, pk, activity_pk):
    """Full activity detail view with all 8 narrative fields."""
    fy = _get_fy(request, pk)
    application = get_object_or_404(RdtiApplication, financial_year=fy)
    activity = get_object_or_404(RdtiCoreActivity, id=activity_pk, application=application)

    # Get flags for this activity
    flags_by_field = {}
    for flag in RdtiFlag.objects.filter(
        application=application,
        target_type="core_activity",
        target_id=activity.id,
        is_resolved=False,
    ):
        if flag.field_name not in flags_by_field:
            flags_by_field[flag.field_name] = []
        flags_by_field[flag.field_name].append(flag)

    # Get draft version history for each field
    versions_by_field = {}
    for version in RdtiDraftVersion.objects.filter(
        application=application,
        target_type="core_activity",
        target_id=activity.id,
    ).order_by("field_name", "-version_number"):
        if version.field_name not in versions_by_field:
            versions_by_field[version.field_name] = []
        versions_by_field[version.field_name].append(version)

    # Build field_values dict and 4-tuple narrative_fields for template
    raw_fields = activity.get_narrative_fields()  # (name, label, value)
    field_values = {name: value for name, label, value in raw_fields}
    narrative_fields = [
        (name, label, help_text, char_limit)
        for name, label, help_text, char_limit in NARRATIVE_FIELD_META
    ]
    char_counts = {name: len(value or "") for name, label, value in raw_fields}
    context = {
        "fy": fy,
        "entity": fy.entity,
        "application": application,
        "activity": activity,
        "project": activity.project,
        "narrative_fields": narrative_fields,
        "field_values": field_values,
        "flags_by_field": flags_by_field,
        "versions_by_field": versions_by_field,
        "char_counts": char_counts,
        "char_limit": 4000,
        "supporting_activities": activity.supporting_activities.all(),
        "expenditure_years": activity.expenditure_years.all(),
    }
    return render(request, "core/rdti/activity_detail.html", context)
