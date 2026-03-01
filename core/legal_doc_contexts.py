"""
Legal Document Generation — Context Builders
=============================================
Each document type has a dedicated build_context() function that:
  1. Takes entity, financial_year (optional), and wizard parameters
  2. Queries the database for officers, relationships, governing docs
  3. Returns a dict suitable for docxtpl rendering

Document Types:
  - Division 7A Loan Agreement
  - Change of Trustee Set (3 documents)
  - Fixed Unit Trust Deed + Ancillaries (5 documents)
  - Unit Transfer Package (7 documents)

Shared helpers handle entity description formatting, director block
rendering, and signatory list construction.
"""
import logging
from datetime import date
from decimal import Decimal

from django.utils import timezone

from core.models import (
    Entity,
    EntityOfficer,
    EntityRelationship,
    GoverningDocument,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared Helpers
# ---------------------------------------------------------------------------

def _format_date(d):
    """Format a date object as 'DD Month YYYY'."""
    if not d:
        return ""
    if isinstance(d, str):
        return d
    return d.strftime("%d %B %Y")


def _format_currency(amount):
    """Format a Decimal/float as '$X,XXX.XX'."""
    if amount is None:
        return "$0.00"
    return f"${Decimal(str(amount)):,.2f}"


def _ordinal(n):
    """Return ordinal string for an integer (1st, 2nd, 3rd, etc.)."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_date_ordinal(d):
    """Format date as 'the 1st day of July 2025'."""
    if not d:
        return ""
    if isinstance(d, str):
        return d
    return f"the {_ordinal(d.day)} day of {d.strftime('%B %Y')}"


def _build_entity_description(entity):
    """
    Build the full legal description of an entity for use in documents.
    - Trust: "TRUSTEE_NAME ACN XXX XXX XXX AS TRUSTEE FOR TRUST_NAME"
    - Company: "ENTITY_NAME ACN XXX XXX XXX"
    - Individual (officer): "FULL_NAME"
    """
    if not entity:
        return ""

    if isinstance(entity, str):
        return entity

    name = entity.entity_name.upper()
    acn = entity.acn or ""
    trustee_name = getattr(entity, "trustee_name", "") or ""
    trustee_acn = getattr(entity, "trustee_acn", "") or ""

    if entity.entity_type == "trust":
        if trustee_name:
            desc = trustee_name.upper()
            if trustee_acn:
                desc += f" ACN {_format_acn(trustee_acn)}"
            desc += f" AS TRUSTEE FOR {name}"
            return desc
        return name
    elif entity.entity_type == "company":
        desc = name
        if acn:
            desc += f" ACN {_format_acn(acn)}"
        return desc
    else:
        return name


def _format_acn(acn):
    """Format ACN as 'XXX XXX XXX'."""
    acn = str(acn).replace(" ", "")
    if len(acn) == 9:
        return f"{acn[:3]} {acn[3:6]} {acn[6:]}"
    return acn


def _format_abn(abn):
    """Format ABN as 'XX XXX XXX XXX'."""
    abn = str(abn).replace(" ", "")
    if len(abn) == 11:
        return f"{abn[:2]} {abn[2:5]} {abn[5:8]} {abn[8:]}"
    return abn


def _get_active_directors(entity):
    """Get active directors for an entity, ordered by display_order."""
    return EntityOfficer.objects.filter(
        entity=entity,
        date_ceased__isnull=True,
    ).filter(
        # Check both legacy role field and new roles JSONField
        **{}  # We'll use Q objects below
    ).order_by("display_order", "full_name")


def _get_directors_for_entity(entity):
    """
    Get active directors for an entity. Handles both the legacy 'role' field
    and the newer 'roles' JSONField.
    """
    from django.db.models import Q
    officers = EntityOfficer.objects.filter(
        entity=entity,
        date_ceased__isnull=True,
    ).filter(
        Q(role="director") | Q(roles__contains=["director"])
    ).order_by("display_order", "full_name")
    return list(officers)


def _get_officers_by_role(entity, role):
    """Get active officers with a specific role."""
    from django.db.models import Q
    return list(EntityOfficer.objects.filter(
        entity=entity,
        date_ceased__isnull=True,
    ).filter(
        Q(role=role) | Q(roles__contains=[role])
    ).order_by("display_order", "full_name"))


def _director_context(officer):
    """Build a context dict for a single director/officer."""
    return {
        "name": officer.full_name,
        "full_name": officer.full_name,
        "title": officer.title or "",
        "role": officer.roles_display if officer.roles else officer.get_role_display(),
        "date_appointed": _format_date(officer.date_appointed),
        "email": officer.email or "",
        "is_signatory": officer.is_signatory,
    }


def _is_sole_director(entity):
    """Check if entity has exactly one active director."""
    directors = _get_directors_for_entity(entity)
    return len(directors) == 1


def _build_execution_block(entity):
    """
    Build the execution/signing block context for an entity.
    Returns dict with sole_director flag and director list.
    """
    directors = _get_directors_for_entity(entity)
    return {
        "is_sole_director": len(directors) == 1,
        "directors": [_director_context(d) for d in directors],
        "director_count": len(directors),
        "company_name": entity.entity_name,
        "acn": entity.acn or "",
        "acn_formatted": _format_acn(entity.acn) if entity.acn else "",
    }


def _get_entity_address(entity):
    """Build a formatted address string from entity fields."""
    parts = []
    if entity.address_line_1:
        parts.append(entity.address_line_1)
    if entity.address_line_2:
        parts.append(entity.address_line_2)
    city_state = []
    if entity.suburb:
        city_state.append(entity.suburb)
    if entity.state:
        city_state.append(entity.state)
    if entity.postcode:
        city_state.append(entity.postcode)
    if city_state:
        parts.append(" ".join(city_state))
    if entity.country and entity.country != "Australia":
        parts.append(entity.country)
    return ", ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Standard disclaimer and firm details
# ---------------------------------------------------------------------------
STANDARD_DISCLAIMER = (
    "This document has been generated by StatementHub on behalf of MC & S Chartered "
    "Accountants. It is based on the information provided and the applicable template. "
    "MC & S recommends that all legal documents be reviewed by a qualified solicitor "
    "before execution. MC & S accepts no liability for any loss arising from the use "
    "of this document without independent legal advice."
)

FIRM_DETAILS = {
    "firm_name": "MC & S Chartered Accountants",
    "firm_abn": "",
    "firm_address": "",
}


# ---------------------------------------------------------------------------
# Document 1: Division 7A Loan Agreement
# ---------------------------------------------------------------------------

def build_div7a_context(entity, financial_year, params):
    """
    Build the template context for a Division 7A Loan Agreement.

    Wizard params:
      - borrower_type: "individual" | "trust" | "company"
      - borrower_entity_id: UUID (for trust/company borrowers)
      - borrower_name: str (for individual borrowers)
      - agreement_date: date string
      - loan_security_type: "unsecured" | "secured"
      - loan_amount: decimal (optional, from risk flag)

    Three conditional branches based on borrower_type:
      1. Individual: borrower is a natural person (shareholder/director)
      2. Trust: borrower is a trust entity (trustee signs)
      3. Company: borrower is a company entity (directors sign)
    """
    borrower_type = params.get("borrower_type", "individual")
    borrower_entity_id = params.get("borrower_entity_id")
    borrower_name = params.get("borrower_name", "")
    agreement_date = params.get("agreement_date", "")
    loan_security_type = params.get("loan_security_type", "unsecured")
    loan_amount = params.get("loan_amount", "")

    # Parse agreement date
    if agreement_date and isinstance(agreement_date, str):
        try:
            from datetime import datetime
            agreement_date_obj = datetime.strptime(agreement_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            agreement_date_obj = None
    elif isinstance(agreement_date, date):
        agreement_date_obj = agreement_date
    else:
        agreement_date_obj = None

    # Lender context (always the company entity)
    lender_directors = _get_directors_for_entity(entity)
    lender_context = {
        "name": entity.entity_name,
        "acn": entity.acn or "",
        "acn_formatted": _format_acn(entity.acn) if entity.acn else "",
        "abn": entity.abn or "",
        "abn_formatted": _format_abn(entity.abn) if entity.abn else "",
        "address": _get_entity_address(entity),
        "is_sole_director": len(lender_directors) == 1,
        "directors": [_director_context(d) for d in lender_directors],
        "execution": _build_execution_block(entity),
    }

    # Borrower context — varies by type
    borrower_context = _build_borrower_context(
        borrower_type, borrower_entity_id, borrower_name, params
    )

    # Benchmark interest rate (from ATO — current year)
    # This would normally come from TaxReferenceData, but we allow override
    benchmark_rate = params.get("benchmark_interest_rate", "8.27")

    # Loan term
    loan_term_years = params.get("loan_term_years", "7")
    if loan_security_type == "secured":
        loan_term_years = params.get("loan_term_years", "25")

    # Financial year for minimum yearly repayment calculation
    fy_end = financial_year.end_date if financial_year else None

    context = {
        # Document metadata
        "document_title": "DIVISION 7A LOAN AGREEMENT",
        "agreement_date": _format_date(agreement_date_obj),
        "agreement_date_ordinal": _format_date_ordinal(agreement_date_obj),
        "generation_date": _format_date(timezone.now().date()),

        # Lender (the company)
        "lender": lender_context,
        "lender_name": entity.entity_name,
        "lender_acn": entity.acn or "",
        "lender_acn_formatted": _format_acn(entity.acn) if entity.acn else "",
        "lender_abn": entity.abn or "",
        "lender_address": _get_entity_address(entity),

        # Borrower
        "borrower": borrower_context,
        "borrower_type": borrower_type,
        "borrower_name": borrower_context.get("name", borrower_name),
        "borrower_description": borrower_context.get("description", borrower_name),
        "borrower_is_individual": borrower_type == "individual",
        "borrower_is_trust": borrower_type == "trust",
        "borrower_is_company": borrower_type == "company",

        # Loan terms
        "loan_amount": loan_amount,
        "loan_amount_formatted": _format_currency(loan_amount) if loan_amount else "",
        "loan_security_type": loan_security_type,
        "is_secured": loan_security_type == "secured",
        "is_unsecured": loan_security_type == "unsecured",
        "benchmark_interest_rate": benchmark_rate,
        "loan_term_years": loan_term_years,
        "maximum_term": f"{loan_term_years} years",

        # Financial year
        "fy_end_date": _format_date(fy_end),
        "fy_year": str(fy_end.year) if fy_end else "",

        # Legislation
        "legislation_ref": "Division 7A, Income Tax Assessment Act 1936",

        # Firm
        **FIRM_DETAILS,
        "disclaimer": STANDARD_DISCLAIMER,
    }

    return context


def _build_borrower_context(borrower_type, borrower_entity_id, borrower_name, params):
    """Build borrower-specific context based on borrower type."""
    if borrower_type == "individual":
        return {
            "name": borrower_name or params.get("borrower_name", ""),
            "description": borrower_name or params.get("borrower_name", ""),
            "address": params.get("borrower_address", ""),
            "is_individual": True,
            "is_trust": False,
            "is_company": False,
            "directors": [],
            "execution": {
                "is_sole_director": False,
                "directors": [],
                "is_individual": True,
                "signatory_name": borrower_name,
            },
        }

    if borrower_entity_id:
        try:
            borrower_entity = Entity.objects.get(pk=borrower_entity_id)
        except Entity.DoesNotExist:
            logger.warning("Borrower entity %s not found", borrower_entity_id)
            return {"name": borrower_name, "description": borrower_name, "directors": []}
    else:
        return {"name": borrower_name, "description": borrower_name, "directors": []}

    if borrower_type == "trust":
        directors = _get_directors_for_entity(borrower_entity)
        # For trusts, get the trustee company directors
        trustee_name = borrower_entity.trustee_name or borrower_entity.entity_name
        return {
            "name": borrower_entity.entity_name,
            "description": _build_entity_description(borrower_entity),
            "trustee_name": trustee_name,
            "trustee_acn": borrower_entity.trustee_acn or "",
            "trustee_acn_formatted": _format_acn(borrower_entity.trustee_acn) if borrower_entity.trustee_acn else "",
            "abn": borrower_entity.abn or "",
            "address": _get_entity_address(borrower_entity),
            "is_individual": False,
            "is_trust": True,
            "is_company": False,
            "directors": [_director_context(d) for d in directors],
            "execution": _build_execution_block(borrower_entity),
        }

    elif borrower_type == "company":
        directors = _get_directors_for_entity(borrower_entity)
        return {
            "name": borrower_entity.entity_name,
            "description": _build_entity_description(borrower_entity),
            "acn": borrower_entity.acn or "",
            "acn_formatted": _format_acn(borrower_entity.acn) if borrower_entity.acn else "",
            "abn": borrower_entity.abn or "",
            "address": _get_entity_address(borrower_entity),
            "is_individual": False,
            "is_trust": False,
            "is_company": True,
            "directors": [_director_context(d) for d in directors],
            "execution": _build_execution_block(borrower_entity),
        }

    return {"name": borrower_name, "description": borrower_name, "directors": []}


def get_div7a_signatories(entity, params):
    """
    Build FuseSign signatory list for Div 7A.
    Lender directors + borrower signatories + witnesses.
    """
    signatories = []

    # Lender directors
    for d in _get_directors_for_entity(entity):
        if d.email:
            signatories.append({
                "name": d.full_name,
                "email": d.email,
                "role": "Lender Director",
            })

    # Borrower signatories
    borrower_type = params.get("borrower_type", "individual")
    borrower_entity_id = params.get("borrower_entity_id")

    if borrower_type == "individual":
        borrower_email = params.get("borrower_email", "")
        if borrower_email:
            signatories.append({
                "name": params.get("borrower_name", ""),
                "email": borrower_email,
                "role": "Borrower",
            })
    elif borrower_entity_id:
        try:
            borrower_entity = Entity.objects.get(pk=borrower_entity_id)
            for d in _get_directors_for_entity(borrower_entity):
                if d.email:
                    signatories.append({
                        "name": d.full_name,
                        "email": d.email,
                        "role": "Borrower Director",
                    })
        except Entity.DoesNotExist:
            pass

    return signatories


# ---------------------------------------------------------------------------
# Document 2: Change of Trustee Set
# ---------------------------------------------------------------------------

def build_change_of_trustee_context(entity, financial_year, params):
    """
    Build the template context for a Change of Trustee document set.
    Generates 3 documents from one wizard:
      1. Deed of Change of Trustee
      2. Outgoing Trustee Director Resolution
      3. New Trustee Director Resolution (Minutes)

    Wizard params:
      - outgoing_trustee_entity_id: UUID of the outgoing trustee company
      - new_trustee_entity_id: UUID of the new trustee company
      - effective_date: date string
      - reason_for_change: str (optional)
      - appointment_clause: str (Eva pre-populated from deed text)
      - retirement_clause: str (Eva pre-populated from deed text)

    Two independent sole/multi-director conditional branches:
      - Outgoing trustee company (sole director vs multi-director)
      - New trustee company (sole director vs multi-director)
    """
    outgoing_id = params.get("outgoing_trustee_entity_id")
    new_id = params.get("new_trustee_entity_id")
    effective_date = params.get("effective_date", "")
    reason = params.get("reason_for_change", "")
    appointment_clause = params.get("appointment_clause", "")
    retirement_clause = params.get("retirement_clause", "")

    # Parse effective date
    if effective_date and isinstance(effective_date, str):
        try:
            from datetime import datetime
            effective_date_obj = datetime.strptime(effective_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            effective_date_obj = None
    elif isinstance(effective_date, date):
        effective_date_obj = effective_date
    else:
        effective_date_obj = None

    # Trust entity context
    trust_name = entity.entity_name
    trust_abn = entity.abn or ""
    deed_date = entity.deed_date
    deed_reference = entity.deed_reference or ""
    vesting_date = entity.vesting_date
    appointor = entity.appointor or ""

    # Outgoing trustee
    outgoing_entity = None
    outgoing_context = {}
    if outgoing_id:
        try:
            outgoing_entity = Entity.objects.get(pk=outgoing_id)
            outgoing_directors = _get_directors_for_entity(outgoing_entity)
            outgoing_context = {
                "name": outgoing_entity.entity_name,
                "acn": outgoing_entity.acn or "",
                "acn_formatted": _format_acn(outgoing_entity.acn) if outgoing_entity.acn else "",
                "abn": outgoing_entity.abn or "",
                "address": _get_entity_address(outgoing_entity),
                "is_sole_director": len(outgoing_directors) == 1,
                "directors": [_director_context(d) for d in outgoing_directors],
                "director_count": len(outgoing_directors),
                "execution": _build_execution_block(outgoing_entity),
            }
        except Entity.DoesNotExist:
            logger.warning("Outgoing trustee entity %s not found", outgoing_id)
    else:
        # Use the current trustee info from entity
        outgoing_context = {
            "name": entity.trustee_name or "",
            "acn": entity.trustee_acn or "",
            "acn_formatted": _format_acn(entity.trustee_acn) if entity.trustee_acn else "",
        }

    # New trustee
    new_entity = None
    new_context = {}
    if new_id:
        try:
            new_entity = Entity.objects.get(pk=new_id)
            new_directors = _get_directors_for_entity(new_entity)
            new_context = {
                "name": new_entity.entity_name,
                "acn": new_entity.acn or "",
                "acn_formatted": _format_acn(new_entity.acn) if new_entity.acn else "",
                "abn": new_entity.abn or "",
                "address": _get_entity_address(new_entity),
                "is_sole_director": len(new_directors) == 1,
                "directors": [_director_context(d) for d in new_directors],
                "director_count": len(new_directors),
                "execution": _build_execution_block(new_entity),
            }
        except Entity.DoesNotExist:
            logger.warning("New trustee entity %s not found", new_id)

    context = {
        # Document metadata
        "document_title": "DEED OF CHANGE OF TRUSTEE",
        "effective_date": _format_date(effective_date_obj),
        "effective_date_ordinal": _format_date_ordinal(effective_date_obj),
        "generation_date": _format_date(timezone.now().date()),

        # Trust details
        "trust_name": trust_name,
        "trust_abn": trust_abn,
        "trust_abn_formatted": _format_abn(trust_abn) if trust_abn else "",
        "deed_date": _format_date(deed_date),
        "deed_date_ordinal": _format_date_ordinal(deed_date),
        "deed_reference": deed_reference,
        "vesting_date": _format_date(vesting_date),
        "appointor": appointor,

        # Outgoing trustee
        "outgoing_trustee": outgoing_context,
        "outgoing_trustee_name": outgoing_context.get("name", ""),
        "outgoing_trustee_acn": outgoing_context.get("acn", ""),
        "outgoing_trustee_acn_formatted": outgoing_context.get("acn_formatted", ""),

        # New trustee
        "new_trustee": new_context,
        "new_trustee_name": new_context.get("name", ""),
        "new_trustee_acn": new_context.get("acn", ""),
        "new_trustee_acn_formatted": new_context.get("acn_formatted", ""),

        # Clause references (Eva pre-populated)
        "appointment_clause": appointment_clause,
        "retirement_clause": retirement_clause,
        "reason_for_change": reason,

        # Execution blocks (independent sole/multi-director branches)
        "outgoing_execution": outgoing_context.get("execution", {}),
        "new_execution": new_context.get("execution", {}),

        # Firm
        **FIRM_DETAILS,
        "disclaimer": STANDARD_DISCLAIMER,
    }

    return context


def get_change_of_trustee_signatories(entity, params):
    """
    Build FuseSign signatory list for Change of Trustee.
    Outgoing trustee directors + new trustee directors.
    """
    signatories = []
    outgoing_id = params.get("outgoing_trustee_entity_id")
    new_id = params.get("new_trustee_entity_id")

    if outgoing_id:
        try:
            outgoing_entity = Entity.objects.get(pk=outgoing_id)
            for d in _get_directors_for_entity(outgoing_entity):
                if d.email:
                    signatories.append({
                        "name": d.full_name,
                        "email": d.email,
                        "role": "Outgoing Trustee Director",
                    })
        except Entity.DoesNotExist:
            pass

    if new_id:
        try:
            new_entity = Entity.objects.get(pk=new_id)
            for d in _get_directors_for_entity(new_entity):
                if d.email:
                    signatories.append({
                        "name": d.full_name,
                        "email": d.email,
                        "role": "New Trustee Director",
                    })
        except Entity.DoesNotExist:
            pass

    return signatories


async def prefill_trustee_clauses(entity):
    """
    Use Eva (AI) to extract appointment and retirement clause numbers
    from the trust deed text stored in Governing Documents.
    Returns dict with 'appointment_clause' and 'retirement_clause'.
    Called before the wizard renders.
    """
    clauses = {"appointment_clause": "", "retirement_clause": ""}

    governing_doc = entity.governing_documents.filter(
        is_primary=True,
        status="active",
        extraction_status__in=["completed", "completed_with_warnings"],
    ).first()

    if not governing_doc or not governing_doc.extracted_text:
        return clauses

    try:
        from core.ai_service import call_ai
        prompt = (
            "You are reading a trust deed. Extract the following clause numbers:\n"
            "1. The clause that deals with the APPOINTMENT of a new trustee\n"
            "2. The clause that deals with the RETIREMENT or REMOVAL of a trustee\n\n"
            "Return ONLY a JSON object like: "
            '{"appointment_clause": "Clause 15.1", "retirement_clause": "Clause 15.2"}\n\n'
            "If you cannot find a clause, return an empty string for that field.\n\n"
            f"Trust Deed Text (first 5000 chars):\n{governing_doc.extracted_text[:5000]}"
        )
        result = call_ai(prompt, model="gpt-4.1-nano")
        if result:
            import json
            try:
                parsed = json.loads(result)
                clauses["appointment_clause"] = parsed.get("appointment_clause", "")
                clauses["retirement_clause"] = parsed.get("retirement_clause", "")
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse Eva clause extraction result")
    except Exception:
        logger.exception("Eva clause pre-population failed")

    return clauses


# ---------------------------------------------------------------------------
# Document 3: Fixed Unit Trust Deed + Ancillaries
# ---------------------------------------------------------------------------

def build_fixed_unit_trust_context(entity, financial_year, params):
    """
    Build the template context for a Fixed Unit Trust Deed + Ancillaries.
    Generates 5 documents from one wizard:
      1. Unit Trust Deed
      2. Director Resolution (Trustee)
      3. Unit Applications (one per unitholder — looped)
      4. Unit Certificates (one per unitholder — looped)
      5. Register of Unitholders

    Wizard params:
      - trust_name: str (pre-populated from entity)
      - trustee_entity_id: UUID of the trustee company
      - governing_state: str (e.g. "NSW", "VIC")
      - deed_date: date string
      - unit_class: str (e.g. "Ordinary")
      - unitholders: list of dicts, each with:
          - entity_id: UUID
          - entity_name: str (fallback)
          - units: int
          - is_trustee: bool (company acting as trustee of another trust)
    """
    trust_name = params.get("trust_name", entity.entity_name)
    trustee_entity_id = params.get("trustee_entity_id")
    governing_state = params.get("governing_state", "")
    deed_date_str = params.get("deed_date", "")
    unit_class = params.get("unit_class", "Ordinary")
    unitholders_raw = params.get("unitholders", [])

    # Parse deed date
    deed_date_obj = _parse_date(deed_date_str)

    # Trustee entity
    trustee_entity = None
    trustee_context = {}
    if trustee_entity_id:
        try:
            trustee_entity = Entity.objects.get(pk=trustee_entity_id)
            trustee_directors = _get_directors_for_entity(trustee_entity)
            trustee_context = {
                "name": trustee_entity.entity_name,
                "acn": trustee_entity.acn or "",
                "acn_formatted": _format_acn(trustee_entity.acn) if trustee_entity.acn else "",
                "abn": trustee_entity.abn or "",
                "address": _get_entity_address(trustee_entity),
                "is_sole_director": len(trustee_directors) == 1,
                "directors": [_director_context(d) for d in trustee_directors],
                "director_count": len(trustee_directors),
                "execution": _build_execution_block(trustee_entity),
            }
        except Entity.DoesNotExist:
            logger.warning("Trustee entity %s not found", trustee_entity_id)

    # Build unitholder list with unit number ranges
    unitholders = []
    running_unit_start = 1
    total_units = 0

    for idx, uh_raw in enumerate(unitholders_raw):
        uh_entity_id = uh_raw.get("entity_id")
        uh_name = uh_raw.get("entity_name", "")
        uh_units = int(uh_raw.get("units", 0))
        uh_is_trustee = uh_raw.get("is_trustee", False)

        # Look up entity if ID provided
        uh_entity = None
        uh_directors = []
        uh_description = uh_name
        if uh_entity_id:
            try:
                uh_entity = Entity.objects.get(pk=uh_entity_id)
                uh_name = uh_entity.entity_name
                uh_description = _build_entity_description(uh_entity)
                uh_directors = _get_directors_for_entity(uh_entity)
            except Entity.DoesNotExist:
                pass

        # Calculate unit number range
        unit_range_start = running_unit_start
        unit_range_end = running_unit_start + uh_units - 1 if uh_units > 0 else running_unit_start
        running_unit_start = unit_range_end + 1
        total_units += uh_units

        unitholders.append({
            "index": idx + 1,
            "name": uh_name,
            "description": uh_description,
            "entity_id": str(uh_entity_id) if uh_entity_id else "",
            "units": uh_units,
            "unit_range_start": unit_range_start,
            "unit_range_end": unit_range_end,
            "unit_range": f"{unit_range_start} to {unit_range_end}" if uh_units > 1 else str(unit_range_start),
            "is_trustee": uh_is_trustee,
            "is_company": uh_entity.entity_type == "company" if uh_entity else False,
            "is_trust": uh_entity.entity_type == "trust" if uh_entity else False,
            "acn": uh_entity.acn if uh_entity else "",
            "acn_formatted": _format_acn(uh_entity.acn) if uh_entity and uh_entity.acn else "",
            "abn": uh_entity.abn if uh_entity else "",
            "address": _get_entity_address(uh_entity) if uh_entity else "",
            "directors": [_director_context(d) for d in uh_directors],
            "is_sole_director": len(uh_directors) == 1,
            "execution": _build_execution_block(uh_entity) if uh_entity else {},
            "certificate_number": f"CERT-{idx + 1:03d}",
        })

    # NSW stamp duty warning
    show_nsw_warning = governing_state.upper() == "NSW" if governing_state else False

    context = {
        # Document metadata
        "document_title": "FIXED UNIT TRUST DEED",
        "deed_date": _format_date(deed_date_obj),
        "deed_date_ordinal": _format_date_ordinal(deed_date_obj),
        "generation_date": _format_date(timezone.now().date()),

        # Trust details
        "trust_name": trust_name,
        "trust_abn": entity.abn or "",
        "trust_abn_formatted": _format_abn(entity.abn) if entity.abn else "",
        "governing_state": governing_state,
        "show_nsw_warning": show_nsw_warning,
        "unit_class": unit_class,

        # Trustee
        "trustee": trustee_context,
        "trustee_name": trustee_context.get("name", ""),
        "trustee_acn": trustee_context.get("acn", ""),
        "trustee_acn_formatted": trustee_context.get("acn_formatted", ""),

        # Unitholders
        "unitholders": unitholders,
        "unitholder_count": len(unitholders),
        "total_units": total_units,

        # Firm
        **FIRM_DETAILS,
        "disclaimer": STANDARD_DISCLAIMER,
    }

    return context


def validate_fixed_unit_trust(entity, params):
    """
    Pre-flight validation for Fixed Unit Trust Deed generation.
    Returns list of error messages (empty = valid).
    """
    errors = []
    trustee_entity_id = params.get("trustee_entity_id")
    unitholders_raw = params.get("unitholders", [])

    # Trust name
    if not params.get("trust_name", "").strip() and not entity.entity_name:
        errors.append("Trust name is required.")

    # Trustee must have at least one active director
    if trustee_entity_id:
        try:
            trustee_entity = Entity.objects.get(pk=trustee_entity_id)
            directors = _get_directors_for_entity(trustee_entity)
            if not directors:
                errors.append(
                    f"Trustee company '{trustee_entity.entity_name}' has no active directors. "
                    "Add at least one director before generating."
                )
        except Entity.DoesNotExist:
            errors.append("Selected trustee entity not found.")
    else:
        errors.append("A trustee company must be selected.")

    # At least one unitholder
    if not unitholders_raw:
        errors.append("At least one unitholder must be added.")

    # Validate each unitholder
    for idx, uh in enumerate(unitholders_raw):
        uh_units = int(uh.get("units", 0))
        if uh_units <= 0:
            errors.append(f"Unitholder {idx + 1}: units must be greater than zero.")

        uh_entity_id = uh.get("entity_id")
        if uh_entity_id:
            try:
                uh_entity = Entity.objects.get(pk=uh_entity_id)
                if uh_entity.entity_type in ("company", "trust"):
                    uh_directors = _get_directors_for_entity(uh_entity)
                    if not uh_directors:
                        errors.append(
                            f"Unitholder '{uh_entity.entity_name}' has no active directors. "
                            "Add at least one director before generating."
                        )
            except Entity.DoesNotExist:
                errors.append(f"Unitholder {idx + 1}: selected entity not found.")

    return errors


def get_fixed_unit_trust_signatories(entity, params):
    """
    Build FuseSign signatory list for Fixed Unit Trust.
    Trustee directors + each unitholder's directors.
    """
    signatories = []
    trustee_entity_id = params.get("trustee_entity_id")
    unitholders_raw = params.get("unitholders", [])

    # Trustee directors
    if trustee_entity_id:
        try:
            trustee_entity = Entity.objects.get(pk=trustee_entity_id)
            for d in _get_directors_for_entity(trustee_entity):
                if d.email:
                    signatories.append({
                        "name": d.full_name,
                        "email": d.email,
                        "role": "Trustee Director",
                    })
        except Entity.DoesNotExist:
            pass

    # Unitholder directors
    for uh in unitholders_raw:
        uh_entity_id = uh.get("entity_id")
        if uh_entity_id:
            try:
                uh_entity = Entity.objects.get(pk=uh_entity_id)
                for d in _get_directors_for_entity(uh_entity):
                    if d.email:
                        # Avoid duplicates (trustee director may also be unitholder director)
                        if not any(s["email"] == d.email for s in signatories):
                            signatories.append({
                                "name": d.full_name,
                                "email": d.email,
                                "role": f"Unitholder Director ({uh_entity.entity_name})",
                            })
            except Entity.DoesNotExist:
                pass

    return signatories


# ---------------------------------------------------------------------------
# Document 4: Unit Transfer Package
# ---------------------------------------------------------------------------

def build_unit_transfer_context(entity, financial_year, params):
    """
    Build the template context for a Unit Transfer Package.
    Generates 7 documents from one wizard, loop-heavy, scales with transfer count:
      1. Cover Page
      2. Director Resolution (Trustee)
      3. Unit Transfer Instrument(s) — one per transfer
      4. Transferor Acknowledgements
      5. Transferee Acknowledgements
      6. Updated Unit Certificate(s)
      7. Updated Register of Unitholders

    Wizard params:
      - trust_entity_id: UUID (the trust entity — pre-populates trust name, trustee, unitholders)
      - transfers: list of dicts, each with:
          - transferor_entity_id: UUID (current unitholder)
          - transferee_entity_id: UUID or None
          - transferee_name: str (if not an existing entity)
          - units: int
          - consideration: decimal
          - transfer_date: date string
      - unit_class: str (pre-populated from trust entity)
    """
    transfers_raw = params.get("transfers", [])
    unit_class = params.get("unit_class", "Ordinary")

    # Trust details
    trust_name = entity.entity_name
    trustee_name = entity.trustee_name or ""
    trustee_acn = entity.trustee_acn or ""

    # Get trustee entity for execution block
    trustee_entity = _find_trustee_entity(entity)
    trustee_context = {}
    if trustee_entity:
        trustee_directors = _get_directors_for_entity(trustee_entity)
        trustee_context = {
            "name": trustee_entity.entity_name,
            "acn": trustee_entity.acn or "",
            "acn_formatted": _format_acn(trustee_entity.acn) if trustee_entity.acn else "",
            "is_sole_director": len(trustee_directors) == 1,
            "directors": [_director_context(d) for d in trustee_directors],
            "execution": _build_execution_block(trustee_entity),
        }

    # Build current unitholder positions from EntityOfficer (beneficiary/unitholder role)
    current_positions = _get_current_unitholder_positions(entity)

    # Build transfer objects
    transfers = []
    for idx, t_raw in enumerate(transfers_raw):
        transferor_id = t_raw.get("transferor_entity_id")
        transferee_id = t_raw.get("transferee_entity_id")
        transferee_name = t_raw.get("transferee_name", "")
        units = int(t_raw.get("units", 0))
        consideration = t_raw.get("consideration", "0")
        transfer_date_str = t_raw.get("transfer_date", "")

        transfer_date_obj = _parse_date(transfer_date_str)

        # Transferor
        transferor_context = _build_transfer_party_context(transferor_id, "")
        # Transferee
        transferee_context = _build_transfer_party_context(transferee_id, transferee_name)

        transfers.append({
            "index": idx + 1,
            "transferor": transferor_context,
            "transferor_full_description": transferor_context.get("description", ""),
            "transferor_name": transferor_context.get("name", ""),
            "transferor_is_company": transferor_context.get("is_company", False),
            "transferor_is_trust": transferor_context.get("is_trust", False),
            "transferor_directors": transferor_context.get("directors", []),
            "transferor_execution": transferor_context.get("execution", {}),
            "transferee": transferee_context,
            "transferee_full_description": transferee_context.get("description", ""),
            "transferee_name": transferee_context.get("name", ""),
            "transferee_is_company": transferee_context.get("is_company", False),
            "transferee_is_trust": transferee_context.get("is_trust", False),
            "transferee_directors": transferee_context.get("directors", []),
            "transferee_execution": transferee_context.get("execution", {}),
            "units_transferred": units,
            "consideration_amount": _format_currency(consideration),
            "consideration_raw": str(consideration),
            "transfer_date": _format_date(transfer_date_obj),
            "transfer_date_ordinal": _format_date_ordinal(transfer_date_obj),
        })

    # Calculate updated register
    updated_positions = _calculate_updated_register(current_positions, transfers_raw)

    # Build updated unitholder list for certificates and register
    updated_unitholders = []
    cert_number = 1
    for holder_name, position in updated_positions.items():
        if position["units"] > 0:
            updated_unitholders.append({
                "name": holder_name,
                "description": position.get("description", holder_name),
                "units": position["units"],
                "unit_range": position.get("unit_range", ""),
                "certificate_number": f"CERT-{cert_number:03d}",
                "entity_id": position.get("entity_id", ""),
                "is_company": position.get("is_company", False),
                "is_trust": position.get("is_trust", False),
                "directors": position.get("directors", []),
                "execution": position.get("execution", {}),
            })
            cert_number += 1

    context = {
        # Document metadata
        "document_title": "UNIT TRANSFER PACKAGE",
        "generation_date": _format_date(timezone.now().date()),

        # Trust details
        "trust_name": trust_name,
        "trust_abn": entity.abn or "",
        "trust_abn_formatted": _format_abn(entity.abn) if entity.abn else "",
        "trustee_name": trustee_name,
        "trustee_acn": trustee_acn,
        "trustee_acn_formatted": _format_acn(trustee_acn) if trustee_acn else "",
        "unit_class": unit_class,

        # Trustee execution
        "trustee": trustee_context,

        # Transfers
        "transfers": transfers,
        "transfer_count": len(transfers),

        # Updated register
        "updated_unitholders": updated_unitholders,
        "updated_unitholder_count": len(updated_unitholders),
        "total_units_after_transfer": sum(uh["units"] for uh in updated_unitholders),

        # Firm
        **FIRM_DETAILS,
        "disclaimer": STANDARD_DISCLAIMER,
    }

    return context


def _find_trustee_entity(trust_entity):
    """
    Find the trustee company entity for a trust.
    Uses EntityRelationship or falls back to trustee_name matching.
    """
    # Try EntityRelationship first
    rel = EntityRelationship.objects.filter(
        from_entity=trust_entity,
        relationship_type="trustee_of",
    ).select_related("to_entity").first()
    if rel:
        return rel.to_entity

    # Reverse lookup
    rel = EntityRelationship.objects.filter(
        to_entity=trust_entity,
        relationship_type="trustee_of",
    ).select_related("from_entity").first()
    if rel:
        return rel.from_entity

    # Fallback: match by trustee_name
    if trust_entity.trustee_name:
        match = Entity.objects.filter(
            entity_name__iexact=trust_entity.trustee_name,
            entity_type="company",
        ).first()
        if match:
            return match

    return None


def _get_current_unitholder_positions(entity):
    """
    Get current unitholder positions from EntityOfficer records.
    Returns dict: {entity_name: {units: int, entity_id: str, ...}}
    """
    positions = {}
    officers = EntityOfficer.objects.filter(
        entity=entity,
        date_ceased__isnull=True,
    ).order_by("display_order", "full_name")

    for officer in officers:
        # Check if this officer is a beneficiary/unitholder
        is_unitholder = (
            officer.role == "beneficiary"
            or (officer.roles and "beneficiary" in officer.roles)
        )
        if is_unitholder and officer.shares_held:
            positions[officer.full_name] = {
                "units": officer.shares_held,
                "entity_id": "",
                "description": officer.full_name,
                "is_company": False,
                "is_trust": False,
                "directors": [],
                "execution": {},
            }

    return positions


def _build_transfer_party_context(entity_id, fallback_name):
    """Build context for a transfer party (transferor or transferee)."""
    if entity_id:
        try:
            party_entity = Entity.objects.get(pk=entity_id)
            directors = _get_directors_for_entity(party_entity)
            return {
                "name": party_entity.entity_name,
                "description": _build_entity_description(party_entity),
                "entity_id": str(party_entity.pk),
                "acn": party_entity.acn or "",
                "acn_formatted": _format_acn(party_entity.acn) if party_entity.acn else "",
                "abn": party_entity.abn or "",
                "address": _get_entity_address(party_entity),
                "is_company": party_entity.entity_type == "company",
                "is_trust": party_entity.entity_type == "trust",
                "is_individual": party_entity.entity_type not in ("company", "trust"),
                "directors": [_director_context(d) for d in directors],
                "is_sole_director": len(directors) == 1,
                "execution": _build_execution_block(party_entity),
            }
        except Entity.DoesNotExist:
            pass

    return {
        "name": fallback_name,
        "description": fallback_name,
        "entity_id": "",
        "is_company": False,
        "is_trust": False,
        "is_individual": True,
        "directors": [],
        "execution": {},
    }


def _calculate_updated_register(current_positions, transfers_raw):
    """
    Apply transfers to current positions and return updated register.
    Start from current unitholder positions, subtract from transferors,
    add to transferees.
    """
    import copy
    positions = copy.deepcopy(current_positions)

    for t in transfers_raw:
        units = int(t.get("units", 0))
        transferor_id = t.get("transferor_entity_id")
        transferee_id = t.get("transferee_entity_id")
        transferee_name = t.get("transferee_name", "")

        # Find transferor name
        transferor_name = ""
        if transferor_id:
            try:
                transferor_entity = Entity.objects.get(pk=transferor_id)
                transferor_name = transferor_entity.entity_name
            except Entity.DoesNotExist:
                pass

        # Subtract from transferor
        if transferor_name and transferor_name in positions:
            positions[transferor_name]["units"] = max(
                0, positions[transferor_name]["units"] - units
            )

        # Add to transferee
        if transferee_id:
            try:
                transferee_entity = Entity.objects.get(pk=transferee_id)
                tee_name = transferee_entity.entity_name
                if tee_name not in positions:
                    directors = _get_directors_for_entity(transferee_entity)
                    positions[tee_name] = {
                        "units": 0,
                        "entity_id": str(transferee_entity.pk),
                        "description": _build_entity_description(transferee_entity),
                        "is_company": transferee_entity.entity_type == "company",
                        "is_trust": transferee_entity.entity_type == "trust",
                        "directors": [_director_context(d) for d in directors],
                        "execution": _build_execution_block(transferee_entity),
                    }
                positions[tee_name]["units"] += units
            except Entity.DoesNotExist:
                pass
        elif transferee_name:
            if transferee_name not in positions:
                positions[transferee_name] = {
                    "units": 0,
                    "entity_id": "",
                    "description": transferee_name,
                    "is_company": False,
                    "is_trust": False,
                    "directors": [],
                    "execution": {},
                }
            positions[transferee_name]["units"] += units

    return positions


def get_unit_transfer_signatories(entity, params):
    """
    Build FuseSign signatory list for Unit Transfer.
    Trustee directors + transferor signatories + transferee signatories.
    """
    signatories = []
    transfers_raw = params.get("transfers", [])

    # Trustee directors
    trustee_entity = _find_trustee_entity(entity)
    if trustee_entity:
        for d in _get_directors_for_entity(trustee_entity):
            if d.email:
                signatories.append({
                    "name": d.full_name,
                    "email": d.email,
                    "role": "Trustee Director",
                })

    # Transfer party signatories
    seen_emails = {s["email"] for s in signatories}
    for t in transfers_raw:
        for party_key, role_prefix in [
            ("transferor_entity_id", "Transferor"),
            ("transferee_entity_id", "Transferee"),
        ]:
            party_id = t.get(party_key)
            if party_id:
                try:
                    party_entity = Entity.objects.get(pk=party_id)
                    for d in _get_directors_for_entity(party_entity):
                        if d.email and d.email not in seen_emails:
                            signatories.append({
                                "name": d.full_name,
                                "email": d.email,
                                "role": f"{role_prefix} Director ({party_entity.entity_name})",
                            })
                            seen_emails.add(d.email)
                except Entity.DoesNotExist:
                    pass

    return signatories


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _parse_date(date_str):
    """Parse a date string in YYYY-MM-DD format. Returns date object or None."""
    if not date_str:
        return None
    if isinstance(date_str, date):
        return date_str
    try:
        from datetime import datetime
        return datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Governing Documents Auto-Save Descriptions
# ---------------------------------------------------------------------------

GOVERNING_DOC_DESCRIPTIONS = {
    "div7a_loan_agreement": lambda params: (
        f"Division 7A Loan Agreement — {params.get('borrower_name', 'Unknown')}"
    ),
    "trust_deed_change_trustee": lambda params: (
        f"Deed of Change of Trustee — {params.get('effective_date', '')}"
    ),
    "unit_trust_deed": lambda params: "Fixed Unit Trust Deed",
    "unit_transfer": lambda params: (
        f"Unit Transfer — {params.get('transfer_date', timezone.now().strftime('%d %B %Y'))}"
    ),
}

GOVERNING_DOC_TYPES = {
    "div7a_loan_agreement": "amendment",
    "trust_deed_change_trustee": "amendment",
    "unit_trust_deed": "trust_deed",
    "unit_transfer": "amendment",
}

GOVERNING_DOC_IS_PRIMARY = {
    "div7a_loan_agreement": False,
    "trust_deed_change_trustee": False,
    "unit_trust_deed": True,  # The deed auto-saves as primary governing document
    "unit_transfer": False,
}
