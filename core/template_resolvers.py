"""
StatementHub — Template Merge Field Resolvers

Each document category has a dedicated resolver function that queries the
database and returns a flat dict of {field_name: value}.

Supported merge fields per category:

Distribution Minutes:
  trust_name, trustee_name, trustee_names_list, chairperson_name,
  financial_year, financial_year_end, minutes_date,
  beneficiary_rows (list[dict]), total_distributed

Trust Election (s97):
  trust_name, trustee_name, chairperson_name, resolution_date,
  financial_year_end, distributable_income, beneficiary_rows,
  streaming_rows, has_streaming, has_capital_gains, has_franked_dividends,
  capital_gains_total, franked_dividends_total, franking_credits_total

Tax Planning Summary:
  trust_name, trustee_name, financial_year_end, distributable_income,
  non_deductible_expenses, non_assessable_income, capital_gains,
  franked_dividends, franking_credits, beneficiary_rows,
  total_distributed, total_tax_payable, weighted_effective_rate,
  undistributed_balance, scenario_name, accountant_recommendation,
  summary_items
"""
import logging
import re
from decimal import Decimal

logger = logging.getLogger(__name__)


def resolve_context(document_category: str, financial_year_id) -> dict:
    """
    Dispatch to the appropriate resolver based on document category.
    """
    resolvers = {
        "distribution_minutes": resolve_distribution_minutes,
        "trust_election": resolve_trust_election,
        "tax_planning_summary": resolve_tax_planning_summary,
    }
    resolver = resolvers.get(document_category)
    if not resolver:
        raise ValueError(f"No resolver for document category: {document_category}")
    return resolver(financial_year_id)


def _get_fy_and_entity(financial_year_id):
    """Common helper to load FY and entity."""
    from core.models import FinancialYear
    fy = FinancialYear.objects.select_related("entity").get(pk=financial_year_id)
    return fy, fy.entity


def _get_officers(entity):
    """Get all active officers for an entity."""
    from core.models import EntityOfficer
    return EntityOfficer.objects.filter(
        entity=entity,
        date_ceased__isnull=True,
    ).order_by("display_order", "full_name")


def _officer_has_role(officer, role_value):
    """Check if an officer has a specific role (JSONField or legacy)."""
    if officer.roles:
        return role_value in officer.roles
    return officer.role == role_value


def _find_trustees(officers):
    """Find all trustees from officer list."""
    return [o for o in officers if _officer_has_role(o, "trustee")]


def _find_chairperson(officers):
    """Find the chairperson from officer list."""
    for o in officers:
        if _officer_has_role(o, "chairperson"):
            return o
    for o in officers:
        if getattr(o, "is_chairperson", False):
            return o
    return None


def _format_name_list(names):
    """Format a list of names with commas and 'and'."""
    if len(names) == 1:
        return names[0]
    elif len(names) == 2:
        return f"{names[0]} and {names[1]}"
    else:
        return ", ".join(names[:-1]) + f", and {names[-1]}"


def _fmt_money(value):
    """Format a Decimal as $X,XXX.XX."""
    if value is None:
        return "$0.00"
    val = Decimal(str(value))
    if val < 0:
        return f"(${abs(val):,.2f})"
    return f"${val:,.2f}"


def _get_fy_year(fy):
    """Extract the year digits from a FY label."""
    digits = "".join(c for c in fy.year_label if c.isdigit())
    return digits if digits else str(fy.end_date.year)


def _strip_html(text):
    """Strip HTML tags from rich text content."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = clean.replace("&nbsp;", " ").replace("&amp;", "&")
    clean = clean.replace("&lt;", "<").replace("&gt;", ">")
    return clean.strip()


# =============================================================================
# Distribution Minutes Resolver
# =============================================================================
def resolve_distribution_minutes(financial_year_id) -> dict:
    """Resolve all merge fields for Distribution Minutes."""
    fy, entity = _get_fy_and_entity(financial_year_id)
    officers = _get_officers(entity)

    trustees = _find_trustees(officers)
    if not trustees:
        # Fall back to the entity-level trustee_name field (e.g. trustee company name)
        if entity.trustee_name:
            trustee_names = [entity.trustee_name]
        else:
            trustee_names = ["[Trustee not set — please add in Directors/Trustees/Beneficiaries tab]"]
    else:
        trustee_names = [t.full_name for t in trustees]

    chairperson = _find_chairperson(officers)
    fy_year = _get_fy_year(fy)

    # Get beneficiary data from selected TaxPlanningScenario (Stage 2)
    beneficiary_rows = []
    total_distributed = Decimal("0")
    try:
        from core.models import TrustWorkspace, EntityOfficer
        workspace = TrustWorkspace.objects.select_related(
            "selected_tax_scenario"
        ).get(financial_year=fy)
        scenario = workspace.selected_tax_scenario
        logger.info(
            "distmin resolver: fy=%s workspace=%s scenario=%s distributions=%s",
            fy.pk, workspace.pk,
            scenario.scenario_name if scenario else None,
            scenario.distributions if scenario else None,
        )
        if scenario and scenario.distributions:
            officer_map = {
                str(o.pk): o.full_name
                for o in EntityOfficer.objects.filter(entity=entity)
            }
            for entry in scenario.distributions:
                amount = Decimal(str(entry.get("proposed_distribution", 0)))
                if amount > 0:
                    ben_id = str(entry.get("beneficiary_id", ""))
                    name = officer_map.get(ben_id, f"Beneficiary {ben_id[:8]}")
                    beneficiary_rows.append({
                        "name": name,
                        "type": entry.get("beneficiary_type", "Individual").title(),
                        "distribution": _fmt_money(amount),
                        "distribution_raw": amount,
                        "percentage": "",
                    })
                    total_distributed += amount
            beneficiary_rows.sort(key=lambda r: r["name"])
            if total_distributed > 0:
                for br in beneficiary_rows:
                    pct = (br["distribution_raw"] / total_distributed * 100).quantize(Decimal("0.01"))
                    br["percentage"] = f"{pct}%"
    except TrustWorkspace.DoesNotExist:
        logger.warning("distmin resolver: No TrustWorkspace for fy=%s", fy.pk)
    except Exception as exc:
        logger.exception("Failed to load distribution data for Distribution Minutes: %s", exc)

    context = {
        "trust_name": entity.entity_name,
        "trustee_name": _format_name_list(trustee_names),
        "trustee_names_list": trustee_names,
        "chairperson_name": chairperson.full_name if chairperson else "[Chairperson not set — please assign in Directors/Trustees/Beneficiaries tab]",
        "financial_year": fy_year,
        "financial_year_end": f"30 June {fy_year}",
        "minutes_date": f"30 June {fy_year}",
        "beneficiary_rows": beneficiary_rows,
        "total_distributed": _fmt_money(total_distributed),
        "total_distributed_raw": total_distributed,
        "has_beneficiaries": len(beneficiary_rows) > 0,
    }
    logger.info(
        "distmin context: has_beneficiaries=%s rows=%d total=%s",
        context["has_beneficiaries"], len(beneficiary_rows), context["total_distributed"],
    )
    return context


# =============================================================================
# Trust Election (s97) Resolver
# =============================================================================
def resolve_trust_election(financial_year_id) -> dict:
    """Resolve all merge fields for Trust Election (s97/streaming)."""
    fy, entity = _get_fy_and_entity(financial_year_id)
    officers = _get_officers(entity)

    trustees = _find_trustees(officers)
    chairperson = _find_chairperson(officers)

    fy_year = _get_fy_year(fy)
    trustee_names = [t.full_name for t in trustees] if trustees else ["[Trustee not set]"]

    # Get worksheet data
    from core.models import TaxPlanningWorksheet
    try:
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=fy)
    except TaxPlanningWorksheet.DoesNotExist:
        raise ValueError("Tax Planning Worksheet must be completed before generating Trust Election.")

    rows = worksheet.beneficiary_rows.select_related("beneficiary").order_by(
        "beneficiary__full_name"
    )

    distributable = worksheet.distributable_income
    capital_gains = worksheet.capital_gains
    franked_dividends = worksheet.franked_dividends
    franking_credits = worksheet.franking_credits

    has_capital_gains = capital_gains > 0
    has_franked_dividends = franked_dividends > 0
    has_streaming = has_capital_gains or has_franked_dividends

    # Build beneficiary rows
    beneficiary_rows = []
    streaming_rows = []
    total_distributed = Decimal("0")
    total_tax = Decimal("0")

    for row in rows:
        eff_rate = (
            f"{(row.effective_tax_rate * 100).quantize(Decimal('0.01'))}%"
            if row.effective_tax_rate else "0.00%"
        )
        beneficiary_rows.append({
            "name": row.beneficiary.full_name,
            "type": row.get_beneficiary_type_display(),
            "distribution": _fmt_money(row.proposed_distribution),
            "net_tax": _fmt_money(row.net_tax_payable) if row.beneficiary_type != "trust" else "Refer to sub-trust",
            "effective_rate": eff_rate if row.beneficiary_type != "trust" else "—",
            "notes": _get_beneficiary_notes(row),
        })
        total_distributed += row.proposed_distribution
        if row.beneficiary_type != "trust":
            total_tax += row.net_tax_payable

        # Streaming allocation (proportional)
        if has_streaming and row.proposed_distribution > 0 and distributable > 0:
            proportion = row.proposed_distribution / distributable
            streaming_rows.append({
                "name": row.beneficiary.full_name,
                "capital_gains": _fmt_money(capital_gains * proportion) if has_capital_gains else "—",
                "franked_dividends": _fmt_money(franked_dividends * proportion) if has_franked_dividends else "—",
                "franking_credits": _fmt_money(franking_credits * proportion) if has_franked_dividends else "—",
                "other_income": _fmt_money(
                    row.proposed_distribution - (capital_gains + franked_dividends) * proportion
                ),
            })

    return {
        "trust_name": entity.entity_name,
        "trustee_name": _format_name_list(trustee_names),
        "chairperson_name": chairperson.full_name if chairperson else "[Chairperson not set]",
        "resolution_date": f"30 June {fy_year}",
        "financial_year_end": f"30 June {fy_year}",
        "financial_year": fy_year,
        "distributable_income": _fmt_money(distributable),
        "distributable_income_raw": distributable,
        "beneficiary_rows": beneficiary_rows,
        "streaming_rows": streaming_rows,
        "has_streaming": has_streaming,
        "has_capital_gains": has_capital_gains,
        "has_franked_dividends": has_franked_dividends,
        "capital_gains_total": _fmt_money(capital_gains),
        "franked_dividends_total": _fmt_money(franked_dividends),
        "franking_credits_total": _fmt_money(franking_credits),
        "total_distributed": _fmt_money(total_distributed),
        "total_tax_payable": _fmt_money(total_tax),
    }


# =============================================================================
# Tax Planning Summary Resolver
# =============================================================================
def resolve_tax_planning_summary(financial_year_id) -> dict:
    """Resolve all merge fields for Tax Planning Summary."""
    fy, entity = _get_fy_and_entity(financial_year_id)
    officers = _get_officers(entity)

    trustees = _find_trustees(officers)
    trustee_names = [t.full_name for t in trustees] if trustees else ["[Trustee not set]"]

    fy_year = _get_fy_year(fy)

    # Get worksheet data
    from core.models import TaxPlanningWorksheet, TaxPlanningScenario
    try:
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=fy)
    except TaxPlanningWorksheet.DoesNotExist:
        raise ValueError("Tax Planning Worksheet must be completed before generating Tax Planning Summary.")

    rows = worksheet.beneficiary_rows.select_related("beneficiary").order_by(
        "beneficiary__full_name"
    )

    distributable = worksheet.distributable_income
    total_distributed = Decimal("0")
    total_tax = Decimal("0")

    beneficiary_rows = []
    for row in rows:
        eff_rate = (
            f"{(row.effective_tax_rate * 100).quantize(Decimal('0.01'))}%"
            if row.effective_tax_rate else "0.00%"
        )
        beneficiary_rows.append({
            "name": row.beneficiary.full_name,
            "type": row.get_beneficiary_type_display(),
            "distribution": _fmt_money(row.proposed_distribution),
            "net_tax": _fmt_money(row.net_tax_payable) if row.beneficiary_type != "trust" else "Refer to sub-trust",
            "effective_rate": eff_rate if row.beneficiary_type != "trust" else "—",
            "notes": _get_beneficiary_notes(row),
        })
        total_distributed += row.proposed_distribution
        if row.beneficiary_type != "trust":
            total_tax += row.net_tax_payable

    undistributed = distributable - total_distributed
    weighted_rate = (
        f"{(total_tax / total_distributed * 100).quantize(Decimal('0.01'))}%"
        if total_distributed > 0 else "0.00%"
    )

    # Get latest scenario name
    latest_scenario = TaxPlanningScenario.objects.filter(
        financial_year=fy
    ).order_by("-created_at").first()
    scenario_name = latest_scenario.scenario_name if latest_scenario else "Current"

    # Recommendation
    recommendation = _strip_html(worksheet.recommendation_notes)

    # Summary items for key-value table
    summary_items = [
        {"label": "Total Distributable Income", "value": _fmt_money(distributable)},
        {"label": "Total Proposed Distributions", "value": _fmt_money(total_distributed)},
        {"label": "Undistributed Balance", "value": _fmt_money(undistributed)},
        {"label": "Total Estimated Tax", "value": _fmt_money(total_tax)},
        {"label": "Weighted Effective Tax Rate", "value": weighted_rate},
    ]

    return {
        "trust_name": entity.entity_name,
        "trustee_name": _format_name_list(trustee_names),
        "financial_year_end": f"30 June {fy_year}",
        "financial_year": fy_year,
        "distributable_income": _fmt_money(distributable),
        "distributable_income_raw": distributable,
        "non_deductible_expenses": _fmt_money(worksheet.non_deductible_expenses),
        "non_assessable_income": _fmt_money(worksheet.non_assessable_income),
        "capital_gains": _fmt_money(worksheet.capital_gains),
        "franked_dividends": _fmt_money(worksheet.franked_dividends),
        "franking_credits": _fmt_money(worksheet.franking_credits),
        "beneficiary_rows": beneficiary_rows,
        "total_distributed": _fmt_money(total_distributed),
        "total_distributed_raw": total_distributed,
        "total_tax_payable": _fmt_money(total_tax),
        "total_tax_raw": total_tax,
        "undistributed_balance": _fmt_money(undistributed),
        "weighted_effective_rate": weighted_rate,
        "scenario_name": scenario_name,
        "accountant_recommendation": recommendation,
        "has_recommendation": bool(recommendation),
        "summary_items": summary_items,
    }


# =============================================================================
# Helpers
# =============================================================================
def _get_beneficiary_notes(row):
    """Build notes string for a beneficiary row."""
    notes = []
    if row.beneficiary_type == "company":
        if row.company_tax_rate_override:
            notes.append(f"Non-base rate ({row.company_tax_rate_override * 100:.0f}%)")
        else:
            notes.append("Base rate entity (25%)")
    elif row.beneficiary_type == "trust":
        notes.append("Separate tax plan required")
    return "; ".join(notes) if notes else ""
