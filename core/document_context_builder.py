"""
StatementHub — DocumentContextBuilder
======================================
Central context assembly engine for all generated documents.

Every document type (financial statements, compilation reports, engagement
letters, legal documents, etc.) is rendered from a Jinja2-templated .docx
file via docxtpl.  This module builds the context dictionary that is passed
to the template renderer.

Architecture
------------
- DocumentContextBuilder is instantiated per-document-generation request.
- Callers set ``builder.tpl`` to the loaded DocxTemplate before calling
  ``builder.build(document_type)`` so that InlineImage objects can be
  constructed for logo embedding in Word documents.
- ``build()`` assembles context by calling the relevant private methods,
  runs ``_validate_context()``, logs the audit trail to the LegalDocument
  record, and returns the final dict.

Usage
-----
    from core.document_context_builder import DocumentContextBuilder
    from docxtpl import DocxTemplate

    builder = DocumentContextBuilder(
        entity=entity,
        financial_year=fy,          # Optional — None for standalone docs
        legal_document=legal_doc,   # Optional — for audit trail logging
        wizard_data=wizard_data,    # Optional — dict of wizard inputs
    )
    tpl = DocxTemplate(template_path)
    builder.tpl = tpl               # Inject before build() so InlineImage works
    context = builder.build(document_type)
    tpl.render(context, jinja_env=get_jinja_env())
    tpl.save(output_path)
    builder.cleanup()               # Remove any temp logo files

Spec reference: StatementHub DocumentContextBuilder Spec v1.0
"""

import json
import logging
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, DivisionByZero, InvalidOperation

from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DISCLAIMER_TEXT = (
    "This document has been prepared for the exclusive use of the addressee. "
    "The information contained herein is confidential. "
    "No liability is accepted for any loss or damage arising from reliance "
    "on this document by any person other than the addressee."
)

VALID_DOCUMENT_TYPES = {
    "financial_statements",
    "compilation_report",
    "directors_declaration",
    "directors_report",
    "solvency_resolution",
    "dividend_statement",
    "dividend_declaration_minutes",
    "distribution_minutes",
    "beneficiary_statement",
    "partner_statement",
    "partnership_tax_summary",
    "div7a_loan_agreement",
    "management_representation_letter",
    "engagement_letter",
    "client_cover_letter",
    "eva_client_summary",
}

# APES 315 paragraph 14(f) — exact wording, do not paraphrase
APES_315_NO_ASSURANCE = (
    "We have not audited or reviewed the accompanying financial report and "
    "accordingly express no opinion or conclusion thereon."
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ContextValidationError(Exception):
    """
    Raised when the context builder detects a missing or invalid value that
    would produce a non-compliant or unprofessional document.

    Always carries a plain-English ``message`` suitable for surfacing to the
    accountant in the UI.
    """

    def __init__(self, message, document_type=None, entity_id=None, missing_fields=None, resolution_hints=None):
        self.message = message
        self.document_type = document_type
        self.entity_id = entity_id
        self.missing_fields = missing_fields or []
        self.resolution_hints = resolution_hints or {}
        super().__init__(message)


# ---------------------------------------------------------------------------
# Jinja2 filter helpers
# ---------------------------------------------------------------------------

def format_currency(value):
    """
    Format a Decimal or numeric value as Australian currency.
    Positive: '$1,234,567'  Negative: '($1,234,567)'  Zero: '$-'
    """
    if value is None:
        return "$-"
    try:
        d = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        return "$-"
    if d == 0:
        return "$-"
    abs_str = f"${abs(d):,.0f}"
    return f"({abs_str})" if d < 0 else abs_str


def format_currency_abs(value):
    """Format absolute value as currency — used when negative balances display positive."""
    if value is None:
        return "$-"
    try:
        d = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        return "$-"
    if d == 0:
        return "$-"
    return f"${abs(d):,.0f}"


def format_percentage(value, decimal_places=1):
    """Format a Decimal as a percentage: '12.5%'"""
    if value is None:
        return "—"
    try:
        fmt = f"{{:.{decimal_places}f}}%"
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return "—"


def format_date_long(value):
    """Format a date as 'd MMMM YYYY': '30 June 2025'"""
    if not value:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%-d %B %Y")
    return str(value)


def format_date_short(value):
    """Format a date as 'DD/MM/YYYY'"""
    if not value:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")
    return str(value)


def upper_first(value):
    """Capitalise first letter only."""
    if not value:
        return ""
    return str(value)[0].upper() + str(value)[1:]


def format_yesno(value):
    """Convert Boolean to 'Yes' / 'No'."""
    return "Yes" if value else "No"


def mask_tfn(value):
    """Format TFN as 'XXX XXX 789' — last 3 digits visible only."""
    if not value:
        return ""
    digits = "".join(c for c in str(value) if c.isdigit())
    if len(digits) < 3:
        return "XXX XXX XXX"
    return f"XXX XXX {digits[-3:]}"


def format_abn(value):
    """Group ABN as '12 345 678 901'."""
    if not value:
        return ""
    digits = "".join(c for c in str(value) if c.isdigit())
    if len(digits) != 11:
        return str(value)
    return f"{digits[0:2]} {digits[2:5]} {digits[5:8]} {digits[8:11]}"


def format_acn(value):
    """Group ACN as '123 456 789'."""
    if not value:
        return ""
    digits = "".join(c for c in str(value) if c.isdigit())
    if len(digits) != 9:
        return str(value)
    return f"{digits[0:3]} {digits[3:6]} {digits[6:9]}"


def safe_logo(value):
    """
    Guard filter: prevents practice_logo_url (a URL string) being used in a
    DOCX template where practice_logo (an InlineImage) is expected.
    Usage: {{ practice_logo | safe_logo }}
    """
    if isinstance(value, str) and (value.startswith("http") or value.startswith("/")):
        logger.warning(
            "practice_logo_url was used in a DOCX template context. "
            "Use {{ practice_logo }} for Word documents. "
            "practice_logo_url is for HTML and email templates only."
        )
        return ""
    return value


def get_jinja_env():
    """
    Return a Jinja2 Environment with all StatementHub custom filters registered.
    Pass to docxtpl: doc.render(context, jinja_env=get_jinja_env())
    """
    from jinja2 import Environment
    env = Environment()
    # Primary names (short)
    env.filters["currency"] = format_currency
    env.filters["currency_abs"] = format_currency_abs
    env.filters["percentage"] = format_percentage
    env.filters["date_long"] = format_date_long
    env.filters["date_short"] = format_date_short
    env.filters["upper_first"] = upper_first
    env.filters["yesno"] = format_yesno
    env.filters["masked_tfn"] = mask_tfn
    env.filters["abn_format"] = format_abn
    env.filters["acn_format"] = format_acn
    env.filters["safe_logo"] = safe_logo
    # Aliases with format_* prefix (spec-compliant names for templates)
    env.filters["format_currency"] = format_currency
    env.filters["format_percentage"] = format_percentage
    env.filters["format_date_long"] = format_date_long
    env.filters["format_date_short"] = format_date_short
    env.filters["format_abn"] = format_abn
    env.filters["format_acn"] = format_acn
    env.filters["mask_tfn"] = mask_tfn
    env.filters["format_yesno"] = format_yesno
    return env


# ---------------------------------------------------------------------------
# Resolution hints for validation errors
# ---------------------------------------------------------------------------

_RESOLUTION_HINTS = {
    "entity_name": "Go to Entity → Edit and enter the entity's legal name.",
    "entity_abn": "Go to Entity → Edit and enter the ABN.",
    "practice_name": "Go to Administration → Firm Settings and enter the firm name.",
    "practice_tax_agent_number": "Go to Administration → Firm Settings and enter the Tax Agent Number.",
    "report_framework": "Go to Entity → Edit and select a Reporting Framework.",
    "directors": "Go to Entity → People and add at least one director.",
    "solvency_confirmed": "Resolve the going concern or solvency Eva finding before generating this document.",
    "dividend_event": "Create a Dividend Event for this financial year before generating dividend documents.",
    "resolution_date": "Set the distribution resolution date to on or before the financial year end (30 June).",
    "is_balanced": "Ensure all distribution/partnership allocations sum to 100% before generating.",
    "borrower": "Select a borrower in the Div 7A loan agreement wizard.",
    "benchmark_rate": "Go to Administration → Reference Data and add the current Division 7A benchmark rate.",
    "services_engaged": "Select at least one service in the engagement letter wizard.",
}


def get_resolution_hint(missing_fields):
    """Return a list of resolution hint strings for the given missing field names."""
    return [_RESOLUTION_HINTS[f] for f in missing_fields if f in _RESOLUTION_HINTS]


# ---------------------------------------------------------------------------
# DocumentContextBuilder
# ---------------------------------------------------------------------------

class DocumentContextBuilder:
    """
    Assembles the complete Jinja2 context dictionary for any StatementHub
    document type.

    Instantiate with the entity and optional financial year, then call
    ``build(document_type)`` to get the context dict.

    The ``tpl`` attribute must be set to the loaded DocxTemplate instance
    before ``build()`` is called so that InlineImage objects for the logo
    can be constructed.  If ``tpl`` is not set, the logo will be omitted
    from the context (document renders without logo — not a fatal error).
    """

    def __init__(self, entity, financial_year=None, legal_document=None, wizard_data=None):
        """
        Parameters
        ----------
        entity : core.models.Entity
        financial_year : core.models.FinancialYear | None
        legal_document : core.models.LegalDocument | None
            If provided, the built context is stored in
            legal_document.parameters for audit trail purposes.
        wizard_data : dict | None
            Wizard inputs (dates, selections, free-text fields) that
            supplement the database data.
        """
        self.entity = entity
        self.financial_year = financial_year
        self.legal_document = legal_document
        self.wizard_data = wizard_data or {}
        self.tpl = None          # Set by caller before build()
        self._temp_files = []    # Temp files created for logo; cleaned up by cleanup()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, document_type):
        """
        Build and return the complete context dictionary for the given
        document type.  Runs validation before returning.

        Parameters
        ----------
        document_type : str
            One of VALID_DOCUMENT_TYPES.

        Returns
        -------
        dict

        Raises
        ------
        ContextValidationError
            If required fields are missing or business rules are violated.
        """
        ctx = {}
        ctx.update(self._base_context())
        ctx.update(self._practice_branding_context())

        ctx.update(self._entity_people_context())
        if self.financial_year:
            ctx.update(self._financial_year_context())
            ctx.update(self._financial_data_context())
            ctx.update(self._computed_flags(ctx))

        # Document-type-specific context
        doc_ctx_method = {
            "financial_statements": self._context_financial_statements,
            "compilation_report": self._context_compilation_report,
            "directors_declaration": self._context_directors_declaration,
            "directors_report": self._context_directors_report,
            "solvency_resolution": self._context_solvency_resolution,
            "dividend_statement": self._context_dividend_statement,
            "dividend_declaration_minutes": self._context_dividend_declaration_minutes,
            "distribution_minutes": self._context_distribution_minutes,
            "beneficiary_statement": self._context_beneficiary_statement,
            "partner_statement": self._context_partner_statement,
            "partnership_tax_summary": self._context_partnership_tax_summary,
            "div7a_loan_agreement": self._context_div7a_loan_agreement,
            "management_representation_letter": self._context_management_representation_letter,
            "engagement_letter": self._context_engagement_letter,
            "client_cover_letter": self._context_client_cover_letter,
            "eva_client_summary": self._context_eva_client_summary,
        }.get(document_type)

        if doc_ctx_method:
            ctx.update(doc_ctx_method(ctx))

        # Universal metadata
        ctx["document_type"] = document_type
        ctx["document_generated_date"] = format_date_long(date.today())
        ctx["document_generated_date_short"] = format_date_short(date.today())

        self._validate_context(ctx, document_type)
        self._write_audit_trail(ctx)
        return ctx

    def cleanup(self):
        """Remove any temporary files created during logo resolution."""
        for tmp_path in self._temp_files:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        self._temp_files = []

    # ------------------------------------------------------------------
    # Part 2 — Base Entity Context
    # ------------------------------------------------------------------

    def _base_context(self):
        """
        Universal entity variables present in every document context.
        Spec Part 2.
        """
        e = self.entity
        entity_type = e.entity_type  # 'company' | 'trust' | 'partnership' | 'sole_trader'

        # Registered address — built from address fields
        addr_parts = [
            p for p in [
                e.address_line_1,
                e.address_line_2,
                e.suburb,
                e.state,
                e.postcode,
            ] if p
        ]
        registered_address = ", ".join(addr_parts)
        postal_address = registered_address  # Same unless overridden

        # Reporting framework
        rf_map = {
            "GPFR_tier1": "GPFR",
            "GPFR_tier2": "GPFR",
            "SPFR": "SPFS",
        }
        report_framework = rf_map.get(e.reporting_framework, "SPFS")

        # Incorporation date
        incorporation_date = ""
        if e.registration_date:
            incorporation_date = format_date_long(e.registration_date)

        return {
            "entity_name": e.entity_name,
            "entity_type": entity_type,
            "entity_abn": format_abn(e.abn) if e.abn else "",
            "entity_acn": format_acn(e.acn) if e.acn else "",
            "entity_tfn_masked": mask_tfn(e.tfn) if getattr(e, "tfn", None) else "",
            "entity_registered_address": registered_address,
            "entity_postal_address": postal_address,
            "entity_incorporation_date": incorporation_date,
            "entity_financial_year_end": e.financial_year_end,
            "entity_trading_as": getattr(e, "trading_as", ""),
            "entity_industry": e.get_industry_display() if hasattr(e, "get_industry_display") else "",
            # Entity type booleans
            "is_company": entity_type == "company",
            "is_trust": entity_type == "trust",
            "is_partnership": entity_type == "partnership",
            "is_sole_trader": entity_type == "sole_trader",
            "is_smsf": entity_type == "smsf",
            # Company-specific
            "entity_acn_abn": (
                f"ACN {format_acn(e.acn)}" if e.acn else
                (f"ABN {format_abn(e.abn)}" if e.abn else "")
            ),
            "is_large_proprietary": getattr(e, "is_large_proprietary", False),
            "is_small_business_entity": getattr(e, "is_small_business_entity", None),
            "is_base_rate_entity": getattr(e, "is_base_rate_entity", None),
            "total_shares_on_issue": getattr(e, "total_shares_on_issue", None) or 0,
            # Trust-specific
            "trust_name": e.entity_name if entity_type == "trust" else "",
            "trustee_name": getattr(e, "trustee_name", ""),
            "trustee_acn": format_acn(e.trustee_acn) if getattr(e, "trustee_acn", None) else "",
            "trust_vesting_date": format_date_long(e.vesting_date) if getattr(e, "vesting_date", None) else "",
            "appointor_name": getattr(e, "appointor", ""),
            "trust_deed_date": format_date_long(e.deed_date) if getattr(e, "deed_date", None) else "",
            # Reporting framework
            "report_framework": report_framework,
            "is_reporting_entity": e.reporting_framework in ("GPFR_tier1",),
            "reporting_framework_raw": e.reporting_framework,
            # Presentation
            "rounding_basis": "The nearest dollar",
            "accounting_basis": "Accrual",
            "functional_currency": "Australian dollars",
            "show_cents": getattr(e, "show_cents", False),
        }

    # ------------------------------------------------------------------
    # Part 3 — Practice Branding Context
    # ------------------------------------------------------------------

    def _practice_branding_context(self):
        """
        Firm identity and branding variables.  Spec Part 3.
        """
        from core.models import FirmSettings
        firm = FirmSettings.get()

        # Build address string
        addr_parts = [p for p in [firm.firm_address_1, firm.firm_address_2] if p]
        address = ", ".join(addr_parts) if addr_parts else ""

        # Extract city from address_2 (first component before comma)
        signing_city = ""
        if firm.firm_address_2:
            signing_city = firm.firm_address_2.split(",")[0].strip()

        return {
            "practice_name": firm.firm_name,
            "practice_legal_name": firm.firm_legal_name or firm.firm_name,
            "practice_abn": format_abn(firm.firm_abn) if firm.firm_abn else "",
            "practice_address_1": firm.firm_address_1 or "",
            "practice_address_2": firm.firm_address_2 or "",
            "practice_registered_address": address,
            "practice_phone": firm.firm_phone or "",
            "practice_email": firm.firm_email or "",
            "practice_website": firm.firm_website or "",
            "practice_compilation_report_name": firm.compilation_report_name or firm.firm_name,
            "practice_legal_disclaimer": firm.document_disclaimer or DEFAULT_DISCLAIMER_TEXT,
            "practice_tax_agent_number": firm.tax_agent_number or "",
            "practice_bas_agent_number": firm.bas_agent_number or "",
            "practice_asic_agent_number": firm.asic_agent_number or "",
            "practice_signatory_name": firm.signatory_name or "",
            "practice_signatory_designation": firm.signatory_designation or "",
            "practice_professional_body": firm.professional_body or "CPA Australia",
            "practice_membership_number": firm.membership_number or "",
            "practice_independence_maintained": firm.practice_independence_maintained,
            "practice_logo_url": firm.logo.url if firm.logo else "",
            "practice_logo_width_cm": 4.0,
            "practice_logo": self._resolve_logo_for_docx(firm),
            # Signing city for compilation reports
            "signing_city": signing_city,
        }

    def _resolve_logo_for_docx(self, firm):
        """
        Resolve the firm logo as a docxtpl InlineImage for Word documents.

        Failure matrix (spec §3.7):
        - No logo uploaded → empty string, no log
        - Logo file missing from disk → empty string, WARNING log
        - Storage backend is object storage (no .path) → try HTTP download
        - Object storage download fails → empty string, ERROR log
        - self.tpl not set → empty string, no log
        - Any other exception → empty string, ERROR log
        """
        if not firm.logo:
            return ""

        if not self.tpl:
            return ""

        try:
            from docxtpl import InlineImage
            from docx.shared import Cm

            # Try local filesystem path first
            try:
                logo_path = firm.logo.path
                if not os.path.exists(logo_path):
                    logger.warning(
                        "FirmSettings logo file missing from disk at %s. "
                        "Document will render without logo. Re-upload in Firm Settings.",
                        logo_path,
                    )
                    return ""
                return InlineImage(self.tpl, logo_path, width=Cm(4.0))
            except NotImplementedError:
                # Object storage backend — fall through to HTTP download
                return self._resolve_logo_from_storage(firm)

        except Exception as exc:
            logger.error(
                "Unexpected error resolving firm logo for DOCX: %s. "
                "Document will render without logo.",
                exc,
            )
            return ""

    def _resolve_logo_from_storage(self, firm):
        """
        Download logo from object storage (S3, Digital Ocean Spaces) to a
        temp file and return an InlineImage.  Temp file is tracked in
        self._temp_files for cleanup after rendering.
        """
        import requests
        from docxtpl import InlineImage
        from docx.shared import Cm

        try:
            url = firm.logo.url
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            suffix = os.path.splitext(firm.logo.name)[1] or ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
            self._temp_files.append(tmp_path)
            return InlineImage(self.tpl, tmp_path, width=Cm(4.0))
        except Exception as exc:
            logger.error(
                "Logo download from object storage failed: %s. "
                "Check storage credentials and bucket permissions. "
                "Document will render without logo.",
                exc,
            )
            return ""

    # ------------------------------------------------------------------
    # Part 4 — Financial Year Context
    # ------------------------------------------------------------------

    def _financial_year_context(self):
        """Financial year identification and date variables. Spec Part 4.1."""
        fy = self.financial_year
        year_int = fy.end_date.year

        # Prior year
        has_prior_year = fy.prior_year is not None
        prior_year_label = str(fy.prior_year.end_date.year) if has_prior_year else ""

        # Finalised by
        finalised_by = ""
        if fy.finalised_at and fy.reviewed_by:
            try:
                finalised_by = fy.reviewed_by.get_full_name() or fy.reviewed_by.email
            except Exception:
                pass

        # Prior year end/start dates
        fy_prior_end_date = format_date_long(fy.prior_year.end_date) if has_prior_year else ""
        fy_prior_start_date = format_date_long(fy.prior_year.start_date) if has_prior_year else ""
        today_str = format_date_long(date.today())

        return {
            "fy_year": str(year_int),                             # always string for template consistency
            "fy_year_int": year_int,                              # int version for arithmetic
            "fy_year_label": fy.year_label,
            "fy_label": fy.year_label,                           # alias
            "fy_start_date": format_date_long(fy.start_date),
            "fy_start_formatted": format_date_long(fy.start_date),  # alias for templates
            "fy_end_date": format_date_long(fy.end_date),
            "fy_end_date_short": format_date_short(fy.end_date),
            "fy_end_date_year": str(year_int),
            "fy_end_formatted": format_date_long(fy.end_date),   # alias for templates
            "fy_period_label": f"For the year ended {format_date_long(fy.end_date)}",
            "fy_prior_end_date": fy_prior_end_date,
            "fy_prior_start_date": fy_prior_start_date,
            "fy_status": fy.status,
            "fy_is_finalised": fy.status == "finalised",
            "fy_finalised_date": format_date_long(fy.finalised_at) if fy.finalised_at else "",
            "fy_finalised_by": finalised_by,
            "has_prior_year": has_prior_year,
            "prior_year_label": prior_year_label,
            "comparative_period_label": prior_year_label,
            "generation_date": today_str,                        # alias for document_generated_date
            "document_generated_date": today_str,
            "document_generated_date_short": format_date_short(date.today()),
        }

    # ------------------------------------------------------------------
    # Part 4.2 — Financial Data Context
    # ------------------------------------------------------------------

    def _financial_data_context(self):
        """
        All monetary values from the trial balance.
        Spec Part 4.2 — P&L, Balance Sheet, Key Account Balances.
        """
        fy = self.financial_year
        lines = list(fy.trial_balance_lines.all())

        # ── Section classification (mirrors _get_tb_sections logic) ──────
        sections = self._classify_tb_lines(lines)

        # ── P&L ──────────────────────────────────────────────────────────
        # Income accounts are credit-normal (negative closing_balance = positive income).
        # Negate so revenue is a positive number for display and ratio purposes.
        revenue = -self._sum_section(sections["trading_income"] + sections["income"])
        revenue_py = -self._sum_section(sections["trading_income"] + sections["income"], field="py")
        # COGS are debit-normal (positive closing_balance = positive cost).
        cogs = self._sum_section(sections["cogs"])
        cogs_py = self._sum_section(sections["cogs"], field="py")
        gross_profit = revenue - cogs
        gross_profit_py = revenue_py - cogs_py
        gross_margin_pct = (gross_profit / revenue * 100) if revenue != 0 else Decimal(0)
        gross_margin_pct_py = (gross_profit_py / revenue_py * 100) if revenue_py != 0 else Decimal(0)

        # Expenses are debit-normal (positive closing_balance = positive expense).
        expenses = self._sum_section(sections["expenses"])
        expenses_py = self._sum_section(sections["expenses"], field="py")

        # Specific line items extracted by keyword
        depreciation = self._sum_keyword(sections["expenses"], ["depreciation"])
        amortisation = self._sum_keyword(sections["expenses"], ["amortisation", "amortization"])
        interest_expense = self._sum_keyword(
            sections["expenses"] + sections["noncurrent_liabilities"],
            ["interest expense", "finance cost", "finance charge", "bank charge"],
        )
        interest_expense_py = self._sum_keyword(
            sections["expenses"] + sections["noncurrent_liabilities"],
            ["interest expense", "finance cost", "finance charge", "bank charge"],
            field="py",
        )
        income_tax_expense = self._sum_keyword(
            sections["equity"] + sections["expenses"],
            ["income tax", "tax expense", "tax provision"],
        )
        income_tax_expense_py = self._sum_keyword(
            sections["equity"] + sections["expenses"],
            ["income tax", "tax expense", "tax provision"],
            field="py",
        )

        ebitda = gross_profit - expenses + depreciation + amortisation
        ebitda_py = gross_profit_py - expenses_py + self._sum_keyword(
            sections["expenses"], ["depreciation"], field="py"
        ) + self._sum_keyword(sections["expenses"], ["amortisation", "amortization"], field="py")

        net_profit = gross_profit - expenses
        net_profit_py = gross_profit_py - expenses_py
        net_profit_variance = net_profit - net_profit_py
        net_profit_variance_pct = (
            (net_profit_variance / abs(net_profit_py) * 100)
            if net_profit_py != 0 else None
        )
        net_profit_margin_pct = (net_profit / revenue * 100) if revenue != 0 else Decimal(0)
        # Aliases — defined here after net_profit is assigned
        net_profit_after_tax = net_profit
        net_margin_pct = (net_profit / revenue * 100) if revenue != 0 else Decimal(0)
        net_margin_pct_py = (net_profit_py / revenue_py * 100) if revenue_py != 0 else Decimal(0)

        # ── Balance Sheet ─────────────────────────────────────────────────
        total_current_assets = self._sum_section(sections["current_assets"])
        total_current_assets_py = self._sum_section(sections["current_assets"], field="py")
        total_non_current_assets = self._sum_section(sections["noncurrent_assets"])
        total_non_current_assets_py = self._sum_section(sections["noncurrent_assets"], field="py")
        total_assets = total_current_assets + total_non_current_assets
        total_assets_py = total_current_assets_py + total_non_current_assets_py

        total_current_liabilities = self._sum_section(sections["current_liabilities"])
        total_current_liabilities_py = self._sum_section(sections["current_liabilities"], field="py")
        total_non_current_liabilities = self._sum_section(sections["noncurrent_liabilities"])
        total_non_current_liabilities_py = self._sum_section(sections["noncurrent_liabilities"], field="py")
        # Liabilities are credit-normal (negative); take abs for display and ratio purposes
        total_current_liabilities = abs(total_current_liabilities)
        total_current_liabilities_py = abs(total_current_liabilities_py)
        total_non_current_liabilities = abs(total_non_current_liabilities)
        total_non_current_liabilities_py = abs(total_non_current_liabilities_py)
        total_liabilities = total_current_liabilities + total_non_current_liabilities
        total_liabilities_py = total_current_liabilities_py + total_non_current_liabilities_py

        # Equity accounts are credit-normal (negative closing_balance = positive equity).
        # Negate the sum so that total_equity is a positive number representing net equity.
        total_equity = -self._sum_section(sections["equity"])
        total_equity_py = -self._sum_section(sections["equity"], field="py")
        net_assets = total_assets - total_liabilities
        net_assets_py = total_assets_py - total_liabilities_py

        # Ratios (total_current_liabilities is already abs'd above)
        current_ratio = (
            total_current_assets / total_current_liabilities
            if total_current_liabilities != 0 else None
        )
        working_capital = total_current_assets - total_current_liabilities
        debt_to_equity_ratio = (
            total_liabilities / total_equity if total_equity != 0 else None
        )

        # Retained earnings and share capital are credit-normal (negative = positive equity).
        # Negate so positive value = accumulated profits, negative = accumulated losses.
        retained_earnings = -self._sum_keyword(
            sections["equity"], ["retained", "accumulated"]
        )
        retained_earnings_py = -self._sum_keyword(
            sections["equity"], ["retained", "accumulated"], field="py"
        )
        share_capital = -self._sum_keyword(sections["equity"], ["share capital", "paid up capital"])

        # ── Key compliance balances ───────────────────────────────────────
        director_loan_balance = self._sum_keyword(
            sections["current_assets"] + sections["current_liabilities"] +
            sections["noncurrent_assets"] + sections["noncurrent_liabilities"],
            ["director loan", "shareholder loan", "loan to director", "loan from director"],
        )
        director_loan_balance_py = self._sum_keyword(
            sections["current_assets"] + sections["current_liabilities"] +
            sections["noncurrent_assets"] + sections["noncurrent_liabilities"],
            ["director loan", "shareholder loan", "loan to director", "loan from director"],
            field="py",
        )
        related_party_receivables = self._sum_keyword(
            sections["current_assets"] + sections["noncurrent_assets"],
            ["related party", "intercompany"],
        )
        related_party_payables = self._sum_keyword(
            sections["current_liabilities"] + sections["noncurrent_liabilities"],
            ["related party", "intercompany"],
        )
        superannuation_expense = self._sum_keyword(
            sections["expenses"], ["superannuation", "super expense"]
        )
        superannuation_payable = self._sum_keyword(
            sections["current_liabilities"], ["superannuation payable", "super payable"]
        )
        wages_expense = self._sum_keyword(
            sections["expenses"], ["wages", "salaries", "payroll"]
        )
        gst_payable = self._sum_keyword(sections["current_liabilities"], ["gst payable"])
        gst_receivable = self._sum_keyword(sections["current_assets"], ["gst receivable", "gst refund"])
        tax_payable = self._sum_keyword(sections["current_liabilities"], ["income tax payable", "tax payable"])
        inventory_balance = self._sum_keyword(sections["current_assets"], ["inventory", "stock on hand"])
        inventory_balance_py = self._sum_keyword(sections["current_assets"], ["inventory", "stock on hand"], field="py")
        fixed_assets_net = self._sum_keyword(sections["noncurrent_assets"], ["property", "plant", "equipment", "motor vehicle"])
        franking_account_balance = self._sum_keyword(
            sections["equity"] + sections["current_assets"],
            ["franking", "franking account"],
        )
        trade_debtors_balance = self._sum_keyword(
            sections["current_assets"], ["trade debtors", "accounts receivable", "debtors"]
        )
        total_borrowings = self._sum_keyword(
            sections["current_liabilities"] + sections["noncurrent_liabilities"],
            ["loan", "borrowing", "mortgage", "finance lease"],
        )

        # SG shortfall
        sg_rate = Decimal("11.5")  # Default — will be overridden by ReferenceData if available
        sg_required_amount = wages_expense * sg_rate / 100 if wages_expense else Decimal(0)
        superannuation_shortfall = max(Decimal(0), sg_required_amount - superannuation_expense)

        return {
            # P&L
            "revenue": revenue,
            "revenue_py": revenue_py,
            "revenue_variance": revenue - revenue_py,
            "revenue_variance_pct": (
                ((revenue - revenue_py) / abs(revenue_py) * 100) if revenue_py != 0 else None
            ),
            "revenue_variance_direction": self._variance_direction(revenue, revenue_py),
            "cost_of_sales": cogs,
            "cost_of_sales_py": cogs_py,
            "gross_profit": gross_profit,
            "gross_profit_py": gross_profit_py,
            "gross_margin_pct": gross_margin_pct,
            "gross_margin_pct_py": gross_margin_pct_py,
            "net_margin_pct": net_margin_pct,
            "net_margin_pct_py": net_margin_pct_py,
            "operating_expenses": expenses,
            "operating_expenses_py": expenses_py,
            "expenses": expenses,                        # alias for operating_expenses
            "expenses_py": expenses_py,
            "ebitda": ebitda,
            "ebitda_py": ebitda_py,
            "depreciation": depreciation,
            "amortisation": amortisation,
            "interest_expense": interest_expense,
            "interest_expense_py": interest_expense_py,
            "income_tax_expense": income_tax_expense,
            "income_tax_expense_py": income_tax_expense_py,
            "net_profit": net_profit,
            "net_profit_py": net_profit_py,
            "net_profit_variance": net_profit_variance,
            "net_profit_variance_pct": net_profit_variance_pct,
            "net_profit_variance_direction": self._variance_direction(net_profit, net_profit_py),
            "net_profit_margin_pct": net_profit_margin_pct,
            "is_profitable": net_profit > Decimal(0),
            "is_loss_making": net_profit < Decimal(0),
            # Balance sheet
            "total_current_assets": total_current_assets,
            "total_current_assets_py": total_current_assets_py,
            "total_non_current_assets": total_non_current_assets,
            "total_non_current_assets_py": total_non_current_assets_py,
            "total_assets": total_assets,
            "total_assets_py": total_assets_py,
            "total_current_liabilities": total_current_liabilities,
            "total_current_liabilities_py": total_current_liabilities_py,
            "total_non_current_liabilities": total_non_current_liabilities,
            "total_non_current_liabilities_py": total_non_current_liabilities_py,
            "total_liabilities": total_liabilities,
            "total_liabilities_py": total_liabilities_py,
            "net_assets": net_assets,
            "net_assets_py": net_assets_py,
            "total_equity": total_equity,
            "total_equity_py": total_equity_py,
            "retained_earnings": retained_earnings,
            "retained_earnings_py": retained_earnings_py,
            "retained_earnings_positive": retained_earnings >= Decimal(0),
            "share_capital": share_capital,
            "current_ratio": current_ratio,
            "working_capital": working_capital,
            "working_capital_positive": working_capital >= Decimal(0),
            "debt_to_equity_ratio": debt_to_equity_ratio,
            "negative_equity": net_assets < Decimal(0),
            # Compliance balances
            "director_loan_balance": director_loan_balance,
            "director_loan_balance_py": director_loan_balance_py,
            "director_loan_increased": director_loan_balance > director_loan_balance_py,
            "related_party_receivables": related_party_receivables,
            "related_party_payables": related_party_payables,
            "superannuation_expense": superannuation_expense,
            "superannuation_payable": superannuation_payable,
            "superannuation_shortfall": superannuation_shortfall,
            "wages_expense": wages_expense,
            "gst_payable": gst_payable,
            "gst_receivable": gst_receivable,
            "franking_account_balance": franking_account_balance,
            "tax_payable": tax_payable,
            "inventory_balance": inventory_balance,
            "inventory_balance_py": inventory_balance_py,
            "fixed_assets_net": fixed_assets_net,
            "trade_debtors_balance": trade_debtors_balance,
            "total_borrowings": total_borrowings,
            "sg_rate": sg_rate,
            "sg_required_amount": sg_required_amount,
        }

    # ------------------------------------------------------------------
    # Part 5 — Computed Flags
    # ------------------------------------------------------------------

    def _computed_flags(self, ctx):
        """
        Boolean and categorical flags computed from financial data.
        Spec Part 5.
        """
        from core.models import Section100AAssessment

        net_profit = ctx.get("net_profit", Decimal(0))
        net_assets = ctx.get("net_assets", Decimal(0))
        total_assets = ctx.get("total_assets", Decimal(0))
        total_liabilities = ctx.get("total_liabilities", Decimal(0))
        total_equity = ctx.get("total_equity", Decimal(0))
        current_ratio = ctx.get("current_ratio")
        working_capital = ctx.get("working_capital", Decimal(0))
        retained_earnings = ctx.get("retained_earnings", Decimal(0))
        director_loan_balance = ctx.get("director_loan_balance", Decimal(0))
        director_loan_balance_py = ctx.get("director_loan_balance_py", Decimal(0))
        superannuation_shortfall = ctx.get("superannuation_shortfall", Decimal(0))
        wages_expense = ctx.get("wages_expense", Decimal(0))
        sg_rate = ctx.get("sg_rate", Decimal("11.5"))
        superannuation_expense = ctx.get("superannuation_expense", Decimal(0))
        income_tax_expense = ctx.get("income_tax_expense", Decimal(0))
        trade_debtors_balance = ctx.get("trade_debtors_balance", Decimal(0))
        total_borrowings = ctx.get("total_borrowings", Decimal(0))
        inventory_balance = ctx.get("inventory_balance", Decimal(0))
        fixed_assets_gross = ctx.get("fixed_assets_net", Decimal(0))  # Proxy
        intangibles_balance = Decimal(0)  # Placeholder
        has_contingencies = self.wizard_data.get("has_contingencies", False)
        has_subsequent_events = self.wizard_data.get("has_subsequent_events", False)

        # ── Going concern ─────────────────────────────────────────────────
        going_concern_flag = False
        going_concern_severity = None

        # Condition 1: negative working capital AND current ratio < 1
        if working_capital < 0 and current_ratio is not None and current_ratio < Decimal("1.0"):
            going_concern_flag = True

        # Condition 2: accumulated losses > 20% of total assets.
        # retained_earnings is now normalised: positive = profits, negative = losses.
        # A NEGATIVE retained_earnings value means accumulated losses.
        if total_assets > 0 and retained_earnings < 0:
            if abs(retained_earnings) > (total_assets * Decimal("0.20")):
                going_concern_flag = True

        # Condition 3: negative equity
        if net_assets < 0:
            going_concern_flag = True
            going_concern_severity = "material_uncertainty"

        # Check Eva engine findings
        try:
            from core.eva_engine import get_going_concern_severity
            eva_severity = get_going_concern_severity(self.financial_year)
            if eva_severity:
                going_concern_flag = True
                going_concern_severity = eva_severity
        except Exception:
            pass

        if going_concern_flag and not going_concern_severity:
            going_concern_severity = "emphasis_of_matter"

        # ── Solvency ──────────────────────────────────────────────────────
        solvency_confirmed = (
            net_assets > 0
            and (current_ratio is None or current_ratio >= Decimal("1.0"))
            and not going_concern_flag
        )
        insolvent_risk = not solvency_confirmed and total_liabilities > total_assets

        # ── Division 7A ───────────────────────────────────────────────────
        # ATO benchmark threshold: any director loan > $0 triggers review
        div7a_risk = director_loan_balance > Decimal(0)

        # Check for existing loan agreement
        div7a_loan_agreement_exists = False
        div7a_complying_rate_applied = False
        div7a_benchmark_rate = Decimal("8.27")  # Default — overridden by ReferenceData
        div7a_minimum_repayment = Decimal(0)

        try:
            from core.models import GoverningDocument
            loan_agreements = GoverningDocument.objects.filter(
                entity=self.entity,
                document_type="div7a_loan_agreement",
            )
            if self.financial_year:
                loan_agreements = loan_agreements.filter(
                    document_date__lte=self.financial_year.end_date
                )
            div7a_loan_agreement_exists = loan_agreements.exists()
        except Exception:
            pass

        div7a_action_required = (
            div7a_risk and (not div7a_loan_agreement_exists or not div7a_complying_rate_applied)
        )

        # ── Superannuation ────────────────────────────────────────────────
        sg_shortfall_flag = superannuation_shortfall > Decimal(0)
        sg_shortfall_amount = superannuation_shortfall if sg_shortfall_flag else Decimal(0)

        # ── Related parties ───────────────────────────────────────────────
        has_related_party_transactions = (
            ctx.get("related_party_receivables", Decimal(0)) > 0
            or ctx.get("related_party_payables", Decimal(0)) > 0
            or div7a_risk
        )

        # ── Section 100A ──────────────────────────────────────────────────
        section_100a_risk_flag = False
        try:
            assessment = Section100AAssessment.objects.filter(
                trust_workspace__financial_year=self.financial_year
            ).order_by("-created_at").first()
            if assessment and assessment.risk_level in ("medium", "high"):
                section_100a_risk_flag = True
        except Exception:
            pass

        # ── Conditional notes flags ───────────────────────────────────────
        is_company = ctx.get("is_company", False)
        has_directors = bool(ctx.get("directors", []))

        return {
            # Going concern
            "going_concern_flag": going_concern_flag,
            "going_concern_severity": going_concern_severity,
            "show_going_concern_paragraph": (
                going_concern_flag and going_concern_severity == "material_uncertainty"
            ),
            # Solvency
            "solvency_confirmed": solvency_confirmed,
            "insolvent_risk": insolvent_risk,
            "negative_equity": net_assets < Decimal(0),
            "consecutive_losses": 0,  # Requires multi-year query — placeholder
            # Division 7A
            "div7a_risk": div7a_risk,
            "div7a_risk_flag": div7a_risk,          # alias
            "has_director_loans": director_loan_balance > Decimal(0),  # alias
            "div7a_loan_agreement_exists": div7a_loan_agreement_exists,
            "div7a_complying_rate_applied": div7a_complying_rate_applied,
            "div7a_benchmark_rate": div7a_benchmark_rate,
            "div7a_minimum_repayment": div7a_minimum_repayment,
            "div7a_repayment_made": False,  # Requires journal check — placeholder
            "div7a_action_required": div7a_action_required,
            # Superannuation
            "sg_shortfall_flag": sg_shortfall_flag,
            "sg_shortfall_amount": sg_shortfall_amount,
            # Related parties
            "has_related_party_transactions": has_related_party_transactions,
            # Section 100A
            "section_100a_risk_flag": section_100a_risk_flag,
            "show_section_100a_caveat": section_100a_risk_flag,
            # Trust distribution flag
            "has_trust_distribution": ctx.get("is_trust", False) and len(ctx.get("beneficiaries", [])) > 0,
            # Reporting framework flags
            "is_reporting_entity": ctx.get("is_reporting_entity", False),
            # Conditional notes
            "show_note_revenue": ctx.get("revenue", Decimal(0)) > 0,
            "show_note_trade_debtors": trade_debtors_balance > 0,
            "show_note_inventory": inventory_balance > 0,
            "show_note_fixed_assets": fixed_assets_gross > 0,
            "show_note_intangibles": intangibles_balance > 0,
            "show_note_borrowings": total_borrowings > 0,
            "show_note_related_parties": has_related_party_transactions,
            "show_note_div7a": div7a_risk or div7a_loan_agreement_exists,
            "show_note_contingencies": has_contingencies,
            "show_note_subsequent_events": has_subsequent_events,
            "show_note_going_concern": going_concern_flag,
            "show_note_superannuation": (
                sg_shortfall_flag or ctx.get("superannuation_payable", Decimal(0)) > 0
            ),
            "show_note_income_tax": is_company and income_tax_expense != 0,
            "show_note_key_management": has_directors and ctx.get("is_reporting_entity", False),
            "show_note_financial_instruments": total_borrowings > 0 or trade_debtors_balance > 0,
            "show_note_segment_info": False,
        }

    # ------------------------------------------------------------------
    # Part 6 — Entity People Context
    # ------------------------------------------------------------------

    def _entity_people_context(self):
        """
        Directors, shareholders, trustees, beneficiaries, partners.
        Spec Part 6.
        """
        from core.models import EntityOfficer

        entity_type = self.entity.entity_type
        officers = list(EntityOfficer.objects.filter(
            entity=self.entity,
            date_ceased__isnull=True,
        ).order_by("display_order", "full_name"))

        def officer_to_dict(o):
            return {
                "full_name": o.full_name,
                "first_name": o.full_name.split()[0] if o.full_name else "",
                "address": "",  # EntityOfficer has no address field — left blank
                "appointment_date": format_date_long(o.date_appointed) if o.date_appointed else "",
                "is_secretary": o.has_role("secretary") if hasattr(o, "has_role") else False,
                "credentials": o.title or "",
                "role": o.role,
                "roles": o.roles or [o.role],
                "shares_held": o.shares_held or 0,
                "profit_share_pct": float(o.profit_share_percentage or 0),
                "distribution_percentage": float(o.distribution_percentage or 0),
                "is_signatory": o.is_signatory,
                "email": o.email or "",
                "beneficiary_type": o.beneficiary_type or "",
                "tax_residency": o.tax_residency or "resident",
            }

        def _has_role(o, role):
            if hasattr(o, "has_role"):
                return o.has_role(role)
            return getattr(o, "role", "") == role

        directors = [officer_to_dict(o) for o in officers if _has_role(o, "director")]
        shareholders = [officer_to_dict(o) for o in officers if _has_role(o, "shareholder")]
        trustees = [officer_to_dict(o) for o in officers if _has_role(o, "trustee")]
        beneficiaries = [officer_to_dict(o) for o in officers if _has_role(o, "beneficiary")]
        partners = [officer_to_dict(o) for o in officers if _has_role(o, "partner")]

        # Formatted name lists
        def names_list(people):
            names = [p["full_name"] for p in people]
            if len(names) == 0:
                return ""
            if len(names) == 1:
                return names[0]
            return ", ".join(names[:-1]) + " and " + names[-1]

        def names_csv(people):
            return ", ".join(p["full_name"] for p in people)

        # Addressee salutation (spec §13.1)
        if entity_type == "company":
            salutation = "Dear Directors" if len(directors) > 1 else (
                f"Dear {directors[0]['first_name']}" if directors else "Dear Director"
            )
        elif entity_type == "trust":
            if trustees and trustees[0].get("role") == "trustee":
                salutation = f"Dear {trustees[0]['first_name']}"
            else:
                salutation = "Dear Trustee"
        elif entity_type == "partnership":
            salutation = "Dear Partners"
        else:
            salutation = "Dear " + (directors[0]["first_name"] if directors else "Client")

        return {
            "directors": directors,
            "directors_count": len(directors),
            "primary_director": directors[0] if directors else {},
            "directors_names_list": names_list(directors),
            "directors_names_csv": names_csv(directors),
            "has_directors": len(directors) > 0,
            "shareholders": shareholders,
            "total_shares_on_issue": self.entity.total_shares_on_issue or 0,
            "has_multiple_share_classes": False,  # Placeholder
            "trustees": trustees,
            "trustee_names_list": names_list(trustees),
            "beneficiaries": beneficiaries,
            "beneficiary_count": len(beneficiaries),
            "partners": partners,
            "addressee_salutation": salutation,
        }

    # ------------------------------------------------------------------
    # Part 7 — Financial Statements Context
    # ------------------------------------------------------------------

    def _context_financial_statements(self, ctx):
        """Financial statements presentation variables. Spec Part 7."""
        e = self.entity
        has_prior_year = ctx.get("has_prior_year", False)

        return {
            "functional_currency": "Australian dollars",
            "rounding_basis": "The nearest dollar",
            "accounting_basis": "Accrual",
            "show_cash_flow_statement": False,
            "show_changes_in_equity": ctx.get("is_company", False),
            "show_prior_year_column": has_prior_year,
            "financial_statements_basis_note": self._basis_of_preparation_note(ctx),
            "significant_accounting_policies": [],
            "notes_list": [],
        }

    def _basis_of_preparation_note(self, ctx):
        """Pre-built basis of preparation note text."""
        report_framework = ctx.get("report_framework", "SPFS")
        entity_name = ctx.get("entity_name", "the entity")
        if report_framework == "GPFR":
            return (
                f"These financial statements are general purpose financial statements "
                f"prepared in accordance with Australian Accounting Standards — Simplified "
                f"Disclosures (AASB 1060) and the Corporations Act 2001. "
                f"The financial statements have been prepared on an accruals basis and are "
                f"based on historical costs."
            )
        return (
            f"These financial statements are special purpose financial statements "
            f"prepared for the use of the members of {entity_name}. "
            f"The directors have determined that {entity_name} is not a reporting entity. "
            f"The financial statements have been prepared in accordance with the recognition "
            f"and measurement requirements of Australian Accounting Standards."
        )

    # ------------------------------------------------------------------
    # Part 8 — Compilation Report Context
    # ------------------------------------------------------------------

    def _context_compilation_report(self, ctx):
        """Compilation report variables. Spec Part 8. APES 315 compliant."""
        entity_type = ctx.get("entity_type", "company")
        entity_name = ctx.get("entity_name", "")
        trust_name = ctx.get("trust_name", "")
        report_framework = ctx.get("report_framework", "SPFS")
        fy_end_date = ctx.get("fy_end_date", "")
        practice_name = ctx.get("practice_name", "")
        practice_tax_agent_number = ctx.get("practice_tax_agent_number", "")
        practice_independence_maintained = ctx.get("practice_independence_maintained", True)

        # Addressee
        if entity_type == "company":
            addressee = f"To the Directors of {entity_name}"
        elif entity_type == "trust":
            addressee = f"To the Trustee of {trust_name or entity_name}"
        elif entity_type == "partnership":
            addressee = f"To the Partners of {entity_name}"
        else:
            addressee = f"To {entity_name}"

        # Report title and framework description
        if report_framework == "GPFR":
            compilation_report_title = "Independent Compilation Report"
            report_framework_description = "Australian Accounting Standards"
            applicable_standards_list = ["AASB 1060"]
        else:
            compilation_report_title = "Compilation Report"
            report_framework_description = (
                "Special purpose financial statements prepared in accordance with "
                "the accounting policies described in the notes to the financial statements"
            )
            applicable_standards_list = []

        # APES 315 standard paragraphs
        responsibility_paragraph = (
            f"The directors of {entity_name} are responsible for the preparation and "
            f"fair presentation of the financial information in accordance with the financial "
            f"reporting framework described in the notes to the financial statements, and for "
            f"such internal control as the directors determine is necessary to enable the "
            f"preparation of financial information that is free from material misstatement, "
            f"whether due to fraud or error."
        )
        practitioner_responsibility_paragraph = (
            f"On the basis of information provided by the directors, we have compiled the "
            f"accompanying financial information in accordance with APES 315 Compilation of "
            f"Financial Information. We have applied our expertise in accounting and financial "
            f"reporting to compile the financial information in accordance with the financial "
            f"reporting framework. We have complied with the relevant ethical requirements of "
            f"APES 110 Code of Ethics for Professional Accountants."
        )

        # Independence statement
        independence_statement = ""
        if practice_independence_maintained:
            independence_statement = (
                f"We confirm that we have complied with the independence requirements of "
                f"APES 110 Code of Ethics for Professional Accountants in relation to this "
                f"compilation engagement."
            )

        return {
            "compilation_report_title": compilation_report_title,
            "addressee": addressee,
            "compilation_engagement_type": "compilation",
            "report_framework_description": report_framework_description,
            "applicable_standards_list": applicable_standards_list,
            "compilation_period_description": f"For the year ended {fy_end_date}",
            "responsibility_paragraph": responsibility_paragraph,
            "practitioner_responsibility_paragraph": practitioner_responsibility_paragraph,
            "no_assurance_statement": APES_315_NO_ASSURANCE,
            "going_concern_paragraph": "",  # Eva-authored if needed
            "show_going_concern_paragraph": ctx.get("show_going_concern_paragraph", False),
            "independence_statement": independence_statement,
            "compilation_report_date": format_date_long(date.today()),
            "signing_city": ctx.get("signing_city", ""),
            "declaration_variant": entity_type,
        }

    # ------------------------------------------------------------------
    # Part 9 — Director's Declaration Context
    # ------------------------------------------------------------------

    def _context_directors_declaration(self, ctx):
        """Director's Declaration variables. Spec Part 9. s.295(4) Corporations Act."""
        solvency_confirmed = ctx.get("solvency_confirmed", False)
        directors = ctx.get("directors", [])
        is_large_proprietary = ctx.get("is_large_proprietary", False)
        fy_end_date = ctx.get("fy_end_date", "")
        report_framework = ctx.get("report_framework", "SPFS")
        entity_name = ctx.get("entity_name", "")

        # Declaration date: default to fy_end_date + 90 days
        declaration_date = self.wizard_data.get("declaration_date", "")
        if not declaration_date and self.financial_year:
            from datetime import timedelta
            d = self.financial_year.end_date + timedelta(days=90)
            declaration_date = format_date_long(d)

        declaration_city = self.wizard_data.get("declaration_city", ctx.get("signing_city", ""))

        # Signing directors
        signing_directors = [d for d in directors if d.get("is_signatory", True)]
        requires_two_signatories = is_large_proprietary or len(directors) >= 2

        # Solvency declaration text
        solvency_declaration_text = (
            "In the directors' opinion, there are reasonable grounds to believe that the "
            "company will be able to pay its debts as and when they become due and payable."
        )
        modified_solvency_declaration_text = self.wizard_data.get(
            "modified_solvency_declaration_text", ""
        )

        return {
            "declaration_date": declaration_date,
            "declaration_city": declaration_city,
            "signing_directors": signing_directors,
            "signing_director_primary": signing_directors[0] if signing_directors else {},
            "signing_director_secondary": signing_directors[1] if len(signing_directors) >= 2 else {},
            "requires_two_signatories": requires_two_signatories,
            "solvency_declaration_text": solvency_declaration_text,
            "show_solvency_declaration": solvency_confirmed and ctx.get("is_company", False),
            "modified_solvency_declaration_text": modified_solvency_declaration_text,
            "show_modified_solvency": not solvency_confirmed and ctx.get("is_company", False),
            "report_framework_reference": (
                "Australian Accounting Standards" if report_framework == "GPFR"
                else "the accounting policies described in the notes to the financial statements"
            ),
            "financial_statements_fair_view": report_framework == "GPFR",
            "declaration_variant": "company",
        }

    # ------------------------------------------------------------------
    # Part 10 — Director's Report Context
    # ------------------------------------------------------------------

    def _context_directors_report(self, ctx):
        """Director's Report variables. Spec Part 10. s.298–300 Corporations Act."""
        directors = ctx.get("directors", [])
        fy_end_date_raw = self.financial_year.end_date if self.financial_year else None

        directors_at_year_end = directors  # Simplified — all active directors
        directors_appointed = [
            d for d in directors
            if d.get("appointment_date") and self.financial_year
            and d["appointment_date"] >= format_date_long(self.financial_year.start_date)
        ]

        return {
            "report_date": format_date_long(date.today()),
            "directors_at_year_end": directors_at_year_end,
            "directors_appointed_during_year": directors_appointed,
            "directors_resigned_during_year": [],
            "principal_activities": self.wizard_data.get("principal_activities", ""),
            "review_of_operations": self.wizard_data.get("review_of_operations", ""),
            "review_of_financial_position": self.wizard_data.get("review_of_financial_position", ""),
            "significant_changes_during_year": self.wizard_data.get("significant_changes", ""),
            "show_significant_changes": bool(self.wizard_data.get("significant_changes", "")),
            "subsequent_events": self.wizard_data.get("subsequent_events", ""),
            "show_subsequent_events": bool(self.wizard_data.get("subsequent_events", "")),
            "likely_developments": self.wizard_data.get("likely_developments", ""),
            "show_likely_developments": bool(self.wizard_data.get("likely_developments", "")),
            "dividends_paid_or_declared": self._has_dividend_events(),
            "dividends_summary": self.wizard_data.get("dividends_summary", ""),
            "environmental_regulations_apply": self.wizard_data.get("environmental_regulations", False),
            "auditor_name": getattr(self.entity, "auditor_name", ""),
            "is_audited": getattr(self.entity, "is_audited", False),
            "eva_drafted": False,
        }

    def _has_dividend_events(self):
        if not self.financial_year:
            return False
        try:
            from core.models import DividendEvent
            return DividendEvent.objects.filter(
                entity=self.entity,
                financial_year=self.financial_year,
            ).exists()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Part 11 — Solvency Resolution Context
    # ------------------------------------------------------------------

    def _context_solvency_resolution(self, ctx):
        """Solvency Resolution variables. Spec Part 11. s.254T Corporations Act."""
        dividend_event = self._get_dividend_event()

        return {
            "resolution_date": (
                format_date_long(dividend_event.declaration_date)
                if dividend_event else format_date_long(date.today())
            ),
            "resolution_type": getattr(dividend_event, "resolution_type", "circular") if dividend_event else "circular",
            "meeting_location": getattr(dividend_event, "meeting_location", "") if dividend_event else "",
            "dividend_type": getattr(dividend_event, "dividend_type", "") if dividend_event else "",
            "dividend_amount_total": format_currency(dividend_event.total_amount) if dividend_event else "$-",
            "directors_signing": ctx.get("directors", []),
            "solvency_test_narrative": (
                "The directors have considered the company's financial position and are "
                "satisfied that the company is solvent and will remain solvent immediately "
                "after payment of the dividend, in accordance with section 254T of the "
                "Corporations Act 2001."
            ),
            "net_assets_at_resolution_date": ctx.get("net_assets", Decimal(0)),
            "working_capital_at_resolution_date": ctx.get("working_capital", Decimal(0)),
            "current_ratio_at_resolution_date": ctx.get("current_ratio"),
        }

    def _get_dividend_event(self):
        if not self.financial_year:
            return None
        try:
            from core.models import DividendEvent
            return DividendEvent.objects.filter(
                entity=self.entity,
                financial_year=self.financial_year,
            ).order_by("-declaration_date").first()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Part 12 — Dividend Documents Context
    # ------------------------------------------------------------------

    def _context_dividend_statement(self, ctx):
        """Dividend statement per-shareholder variables. Spec Part 12."""
        return self._shared_dividend_context(ctx)

    def _context_dividend_declaration_minutes(self, ctx):
        """Dividend declaration minutes variables. Spec Part 12."""
        return self._shared_dividend_context(ctx)

    def _shared_dividend_context(self, ctx):
        """Shared dividend variables (spec §12.1)."""
        dividend_event = self._get_dividend_event()
        if not dividend_event:
            return {}

        total_shares = self.entity.total_shares_on_issue or 1
        total_amount = dividend_event.total_amount
        franking_pct = dividend_event.franking_percentage
        company_tax_rate = dividend_event.company_tax_rate / 100  # e.g. 0.25
        dividend_per_share = total_amount / total_shares if total_shares else Decimal(0)

        # Franking credit per share
        if franking_pct > 0 and company_tax_rate > 0:
            franking_credit_per_share = (
                dividend_per_share * (franking_pct / 100) * (company_tax_rate / (1 - company_tax_rate))
            )
        else:
            franking_credit_per_share = Decimal(0)

        franking_credits_attached = (
            total_amount * (franking_pct / 100) * (company_tax_rate / (1 - company_tax_rate))
            if company_tax_rate > 0 else Decimal(0)
        )

        type_labels = {
            "interim": "Interim Dividend",
            "final": "Final Dividend",
            "special": "Special Dividend",
        }

        return {
            "dividend_type_label": type_labels.get(dividend_event.dividend_type, "Dividend"),
            "dividend_declaration_date": format_date_long(dividend_event.declaration_date),
            "dividend_record_date": format_date_long(dividend_event.record_date),
            "dividend_payment_date": format_date_long(dividend_event.payment_date) if dividend_event.payment_date else "",
            "dividend_total_amount": format_currency(total_amount),
            "dividend_per_share": f"{dividend_per_share:.4f}",
            "franking_percentage": int(franking_pct),
            "is_fully_franked": franking_pct == 100,
            "is_partially_franked": 0 < franking_pct < 100,
            "is_unfranked": franking_pct == 0,
            "company_tax_rate_pct": f"{int(dividend_event.company_tax_rate)}%",
            "franking_credit_per_share": format_currency(franking_credit_per_share),
            "franking_account_opening": format_currency(dividend_event.franking_account_opening_balance or 0),
            "franking_account_closing": format_currency(dividend_event.franking_account_closing_balance or 0),
            "franking_credits_attached": format_currency(franking_credits_attached),
            "statement_year_label": ctx.get("fy_year_label", ""),
        }

    # ------------------------------------------------------------------
    # Part 14 — Trust Distribution Documents
    # ------------------------------------------------------------------

    def _context_distribution_minutes(self, ctx):
        """Trust distribution minutes variables. Spec Part 14."""
        try:
            from core.models import TrustWorkspace
            workspace = TrustWorkspace.objects.get(financial_year=self.financial_year)
        except Exception:
            workspace = None

        resolution_date = self.wizard_data.get("resolution_date", "")
        fy_end_date = self.financial_year.end_date if self.financial_year else date.today()
        resolution_is_late = False
        if resolution_date and isinstance(resolution_date, date):
            resolution_is_late = resolution_date > fy_end_date
        days_to_deadline = (fy_end_date - date.today()).days if fy_end_date else 0

        return {
            "resolution_date": format_date_long(resolution_date) if resolution_date else "",
            "resolution_deadline": format_date_long(fy_end_date),
            "days_to_deadline": days_to_deadline,
            "resolution_is_late": resolution_is_late,
            "total_trust_income": format_currency(workspace.net_distributable_income if workspace else 0),
            "tax_free_component": "$-",
            "capital_gains_component": "$-",
            "franked_dividend_component": "$-",
            "income_streams": workspace.income_streams if workspace else [],
            "distributions": [],
            "has_default_beneficiary": False,
            "default_beneficiary_name": getattr(self.entity, "default_beneficiary", ""),
            "section_100a_risk_flag": ctx.get("section_100a_risk_flag", False),
            "show_section_100a_caveat": ctx.get("show_section_100a_caveat", False),
            "trustee_execution_blocks": ctx.get("trustees", []),
            "appointor_consent_required": bool(getattr(self.entity, "appointor", "")),
        }

    def _context_beneficiary_statement(self, ctx):
        """Beneficiary statement variables. Spec Part 14.2."""
        return {
            "trust_name": ctx.get("trust_name", ctx.get("entity_name", "")),
            "trustee_name": ctx.get("trustee_names_list", ""),
            "statement_year_label": ctx.get("fy_year_label", ""),
            "ato_item_reference": "This amount should be reported at Item 13 of your Individual Tax Return.",
        }

    # ------------------------------------------------------------------
    # Part 15 — Partnership Documents
    # ------------------------------------------------------------------

    def _context_partner_statement(self, ctx):
        """Partner statement variables. Spec Part 15."""
        return {
            "partnership_name": ctx.get("entity_name", ""),
            "statement_year_label": ctx.get("fy_year_label", ""),
            "all_partners_summary": ctx.get("partners", []),
            "is_balanced": True,  # Placeholder — validation checks this
        }

    def _context_partnership_tax_summary(self, ctx):
        """Partnership tax summary variables. Spec Part 15."""
        return self._context_partner_statement(ctx)

    # ------------------------------------------------------------------
    # Part 16 — Division 7A Loan Agreement
    # ------------------------------------------------------------------

    def _context_div7a_loan_agreement(self, ctx):
        """Div 7A loan agreement variables. Spec Part 16."""
        from datetime import timedelta

        loan_date = self.wizard_data.get("loan_date", date.today())
        loan_principal = self.wizard_data.get("loan_principal", ctx.get("director_loan_balance", Decimal(0)))
        loan_term_years = int(self.wizard_data.get("loan_term_years", 7))
        loan_term_type = "secured" if loan_term_years == 25 else "unsecured"
        benchmark_rate = ctx.get("div7a_benchmark_rate", Decimal("8.27"))
        borrower_name = self.wizard_data.get("borrower_name", "")
        borrower_address = self.wizard_data.get("borrower_address", "")
        borrower_capacity = self.wizard_data.get("borrower_capacity", "director")

        # Minimum annual repayment (simplified formula)
        if loan_principal and benchmark_rate and loan_term_years:
            rate = float(benchmark_rate) / 100
            n = loan_term_years
            try:
                min_repayment = Decimal(str(
                    float(loan_principal) * rate / (1 - (1 + rate) ** -n)
                )).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except Exception:
                min_repayment = Decimal(0)
        else:
            min_repayment = Decimal(0)

        if isinstance(loan_date, str):
            try:
                from datetime import datetime
                loan_date = datetime.strptime(loan_date, "%Y-%m-%d").date()
            except Exception:
                loan_date = date.today()

        first_repayment = date(loan_date.year + 1, 6, 30) if loan_date.month <= 6 else date(loan_date.year + 1, 6, 30)
        final_repayment = date(loan_date.year + loan_term_years, loan_date.month, loan_date.day)

        fy_year = self.financial_year.end_date.year if self.financial_year else date.today().year
        ato_reference_year = f"{fy_year - 1}-{str(fy_year)[2:]} income year"

        return {
            "loan_date": format_date_long(loan_date),
            "lender_name": ctx.get("entity_name", ""),
            "lender_acn": ctx.get("entity_acn", ""),
            "lender_registered_address": ctx.get("entity_registered_address", ""),
            "borrower_name": borrower_name,
            "borrower_address": borrower_address,
            "borrower_capacity": borrower_capacity,
            "loan_principal": format_currency(loan_principal),
            "benchmark_interest_rate": f"{benchmark_rate}%",
            "loan_term_years": loan_term_years,
            "loan_term_type": loan_term_type,
            "security_description": self.wizard_data.get("security_description", ""),
            "show_security_clause": loan_term_type == "secured",
            "minimum_annual_repayment": format_currency(min_repayment),
            "repayment_due_date": "30 June each year",
            "first_repayment_date": format_date_long(first_repayment),
            "final_repayment_date": format_date_long(final_repayment),
            "interest_calculation_method": "simple interest calculated daily on the outstanding principal balance",
            "default_clause_text": (
                "In the event of default, the outstanding principal balance and all accrued "
                "interest shall become immediately due and payable."
            ),
            "has_related_trust": False,
            "related_trust_name": "",
            "ato_reference_year": ato_reference_year,
        }

    # ------------------------------------------------------------------
    # Part 17 — Management Representation Letter
    # ------------------------------------------------------------------

    def _context_management_representation_letter(self, ctx):
        """Management rep letter variables. Spec Part 17. APES 315 Appendix."""
        div7a_risk = ctx.get("div7a_risk", False)
        going_concern_flag = ctx.get("going_concern_flag", False)
        entity_type = ctx.get("entity_type", "company")

        if entity_type == "company":
            signatory_capacity = "Directors"
        elif entity_type == "trust":
            signatory_capacity = "Trustee"
        else:
            signatory_capacity = "Partners"

        standard_representations = [
            "All accounting records have been made available to you for the purpose of your compilation engagement.",
            "All transactions have been recorded in the accounting records and are reflected in the financial statements.",
            "We have disclosed to you all known actual or possible litigation and claims whose effects should be considered when preparing the financial statements.",
            "There have been no irregularities involving management or employees who have a significant role in internal control.",
            "The entity has complied with all aspects of contractual agreements that would have a material effect on the financial statements.",
            "We are not aware of any material subsequent events that have not been disclosed in the financial statements.",
        ]

        div7a_representation = (
            "All loans to shareholders/directors have been disclosed and we are aware of "
            "our Division 7A obligations under the Income Tax Assessment Act 1936."
        ) if div7a_risk else ""

        going_concern_representation = (
            "We confirm that we are not aware of any events or conditions that cast "
            "significant doubt on the company's ability to continue as a going concern."
        ) if not going_concern_flag else ""

        return {
            "letter_date": format_date_long(date.today()),
            "addressee_name": ctx.get("practice_name", ""),
            "signatory_capacity": signatory_capacity,
            "representations_list": standard_representations,
            "standard_representations": standard_representations,
            "div7a_representation": div7a_representation,
            "show_div7a_representation": div7a_risk,
            "going_concern_representation": going_concern_representation,
            "show_going_concern_representation": not going_concern_flag,
            "signing_blocks": ctx.get("directors", []) or ctx.get("trustees", []) or ctx.get("partners", []),
            "representation_period": ctx.get("fy_period_label", ""),
        }

    # ------------------------------------------------------------------
    # Part 13 — Engagement Letter Context
    # ------------------------------------------------------------------

    def _context_engagement_letter(self, ctx):
        """Engagement letter variables. Spec Part 13. APES 305 compliant."""
        try:
            config = self.entity.engagement_letter_config
        except Exception:
            config = None

        services = list(getattr(config, "services_engaged", []) or [])
        # Fall back to wizard_data if no config or config has no services
        if not services:
            wizard_services = self.wizard_data.get("services", [])
            if isinstance(wizard_services, (list, tuple)):
                services = list(wizard_services)
        fee_amount = getattr(config, "fee_amount", None) or self.wizard_data.get("fee_amount")
        fee_basis = getattr(config, "fee_basis", None) or self.wizard_data.get("fee_basis", "fixed")
        additional_terms = getattr(config, "additional_terms", "") or self.wizard_data.get("additional_terms", "")
        prior_year_letter_existed = getattr(config, "last_generated_fy_id", None) is not None

        practice_name = ctx.get("practice_name", "")
        practice_tax_agent_number = ctx.get("practice_tax_agent_number", "")
        practice_professional_body = ctx.get("practice_professional_body", "CPA Australia")

        tpb_registration_statement = (
            f"{practice_name} is a registered tax agent, registration number "
            f"{practice_tax_agent_number}, registered with the Tax Practitioners Board."
        ) if practice_tax_agent_number else ""

        if practice_professional_body == "CAANZ":
            dispute_resolution_clause = (
                "Any dispute arising from this engagement shall be referred to mediation "
                "in accordance with the CAANZ dispute resolution procedures before "
                "commencing legal proceedings."
            )
        else:
            dispute_resolution_clause = (
                "Any dispute arising from this engagement shall be referred to mediation "
                "in accordance with the CPA Australia dispute resolution procedures before "
                "commencing legal proceedings."
            )

        # engagement_date: wizard sends key 'date', DCB also checks 'engagement_date'
        _raw_date = (
            self.wizard_data.get("engagement_date")
            or self.wizard_data.get("date")
            or format_date_long(date.today())
        )

        return {
            "client_name": ctx.get("entity_name", ""),
            "client_address": ctx.get("entity_postal_address", ""),
            "engagement_date": _raw_date,
            "addressee_salutation": ctx.get("addressee_salutation", "Dear Client"),
            "services_engaged": services,
            # -------------------------------------------------------
            # Service flags — keys MUST match wizard service IDs
            # (see views_partnership_docs._get_service_options)
            # -------------------------------------------------------
            "show_service_compilation": "compilation" in services,
            "show_service_financial_statements": "financial_statements" in services,
            "show_service_tax_return": "tax_return" in services,
            # wizard sends 'bas'; also accept legacy 'bas_preparation'
            "show_service_bas": "bas" in services or "bas_preparation" in services,
            "show_service_bookkeeping": "bookkeeping" in services,
            "show_service_tax_planning": "tax_planning" in services,
            # wizard sends 'payroll'; also accept legacy 'payroll_tax'
            "show_service_payroll": "payroll" in services or "payroll_tax" in services,
            # wizard sends 'asic_compliance'; also accept legacy 'asic_review'
            "show_service_asic": "asic_compliance" in services or "asic_review" in services,
            # wizard sends 'trust_distribution'; also accept legacy 'trust_distribution_planning'
            "show_service_trust_distribution": (
                ("trust_distribution" in services or "trust_distribution_planning" in services)
                and ctx.get("is_trust", False)
            ),
            "show_service_trust_deed_review": "trust_deed_review" in services,
            # wizard sends 'div7a_monitoring'
            "show_service_div7a": "div7a_monitoring" in services or "div7a" in services,
            "show_service_fbt": "fbt" in services,
            "show_service_tpar": "tpar" in services,
            # Company-specific
            "show_service_dividend_management": "dividend_management" in services,
            "show_service_directors_report": "directors_report" in services,
            # Partnership-specific
            "show_service_partner_statements": "partner_statements" in services,
            "show_service_partnership_agreement_review": "partnership_agreement_review" in services,
            # SMSF-specific
            "show_service_smsf_audit": "smsf_audit" in services,
            "show_service_smsf_compliance": "smsf_compliance" in services,
            "show_service_member_statements": "member_statements" in services,
            "fee_amount": format_currency(fee_amount) if fee_amount else "",
            "fee_basis": fee_basis,
            "show_fixed_fee_clause": fee_basis == "fixed",
            "show_hourly_clause": fee_basis == "hourly",
            "show_estimate_clause": fee_basis == "value_based",
            "additional_terms": additional_terms,
            "show_additional_terms": bool(additional_terms),
            "prior_year_letter_existed": prior_year_letter_existed,
            "apes_305_reference": (
                "This engagement is conducted in accordance with APES 305 Terms of Engagement "
                "issued by the Accounting Professional & Ethical Standards Board (APESB)."
            ),
            "tpb_registration_statement": tpb_registration_statement,
            "dispute_resolution_clause": dispute_resolution_clause,
            # Client portal & e-signing (optional — hidden by default)
            "show_client_portal": bool(ctx.get("practice_website")),
            "client_portal_url": ctx.get("practice_website", ""),
            "show_fusesign": False,  # Set to True when FuseSign is configured
        }

    # ------------------------------------------------------------------
    # Part 18 — Client Cover Letter
    # ------------------------------------------------------------------

    def _context_client_cover_letter(self, ctx):
        """Client cover letter / transmittal variables. Spec Part 18."""
        enclosed_documents = []
        action_sign_and_return = []
        action_for_records = []
        action_for_information = []

        if self.financial_year:
            try:
                from core.models import LegalDocument
                docs = LegalDocument.objects.filter(
                    entity=self.entity,
                    financial_year=self.financial_year,
                ).exclude(status="draft")
                for doc in docs:
                    doc_dict = {
                        "document_title": doc.title or doc.get_document_type_display(),
                        "action_required": "sign_and_return",
                        "document_type": doc.document_type,
                    }
                    enclosed_documents.append(doc_dict)
                    action_sign_and_return.append(doc_dict["document_title"])
            except Exception:
                pass

        fusesign_envelope_id = getattr(self.financial_year, "package_fusesign_id", "") if self.financial_year else ""

        return {
            "cover_date": format_date_long(date.today()),
            "client_name": ctx.get("entity_name", ""),
            "client_address": ctx.get("entity_postal_address", ""),
            "salutation": ctx.get("addressee_salutation", "Dear Client"),
            "enclosed_documents": enclosed_documents,
            "action_sign_and_return": action_sign_and_return,
            "action_for_records": action_for_records,
            "action_for_information": action_for_information,
            "has_signing_required": len(action_sign_and_return) > 0,
            "fusesign_instruction": (
                "These documents have been sent via FuseSign for electronic signature. "
                "You will receive a separate email from FuseSign to complete this process."
            ) if fusesign_envelope_id else "",
            "show_fusesign_instruction": bool(fusesign_envelope_id),
            "practice_contact_name": ctx.get("practice_signatory_name", ""),
            "next_steps": [],
            "upcoming_deadlines": [],
        }

    # ------------------------------------------------------------------
    # Part 19 — Eva Client Summary
    # ------------------------------------------------------------------

    def _context_eva_client_summary(self, ctx):
        """Eva client summary variables. Spec Part 19."""
        summary = None
        if self.financial_year:
            try:
                from core.models import EvaClientSummary
                summary = EvaClientSummary.objects.filter(
                    financial_year=self.financial_year
                ).order_by("-generated_at").first()
            except Exception:
                pass

        net_profit = ctx.get("net_profit", Decimal(0))
        revenue = ctx.get("revenue", Decimal(0))
        net_profit_variance_pct = ctx.get("net_profit_variance_pct")

        return {
            "summary_type": "bullet_point",
            "section_1_performance": getattr(summary, "financial_highlights", "") if summary else "",
            "section_2_key_movements": getattr(summary, "section_revenue", "") if summary else "",
            "section_3_yoy_commentary": getattr(summary, "year_on_year_comparison", "") if summary else "",
            "section_4_items_to_watch": getattr(summary, "section_watch_items", "") if summary else "",
            "section_5_next_steps": getattr(summary, "recommendations", "") if summary else "",
            "eva_confidence_score": Decimal("0.8"),
            "accountant_review_required": summary is None,
            "summary_tone": "positive" if net_profit > 0 else ("cautionary" if net_profit < 0 else "neutral"),
            "key_metric_1_label": "Revenue",
            "key_metric_1_value": format_currency(revenue),
            "key_metric_1_movement": (
                f"{'+' if ctx.get('revenue_variance', 0) >= 0 else ''}"
                f"{format_percentage(ctx.get('revenue_variance_pct'))}"
            ),
            "key_metric_2_label": "Net Profit",
            "key_metric_2_value": format_currency(net_profit),
            "key_metric_2_movement": (
                f"{'+' if (net_profit_variance_pct or 0) >= 0 else ''}"
                f"{format_percentage(net_profit_variance_pct)}"
            ),
            "key_metric_3_label": "Net Assets",
            "key_metric_3_value": format_currency(ctx.get("net_assets", Decimal(0))),
            "key_metric_3_movement": "",
        }

    # ------------------------------------------------------------------
    # Part 20 — Context Validation
    # ------------------------------------------------------------------

    def _validate_context(self, ctx, document_type):
        """
        Validate the assembled context against all rules in spec Part 20.
        Raises ContextValidationError with a plain-English message on failure.
        """
        entity_id = str(self.entity.pk)

        # ── Universal validations ─────────────────────────────────────────
        if not ctx.get("entity_name"):
            raise ContextValidationError(
                "Entity has no legal name. Update entity record before generating documents.",
                document_type=document_type,
                entity_id=entity_id,
                missing_fields=["entity_name"],
            )
        if not ctx.get("entity_abn"):
            raise ContextValidationError(
                "Entity ABN is missing. ABN is required on all generated documents.",
                document_type=document_type,
                entity_id=entity_id,
                missing_fields=["entity_abn"],
            )
        if not ctx.get("practice_name"):
            raise ContextValidationError(
                "Practice Profile is incomplete. Firm name is required.",
                document_type=document_type,
                entity_id=entity_id,
                missing_fields=["practice_name"],
            )
        if not ctx.get("practice_tax_agent_number"):
            raise ContextValidationError(
                "Tax Agent Number is missing from Practice Profile. Required for APES 305 compliance.",
                document_type=document_type,
                entity_id=entity_id,
                missing_fields=["practice_tax_agent_number"],
            )
        if document_type not in VALID_DOCUMENT_TYPES:
            raise ContextValidationError(
                f"Unknown document type: {document_type}. Cannot build context.",
                document_type=document_type,
                entity_id=entity_id,
            )

        # ── Document-type specific validations ────────────────────────────
        if document_type == "financial_statements":
            if not self.financial_year:
                raise ContextValidationError(
                    "Financial year required for financial statement generation.",
                    document_type=document_type,
                    entity_id=entity_id,
                )
            total_assets = ctx.get("total_assets", Decimal(0))
            total_liabilities = ctx.get("total_liabilities", Decimal(0))
            total_equity = ctx.get("total_equity", Decimal(0))
            balance_check = abs(total_assets - total_liabilities - total_equity)
            if balance_check > Decimal("1.00"):
                logger.warning(
                    "Balance sheet imbalance of %s for entity %s FY %s. "
                    "Proceeding with generation but flagging for review.",
                    balance_check, entity_id,
                    self.financial_year.year_label if self.financial_year else "unknown",
                )

        elif document_type == "compilation_report":
            if not ctx.get("report_framework"):
                raise ContextValidationError(
                    "Report framework not set. Cannot determine GPFR or SPFS basis for compilation report.",
                    document_type=document_type,
                    entity_id=entity_id,
                    missing_fields=["report_framework"],
                )

        elif document_type == "directors_declaration":
            if not ctx.get("has_directors"):
                raise ContextValidationError(
                    "No active directors found. Cannot generate Director's Declaration.",
                    document_type=document_type,
                    entity_id=entity_id,
                    missing_fields=["directors"],
                )
            if not ctx.get("solvency_confirmed") and not ctx.get("modified_solvency_declaration_text"):
                # When going_concern_flag=True, allow generation with modified declaration
                # (show_modified_solvency=True will render the appropriate modified wording)
                if not ctx.get("going_concern_flag", False):
                    raise ContextValidationError(
                        "Going concern issue identified. Standard declaration cannot be generated. "
                        "Resolve Eva finding first.",
                        document_type=document_type,
                        entity_id=entity_id,
                        missing_fields=["solvency_confirmed"],
                    )

        elif document_type == "solvency_resolution":
            if not ctx.get("solvency_confirmed"):
                raise ContextValidationError(
                    "Solvency not confirmed. Cannot generate solvency resolution.",
                    document_type=document_type,
                    entity_id=entity_id,
                    missing_fields=["solvency_confirmed"],
                )

        elif document_type in ("dividend_statement", "dividend_declaration_minutes"):
            if not self._get_dividend_event():
                raise ContextValidationError(
                    "No dividend event found. Create a dividend event before generating statements.",
                    document_type=document_type,
                    entity_id=entity_id,
                    missing_fields=["dividend_event"],
                )

        elif document_type == "distribution_minutes":
            resolution_date = self.wizard_data.get("resolution_date")
            if resolution_date and self.financial_year:
                if isinstance(resolution_date, date) and resolution_date > self.financial_year.end_date:
                    raise ContextValidationError(
                        "Resolution date is after the financial year end. "
                        "Distributions after 30 June are ineffective for the current year.",
                        document_type=document_type,
                        entity_id=entity_id,
                        missing_fields=["resolution_date"],
                    )

        elif document_type == "div7a_loan_agreement":
            if not self.wizard_data.get("borrower_name"):
                raise ContextValidationError(
                    "No borrower selected for loan agreement.",
                    document_type=document_type,
                    entity_id=entity_id,
                    missing_fields=["borrower"],
                )
            if not ctx.get("div7a_benchmark_rate"):
                raise ContextValidationError(
                    "Division 7A benchmark rate not found in Reference Data. "
                    "Update before generating agreement.",
                    document_type=document_type,
                    entity_id=entity_id,
                    missing_fields=["benchmark_rate"],
                )

        elif document_type == "engagement_letter":
            if not ctx.get("services_engaged"):
                raise ContextValidationError(
                    "No services selected. Select at least one service in the engagement letter wizard.",
                    document_type=document_type,
                    entity_id=entity_id,
                    missing_fields=["services_engaged"],
                )

    # ------------------------------------------------------------------
    # Appendix B — Audit Trail
    # ------------------------------------------------------------------

    def _write_audit_trail(self, ctx):
        """
        Store the complete context (excluding binary logo data) in
        legal_document.parameters for audit trail and exact regeneration.
        Spec Appendix B.
        """
        if not self.legal_document:
            return
        try:
            audit_context = {k: v for k, v in ctx.items() if k != "practice_logo"}
            self.legal_document.parameters = json.dumps(audit_context, default=str)
            self.legal_document.save(update_fields=["parameters"])
        except Exception as exc:
            logger.warning("Failed to write audit trail to LegalDocument: %s", exc)

    # ------------------------------------------------------------------
    # TB helper methods
    # ------------------------------------------------------------------

    def _classify_tb_lines(self, lines):
        """Classify trial balance lines into financial statement sections."""
        from collections import OrderedDict
        sections = {
            "trading_income": [],
            "income": [],
            "cogs": [],
            "expenses": [],
            "current_assets": [],
            "noncurrent_assets": [],
            "current_liabilities": [],
            "noncurrent_liabilities": [],
            "equity": [],
        }
        for line in lines:
            try:
                code_num = int(str(line.account_code).split(".")[0])
            except (ValueError, TypeError):
                continue
            cy = line.closing_balance
            py = line.prior_closing_balance if hasattr(line, "prior_closing_balance") else (
                line.prior_debit - line.prior_credit
            )
            entry = {
                "account_code": line.account_code,
                "account_name": line.account_name,
                "cy": cy,
                "py": py,
            }
            name_lower = line.account_name.lower()
            is_cogs = any(kw in name_lower for kw in [
                "cost of", "opening stock", "closing stock", "purchases", "stock on hand",
            ])
            if code_num < 1000:
                is_other_income = any(kw in name_lower for kw in [
                    "interest", "other", "fbt", "contribution", "dividend", "sundry",
                ])
                if is_other_income:
                    sections["income"].append(entry)
                else:
                    sections["trading_income"].append(entry)
            elif code_num < 1200:
                sections["cogs"].append(entry)
            elif code_num < 2000:
                if is_cogs:
                    sections["cogs"].append(entry)
                else:
                    sections["expenses"].append(entry)
            elif code_num < 2500:
                sections["current_assets"].append(entry)
            elif code_num < 3000:
                sections["noncurrent_assets"].append(entry)
            elif code_num < 3500:
                sections["current_liabilities"].append(entry)
            elif code_num < 4000:
                sections["noncurrent_liabilities"].append(entry)
            elif code_num < 5000:
                sections["equity"].append(entry)
            elif code_num < 6000:
                sections["cogs"].append(entry)
        return sections

    @staticmethod
    def _sum_section(items, field="cy"):
        """Sum a list of TB entry dicts by field."""
        return sum(item.get(field, Decimal(0)) for item in items)

    @staticmethod
    def _sum_keyword(items, keywords, field="cy"):
        """Sum TB entries whose account_name contains any of the keywords."""
        total = Decimal(0)
        for item in items:
            name_lower = item.get("account_name", "").lower()
            if any(kw in name_lower for kw in keywords):
                total += item.get(field, Decimal(0))
        return total

    @staticmethod
    def _variance_direction(current, prior):
        """Return 'increased' | 'decreased' | 'no change'."""
        if current > prior:
            return "increased"
        if current < prior:
            return "decreased"
        return "no change"
