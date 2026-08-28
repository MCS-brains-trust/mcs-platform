"""
Trust Tax Planning — Views & API Endpoints

Tab view:       GET  /years/<pk>/tax-planning/
Calculate API:  POST /years/<pk>/tax-planning/calculate/
Save API:       POST /years/<pk>/tax-planning/save/
Save notes:     POST /years/<pk>/tax-planning/save-notes/
Scenario save:  POST /years/<pk>/tax-planning/scenario/save/
Scenario delete: POST /years/<pk>/tax-planning/scenario/<scenario_pk>/delete/
Scenario apply: POST /years/<pk>/tax-planning/scenario/<scenario_pk>/apply/
Finalise:       POST /years/<pk>/tax-planning/finalise/
Reopen:         POST /years/<pk>/tax-planning/reopen/
"""
import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import (
    EntityOfficer,
    TaxPlanningBeneficiaryRow,
    TaxPlanningScenario,
    TaxPlanningWorksheet,
    TRUST_LIKE_TYPES,
)
from core.tax_engine import (
    calculate_all_beneficiaries,
    calculate_section1_from_tb,
    get_tax_rates,
)
from core.unit_allocation import allocate_by_units
from core.views import _log_action, get_financial_year_for_user


def _get_or_create_worksheet(fy, user):
    """Get or create the TaxPlanningWorksheet for a trust FY."""
    worksheet, created = TaxPlanningWorksheet.objects.get_or_create(
        financial_year=fy,
        defaults={"last_updated_by": user},
    )
    if created:
        _sync_beneficiary_rows(worksheet)
    return worksheet


def _sync_beneficiary_rows(worksheet):
    """
    Ensure one TaxPlanningBeneficiaryRow per active beneficiary or unit
    holder. Adds missing rows, does NOT delete removed beneficiaries
    (preserves data). A unit trust's holders are its "beneficiaries" for tax
    planning purposes (Task 9) -- tax is still computed per holder even
    though the register, not this row, decides the distribution amount.

    Matching is done in Python rather than with a
    ``roles__contains=...`` queryset filter: ``roles`` is a JSONField, and
    the ``contains`` lookup it would need is not supported on SQLite (the
    backend the test suite runs against), so that filter raised
    ``NotSupportedError`` outright -- not a silent no-op, a hard crash on
    every call, for both "beneficiary" and "unit_holder". A dead code path
    with zero test coverage until now.

    "Active" matches ``EntityOfficer.active_register_q()`` -- the single
    canonical predicate (see its docstring) -- rather than a bare
    ``date_ceased__isnull=True``, so a future-dated ceased holder still
    gets a row, consistent with every other active-register site.
    """
    entity = worksheet.financial_year.entity
    candidates = EntityOfficer.objects.filter(entity=entity).filter(
        EntityOfficer.active_register_q()
    )
    beneficiaries = [
        officer for officer in candidates
        if officer.role in ("beneficiary", "unit_holder")
        or "beneficiary" in (officer.roles or [])
        or "unit_holder" in (officer.roles or [])
    ]

    existing_ids = set(
        worksheet.beneficiary_rows.values_list("beneficiary_id", flat=True)
    )

    for ben in beneficiaries:
        if ben.pk not in existing_ids:
            # Determine beneficiary type
            btype = _infer_beneficiary_type(ben)
            TaxPlanningBeneficiaryRow.objects.create(
                worksheet=worksheet,
                beneficiary=ben,
                beneficiary_type=btype,
            )


def _apply_unit_distributions(worksheet):
    """A unit trust's proposed distribution comes from the register, not a
    proposal. No-op for a discretionary trust (returns None immediately).

    Called from ``tax_planning_tab`` right after ``_sync_beneficiary_rows``
    (so a freshly-created row already carries an amount) and again after
    the Section 1 recalculation, since that is what changes
    ``distributable_income`` on every page load.

    Holdings are keyed on ``(display_order, full_name, pk)`` of the HOLDER,
    not on the ``TaxPlanningBeneficiaryRow``'s own pk: that is the shared
    tie-break key (see ``EntityOfficer.recalculate_unit_percentages`` and
    ``allocate_by_units``), so the holder this tab DISPLAYS at 33.34% is the
    holder it also shows the extra cent. Keying on the row pk -- a random
    UUID -- made those two answers independent. The officer pk rides along
    last, so the key stays unique per row and ``allocate_by_units``'s
    duplicate-key guard still cannot fire here.

    Does nothing to a FINALISED worksheet. This runs on every GET of the
    tab, and the register it derives from is TODAY's register: without this
    guard, merely opening a finalised prior-year worksheet silently
    rewrote its stored ``proposed_distribution`` figures from a register
    that may have changed since -- while ``tax_planning_save`` explicitly
    refuses to touch a finalised worksheet at all. A finalised worksheet is
    a record of what was decided, so it is read-only here too.

    "Active" matches ``EntityOfficer.active_register_q()`` -- the single
    canonical predicate shared with ``Entity.total_units`` and
    ``allocate_unit_trust_distribution`` (core/views_trust.py). A ``None``
    in ``units_held`` is excluded via ``units_held__isnull=False`` before
    the row ever reaches ``allocate_by_units``, which would otherwise raise
    a bare ``TypeError`` on it (see that module's own caveat).

    A holder who has dropped off the active register (ceased, or a unit
    holder whose units were zeroed/nulled) keeps their row, but it is
    zeroed to $0 -- mirroring ``allocate_unit_trust_distribution`` -- so a
    stale row can never silently keep showing money it no longer
    represents.

    Returns a warning string, and leaves every row at $0, when the
    register cannot be allocated at all (no active holder carries units on
    issue). This is a deliberate choice: ``allocate_by_units`` raises
    ValueError in that situation, and letting that exception reach the view
    would 500 the tab. Rendering zero-value rows with a visible warning
    (surfaced by the caller via ``messages.warning``) is safer for a real
    user mid-setup than either a crash or silently fabricating a split
    against no units. Returns None when the register allocates cleanly.
    """
    entity = worksheet.financial_year.entity
    if not entity.is_unit_trust:
        return None
    if worksheet.is_finalised:
        # A finalised worksheet is a record, not a live derivation -- see
        # docstring. tax_planning_save refuses a finalised worksheet too.
        return None

    rows = list(worksheet.beneficiary_rows.select_related("beneficiary"))

    active_holders = {
        officer.pk: officer
        for officer in EntityOfficer.objects.filter(
            EntityOfficer.active_register_q(),
            entity=entity,
            units_held__isnull=False,
        )
    }

    holdings = []
    row_for_key = {}
    for row in rows:
        holder = active_holders.get(row.beneficiary_id)
        if holder is None:
            continue
        key = (holder.display_order, holder.full_name, holder.pk)
        holdings.append((key, holder.units_held))
        row_for_key[key] = row

    if not holdings or not any(units for _, units in holdings):
        for row in rows:
            if row.proposed_distribution != Decimal("0"):
                row.proposed_distribution = Decimal("0")
                row.save(update_fields=["proposed_distribution"])
        return (
            "No active unit holder carries units on issue — the "
            "distribution cannot be derived from the register. Set units "
            "held on this entity's officers before finalising."
        )

    split = allocate_by_units(worksheet.distributable_income, holdings)
    by_row = {
        row_for_key[key].pk: amount for key, amount in split.items()
    }

    for row in rows:
        new_value = by_row.get(row.pk, Decimal("0"))
        if row.proposed_distribution != new_value:
            row.proposed_distribution = new_value
            row.save(update_fields=["proposed_distribution"])
    return None


def _infer_beneficiary_type(officer):
    """Infer whether a beneficiary is Individual, Company, or Trust."""
    name_lower = officer.full_name.lower()
    if "pty ltd" in name_lower or "pty. ltd" in name_lower or "limited" in name_lower:
        return "company"
    if "trust" in name_lower or "family trust" in name_lower:
        return "trust"
    return "individual"


@login_required
def tax_planning_tab(request, pk):
    """
    Main Tax Planning tab view. Trust entities only (discretionary or unit).
    """
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    if entity.entity_type not in TRUST_LIKE_TYPES:
        messages.error(request, "Tax Planning is only available for Trust entities.")
        return redirect("core:financial_year_detail", pk=pk)

    # Staff Accountant (role=accountant) can only view/edit own entities
    if request.user.role == "accountant":
        is_own = (
            entity.primary_accountant == request.user
            or entity.assigned_accountant == request.user
        )
        if not is_own:
            messages.error(request, "You can only access Tax Planning for your own entities.")
            return redirect("core:financial_year_detail", pk=pk)

    worksheet = _get_or_create_worksheet(fy, request.user)

    # Sync beneficiary rows (picks up new beneficiaries/unit holders added
    # to the entity), then -- for a unit trust only -- derive each row's
    # proposed_distribution from the register so a freshly-synced row does
    # not sit at the TaxPlanningBeneficiaryRow default of $0.
    _sync_beneficiary_rows(worksheet)
    _apply_unit_distributions(worksheet)

    # Calculate Section 1 from TB (always fresh)
    section1 = calculate_section1_from_tb(fy)

    # Update worksheet with latest Section 1 values
    for field, value in section1.items():
        setattr(worksheet, field, value)
    worksheet.last_updated_by = request.user
    worksheet.save(update_fields=[
        "distributable_income", "non_deductible_expenses", "non_assessable_income",
        "net_profit_before_distributions", "capital_gains", "franked_dividends",
        "franking_credits", "last_updated_at", "last_updated_by",
    ])

    # Re-derive the unit-trust split: distributable_income just changed
    # above, so the first _apply_unit_distributions call (before Section 1
    # was recalculated) may already be stale.
    register_warning = _apply_unit_distributions(worksheet)
    if register_warning:
        messages.warning(request, register_warning)

    # Get beneficiary rows
    rows = worksheet.beneficiary_rows.select_related("beneficiary").order_by(
        "beneficiary__full_name"
    )

    # Get tax rates for this FY
    rates = get_tax_rates(fy.year_label)

    # Calculate tax for each row with current values
    beneficiary_data = []
    for row in rows:
        beneficiary_data.append({
            "beneficiary_id": str(row.beneficiary.pk),
            "beneficiary_type": row.beneficiary_type,
            "outside_income": row.outside_income,
            "proposed_distribution": row.proposed_distribution,
            "company_tax_rate_override": row.company_tax_rate_override,
        })

    calc_result = calculate_all_beneficiaries(worksheet, beneficiary_data, rates)

    # Merge calculated values into row objects for template
    calc_by_id = {r["beneficiary_id"]: r for r in calc_result["rows"]}
    for row in rows:
        calc = calc_by_id.get(str(row.beneficiary.pk), {})
        row.calc = calc

    # Get scenarios
    scenarios = TaxPlanningScenario.objects.filter(financial_year=fy).order_by("created_at")

    # Log access
    _log_action(request, "view", f"Viewed Tax Planning tab for {entity.entity_name}", fy)

    context = {
        "fy": fy,
        "entity": entity,
        "worksheet": worksheet,
        "section1": section1,
        "rows": rows,
        "optimiser": calc_result["optimiser"],
        "scenarios": scenarios,
        "scenarios_json": json.dumps([
            {
                "id": str(s.pk),
                "name": s.scenario_name,
                "distributions": s.distributions,
                "total_tax": str(s.total_tax),
                "total_distributed": str(s.total_distributed),
            }
            for s in scenarios
        ]),
        "is_finalised": worksheet.is_finalised,
        "can_finalise": request.user.can_finalise,
        "can_generate_docs": request.user.can_finalise,  # Admin/Senior only
        "is_own_entity": (
            entity.primary_accountant == request.user
            or entity.assigned_accountant == request.user
        ) if request.user.role == "accountant" else True,
        "trustee_rate": str(rates.get("trustee_default_tax_rate", Decimal("0.47"))),
        "tax_free_threshold": str(rates.get("tax_free_threshold", Decimal("18200"))),
    }
    return render(request, "core/tax_planning.html", context)


@login_required
@require_POST
def tax_planning_calculate(request, pk):
    """
    POST /years/<pk>/tax-planning/calculate/
    Recalculates all beneficiary tax positions. Does NOT save.
    """
    fy = get_financial_year_for_user(request, pk)

    # Staff Accountant: own entities only
    if request.user.role == "accountant":
        entity = fy.entity
        if entity.primary_accountant != request.user and entity.assigned_accountant != request.user:
            return JsonResponse({"error": "Access denied — not your entity."}, status=403)
    worksheet = _get_or_create_worksheet(fy, request.user)
    rates = get_tax_rates(fy.year_label)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    beneficiary_rows = body.get("beneficiary_rows", [])
    result = calculate_all_beneficiaries(worksheet, beneficiary_rows, rates)

    # Serialise Decimal values
    for row in result["rows"]:
        for k, v in row.items():
            if isinstance(v, Decimal):
                row[k] = str(v)

    return JsonResponse(result)


@login_required
@require_POST
def tax_planning_save(request, pk):
    """
    POST /years/<pk>/tax-planning/save/
    Persists the current beneficiary row values and recalculates.

    For a unit trust, there is no planning: the register decides the
    distribution, not whatever the client posted. Any posted
    proposed_distribution is ignored -- overwritten below with what is
    already persisted (itself set from the register by
    _apply_unit_distributions on the last tab load) before it even reaches
    calculate_all_beneficiaries, so a rejected override cannot leak into the
    calculated tax fields (gross tax, Medicare, LITO, franking offset, net
    tax, effective rate) that DO get saved from this call. outside_income
    is still accepted -- it describes the holder's own tax position, not
    the trust's allocation. The persist loop below skips writing
    proposed_distribution for a unit trust as a second, independent
    safeguard, so this is never relied on alone.
    """
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity
    is_unit_trust = entity.is_unit_trust

    # Staff Accountant: own entities only
    if request.user.role == "accountant":
        if entity.primary_accountant != request.user and entity.assigned_accountant != request.user:
            return JsonResponse({"error": "Access denied — not your entity."}, status=403)
    worksheet = _get_or_create_worksheet(fy, request.user)

    if worksheet.is_finalised:
        return JsonResponse({"error": "Worksheet is finalised. Reopen to edit."}, status=400)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    rates = get_tax_rates(fy.year_label)
    beneficiary_rows_data = body.get("beneficiary_rows", [])

    if is_unit_trust:
        # Neutralise unconditionally -- keyed on entity.is_unit_trust alone,
        # not on "does a row exist for this id" (Ruling 4: do not lean on
        # the DoesNotExist swallow below). A fabricated beneficiary_id that
        # matches no TaxPlanningBeneficiaryRow must not sail through with
        # its posted amount intact and leak into calculate_all_beneficiaries'
        # returned tax figures -- it gets $0, the same as any other id this
        # worksheet does not recognise.
        current_amounts = {
            str(beneficiary_id): amount
            for beneficiary_id, amount in TaxPlanningBeneficiaryRow.objects.filter(
                worksheet=worksheet
            ).values_list("beneficiary_id", "proposed_distribution")
        }
        for bd in beneficiary_rows_data:
            ben_id = str(bd.get("beneficiary_id", ""))
            bd["proposed_distribution"] = str(current_amounts.get(ben_id, Decimal("0")))

    # Calculate
    calc_result = calculate_all_beneficiaries(worksheet, beneficiary_rows_data, rates)
    calc_by_id = {r["beneficiary_id"]: r for r in calc_result["rows"]}

    # Persist each row
    for bd in beneficiary_rows_data:
        ben_id = bd["beneficiary_id"]
        calc = calc_by_id.get(ben_id, {})
        try:
            row = TaxPlanningBeneficiaryRow.objects.get(
                worksheet=worksheet,
                beneficiary_id=ben_id,
            )
            row.outside_income = Decimal(str(bd.get("outside_income", 0)))
            if not is_unit_trust:
                row.proposed_distribution = Decimal(str(bd.get("proposed_distribution", 0)))
            row.beneficiary_type = bd.get("beneficiary_type", row.beneficiary_type)
            if bd.get("company_tax_rate_override"):
                row.company_tax_rate_override = Decimal(str(bd["company_tax_rate_override"]))
            # Update calculated fields
            for field in [
                "grossed_up_franking_credits", "total_taxable_income",
                "gross_tax_payable", "medicare_levy", "lito_offset",
                "franking_credit_offset", "net_tax_payable", "effective_tax_rate",
            ]:
                val = calc.get(field, Decimal("0"))
                if isinstance(val, str):
                    val = Decimal(val)
                setattr(row, field, val)
            row.save()
        except TaxPlanningBeneficiaryRow.DoesNotExist:
            pass

    worksheet.last_updated_by = request.user
    worksheet.save(update_fields=["last_updated_at", "last_updated_by"])

    _log_action(request, "update", "Updated Tax Planning beneficiary distributions", fy)

    # Serialise response
    for row in calc_result["rows"]:
        for k, v in row.items():
            if isinstance(v, Decimal):
                row[k] = str(v)

    return JsonResponse({"success": True, **calc_result})


@login_required
@require_POST
def tax_planning_save_notes(request, pk):
    """
    POST /years/<pk>/tax-planning/save-notes/
    Auto-save recommendation notes (Section 5).
    """
    fy = get_financial_year_for_user(request, pk)
    worksheet = _get_or_create_worksheet(fy, request.user)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    notes = body.get("recommendation_notes", "")
    worksheet.recommendation_notes = notes
    worksheet.last_updated_by = request.user
    worksheet.save(update_fields=["recommendation_notes", "last_updated_at", "last_updated_by"])

    return JsonResponse({"success": True})


@login_required
@require_POST
def tax_planning_scenario_save(request, pk):
    """
    POST /years/<pk>/tax-planning/scenario/save/
    Save current distribution as a named scenario. Max 3.
    """
    fy = get_financial_year_for_user(request, pk)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name = body.get("scenario_name", "").strip()
    if not name:
        return JsonResponse({"error": "Scenario name is required."}, status=400)

    distributions = body.get("distributions", [])
    total_tax = Decimal(str(body.get("total_tax", 0)))
    total_distributed = Decimal(str(body.get("total_distributed", 0)))

    existing = TaxPlanningScenario.objects.filter(financial_year=fy)
    if existing.count() >= 3:
        # Check if we're overwriting
        overwrite_id = body.get("overwrite_id")
        if overwrite_id:
            existing.filter(pk=overwrite_id).delete()
        else:
            return JsonResponse({
                "error": "Maximum 3 scenarios. Provide overwrite_id to replace one.",
                "scenarios": [
                    {"id": str(s.pk), "name": s.scenario_name}
                    for s in existing
                ],
            }, status=400)

    scenario = TaxPlanningScenario.objects.create(
        financial_year=fy,
        scenario_name=name,
        distributions=distributions,
        total_tax=total_tax,
        total_distributed=total_distributed,
        created_by=request.user,
    )

    _log_action(request, "create", f"Saved Tax Planning scenario: {name}", fy)

    return JsonResponse({
        "success": True,
        "scenario": {
            "id": str(scenario.pk),
            "name": scenario.scenario_name,
        },
    })


@login_required
@require_POST
def tax_planning_scenario_delete(request, pk, scenario_pk):
    """
    POST /years/<pk>/tax-planning/scenario/<scenario_pk>/delete/
    """
    fy = get_financial_year_for_user(request, pk)
    scenario = get_object_or_404(TaxPlanningScenario, pk=scenario_pk, financial_year=fy)
    name = scenario.scenario_name
    scenario.delete()
    _log_action(request, "delete", f"Deleted Tax Planning scenario: {name}", fy)
    return JsonResponse({"success": True})


@login_required
@require_POST
def tax_planning_scenario_apply(request, pk, scenario_pk):
    """
    POST /years/<pk>/tax-planning/scenario/<scenario_pk>/apply/
    Returns the scenario's distribution data for the frontend to load.
    """
    fy = get_financial_year_for_user(request, pk)
    scenario = get_object_or_404(TaxPlanningScenario, pk=scenario_pk, financial_year=fy)
    return JsonResponse({
        "success": True,
        "distributions": scenario.distributions,
        "scenario_name": scenario.scenario_name,
    })


@login_required
@require_POST
def tax_planning_finalise(request, pk):
    """
    POST /years/<pk>/tax-planning/finalise/
    Finalisation gate with pre-flight checks.
    """
    fy = get_financial_year_for_user(request, pk)
    worksheet = _get_or_create_worksheet(fy, request.user)

    if not request.user.can_finalise:
        return JsonResponse({"error": "You do not have permission to finalise."}, status=403)

    # Pre-flight checks
    warnings = []
    errors = []

    # 1. Balance check
    rows = worksheet.beneficiary_rows.all()
    total_distributed = sum(r.proposed_distribution for r in rows)
    undistributed = worksheet.distributable_income - total_distributed
    if undistributed != Decimal("0"):
        errors.append(
            f"All distributable income must be allocated before finalising. "
            f"Undistributed: ${undistributed:,.2f}"
        )

    # 2. Company rate check
    for row in rows:
        if row.beneficiary_type == "company" and row.company_tax_rate_override is None:
            warnings.append(
                f"Company beneficiary '{row.beneficiary.full_name}' has no base-rate flag set. "
                f"Defaulting to 25%."
            )

    # 3. Sub-trust check
    for row in rows:
        if row.beneficiary_type == "trust":
            warnings.append(
                f"Trust beneficiary '{row.beneficiary.full_name}' — "
                f"ensure a separate tax plan is prepared for this sub-trust."
            )

    # 4. Outside income check
    for row in rows:
        if row.beneficiary_type == "individual" and row.outside_income == Decimal("0"):
            warnings.append(
                f"Have you confirmed nil outside income for {row.beneficiary.full_name}?"
            )

    # 5. Recommendation check
    if not worksheet.recommendation_notes.strip():
        warnings.append("No recommendation recorded.")

    # If there are blocking errors, return them
    if errors:
        return JsonResponse({"success": False, "errors": errors, "warnings": warnings})

    # Check if user acknowledged warnings (second pass)
    acknowledged = json.loads(request.body).get("acknowledged", False) if request.body else False
    if warnings and not acknowledged:
        return JsonResponse({
            "success": False,
            "errors": [],
            "warnings": warnings,
            "require_acknowledgement": True,
        })

    # Finalise
    worksheet.status = TaxPlanningWorksheet.WorksheetStatus.FINALISED
    worksheet.finalised_at = timezone.now()
    worksheet.finalised_by = request.user
    worksheet.save(update_fields=["status", "finalised_at", "finalised_by", "last_updated_at"])

    _log_action(request, "finalise", "Finalised Tax Planning Worksheet", fy)

    return JsonResponse({"success": True, "message": "Tax Plan finalised successfully."})


@login_required
@require_POST
def tax_planning_reopen(request, pk):
    """
    POST /years/<pk>/tax-planning/reopen/
    Re-open a finalised tax plan for editing.
    """
    fy = get_financial_year_for_user(request, pk)
    worksheet = _get_or_create_worksheet(fy, request.user)

    if not request.user.can_finalise:
        return JsonResponse({"error": "You do not have permission."}, status=403)

    worksheet.status = TaxPlanningWorksheet.WorksheetStatus.DRAFT
    worksheet.finalised_at = None
    worksheet.finalised_by = None
    worksheet.save(update_fields=["status", "finalised_at", "finalised_by", "last_updated_at"])

    _log_action(request, "reopen", "Re-opened Tax Planning Worksheet", fy)

    return JsonResponse({"success": True, "message": "Tax Plan re-opened for editing."})


@login_required
def generate_trust_election_view(request, pk):
    """
    GET /years/<pk>/trust-election/
    Generate Trust Election (s97/streaming) document.
    """
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    # Staff Accountant cannot generate documents
    if not request.user.can_finalise:
        messages.error(request, "You do not have permission to generate documents.")
        return redirect("core:tax_planning_tab", pk=pk)

    if entity.entity_type not in TRUST_LIKE_TYPES:
        messages.error(request, "Trust elections are only applicable to trust entities.")
        return redirect("core:financial_year_detail", pk=pk)

    fmt = request.GET.get("format", "docx").lower()
    if fmt not in ("docx", "pdf"):
        fmt = "docx"

    from .template_docgen import generate_from_template

    try:
        buffer = generate_from_template("trust_election", "trust", fy.pk)
    except (ValueError, FileNotFoundError) as e:
        messages.error(request, f"Trust election generation failed: {e}")
        return redirect("core:tax_planning_tab", pk=pk)
    except Exception as e:
        messages.error(request, f"Unexpected error: {e}")
        return redirect("core:tax_planning_tab", pk=pk)

    entity_name = entity.entity_name.replace(" ", "_")
    base_filename = f"{entity_name}_Trust_Election_{fy.year_label}"

    if fmt == "pdf":
        import subprocess, tempfile, os
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                docx_path = os.path.join(tmpdir, f"{base_filename}.docx")
                with open(docx_path, "wb") as f:
                    f.write(buffer.getvalue())

                from core.libreoffice_utils import convert_docx_to_pdf
                convert_docx_to_pdf(docx_path, tmpdir, timeout=120)
                pdf_path = os.path.join(tmpdir, f"{base_filename}.pdf")
                if not os.path.exists(pdf_path):
                    raise RuntimeError("PDF conversion failed.")
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()

            filename = f"{base_filename}.pdf"
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            file_content = pdf_bytes
            file_format = "pdf"
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"PDF conversion failed: {e}")
            messages.error(request, f"PDF conversion failed: {e}. Falling back to DOCX.")
            filename = f"{base_filename}.docx"
            response = HttpResponse(
                buffer.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            file_content = buffer.getvalue()
            file_format = "docx"
    else:
        filename = f"{base_filename}.docx"
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        file_content = buffer.getvalue()
        file_format = "docx"

    _log_action(request, "generate", f"Generated Trust Election ({file_format.upper()}) for {fy}", fy)

    from django.core.files.base import ContentFile
    from core.models import GeneratedDocument
    doc = GeneratedDocument(
        financial_year=fy,
        file_format=file_format,
        document_type=GeneratedDocument.DocumentType.TRUST_ELECTION,
        generated_by=request.user,
    )
    doc.file.save(filename, ContentFile(file_content), save=True)

    return response


@login_required
def generate_tax_planning_summary_view(request, pk):
    """
    GET /years/<pk>/tax-planning-summary/
    Generate Tax Planning Summary (client-facing) document.
    """
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    # Staff Accountant cannot generate documents
    if not request.user.can_finalise:
        messages.error(request, "You do not have permission to generate documents.")
        return redirect("core:tax_planning_tab", pk=pk)

    if entity.entity_type not in TRUST_LIKE_TYPES:
        messages.error(request, "Tax Planning Summary is only applicable to trust entities.")
        return redirect("core:financial_year_detail", pk=pk)

    fmt = request.GET.get("format", "docx").lower()
    if fmt not in ("docx", "pdf"):
        fmt = "docx"

    from .template_docgen import generate_from_template

    try:
        buffer = generate_from_template("tax_planning_summary", "trust", fy.pk)
    except (ValueError, FileNotFoundError) as e:
        messages.error(request, f"Tax Planning Summary generation failed: {e}")
        return redirect("core:tax_planning_tab", pk=pk)
    except Exception as e:
        messages.error(request, f"Unexpected error: {e}")
        return redirect("core:tax_planning_tab", pk=pk)

    entity_name = entity.entity_name.replace(" ", "_")
    base_filename = f"{entity_name}_Tax_Planning_Summary_{fy.year_label}"

    if fmt == "pdf":
        import subprocess, tempfile, os
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                docx_path = os.path.join(tmpdir, f"{base_filename}.docx")
                with open(docx_path, "wb") as f:
                    f.write(buffer.getvalue())

                from core.libreoffice_utils import convert_docx_to_pdf
                convert_docx_to_pdf(docx_path, tmpdir, timeout=120)
                pdf_path = os.path.join(tmpdir, f"{base_filename}.pdf")
                if not os.path.exists(pdf_path):
                    raise RuntimeError("PDF conversion failed.")
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()

            filename = f"{base_filename}.pdf"
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            file_content = pdf_bytes
            file_format = "pdf"
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"PDF conversion failed: {e}")
            messages.error(request, f"PDF conversion failed: {e}. Falling back to DOCX.")
            filename = f"{base_filename}.docx"
            response = HttpResponse(
                buffer.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            file_content = buffer.getvalue()
            file_format = "docx"
    else:
        filename = f"{base_filename}.docx"
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        file_content = buffer.getvalue()
        file_format = "docx"

    _log_action(request, "generate", f"Generated Tax Planning Summary ({file_format.upper()}) for {fy}", fy)

    from django.core.files.base import ContentFile
    from core.models import GeneratedDocument
    doc = GeneratedDocument(
        financial_year=fy,
        file_format=file_format,
        document_type=GeneratedDocument.DocumentType.TAX_PLANNING_SUMMARY,
        generated_by=request.user,
    )
    doc.file.save(filename, ContentFile(file_content), save=True)

    return response
