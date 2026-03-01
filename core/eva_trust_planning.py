"""
Eva Trust Tax Planning Service
==============================

Provides conversational distribution planning within Eva's chat interface.
Triggered when an accountant asks Eva about trust distributions for a trust entity.

Key capabilities:
- Income summary from TB (ordinary, CGT, franked dividends, etc.)
- Beneficiary tax profile analysis
- Distribution recommendation with compliance flags
- "What if" scenario modelling
- Draft trustee resolution pre-population
"""

import json
import logging
from decimal import Decimal

from django.utils import timezone

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

# Keywords that trigger trust planning mode
TRUST_PLANNING_TRIGGERS = [
    "distribution", "distribute", "trust income", "beneficiary",
    "trust planning", "trust tax", "net distributable", "ndi",
    "section 100a", "family trust", "fte", "iee",
    "who should we distribute", "how should we distribute",
    "distribution recommendation", "distribution scenario",
]


def is_trust_planning_query(message_text, entity_type):
    """Check if a chat message should trigger trust planning mode."""
    if entity_type not in ("trust_discretionary", "trust_unit", "trust_hybrid"):
        return False
    msg_lower = message_text.lower()
    return any(trigger in msg_lower for trigger in TRUST_PLANNING_TRIGGERS)


def build_trust_planning_context(financial_year):
    """
    Build comprehensive context for trust distribution planning.

    Returns a dict with:
    - income_summary: breakdown of trust income by stream
    - beneficiary_profiles: list of beneficiaries with tax profiles
    - entity_relationships: related entities for streaming
    - compliance_flags: any issues detected
    """
    from core.models import (
        TrialBalanceLine, EntityOfficer, TrustWorkspace,
        BeneficiaryProfile, DistributionScenario,
    )

    fy = financial_year
    entity = fy.entity
    context = {}

    # 1. Income Summary from TB
    income_data = _calculate_income_streams(fy)
    context["income_summary"] = income_data

    # 2. Beneficiary Profiles
    officers = EntityOfficer.objects.filter(entity=entity)
    beneficiaries = []
    for officer in officers:
        profile = {
            "name": officer.full_name,
            "role": officer.get_role_display() if hasattr(officer, 'get_role_display') else officer.role,
            "tax_residency": getattr(officer, 'tax_residency', 'AU'),
            "beneficiary_type": getattr(officer, 'beneficiary_type', ''),
            "email": getattr(officer, 'email', ''),
        }
        # Check for BeneficiaryProfile if TrustWorkspace exists
        try:
            workspace = TrustWorkspace.objects.filter(financial_year=fy).first()
            if workspace:
                bp = BeneficiaryProfile.objects.filter(
                    workspace=workspace,
                    officer=officer,
                ).first()
                if bp:
                    profile["marginal_tax_rate"] = str(bp.marginal_tax_rate) if bp.marginal_tax_rate else None
                    profile["other_income"] = str(bp.other_income) if bp.other_income else "0"
                    profile["has_tax_losses"] = bp.has_tax_losses
                    profile["is_under_18"] = bp.is_under_18
                    profile["is_non_resident"] = bp.is_non_resident
                    profile["notes"] = bp.notes
        except Exception:
            pass

        beneficiaries.append(profile)

    context["beneficiaries"] = beneficiaries

    # 3. Entity Relationships (for streaming to related entities)
    context["entity_info"] = {
        "name": entity.entity_name,
        "type": entity.get_entity_type_display(),
        "abn": entity.abn or "",
        "trust_deed_date": str(entity.deed_date) if hasattr(entity, 'deed_date') and entity.deed_date else "",
        "vesting_date": str(entity.vesting_date) if hasattr(entity, 'vesting_date') and entity.vesting_date else "",
    }

    # 4. Existing scenarios (if any)
    try:
        workspace = TrustWorkspace.objects.filter(financial_year=fy).first()
        if workspace:
            scenarios = DistributionScenario.objects.filter(workspace=workspace)
            context["existing_scenarios"] = [
                {
                    "name": s.name,
                    "is_final": s.is_final,
                    "allocation": s.allocation_data,
                    "total_tax_estimate": str(s.total_tax_estimate) if s.total_tax_estimate else None,
                }
                for s in scenarios
            ]
    except Exception:
        context["existing_scenarios"] = []

    # 5. Compliance flags
    context["compliance_flags"] = _check_trust_compliance(fy, income_data)

    return context


def _calculate_income_streams(financial_year):
    """
    Calculate trust income streams from the trial balance.

    Returns dict with:
    - total_revenue, total_expenses, net_profit
    - income_streams: ordinary, cgt_discount, cgt_non_discount,
      franked_dividends, franking_credits, tax_free
    """
    from core.models import TrialBalanceLine

    lines = TrialBalanceLine.objects.filter(
        financial_year=financial_year,
    ).select_related("mapped_line_item")

    total_revenue = ZERO
    total_expenses = ZERO
    income_streams = {
        "ordinary_income": ZERO,
        "cgt_discount": ZERO,
        "cgt_non_discount": ZERO,
        "franked_dividends": ZERO,
        "franking_credits": ZERO,
        "tax_free_income": ZERO,
    }

    for line in lines:
        net = (line.debit or ZERO) - (line.credit or ZERO)
        name_lower = (line.account_name or "").lower()
        section = ""
        if line.mapped_line_item:
            section = (line.mapped_line_item.statement_section or "").lower()

        # Classify into income streams
        if "capital gain" in name_lower and "discount" in name_lower:
            income_streams["cgt_discount"] += abs(net)
        elif "capital gain" in name_lower:
            income_streams["cgt_non_discount"] += abs(net)
        elif "franked dividend" in name_lower or "franking credit" in name_lower:
            if "credit" in name_lower:
                income_streams["franking_credits"] += abs(net)
            else:
                income_streams["franked_dividends"] += abs(net)
        elif "tax free" in name_lower or "tax-free" in name_lower:
            income_streams["tax_free_income"] += abs(net)
        elif "revenue" in section or "income" in section:
            total_revenue += abs(net)
            income_streams["ordinary_income"] += abs(net)
        elif "expense" in section or "cost" in section:
            total_expenses += abs(net)

    net_profit = total_revenue - total_expenses

    return {
        "total_revenue": str(total_revenue),
        "total_expenses": str(total_expenses),
        "net_profit": str(net_profit),
        "net_distributable_income": str(net_profit),  # Simplified; trust law adjustments may apply
        "income_streams": {k: str(v) for k, v in income_streams.items()},
    }


def _check_trust_compliance(financial_year, income_data):
    """Check for trust distribution compliance issues."""
    flags = []
    fy = financial_year
    entity = fy.entity

    # Check 1: Resolution date (must be before 30 June)
    if fy.end_date and fy.end_date.month == 6 and fy.end_date.day == 30:
        flags.append({
            "type": "resolution_deadline",
            "severity": "CRITICAL",
            "message": f"Distribution resolution MUST be made on or before {fy.end_date.strftime('%d %B %Y')}.",
        })

    # Check 2: Section 100A risk
    flags.append({
        "type": "section_100a",
        "severity": "ADVISORY",
        "message": "Section 100A reimbursement agreements — ensure distributions are genuine and not circular.",
    })

    # Check 3: Minor beneficiaries
    from core.models import EntityOfficer
    officers = EntityOfficer.objects.filter(entity=entity)
    for officer in officers:
        if getattr(officer, 'beneficiary_type', '') == 'minor':
            flags.append({
                "type": "minor_beneficiary",
                "severity": "ADVISORY",
                "message": f"Minor beneficiary detected: {officer.full_name}. "
                           f"Division 6AA applies — unearned income taxed at penalty rates.",
            })

    # Check 4: Non-resident beneficiaries
    for officer in officers:
        if getattr(officer, 'tax_residency', 'AU') != 'AU':
            flags.append({
                "type": "non_resident",
                "severity": "ADVISORY",
                "message": f"Non-resident beneficiary: {officer.full_name}. "
                           f"Withholding tax obligations may apply.",
            })

    return flags


TRUST_PLANNING_SYSTEM_PROMPT = """You are Eva, an expert Australian trust tax planning advisor at MC&S Accountants.

You are helping the accountant plan the trust distribution for {entity_name} ({entity_type}) for the year ending {year_end_date}.

Your role is to:
1. Summarise the trust's income position (total NDI, income streams)
2. Profile each beneficiary's tax position
3. Recommend an optimal distribution strategy that minimises total family group tax
4. Flag any compliance risks (Section 100A, Division 6AA, non-resident withholding)
5. Support "what if" scenario modelling when asked
6. Pre-populate a draft trustee resolution when the accountant is ready

IMPORTANT RULES:
- Always consider the trust deed's distribution powers
- Never recommend distributions that create Section 100A risk
- Flag any Division 6AA issues for minor beneficiaries
- Consider streaming of capital gains and franked dividends
- Account for each beneficiary's marginal tax rate and other income
- Remind the accountant that the resolution MUST be made by 30 June
- Use Australian tax terminology and cite relevant legislation

Be conversational but precise. Ask clarifying questions when needed."""


def get_trust_planning_prompt(financial_year, user, message_text):
    """
    Build the full prompt for a trust planning conversation.

    Returns (system_prompt, user_prompt, planning_context).
    """
    entity = financial_year.entity
    context = build_trust_planning_context(financial_year)

    system_prompt = TRUST_PLANNING_SYSTEM_PROMPT.format(
        entity_name=entity.entity_name,
        entity_type=entity.get_entity_type_display(),
        year_end_date=financial_year.end_date.strftime("%d %B %Y"),
    )

    user_prompt = f"""=== TRUST INCOME SUMMARY ===
{json.dumps(context['income_summary'], indent=2)}

=== BENEFICIARIES ===
{json.dumps(context['beneficiaries'], indent=2)}

=== ENTITY INFO ===
{json.dumps(context['entity_info'], indent=2)}

=== EXISTING DISTRIBUTION SCENARIOS ===
{json.dumps(context.get('existing_scenarios', []), indent=2)}

=== COMPLIANCE FLAGS ===
{json.dumps(context['compliance_flags'], indent=2)}

=== ACCOUNTANT'S QUESTION ===
{message_text}"""

    return system_prompt, user_prompt, context


def get_or_create_planning_session(financial_year, conversation, user):
    """Get or create an active trust planning session."""
    from core.models import EvaTrustPlanningSession

    # Check for active (incomplete) session
    session = EvaTrustPlanningSession.objects.filter(
        financial_year=financial_year,
        conversation=conversation,
        completed_at__isnull=True,
    ).first()

    if not session:
        income_data = _calculate_income_streams(financial_year)
        session = EvaTrustPlanningSession.objects.create(
            financial_year=financial_year,
            conversation=conversation,
            triggered_by=user,
            net_distributable_income=Decimal(income_data["net_distributable_income"]),
        )

    return session
