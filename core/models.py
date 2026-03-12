"""
MCS Platform - Core Data Models
Implements the full data model from the specification:
Clients, Entities, Financial Years, Trial Balance Lines,
Account Mappings, Notes/Disclosures, Adjusting Journals, Audit Log.
"""
import hashlib
import json
import uuid
from django.conf import settings
from django.db import models
from django.urls import reverse
from config.encryption import EncryptedCharField


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class Client(models.Model):
    """A client of MC & S. Each client can have multiple entities."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    assigned_accountant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_clients",
    )
    xpm_client_id = models.CharField(
        max_length=100, blank=True, verbose_name="XPM Client ID",
        help_text="Xero Practice Manager reference",
    )
    is_active = models.BooleanField(default=True)
    is_archived = models.BooleanField(
        default=False,
        help_text="Archived clients are hidden from the default list but data is preserved.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("core:entity_list")

    @property
    def entity_count(self):
        return self.entities.count()

    @property
    def latest_status(self):
        """Return the status of the most recent financial year across all entities."""
        fy = (
            FinancialYear.objects.filter(entity__client=self)
            .order_by("-end_date")
            .first()
        )
        return fy.get_status_display() if fy else "No data"


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------
class Entity(models.Model):
    """
    A legal entity belonging to a client.
    E.g., a company, trust, partnership, or sole trader.
    """

    class EntityType(models.TextChoices):
        COMPANY = "company", "Company"
        TRUST = "trust", "Trust"
        PARTNERSHIP = "partnership", "Partnership"
        SOLE_TRADER = "sole_trader", "Sole Trader"
        SMSF = "smsf", "SMSF"

    class ReportingFramework(models.TextChoices):
        GPFR_TIER1 = "GPFR_tier1", "General Purpose (Tier 1)"
        GPFR_TIER2 = "GPFR_tier2", "General Purpose (Tier 2)"
        SPFR = "SPFR", "Special Purpose"

    class CompanySize(models.TextChoices):
        SMALL = "small_proprietary", "Small Proprietary"
        LARGE = "large_proprietary", "Large Proprietary"
        PUBLIC = "public", "Public"

    # Industry choices are now ATO Business Industry Codes (NAT 1827).
    # See core/industry_codes.py for the full list and helper functions.

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="entities",
        help_text="Legacy client link (optional). Entities are now top-level objects.",
    )
    contact_email = models.EmailField(blank=True)
    assigned_accountant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_entities",
        help_text="Legacy field — use primary_accountant instead.",
    )
    primary_accountant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_entities",
        help_text="The accountant primarily responsible for this entity's work.",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_entities",
        help_text="The senior accountant or partner who reviews this entity's work.",
    )
    entity_name = models.CharField(max_length=255)
    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    abn = models.CharField(max_length=11, blank=True, verbose_name="ABN")
    acn = models.CharField(
        max_length=9, blank=True, verbose_name="ACN",
        help_text="Companies only",
    )
    registration_date = models.DateField(null=True, blank=True)
    financial_year_end = models.CharField(
        max_length=5, default="06-30",
        help_text="Month-day format, e.g. 06-30 for June",
    )
    reporting_framework = models.CharField(
        max_length=20,
        choices=ReportingFramework.choices,
        default=ReportingFramework.GPFR_TIER1,
    )
    company_size = models.CharField(
        max_length=20,
        choices=CompanySize.choices,
        blank=True,
        help_text="Companies only",
    )
    industry = models.CharField(
        max_length=5,
        blank=True,
        default="",
        help_text="ATO Business Industry Code (NAT 1827) — used by Eva for AI analysis, GST coding, and benchmarking.",
    )
    template_id = models.ForeignKey(
        "FinancialStatementTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entities",
    )
    trading_as = models.CharField(
        max_length=255, blank=True,
        help_text="Trading name, e.g. 'Southy's Structures'",
    )
    class BASFrequency(models.TextChoices):
        QUARTERLY = "quarterly", "Quarterly"
        MONTHLY = "monthly", "Monthly"

    is_gst_registered = models.BooleanField(
        default=True,
        help_text="Whether this entity is registered for GST. Affects bank statement coding.",
    )
    gst_registration_date = models.DateField(
        null=True, blank=True,
        help_text="Date GST registration commenced. Transactions before this date are auto-set to Out of Scope.",
    )
    bas_frequency = models.CharField(
        max_length=10,
        choices=BASFrequency.choices,
        default=BASFrequency.QUARTERLY,
        help_text="Determines whether BAS periods are quarterly (4 periods) or monthly (12 periods).",
    )

    class CommentaryFrequency(models.TextChoices):
        MATCH_BAS = "match_bas", "Match BAS Frequency"
        QUARTERLY = "quarterly", "Quarterly"
        HALF_YEARLY = "half_yearly", "Half-Yearly"
        ANNUAL = "annual", "Annual Only"

    commentary_frequency_override = models.CharField(
        max_length=15,
        choices=CommentaryFrequency.choices,
        default=CommentaryFrequency.MATCH_BAS,
        help_text="Override the default commentary generation frequency. 'Match BAS' uses the entity's BAS frequency.",
    )
    is_small_business_entity = models.BooleanField(
        null=True, blank=True,
        help_text="Companies only: whether this entity qualifies as a small business entity under the ATO definition.",
    )
    is_base_rate_entity = models.BooleanField(
        null=True, blank=True,
        help_text="Companies only: True = 25% base rate entity, False = 30% non-base rate. Used in tax calculations.",
    )
    show_cents = models.BooleanField(
        default=False,
        help_text="Show amounts with cents (2 decimal places). Default for trusts and sole traders.",
    )
    xpm_client_id = models.CharField(
        max_length=100, blank=True, verbose_name="XPM Client ID",
        help_text="Xero Practice Manager reference",
    )
    contact_phone = EncryptedCharField(
        blank=True,
        help_text="Primary contact phone for this entity",
    )
    tfn = EncryptedCharField(
        blank=True, verbose_name="TFN",
        help_text="Tax File Number (encrypted at rest)",
    )
    address_line_1 = models.CharField(
        max_length=255, blank=True,
        help_text="Street address line 1",
    )
    address_line_2 = models.CharField(
        max_length=255, blank=True,
        help_text="Street address line 2",
    )
    suburb = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=20, blank=True)
    postcode = models.CharField(max_length=10, blank=True)
    country = models.CharField(max_length=50, blank=True, default="Australia")
    trustee_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Trustee company name (trusts and SMSFs only)",
    )
    trustee_acn = models.CharField(
        max_length=9, blank=True, default="", verbose_name="Trustee ACN",
        help_text="ACN of the trustee company (trusts and SMSFs only)",
    )
    # --- Phase 1 additions (Master Implementation Spec §6.1) ---
    is_large_proprietary = models.BooleanField(
        default=False,
        help_text="Companies only: True = large proprietary (s.45A Corporations Act). Affects Director's Declaration wording.",
    )
    total_shares_on_issue = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Companies only: total ordinary shares on issue. Used for dividend calculations.",
    )
    default_engagement_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Default annual engagement fee (AUD). Pre-populates engagement letter wizard.",
    )
    default_engagement_services = models.JSONField(
        default=list, blank=True,
        help_text="Default services list for engagement letters, e.g. ['Tax Return', 'Financial Statements', 'BAS']",
    )
    deed_date = models.DateField(
        null=True, blank=True,
        help_text="Date of the operative trust deed or partnership agreement.",
    )
    deed_reference = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Reference number or title of the operative deed.",
    )
    vesting_date = models.DateField(
        null=True, blank=True,
        help_text="Trust vesting date (trusts only). Used for compliance checks.",
    )
    appointor = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Name of the appointor (trusts only).",
    )
    is_archived = models.BooleanField(
        default=False,
        help_text="Archived entities are hidden from the default list but data is preserved.",
    )
    legal_doc_prompt_dismissed = models.BooleanField(
        default=False,
        help_text="Set to True when the user dismisses the post-creation legal document prompt.",
    )
    metadata = models.JSONField(
        default=dict, blank=True,
        help_text="Flexible storage: directors, trustees, partners, registered address, etc.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["entity_name"]
        verbose_name_plural = "entities"

    def __str__(self):
        return f"{self.entity_name} ({self.get_entity_type_display()})"

    def get_absolute_url(self):
        return reverse("core:entity_detail", kwargs={"pk": self.pk})

    def get_industry_display(self):
        """Return human-readable ATO industry label (code – description)."""
        from core.industry_codes import get_industry_label
        return get_industry_label(self.industry)


# ---------------------------------------------------------------------------
# Entity Officer / Signatory
# ---------------------------------------------------------------------------
class EntityOfficer(models.Model):
    """
    Directors, partners, trustees, or beneficiaries of an entity.
    These are used on declaration pages and signature blocks of financial statements.
    Officers are set once per entity and rolled forward each year.
    """

    class OfficerRole(models.TextChoices):
        DIRECTOR = "director", "Director"
        PARTNER = "partner", "Partner"
        TRUSTEE = "trustee", "Trustee"
        BENEFICIARY = "beneficiary", "Beneficiary"
        SECRETARY = "secretary", "Secretary"
        PUBLIC_OFFICER = "public_officer", "Public Officer"
        SOLE_TRADER = "sole_trader", "Sole Trader / Proprietor"
        CHAIRPERSON = "chairperson", "Chairperson"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="officers"
    )
    full_name = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=OfficerRole.choices)
    roles = models.JSONField(
        default=list, blank=True,
        help_text="Multiple roles for this person, e.g. ['trustee', 'beneficiary']",
    )
    title = models.CharField(
        max_length=50, blank=True,
        help_text='Optional title, e.g. "Managing Director", "Senior Partner"',
    )
    date_appointed = models.DateField(null=True, blank=True)
    date_ceased = models.DateField(null=True, blank=True)
    is_signatory = models.BooleanField(
        default=True,
        help_text="Whether this person signs the financial statements",
    )
    is_chairperson = models.BooleanField(
        default=False,
        help_text="Whether this person is the chairperson (used in distribution minutes)",
    )
    display_order = models.IntegerField(
        default=0,
        help_text="Order in which signatories appear on declaration page",
    )
    # For partnerships: profit share percentage
    profit_share_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Partnership profit share % (partnerships only)",
    )
    # For trusts: beneficiary distribution percentage
    distribution_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Distribution % (trust beneficiaries only)",
    )
    # --- Phase 1 additions (Master Implementation Spec §6.1) ---
    shares_held = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Number of shares held (shareholders/directors of companies only). Used for dividend calculations.",
    )
    email = models.EmailField(
        blank=True, default="",
        help_text="Contact email for this officer. Used for FuseSign and engagement letters.",
    )
    class TaxResidency(models.TextChoices):
        RESIDENT = "resident", "Australian Resident"
        NON_RESIDENT = "non_resident", "Non-Resident"
        TEMPORARY = "temporary", "Temporary Resident"
    tax_residency = models.CharField(
        max_length=15, choices=TaxResidency.choices, default=TaxResidency.RESIDENT,
        help_text="Tax residency status. Affects withholding tax on dividends and trust distributions.",
    )
    class BeneficiaryType(models.TextChoices):
        ADULT = "adult", "Adult Individual"
        MINOR = "minor", "Minor (Under 18)"
        COMPANY = "company", "Company"
        TRUST = "trust", "Trust"
        SMSF = "smsf", "SMSF"
    beneficiary_type = models.CharField(
        max_length=10, choices=BeneficiaryType.choices, blank=True, default="",
        help_text="Type of beneficiary (trust beneficiaries only). Affects distribution modelling and Div 6AA.",
    )
    other_income = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Estimated other taxable income for this beneficiary. Used in trust distribution planning.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["entity", "display_order", "full_name"]
        verbose_name = "Director / Trustee / Beneficiary"
        verbose_name_plural = "Directors / Trustees / Beneficiaries"

    def __str__(self):
        if self.roles:
            role_labels = ', '.join(
                dict(self.OfficerRole.choices).get(r, r.title()) for r in self.roles
            )
            return f"{self.full_name} ({role_labels}) - {self.entity.entity_name}"
        return f"{self.full_name} ({self.get_role_display()}) - {self.entity.entity_name}"

    @property
    def roles_display(self):
        """Return a human-readable comma-separated list of all roles."""
        if self.roles:
            return ', '.join(
                dict(self.OfficerRole.choices).get(r, r.title()) for r in self.roles
            )
        return self.get_role_display()

    def has_role(self, role_value):
        """Check if this officer has a specific role (checks both roles list and legacy role field)."""
        if self.roles:
            return role_value in self.roles
        return self.role == role_value

    @property
    def is_active(self):
        """Officer is active if they have not ceased."""
        return self.date_ceased is None


# ---------------------------------------------------------------------------
# Financial Year
# ---------------------------------------------------------------------------
class FinancialYear(models.Model):
    """A financial year for an entity. Links to prior year for comparatives."""

    class PeriodType(models.TextChoices):
        ANNUAL = "annual", "Annual (Full Year)"
        HALF_YEAR = "half_year", "Half-Year"
        QUARTERLY = "quarterly", "Quarterly"
        MONTHLY = "monthly", "Monthly"
        INTERIM = "interim", "Interim (Custom Period)"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "In Review"
        FINALISED = "finalised", "Finalised"
        REOPENED = "reopened", "Reopened"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="financial_years"
    )
    year_label = models.CharField(max_length=20, help_text='e.g. "FY2025" or "Q1 2025"')
    period_type = models.CharField(
        max_length=20,
        choices=PeriodType.choices,
        default=PeriodType.ANNUAL,
        help_text="Type of reporting period",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    prior_year = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="next_year",
        help_text="Link to prior year for comparatives",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_years",
    )
    finalised_at = models.DateTimeField(null=True, blank=True)
    eva_model_override = models.CharField(
        max_length=10, blank=True, default="",
        help_text="Stores 'opus' if manually escalated to Opus model. Empty otherwise.",
    )
    reopened_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of the last reopen action",
    )
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reopened_years",
        help_text="User who last reopened this financial year",
    )
    reopen_reason = models.TextField(
        blank=True, default="",
        help_text="Reason provided for reopening this financial year",
    )
    # --- Phase 1 additions (Master Implementation Spec §6.2) ---
    package_assembled = models.BooleanField(
        default=False,
        help_text="Whether the client package has been assembled for this FY.",
    )
    package_assembled_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp when the client package was assembled.",
    )
    package_assembled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="assembled_packages",
        help_text="User who assembled the client package.",
    )
    package_sent_for_signing = models.BooleanField(
        default=False,
        help_text="Whether the package has been sent for signing via FuseSign.",
    )
    package_sent_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp when the package was sent for signing.",
    )
    package_fusesign_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="FuseSign envelope ID for the client package.",
    )
    locked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp when Eva cleared and locked this financial year.",
    )
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="locked_years",
        help_text="User (or system) who locked this financial year.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-end_date"]
        verbose_name_plural = "financial years"
        unique_together = ["entity", "year_label"]

    def __str__(self):
        return f"{self.entity.entity_name} - {self.year_label}"

    def get_absolute_url(self):
        return reverse("core:financial_year_detail", kwargs={"pk": self.pk})

    def save(self, *args, **kwargs):
        """Auto-detect period_type based on date range if not explicitly set."""
        if self.start_date and self.end_date:
            delta_days = (self.end_date - self.start_date).days + 1
            if delta_days <= 45:  # ~1 month
                self.period_type = self.PeriodType.MONTHLY
            elif delta_days <= 105:  # ~3 months
                self.period_type = self.PeriodType.QUARTERLY
            elif delta_days <= 200:  # ~6 months
                self.period_type = self.PeriodType.HALF_YEAR
            elif delta_days <= 380:  # ~12 months (allow a few days tolerance)
                self.period_type = self.PeriodType.ANNUAL
            else:
                self.period_type = self.PeriodType.INTERIM
        super().save(*args, **kwargs)

    # --- Status transition constants ---
    VALID_TRANSITIONS = {
        'draft': ['in_review'],
        'in_review': ['finalised', 'draft'],
        'finalised': ['reopened'],
        'reopened': ['in_review'],
    }

    @property
    def is_locked(self):
        return self.status == self.Status.FINALISED

    @property
    def can_ask_eva(self):
        """Eva review is available once the year has been finalised."""
        return self.status == self.Status.FINALISED

    @property
    def can_finalise(self):
        """
        Finalise button is active when status is in_review.
        Eva compliance review happens *after* finalisation.
        """
        return self.status == self.Status.IN_REVIEW

    @property
    def is_reopened(self):
        return self.status == self.Status.REOPENED

    @property
    def can_assemble_package(self):
        """Package assembly available once finalised and Eva review is cleared."""
        if self.status != self.Status.FINALISED:
            return False
        if not self.eva_reviews.exists():
            return False
        from django.apps import apps
        EvaFindingModel = apps.get_model('core', 'EvaFinding')
        open_findings = EvaFindingModel.objects.filter(
            eva_review__financial_year=self,
            status='open',
        ).count()
        return open_findings == 0

    def transition_to(self, new_status):
        """Validate and execute a status transition. Returns True if valid."""
        allowed = self.VALID_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            return False
        self.status = new_status
        return True


# ---------------------------------------------------------------------------
# Account Mapping (Standard Chart)
# ---------------------------------------------------------------------------
class AccountMapping(models.Model):
    """
    Defines how account codes map to standardised financial statement line items.
    This is the core logic layer of the system.
    """

    class FinancialStatement(models.TextChoices):
        INCOME_STATEMENT = "income_statement", "Income Statement"
        BALANCE_SHEET = "balance_sheet", "Balance Sheet"
        EQUITY = "equity", "Statement of Changes in Equity"
        CASH_FLOW = "cash_flow", "Cash Flow Statement"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    standard_code = models.CharField(
        max_length=20, unique=True,
        help_text='MC&S standard chart code, e.g. "REV001"',
    )
    line_item_label = models.CharField(
        max_length=255,
        help_text='Display label, e.g. "Revenue from contracts with customers"',
    )
    financial_statement = models.CharField(
        max_length=20, choices=FinancialStatement.choices
    )
    statement_section = models.CharField(
        max_length=100,
        help_text='e.g. "Current Assets", "Operating Revenue"',
    )
    display_order = models.IntegerField(default=0)
    note_trigger = models.ForeignKey(
        "NoteTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_by_mappings",
        help_text="Which disclosure note this line activates when non-zero",
    )
    applicable_entities = models.JSONField(
        default=list, blank=True,
        help_text='Entity types this applies to, e.g. ["company", "trust"]',
    )

    class Meta:
        ordering = ["financial_statement", "display_order"]

    def __str__(self):
        return f"{self.standard_code} - {self.line_item_label}"


# ---------------------------------------------------------------------------
# Chart of Account (entity-type-specific detailed accounts)
# ---------------------------------------------------------------------------
class ChartOfAccount(models.Model):
    """
    Entity-type-specific chart of accounts.
    These are the detailed account codes (e.g. 0500 Sales, 1510 Accountancy)
    that transactions and trial balance lines are coded to.
    Each entity type (Company, Trust, Partnership, Sole Trader) has its own
    set of accounts. These accounts roll up to AccountMapping line items
    for financial statement generation.
    """

    class StatementSection(models.TextChoices):
        SUSPENSE = "suspense", "Suspense"
        REVENUE = "revenue", "Revenue"
        COST_OF_SALES = "cost_of_sales", "Cost of Sales"
        EXPENSES = "expenses", "Expenses"
        ASSETS = "assets", "Assets"
        LIABILITIES = "liabilities", "Liabilities"
        EQUITY = "equity", "Equity"
        CAPITAL_ACCOUNTS = "capital_accounts", "Capital Accounts"
        PL_APPROPRIATION = "pl_appropriation", "P&L Appropriation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity_type = models.CharField(
        max_length=20, choices=Entity.EntityType.choices,
        help_text="Which entity type this account belongs to",
    )
    account_code = models.CharField(
        max_length=20,
        help_text='Account code, e.g. "500", "1510", "2000.01"',
    )
    account_name = models.CharField(
        max_length=255,
        help_text='Account name, e.g. "Sales", "Accountancy"',
    )
    classification = models.CharField(
        max_length=255, blank=True, default="",
        help_text='Tax classification, e.g. "Other sales revenue", "Trading income"',
    )
    section = models.CharField(
        max_length=30, choices=StatementSection.choices,
        help_text="Which section of the financial statements this belongs to",
    )
    tax_code = models.CharField(
        max_length=20, blank=True, default="",
        help_text='Default tax code: GST, ADS, ITS, FRE, CAP, INP, etc.',
    )
    maps_to = models.ForeignKey(
        "AccountMapping",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="detailed_accounts",
        help_text="Which financial statement line item this rolls up to",
    )
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(
        default=0,
        help_text="Sort order within the section",
    )

    class Meta:
        ordering = ["entity_type", "section", "account_code"]
        unique_together = ["entity_type", "account_code"]
        indexes = [
            models.Index(fields=["entity_type", "section"]),
            models.Index(fields=["entity_type", "is_active"]),
        ]

    def __str__(self):
        return f"{self.account_code} — {self.account_name} ({self.get_entity_type_display()})"

    @property
    def is_revenue(self):
        return self.section in (self.StatementSection.REVENUE, self.StatementSection.COST_OF_SALES)

    @property
    def is_expense(self):
        return self.section == self.StatementSection.EXPENSES

    @property
    def is_balance_sheet(self):
        return self.section in (
            self.StatementSection.ASSETS,
            self.StatementSection.LIABILITIES,
            self.StatementSection.EQUITY,
            self.StatementSection.CAPITAL_ACCOUNTS,
        )


# ---------------------------------------------------------------------------
# Entity Chart of Accounts (per-entity customisable copy of template)
# ---------------------------------------------------------------------------
class EntityChartOfAccount(models.Model):
    """
    Per-entity chart of accounts. Seeded from the master ChartOfAccount
    template when the first financial year is created. Accountants can
    then add, edit, or remove accounts for each entity without affecting
    the master template.
    """

    class StatementSection(models.TextChoices):
        SUSPENSE = "suspense", "Suspense"
        REVENUE = "revenue", "Revenue"
        COST_OF_SALES = "cost_of_sales", "Cost of Sales"
        EXPENSES = "expenses", "Expenses"
        ASSETS = "assets", "Assets"
        LIABILITIES = "liabilities", "Liabilities"
        EQUITY = "equity", "Equity"
        CAPITAL_ACCOUNTS = "capital_accounts", "Capital Accounts"
        PL_APPROPRIATION = "pl_appropriation", "P&L Appropriation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="entity_accounts",
    )
    account_code = models.CharField(
        max_length=20,
        help_text='Account code, e.g. "500", "1510", "2000.01"',
    )
    account_name = models.CharField(
        max_length=255,
        help_text='Account name, e.g. "Sales", "Accountancy"',
    )
    classification = models.CharField(
        max_length=255, blank=True, default="",
        help_text='Tax classification, e.g. "Other sales revenue"',
    )
    section = models.CharField(
        max_length=30, choices=StatementSection.choices,
        help_text="Which section of the financial statements this belongs to",
    )
    tax_code = models.CharField(
        max_length=20, blank=True, default="",
        help_text='Default tax code: GST, ADS, ITS, FRE, CAP, INP, etc.',
    )
    maps_to = models.ForeignKey(
        "AccountMapping",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="entity_detailed_accounts",
        help_text="Which financial statement line item this rolls up to",
    )
    is_active = models.BooleanField(default=True)
    is_custom = models.BooleanField(
        default=False,
        help_text="True if this account was added by the accountant (not from template)",
    )
    # Trust tax planning tags (used in Section 1 — Distributable Income calculation)
    is_non_deductible = models.BooleanField(
        default=False,
        help_text="Non-deductible expense — added back for trust distributable income",
    )
    is_non_assessable = models.BooleanField(
        default=False,
        help_text="Non-assessable income — deducted for trust distributable income",
    )
    is_cgt = models.BooleanField(
        default=False,
        help_text="Capital gains account — streamed separately in trust distributions",
    )
    is_franked_dividend = models.BooleanField(
        default=False,
        help_text="Franked dividend income — streamed separately in trust distributions",
    )
    is_franking_credit = models.BooleanField(
        default=False,
        help_text="Franking credits account — grossed up in beneficiary calculations",
    )
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section", "account_code"]
        unique_together = ["entity", "account_code"]
        indexes = [
            models.Index(fields=["entity", "section"]),
            models.Index(fields=["entity", "is_active"]),
        ]

    def __str__(self):
        return f"{self.account_code} — {self.account_name} ({self.entity.entity_name})"

    @classmethod
    def seed_from_template(cls, entity):
        """
        Copy all active ChartOfAccount entries for the entity's type
        into EntityChartOfAccount records. Skips if entity already has accounts.
        Returns the number of accounts created.
        """
        if cls.objects.filter(entity=entity).exists():
            return 0

        template_accounts = ChartOfAccount.objects.filter(
            entity_type=entity.entity_type, is_active=True
        )
        created = []
        for tpl in template_accounts:
            created.append(cls(
                entity=entity,
                account_code=tpl.account_code,
                account_name=tpl.account_name,
                classification=tpl.classification,
                section=tpl.section,
                tax_code=tpl.tax_code,
                maps_to=tpl.maps_to,
                is_active=True,
                is_custom=False,
                display_order=tpl.display_order,
            ))
        if created:
            cls.objects.bulk_create(created, ignore_conflicts=True)
        return len(created)


# ---------------------------------------------------------------------------
# Client Account Mapping (per-entity mapping of client codes to standard codes)
# ---------------------------------------------------------------------------
class ClientAccountMapping(models.Model):
    """Maps a specific client account code to a standard AccountMapping line item."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="account_mappings"
    )
    client_account_code = models.CharField(max_length=20)
    client_account_name = models.CharField(max_length=255)
    mapped_line_item = models.ForeignKey(
        AccountMapping,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_mappings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["entity", "client_account_code"]
        ordering = ["client_account_code"]

    def __str__(self):
        target = self.mapped_line_item.line_item_label if self.mapped_line_item else "UNMAPPED"
        return f"{self.client_account_code} -> {target}"


# ---------------------------------------------------------------------------
# Bank Account Mapping (links physical bank accounts to TB account codes)
# ---------------------------------------------------------------------------
class BankAccountMapping(models.Model):
    """
    Maps a physical bank account (identified by BSB + account number, or
    bank name) to a trial balance account code for the entity.

    When bank statement transactions are posted to the TB, this mapping
    determines which balance sheet bank account receives the contra-entry.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="bank_account_mappings",
    )
    # Bank identification fields (from the bank statement PDF)
    bank_account_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Bank account name from the statement (e.g. 'CBA Business Account')",
    )
    bsb = models.CharField(
        max_length=20, blank=True, default="",
        help_text="BSB number (e.g. '062-000')",
    )
    account_number = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Account number (e.g. '12345678')",
    )
    # TB mapping
    tb_account_code = models.CharField(
        max_length=20,
        help_text="Trial balance account code for this bank (e.g. '1100')",
    )
    tb_account_name = models.CharField(
        max_length=255,
        help_text="Trial balance account name (e.g. 'Cash at Bank - CBA')",
    )
    mapped_line_item = models.ForeignKey(
        AccountMapping,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="bank_account_mappings",
        help_text="Financial statement line item this bank account maps to",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="If True, this is the default bank account for the entity",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["entity", "tb_account_code"]
        unique_together = ["entity", "bsb", "account_number"]
        indexes = [
            models.Index(fields=["entity", "bsb", "account_number"]),
        ]

    def __str__(self):
        bank_id = f"{self.bsb} {self.account_number}".strip() or self.bank_account_name
        return f"{bank_id} → {self.tb_account_code} ({self.tb_account_name})"


# ---------------------------------------------------------------------------
# Trial Balance Line
# ---------------------------------------------------------------------------
class TrialBalanceLine(models.Model):
    """
    A single account line in a trial balance.
    Highest-volume table (~200 lines per entity per year).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="trial_balance_lines"
    )
    account_code = models.CharField(max_length=20)
    account_name = models.CharField(max_length=255)
    opening_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    closing_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    prior_debit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Prior year debit amount for comparative column",
    )
    prior_credit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Prior year credit amount for comparative column",
    )
    mapped_line_item = models.ForeignKey(
        AccountMapping,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trial_balance_lines",
    )
    tax_type = models.CharField(
        max_length=30, blank=True, default="",
        help_text="Tax type for GST/BAS reporting (e.g., GST on Income, GST on Expenses, GST Free)",
    )
    is_adjustment = models.BooleanField(
        default=False,
        help_text="True if this line was created by an adjusting journal entry",
    )
    # --- Prior Year Comparatives Engine fields ---
    prior_closing_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Prior year closing balance (auto-populated or manually overridden)",
    )
    prior_balance_override = models.BooleanField(
        default=False,
        help_text="True if prior year balance was manually overridden (Year 1 onboarding)",
    )
    prior_mapped_line_item = models.ForeignKey(
        AccountMapping,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prior_trial_balance_lines",
        help_text="Prior year mapping (for reclassification tracking)",
    )
    reclassified = models.BooleanField(
        default=False,
        help_text="True if account mapping changed between years (triggers note disclosure)",
    )
    comparatives_locked = models.BooleanField(
        default=False,
        help_text="Locked when the current year is finalised",
    )

    SOURCE_CHOICES = [
        ('tb_import', 'Trial Balance Import'),
        ('bank_statement', 'Bank Statement'),
        ('manual_journal', 'Manual Journal'),
        ('journal_upload', 'Journal Upload'),
        ('rollover', 'Rolled Forward'),
    ]
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default='tb_import', blank=True,
        help_text="Where this line originated from",
    )
    description = models.CharField(
        max_length=500, blank=True, default='',
        help_text="Journal description or narration (from bulk upload column B or manual journal)",
    )
    bulk_journal_upload = models.ForeignKey(
        'BulkJournalUpload',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='trial_balance_lines',
        help_text="Links this adjustment line to its parent bulk journal upload",
    )
    source_journal = models.ForeignKey(
        'AdjustingJournal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tb_lines',
        help_text="The manual journal that created this adjustment TB line",
    )

    eva_flags = models.JSONField(
        default=list, blank=True,
        help_text="List of EvaFinding check_name strings that flagged this row",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["account_code"]

    def __str__(self):
        return f"{self.account_code} - {self.account_name}: {self.closing_balance}"

    @property
    def variance_amount(self):
        """Dollar variance: current net minus prior net."""
        from decimal import Decimal
        # Use closing_balance for display when no movements (rolled-forward BS items)
        if self.debit == 0 and self.credit == 0 and self.closing_balance != 0:
            if self.closing_balance > 0:
                current = self.closing_balance  # Dr
            else:
                current = self.closing_balance  # negative = Cr, so net is negative
        else:
            current = self.debit - self.credit
        prior = self.prior_debit - self.prior_credit
        return current - prior

    @property
    def variance_percentage(self):
        """Percentage variance from prior year. Returns None if prior is zero."""
        from decimal import Decimal
        prior = self.prior_debit - self.prior_credit
        if prior == 0:
            return None
        return ((self.variance_amount) / abs(prior) * 100).quantize(Decimal('0.1'))


# ---------------------------------------------------------------------------
# Depreciation Asset
# ---------------------------------------------------------------------------
class DepreciationAsset(models.Model):
    """
    An individual depreciable asset for the depreciation schedule.
    Grouped by category (Furniture & Fixtures, Plant & Equipment, etc.).
    """

    class DepreciationMethod(models.TextChoices):
        DIMINISHING = "D", "Diminishing Value"
        PRIME_COST = "P", "Prime Cost"
        WRITTEN_OFF = "W", "Written Off"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="depreciation_assets"
    )
    category = models.CharField(
        max_length=100,
        help_text='Asset category, e.g. "Furniture and Fixtures", "Motor Vehicles"',
    )
    asset_name = models.CharField(max_length=255)
    purchase_date = models.DateField(null=True, blank=True)
    total_cost = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Original purchase cost",
    )
    private_use_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Private use percentage (0-100)",
    )
    opening_wdv = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        verbose_name="Opening WDV",
    )
    # Disposal fields
    disposal_date = models.DateField(null=True, blank=True)
    disposal_consideration = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    # Addition fields
    addition_date = models.DateField(null=True, blank=True)
    addition_cost = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    # Depreciation fields
    depreciable_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Value on which depreciation is calculated",
    )
    method = models.CharField(
        max_length=1,
        choices=DepreciationMethod.choices,
        default=DepreciationMethod.DIMINISHING,
    )
    rate = models.DecimalField(
        max_digits=7, decimal_places=2, default=0,
        help_text="Depreciation rate as percentage",
    )
    depreciation_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Depreciation charged this year",
    )
    private_depreciation = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Private portion of depreciation",
    )
    closing_wdv = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        verbose_name="Closing WDV",
    )
    # Profit/Loss on disposal
    profit_on_disposal = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    loss_on_disposal = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    display_order = models.IntegerField(default=0)
    source_transaction = models.ForeignKey(
        'review.PendingTransaction',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='depreciation_assets',
        help_text="Bank statement transaction this asset was created from",
    )
    # Account mapping for journal posting
    asset_account_code = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Balance sheet account code where this asset sits (e.g. 2870)",
    )
    asset_account_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Balance sheet account name (e.g. Office equipment)",
    )
    accum_dep_code = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Accumulated depreciation account code paired with this asset (e.g. 2875)",
    )
    accum_dep_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Accumulated depreciation account name (e.g. Less: Accumulated depreciation)",
    )
    dep_expense_code = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Depreciation expense account code (e.g. 1615)",
    )
    dep_expense_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Depreciation expense account name (e.g. Depreciation - Plant)",
    )
    notes = models.TextField(
        blank=True, default="",
        help_text="Internal notes, e.g. roll-forward provenance",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "display_order", "asset_name"]
        verbose_name = "Depreciation Asset"
        verbose_name_plural = "Depreciation Assets"

    def __str__(self):
        return f"{self.asset_name} ({self.category}) - WDV: {self.closing_wdv}"


# ---------------------------------------------------------------------------
# Note / Disclosure Template
# ---------------------------------------------------------------------------
class NoteTemplate(models.Model):
    """
    A disclosure note template for financial statements.
    Contains the note text with merge fields and trigger conditions.
    """

    class TriggerType(models.TextChoices):
        ALWAYS = "always", "Always (mandatory)"
        CONDITIONAL = "conditional", "Conditional (account-based)"
        ENTITY_BASED = "entity_based", "Entity-based"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    note_number = models.IntegerField(help_text="Display order in financial statements")
    title = models.CharField(
        max_length=255,
        help_text='e.g. "Revenue", "Related Party Transactions"',
    )
    template_text = models.TextField(
        help_text="Note body with merge fields for dynamic data"
    )
    trigger_type = models.CharField(
        max_length=20, choices=TriggerType.choices
    )
    trigger_condition = models.JSONField(
        default=dict, blank=True,
        help_text="Conditions: account code ranges, entity types, thresholds, etc.",
    )
    applicable_entities = models.JSONField(
        default=list, blank=True,
        help_text='Entity types this note can appear for, e.g. ["company", "trust"]',
    )
    aasb_reference = models.CharField(
        max_length=50, blank=True,
        help_text='e.g. "AASB 15"',
    )
    last_reviewed = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["note_number"]

    def __str__(self):
        return f"Note {self.note_number}: {self.title}"


# ---------------------------------------------------------------------------
# Adjusting Journal
# ---------------------------------------------------------------------------
class AdjustingJournal(models.Model):
    """An adjusting journal entry for a financial year."""

    class JournalType(models.TextChoices):
        GENERAL = "general", "General Journal"
        ADJUSTING = "adjusting", "Adjusting Entry"
        YEAR_END = "year_end", "Year-End Entry"
        DEPRECIATION = "depreciation", "Depreciation Entry"
        TAX = "tax", "Tax Adjustment"
        TAX_PROVISION = "tax_provision", "Tax Provision"

    class JournalStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="adjusting_journals"
    )
    reference_number = models.CharField(
        max_length=20, blank=True,
        help_text="Auto-generated sequential reference, e.g. JE-001",
    )
    journal_type = models.CharField(
        max_length=20,
        choices=JournalType.choices,
        default=JournalType.GENERAL,
    )
    status = models.CharField(
        max_length=20,
        choices=JournalStatus.choices,
        default=JournalStatus.DRAFT,
    )
    journal_date = models.DateField()
    description = models.TextField()
    narration = models.TextField(
        blank=True,
        help_text="Additional notes or explanation for audit purposes",
    )
    total_debit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Cached total debit for quick display",
    )
    total_credit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Cached total credit for quick display",
    )
    # Audit fields
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_journals",
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_journals",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-journal_date", "-created_at"]

    def __str__(self):
        ref = self.reference_number or "DRAFT"
        return f"{ref} - {self.journal_date}: {self.description[:50]}"

    def save(self, *args, **kwargs):
        """Auto-generate reference number on first save."""
        if not self.reference_number and self.financial_year_id:
            last = (
                AdjustingJournal.objects
                .filter(financial_year=self.financial_year)
                .exclude(reference_number="")
                .order_by("-reference_number")
                .first()
            )
            if last and last.reference_number:
                try:
                    num = int(last.reference_number.split("-")[1]) + 1
                except (IndexError, ValueError):
                    num = 1
            else:
                num = 1
            self.reference_number = f"JE-{num:03d}"
        super().save(*args, **kwargs)

    @property
    def is_balanced(self):
        return self.total_debit == self.total_credit

    @property
    def can_post(self):
        return self.status == self.JournalStatus.DRAFT and self.is_balanced

    @property
    def can_delete(self):
        """Any journal can be deleted if the year is not locked."""
        return not self.financial_year.is_locked

    def recalculate_totals(self):
        """Recalculate cached totals from lines."""
        from django.db.models import Sum as DSum
        agg = self.lines.aggregate(dr=DSum("debit"), cr=DSum("credit"))
        self.total_debit = agg["dr"] or 0
        self.total_credit = agg["cr"] or 0
        self.save(update_fields=["total_debit", "total_credit"])


class JournalLine(models.Model):
    """A single debit/credit line within an adjusting journal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    journal = models.ForeignKey(
        AdjustingJournal, on_delete=models.CASCADE, related_name="lines"
    )
    line_number = models.IntegerField(
        default=0,
        help_text="Display order within the journal",
    )
    account_code = models.CharField(max_length=20)
    account_name = models.CharField(max_length=255)
    description = models.CharField(
        max_length=255, blank=True,
        help_text="Optional per-line description",
    )
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    class Meta:
        ordering = ["line_number", "id"]

    def __str__(self):
        return f"{self.account_code}: Dr {self.debit} / Cr {self.credit}"


# ---------------------------------------------------------------------------
# Financial Statement Template (Word document template)
# ---------------------------------------------------------------------------
class FinancialStatementTemplate(models.Model):
    """
    LEGACY — A Word document template for a specific entity type.
    Retained for backward compatibility. See DocumentTemplate for the new
    JSON-driven architecture.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    entity_type = models.CharField(
        max_length=20, choices=Entity.EntityType.choices
    )
    template_file = models.FileField(upload_to="templates/")
    description = models.TextField(blank=True)
    version = models.CharField(max_length=20, default="1.0")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["entity_type", "name"]

    def __str__(self):
        return f"{self.name} (v{self.version})"


class DocumentTemplate(models.Model):
    """
    JSON-driven document template stored in PostgreSQL.
    Defines the structure, content, merge fields, and styling for a generated
    Word document. Configured via admin UI; read at generation time by the
    template renderer engine.

    The `structure` JSONField holds the full template definition:
    {
        "metadata": {
            "page_setup": {"orientation": "portrait", "margin_top": 2.54, ...}
        },
        "styles": {
            "font_name": "Times New Roman",
            "font_size_body": 11,
            "font_size_heading": 14,
            ...
        },
        "sections": [
            {"type": "heading", "text": "...", "level": 1},
            {"type": "paragraph", "text": "Dear {{trustee_name}}, ..."},
            {"type": "table", "columns": [...], "data_source": "beneficiary_rows"},
            {"type": "conditional", "field": "has_streaming", "children": [...]},
            {"type": "signature_block", "name_field": "chairperson_name", ...},
            ...
        ]
    }
    """

    class DocumentCategory(models.TextChoices):
        DISTRIBUTION_MINUTES = "distribution_minutes", "Distribution Minutes"
        TRUST_ELECTION = "trust_election", "Trust Election (s97)"
        TAX_PLANNING_SUMMARY = "tax_planning_summary", "Tax Planning Summary"
        FINANCIAL_STATEMENTS = "financial_statements", "Financial Statements"
        BENEFICIARY_STATEMENT = "beneficiary_statement", "Beneficiary Statement"
        PARTNER_STATEMENT = "partner_statement", "Partner Statement"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255,
        help_text="Human-readable template name, e.g. 'Trust Distribution Minutes v2'",
    )
    document_category = models.CharField(
        max_length=30,
        choices=DocumentCategory.choices,
        help_text="The type of document this template generates.",
    )
    entity_type = models.CharField(
        max_length=20,
        choices=Entity.EntityType.choices,
        blank=True,
        help_text="Restrict to a specific entity type, or leave blank for all.",
    )
    description = models.TextField(
        blank=True,
        help_text="Internal notes about this template.",
    )
    structure = models.JSONField(
        default=dict,
        help_text="JSON template definition (metadata, styles, sections with merge fields).",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Auto-incremented on each save via admin.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only one active template per document_category + entity_type.",
    )
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supersedes",
        help_text="Points to the newer version that replaced this one.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["document_category", "entity_type", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["document_category", "entity_type", "version"],
                name="unique_template_version",
            ),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({'active' if self.is_active else 'inactive'})"

    @classmethod
    def get_active(cls, document_category, entity_type=""):
        """
        Return the active template for a given category and entity type.
        Falls back to a template with blank entity_type if no exact match.
        """
        # Try exact match first
        tpl = cls.objects.filter(
            document_category=document_category,
            entity_type=entity_type,
            is_active=True,
        ).first()
        if tpl:
            return tpl
        # Fallback to generic (blank entity_type)
        if entity_type:
            tpl = cls.objects.filter(
                document_category=document_category,
                entity_type="",
                is_active=True,
            ).first()
        return tpl

    def get_merge_field_names(self):
        """Extract all {{field_name}} references from the structure."""
        import re
        import json
        fields = set()
        text = json.dumps(self.structure)
        for match in re.finditer(r"\{\{(\w+)\}\}", text):
            fields.add(match.group(1))
        return sorted(fields)

    def create_new_version(self, user=None):
        """Create a new version of this template, deactivating the current one."""
        import copy
        new_version = self.version + 1
        new_tpl = DocumentTemplate(
            name=self.name,
            document_category=self.document_category,
            entity_type=self.entity_type,
            description=self.description,
            structure=copy.deepcopy(self.structure),
            version=new_version,
            is_active=True,
            created_by=user,
        )
        self.is_active = False
        self.superseded_by = new_tpl
        new_tpl.save()
        self.save(update_fields=["is_active", "superseded_by", "updated_at"])
        return new_tpl


# ---------------------------------------------------------------------------
# Generated Document
# ---------------------------------------------------------------------------
class GeneratedDocument(models.Model):
    """A generated financial statement document (Word/PDF) with version control."""

    class DocumentStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINAL = "final", "Final"

    class DocumentType(models.TextChoices):
        FINANCIAL_STATEMENTS = "financial_statements", "Financial Statements"
        DISTRIBUTION_MINUTES = "distribution_minutes", "Distribution Minutes"
        BENEFICIARY_STATEMENT = "beneficiary_statement", "Beneficiary Statement"
        PARTNER_STATEMENT = "partner_statement", "Partner Statement"
        TRUST_ELECTION = "trust_election", "Trust Election (s97)"
        TAX_PLANNING_SUMMARY = "tax_planning_summary", "Tax Planning Summary"
        WORKPAPER_NOTES = "workpaper_notes", "Working Paper Notes"
        BAS_COMMENTARY = "bas_commentary", "BAS Period Commentary"
        MANAGEMENT_ACCOUNTS = "management_accounts", "Management Accounts"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="generated_documents"
    )
    file = models.FileField(upload_to="generated/")
    file_format = models.CharField(max_length=10, default="docx")
    document_type = models.CharField(
        max_length=30,
        choices=DocumentType.choices,
        default=DocumentType.FINANCIAL_STATEMENTS,
    )
    version = models.IntegerField(
        default=1,
        help_text="Version number (auto-incremented on regeneration)",
    )
    status = models.CharField(
        max_length=10,
        choices=DocumentStatus.choices,
        default=DocumentStatus.DRAFT,
    )
    change_summary = models.TextField(
        blank=True, default="",
        help_text="Summary of what changed from the previous version",
    )
    is_locked = models.BooleanField(
        default=False,
        help_text="Locked when financial year is finalised - becomes the definitive version",
    )
    superseded_by = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supersedes',
        help_text="Points to the newer version that replaced this one",
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="generated_documents",
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        status_label = f" [{self.get_status_display()}]" if self.status else ""
        return (
            f"{self.financial_year} - {self.get_document_type_display()} "
            f"v{self.version}{status_label} ({self.generated_at:%Y-%m-%d})"
        )

    @property
    def version_label(self):
        return f"v{self.version}"


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------
class AuditLog(models.Model):
    """
    Tracks every significant action in the system for compliance and audit trail.
    """

    class Action(models.TextChoices):
        VIEW = "view", "View"
        LOGIN = "login", "User Login"
        LOGOUT = "logout", "User Logout"
        IMPORT = "import", "Data Import"
        ADJUSTMENT = "adjustment", "Adjustment Created"
        GENERATE = "generate", "Document Generated"
        STATUS_CHANGE = "status_change", "Status Changed"
        MAPPING_CHANGE = "mapping_change", "Mapping Changed"
        USER_CHANGE = "user_change", "User Modified"
        TEMPLATE_CHANGE = "template_change", "Template Modified"
        AI_FEEDBACK = "ai_feedback", "AI Feedback"
        REOPEN = "reopen", "Year Reopened"
        EVA_CHAT = "eva_chat", "Eva Chat"
        EVA_REVIEW = "eva_review", "Eva Review"
        EVA_FINDING = "eva_finding", "Eva Finding"
        EVA_SYNC = "eva_sync", "Eva Knowledge Sync"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    description = models.TextField()
    affected_object_type = models.CharField(max_length=100, blank=True)
    affected_object_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} - {self.user} - {self.get_action_display()}"


# ---------------------------------------------------------------------------
# Risk Rule (Audit Risk Engine)
# ---------------------------------------------------------------------------
class RiskRule(models.Model):
    """
    Defines an audit risk rule that is evaluated against financial year data.
    Each rule has a trigger configuration and produces RiskFlags when triggered.
    """

    class Category(models.TextChoices):
        VARIANCE = "variance", "Variance Analysis"
        CGT = "cgt", "Capital Gains Tax"
        DIVISION_7A = "division_7a", "Division 7A"
        EXPENSES = "expenses", "Expense Analysis"
        FBT = "fbt", "Fringe Benefits Tax"
        GENERAL = "general", "General"
        GST = "gst", "GST"
        SOLVENCY = "solvency", "Solvency"
        SUPERANNUATION = "superannuation", "Superannuation"
        TRUST = "trust", "Trust"
        RELATED_PARTY = "related_party", "Related Party"

    class Severity(models.TextChoices):
        CRITICAL = "CRITICAL", "Critical"
        HIGH = "HIGH", "High"
        MEDIUM = "MEDIUM", "Medium"
        LOW = "LOW", "Low"

    rule_id = models.CharField(max_length=20, primary_key=True)
    category = models.CharField(max_length=30, choices=Category.choices)
    title = models.CharField(max_length=255)
    description = models.TextField()
    severity = models.CharField(max_length=10, choices=Severity.choices)
    tier = models.IntegerField(
        help_text="Processing tier: 1=variance analysis, 2=compliance checks"
    )
    applicable_entities = models.JSONField(
        default=list,
        help_text='Entity types this rule applies to, e.g. ["company", "trust"]',
    )
    trigger_config = models.JSONField(
        default=dict,
        help_text="Configuration for how this rule evaluates data",
    )
    recommended_action = models.TextField()
    legislation_ref = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier", "rule_id"]

    def __str__(self):
        return f"[{self.rule_id}] {self.title} ({self.get_severity_display()})"


# ---------------------------------------------------------------------------
# Risk Reference Data
# ---------------------------------------------------------------------------
class RiskReferenceData(models.Model):
    """
    Reference thresholds and values used by risk rules.
    E.g., GST registration threshold, super guarantee rate, etc.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=255)
    description = models.TextField()
    applicable_fy = models.CharField(
        max_length=10, blank=True,
        help_text='Financial year this applies to, e.g. "FY2025" or blank for all',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_reference_data",
    )

    class Meta:
        ordering = ["key"]
        verbose_name = "Risk Reference Data"
        verbose_name_plural = "Risk Reference Data"

    def __str__(self):
        return f"{self.key} = {self.value}"


# ---------------------------------------------------------------------------
# Risk Flag (Audit Risk Alert)
# ---------------------------------------------------------------------------
class RiskFlag(models.Model):
    """
    A specific risk flag raised against a financial year after running the
    audit risk engine. Each flag references a RiskRule and contains the
    specific details of what was found.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWED = "reviewed", "Reviewed"
        RESOLVED = "resolved", "Resolved"
        AUTO_RESOLVED = "auto_resolved", "Auto-Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="risk_flags"
    )
    run_id = models.UUIDField(
        help_text="Groups flags from the same analysis run"
    )
    rule_id = models.CharField(max_length=20)
    tier = models.IntegerField()
    severity = models.CharField(max_length=10)
    title = models.CharField(max_length=255)
    description = models.TextField()
    affected_accounts = models.JSONField(default=list)
    calculated_values = models.JSONField(default=dict)
    recommended_action = models.TextField()
    legislation_ref = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    resolution_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_risk_flags",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- Sprint 1: AI Tier 3 fields ---
    ai_explanation = models.TextField(
        blank=True, default="",
        help_text="AI-generated plain-English explanation of this risk"
    )
    ai_suggested_action = models.TextField(
        blank=True, default="",
        help_text="AI-generated recommended action for the accountant"
    )
    ai_data_hash = models.CharField(
        max_length=32, blank=True, default="",
        help_text="MD5 hash of flag data at time of AI analysis (cache key)"
    )
    ato_interest_score = models.IntegerField(
        null=True, blank=True,
        help_text="AI-scored likelihood of ATO interest (1-10)"
    )
    ato_interest_reasoning = models.TextField(
        blank=True, default="",
        help_text="AI reasoning for the ATO interest score"
    )

    # --- AI Feedback Loop fields ---
    ai_feedback = models.CharField(
        max_length=30, blank=True, default="",
        help_text="User feedback on AI analysis: correct, partially_correct, incorrect, irrelevant"
    )
    ai_feedback_notes = models.TextField(
        blank=True, default="",
        help_text="User notes explaining the feedback/correction"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["financial_year", "status"]),
            models.Index(fields=["financial_year", "severity"]),
            models.Index(fields=["run_id"]),
        ]

    def __str__(self):
        return f"[{self.severity}] {self.title} - {self.financial_year}"


# ---------------------------------------------------------------------------
# Client Associate (Related Parties & Family Members)
# ---------------------------------------------------------------------------
class ClientAssociate(models.Model):
    """
    Tracks related parties, family members, and associates of a client.
    Used for related party transaction detection in the audit risk engine,
    and for maintaining a complete picture of the client's family group.
    """

    class RelationshipType(models.TextChoices):
        # Family
        SPOUSE = "spouse", "Spouse"
        CHILD = "child", "Child"
        PARENT = "parent", "Parent"
        SIBLING = "sibling", "Sibling"
        FAMILY_OTHER = "family_other", "Other Family Member"
        # Business
        DIRECTOR = "director", "Director"
        SHAREHOLDER = "shareholder", "Shareholder"
        PARTNER_BIZ = "partner_biz", "Business Partner"
        TRUSTEE = "trustee", "Trustee"
        BENEFICIARY = "beneficiary", "Beneficiary"
        RELATED_ENTITY = "related_entity", "Related Entity"
        ACCOUNTANT = "accountant", "Accountant / Advisor"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="client_associates",
    )
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, null=True, blank=True,
        related_name="associates",
        help_text="The entity this associate is linked to",
    )
    name = models.CharField(max_length=255)
    relationship_type = models.CharField(
        max_length=50,
        choices=RelationshipType.choices,
        default=RelationshipType.OTHER,
    )
    date_of_birth = models.DateField(null=True, blank=True)
    abn = models.CharField(max_length=11, blank=True, verbose_name="ABN")
    tfn_last_three = models.CharField(
        max_length=3, blank=True,
        help_text="Last 3 digits of TFN for identification (never store full TFN)",
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    occupation = models.CharField(max_length=255, blank=True)
    employer = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    related_entity = models.ForeignKey(
        Entity, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="associated_as",
        help_text="Link to an entity in the system if applicable",
    )
    related_client = models.ForeignKey(
        Client, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="associated_from",
        help_text="Link to another client in the system if applicable",
    )
    xpm_contact_uuid = models.CharField(
        max_length=100, blank=True,
        help_text="Xero Practice Manager contact UUID for sync",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["client", "relationship_type", "name"]

    def __str__(self):
        client_label = self.client.name if self.client else "No Client"
        return f"{self.name} ({self.get_relationship_type_display()}) - {client_label}"

    @property
    def is_family(self):
        return self.relationship_type in (
            self.RelationshipType.SPOUSE,
            self.RelationshipType.CHILD,
            self.RelationshipType.PARENT,
            self.RelationshipType.SIBLING,
            self.RelationshipType.FAMILY_OTHER,
        )


# ---------------------------------------------------------------------------
# Entity-to-Entity Relationships
# ---------------------------------------------------------------------------
class EntityRelationship(models.Model):
    """
    Links two entities together for the audit risk AI engine.
    E.g., a Trust linked to its Corporate Trustee, an Individual linked
    to their Company, or related party entities in a family group.
    Relationships are bidirectional — creating A→B also implies B→A.
    """

    class RelationshipType(models.TextChoices):
        TRUSTEE_OF = "trustee_of", "Trustee of"
        BENEFICIARY_OF = "beneficiary_of", "Beneficiary of"
        DIRECTOR_OF = "director_of", "Director of"
        SHAREHOLDER_OF = "shareholder_of", "Shareholder of"
        PARTNER_IN = "partner_in", "Partner in"
        PARENT_ENTITY = "parent_entity", "Parent Entity"
        SUBSIDIARY = "subsidiary", "Subsidiary"
        ASSOCIATED_ENTITY = "associated_entity", "Associated Entity"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE,
        related_name="relationships_from",
        help_text="The entity this relationship originates from",
    )
    to_entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE,
        related_name="relationships_to",
        help_text="The entity this relationship points to",
    )
    relationship_type = models.CharField(
        max_length=50,
        choices=RelationshipType.choices,
        default=RelationshipType.ASSOCIATED_ENTITY,
    )
    notes = models.TextField(blank=True, help_text="Optional notes about this relationship")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["relationship_type", "to_entity"]
        unique_together = ["from_entity", "to_entity", "relationship_type"]

    def __str__(self):
        return f"{self.from_entity.entity_name} → {self.get_relationship_type_display()} → {self.to_entity.entity_name}"

    @property
    def reverse_label(self):
        """Human-readable label for the reverse direction."""
        reverse_map = {
            "trustee_of": "Has trustee",
            "beneficiary_of": "Has beneficiary",
            "director_of": "Has director",
            "shareholder_of": "Has shareholder",
            "partner_in": "Partner in",
            "parent_entity": "Subsidiary of",
            "subsidiary": "Parent entity of",
            "associated_entity": "Associated entity",
            "other": "Other",
        }
        return reverse_map.get(self.relationship_type, "Related")


# ---------------------------------------------------------------------------
# Accounting Software Configuration
# ---------------------------------------------------------------------------
class AccountingSoftware(models.Model):
    """
    Tracks which accounting software a client or entity uses.
    Allows MC&S to know whether to expect Xero, MYOB, QuickBooks, or manual data.
    """

    class SoftwareType(models.TextChoices):
        XERO = "xero", "Xero"
        MYOB = "myob", "MYOB"
        QUICKBOOKS = "quickbooks", "QuickBooks"
        SAGE = "sage", "Sage"
        RECKON = "reckon", "Reckon"
        ACCESS_LEDGER = "access_ledger", "Access Ledger"
        EXCEL = "excel", "Excel / Spreadsheet"
        MANUAL = "manual", "Manual / Paper-based"
        OTHER = "other", "Other"
        NONE = "none", "None / Not Applicable"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="software_configs",
    )
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, null=True, blank=True,
        related_name="software_configs",
        help_text="The entity this software is linked to",
    )
    software_type = models.CharField(
        max_length=20, choices=SoftwareType.choices,
    )
    software_version = models.CharField(
        max_length=100, blank=True,
        help_text='e.g. "Xero Standard", "MYOB AccountRight Plus", "QuickBooks Online"',
    )
    is_cloud = models.BooleanField(
        default=True,
        help_text="Whether this is a cloud-based or desktop installation",
    )
    login_email = models.EmailField(
        blank=True,
        help_text="Client's login email for this software (for support reference)",
    )
    organisation_name = models.CharField(
        max_length=255, blank=True,
        help_text="Organisation name within the software",
    )
    has_advisor_access = models.BooleanField(
        default=False,
        help_text="Whether MC&S has advisor/accountant access to this software",
    )
    advisor_login_email = models.EmailField(
        blank=True,
        help_text="MC&S advisor login email for this software",
    )
    subscription_level = models.CharField(
        max_length=100, blank=True,
        help_text='e.g. "Starter", "Standard", "Premium"',
    )
    notes = models.TextField(blank=True)
    is_primary = models.BooleanField(
        default=True,
        help_text="Whether this is the primary accounting software for this client/entity",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["client", "-is_primary", "software_type"]
        verbose_name = "Accounting Software"
        verbose_name_plural = "Accounting Software"

    def __str__(self):
        entity_label = f" ({self.entity.entity_name})" if self.entity else ""
        client_label = self.client.name if self.client else "No Client"
        return f"{client_label}{entity_label} — {self.get_software_type_display()}"


# ---------------------------------------------------------------------------
# Meeting Notes
# ---------------------------------------------------------------------------
class MeetingNote(models.Model):
    """
    Meeting notes, discussion points, and action items for a client.
    Designed to sit alongside financial data so that outreach emails
    can reference both the numbers and the conversation history.
    """

    class MeetingType(models.TextChoices):
        IN_PERSON = "in_person", "In-Person Meeting"
        PHONE = "phone", "Phone Call"
        VIDEO = "video", "Video Call"
        EMAIL_THREAD = "email_thread", "Email Thread"
        INTERNAL = "internal", "Internal Discussion"
        SITE_VISIT = "site_visit", "Site Visit"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="meeting_notes",
    )
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, null=True, blank=True,
        related_name="meeting_notes",
        help_text="The entity this meeting note is linked to",
    )
    title = models.CharField(
        max_length=255,
        help_text='e.g. "Annual Review Meeting", "Tax Planning Discussion"',
    )
    meeting_date = models.DateField()
    meeting_type = models.CharField(
        max_length=20, choices=MeetingType.choices,
        default=MeetingType.IN_PERSON,
    )
    attendees = models.CharField(
        max_length=500, blank=True,
        help_text='Comma-separated names, e.g. "Elio Scarton, John Smith, Jane Doe"',
    )
    # Rich content fields
    discussion_points = models.TextField(
        blank=True,
        help_text="Key topics discussed during the meeting",
    )
    action_items = models.TextField(
        blank=True,
        help_text="Action items and follow-ups arising from the meeting",
    )
    notes = models.TextField(
        blank=True,
        help_text="General notes, observations, and context",
    )
    # Follow-up tracking
    follow_up_date = models.DateField(
        null=True, blank=True,
        help_text="Date for next follow-up or action",
    )
    follow_up_completed = models.BooleanField(default=False)
    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_meeting_notes",
    )
    is_pinned = models.BooleanField(
        default=False,
        help_text="Pin important notes to the top of the list",
    )
    tags = models.CharField(
        max_length=500, blank=True,
        help_text='Comma-separated tags, e.g. "tax-planning, smsf, urgent"',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-meeting_date", "-created_at"]
        verbose_name = "Meeting Note"
        verbose_name_plural = "Meeting Notes"

    def __str__(self):
        client_label = self.client.name if self.client else "No Client"
        return f"{self.meeting_date:%d/%m/%Y} — {self.title} ({client_label})"

    @property
    def tag_list(self):
        """Return tags as a list."""
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def attendee_list(self):
        """Return attendees as a list."""
        if not self.attendees:
            return []
        return [a.strip() for a in self.attendees.split(",") if a.strip()]


# ---------------------------------------------------------------------------
# Stock Item (Opening / Closing Stock)
# ---------------------------------------------------------------------------
class StockItem(models.Model):
    """
    Tracks opening and closing stock for a financial year.
    When values are entered, they push to the trial balance as
    Opening Stock (debit) and Closing Stock (credit) entries.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="stock_items"
    )
    item_name = models.CharField(
        max_length=255,
        help_text='Description of stock item, e.g. "Raw Materials", "Finished Goods"',
    )
    opening_quantity = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
    )
    opening_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Opening stock value ($)",
    )
    closing_quantity = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
    )
    closing_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Closing stock value ($)",
    )
    notes = models.TextField(blank=True, default="")
    pushed_to_tb = models.BooleanField(
        default=False,
        help_text="Whether this stock item has been pushed to the trial balance",
    )
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "item_name"]
        verbose_name = "Stock Item"
        verbose_name_plural = "Stock Items"

    def __str__(self):
        return f"{self.item_name}: Opening ${self.opening_value}, Closing ${self.closing_value}"

    @property
    def stock_movement(self):
        """Closing stock minus opening stock."""
        return self.closing_value - self.opening_value


# ---------------------------------------------------------------------------
# Activity Log (Dashboard Feed & Notifications)
# ---------------------------------------------------------------------------
class ActivityLog(models.Model):
    """
    Tracks significant events in the system for the dashboard activity feed
    and notification bell. Events include bank statement uploads, AI
    classification completions, trial balance imports, journal postings, etc.
    """

    class EventType(models.TextChoices):
        BANK_UPLOAD = "bank_upload", "Bank Statement Uploaded"
        CLASSIFY_COMPLETE = "classify_complete", "AI Classification Complete"
        CLASSIFY_STARTED = "classify_started", "AI Classification Started"
        TB_IMPORT = "tb_import", "Trial Balance Imported"
        TB_IMPORT_DUPLICATE_MERGED = "tb_dup_merged", "TB Duplicate Accounts Merged"
        JOURNAL_POSTED = "journal_posted", "Journal Entry Posted"
        YEAR_FINALISED = "year_finalised", "Financial Year Finalised"
        FY_STATUS_CHANGED = "fy_status_changed", "Financial Year Status Changed"
        AUDIT_RUN = "audit_run", "Audit Risk Analysis Run"
        REVIEW_APPROVED = "review_approved", "Transactions Approved"
        DOCUMENT_GENERATED = "doc_generated", "Document Generated"
        MGMT_ACCOUNTS_GENERATED = "mgmt_accts_gen", "Management Accounts Generated"
        EVA_REVIEW_TRIGGERED = "eva_review_triggered", "Eva Review Triggered"
        EVA_REVIEW_CLEARED = "eva_review_cleared", "Eva Review Cleared"
        EVA_FINDING_ADDRESSED = "eva_finding_addressed", "Eva Finding Addressed"
        BAS_COMMENTARY_GENERATED = "bas_commentary_generated", "BAS Commentary Generated"
        BAS_COMMENTARY_EDITED = "bas_commentary_edited", "BAS Commentary Edited"
        BAS_COMMENTARY_REGENERATED = "bas_commentary_regenerated", "BAS Commentary Regenerated"
        BAS_COMMENTARY_SENT = "bas_commentary_sent", "BAS Commentary Sent"
        BAS_COMMENTARY_DELETED = "bas_commentary_deleted", "BAS Commentary Deleted"
        GENERAL = "general", "General"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    event_type = models.CharField(
        max_length=40, choices=EventType.choices, default=EventType.GENERAL
    )
    title = models.CharField(max_length=255, default="")
    description = models.TextField(blank=True, default="")
    entity = models.ForeignKey(
        Entity,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    financial_year = models.ForeignKey(
        FinancialYear,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    eva_finding = models.ForeignKey(
        "EvaFinding",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    metadata = models.JSONField(blank=True, default=dict)
    url = models.CharField(max_length=500, blank=True, default="")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.get_event_type_display()}] {self.title}"



# ---------------------------------------------------------------------------
# Bank Account
# ---------------------------------------------------------------------------
class BankAccount(models.Model):
    """
    Represents a bank account or credit card linked to an entity.
    Auto-detected from PDF headers (BSB, account number) during upload.
    Can be mapped to a trial balance account code for automated posting.
    """

    class AccountType(models.TextChoices):
        CHEQUE = "cheque", "Cheque Account"
        SAVINGS = "savings", "Savings Account"
        CREDIT_CARD = "credit_card", "Credit Card"
        LOAN = "loan", "Loan Account"
        TERM_DEPOSIT = "term_deposit", "Term Deposit"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE,
        related_name="bank_accounts",
        help_text="The entity this bank account belongs to",
    )
    bank_name = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Bank name (e.g. CBA, Westpac, ANZ)",
    )
    bsb = models.CharField(
        max_length=20, blank=True, default="",
        verbose_name="BSB",
        help_text="Bank-State-Branch number (e.g. 063-123)",
    )
    account_number = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Account number",
    )
    account_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Account name as shown on statement",
    )
    nickname = models.CharField(
        max_length=100, blank=True, default="",
        help_text="User-friendly nickname (e.g. 'Main Business Account')",
    )
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.CHEQUE,
    )
    tb_account_code = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Linked trial balance account code (e.g. 1-1100)",
    )
    tb_account_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Linked trial balance account name",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["bank_name", "account_number"]
        unique_together = ["entity", "bsb", "account_number"]

    def __str__(self):
        label = self.nickname or self.account_name or f"{self.bank_name} {self.account_number}"
        return f"{label} ({self.get_account_type_display()})"

    @property
    def display_name(self):
        if self.nickname:
            return self.nickname
        if self.account_name:
            return self.account_name
        parts = []
        if self.bank_name:
            parts.append(self.bank_name)
        if self.bsb:
            parts.append(f"BSB {self.bsb}")
        if self.account_number:
            parts.append(f"Acc {self.account_number}")
        return " ".join(parts) or "Unknown Account"


# ---------------------------------------------------------------------------
# Trust Distribution (Upgrade 4)
# ---------------------------------------------------------------------------
class TrustDistribution(models.Model):
    """
    Trust distribution workspace for a financial year.
    Tracks distributable income, streaming categories, and corpus.
    One per trust financial year.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.OneToOneField(
        FinancialYear, on_delete=models.CASCADE, related_name="trust_distribution"
    )
    # Income components
    accounting_profit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Accounting profit from the income statement",
    )
    taxable_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Taxable income after tax adjustments",
    )
    distributable_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total distributable income for the year",
    )
    # Streaming categories
    capital_gains = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Capital gains component",
    )
    franked_dividends = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Franked dividends component",
    )
    foreign_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Foreign income component",
    )
    other_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Other income component",
    )
    corpus = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Trust corpus (capital) amount",
    )
    # Reconciliation adjustments
    reconciliation_adjustments = models.JSONField(
        default=list, blank=True,
        help_text="List of adjustment items: [{label, amount}]",
    )
    # Status
    is_fully_allocated = models.BooleanField(
        default=False,
        help_text="True when 100% of distributable income is allocated to beneficiaries",
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Trust Distribution"
        verbose_name_plural = "Trust Distributions"

    def __str__(self):
        return f"Distribution - {self.financial_year}"

    @property
    def total_streaming(self):
        return self.capital_gains + self.franked_dividends + self.foreign_income + self.other_income

    @property
    def allocation_percentage(self):
        """Total percentage allocated across all beneficiaries."""
        total = self.allocations.aggregate(
            total=models.Sum('percentage')
        )['total'] or 0
        return total


class BeneficiaryAllocation(models.Model):
    """
    Allocation of trust distribution to a specific beneficiary.
    Links to EntityOfficer (beneficiary) and tracks per-stream amounts.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    distribution = models.ForeignKey(
        TrustDistribution, on_delete=models.CASCADE, related_name="allocations"
    )
    beneficiary = models.ForeignKey(
        EntityOfficer, on_delete=models.CASCADE, related_name="trust_allocations",
        help_text="The beneficiary receiving this allocation",
    )
    # Allocation method
    percentage = models.DecimalField(
        max_digits=7, decimal_places=4, default=0,
        help_text="Percentage of distributable income (0-100)",
    )
    fixed_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Fixed dollar amount (alternative to percentage)",
    )
    # Per-stream breakdown (calculated)
    allocated_capital_gains = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    allocated_franked_dividends = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    allocated_foreign_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    allocated_other_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    total_distribution = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total distribution to this beneficiary",
    )
    # Section 100A warning
    section_100a_flag = models.BooleanField(
        default=False,
        help_text="Flagged for Section 100A review (reimbursement arrangement risk)",
    )
    section_100a_notes = models.TextField(
        blank=True, default="",
        help_text="Notes regarding Section 100A assessment",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["beneficiary__full_name"]
        unique_together = ["distribution", "beneficiary"]

    def __str__(self):
        return f"{self.beneficiary.full_name}: {self.percentage}% of {self.distribution}"

    def calculate_allocation(self):
        """Calculate per-stream amounts based on percentage.

        Per-stream amounts are calculated from their respective category totals.
        total_distribution is calculated from the distributable_income to ensure
        the full amount is allocated even if income categories don't sum to the total.
        """
        from decimal import Decimal as D
        dist = self.distribution
        pct = self.percentage / 100
        self.allocated_capital_gains = (dist.capital_gains * pct).quantize(D('0.01'))
        self.allocated_franked_dividends = (dist.franked_dividends * pct).quantize(D('0.01'))
        self.allocated_foreign_income = (dist.foreign_income * pct).quantize(D('0.01'))
        self.allocated_other_income = (dist.other_income * pct).quantize(D('0.01'))
        # Total is based on distributable_income so unstreamed income is not lost
        self.total_distribution = (dist.distributable_income * pct).quantize(D('0.01'))


# ---------------------------------------------------------------------------
# Partnership Profit Allocation (Upgrade 5)
# ---------------------------------------------------------------------------
class PartnershipAllocation(models.Model):
    """
    Partnership profit allocation workspace for a financial year.
    One per partnership financial year.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.OneToOneField(
        FinancialYear, on_delete=models.CASCADE, related_name="partnership_allocation"
    )
    net_profit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Net profit available for distribution",
    )
    total_salary_allowances = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    total_interest_on_capital = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    residual_profit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Profit remaining after salary allowances and interest on capital",
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Partnership Allocation"

    def __str__(self):
        return f"Partnership Allocation - {self.financial_year}"


class PartnerShare(models.Model):
    """Individual partner's share of the partnership profit."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    allocation = models.ForeignKey(
        PartnershipAllocation, on_delete=models.CASCADE, related_name="partner_shares"
    )
    partner = models.ForeignKey(
        EntityOfficer, on_delete=models.CASCADE, related_name="partnership_shares",
    )
    salary_allowance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    interest_on_capital = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    residual_share_pct = models.DecimalField(
        max_digits=7, decimal_places=4, default=0,
        help_text="Percentage share of residual profit",
    )
    residual_share_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    total_share = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total profit share for this partner",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["partner__full_name"]
        unique_together = ["allocation", "partner"]

    def __str__(self):
        return f"{self.partner.full_name}: ${self.total_share}"

    def calculate_share(self):
        """Calculate total share from components."""
        from decimal import Decimal
        residual = self.allocation.residual_profit
        self.residual_share_amount = (
            residual * self.residual_share_pct / 100
        ).quantize(Decimal('0.01'))
        self.total_share = (
            self.salary_allowance + self.interest_on_capital + self.residual_share_amount
        )


class PartnerCapitalAccount(models.Model):
    """
    Capital account tracking per partner per financial year.
    Rolls forward annually.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="partner_capital_accounts"
    )
    partner = models.ForeignKey(
        EntityOfficer, on_delete=models.CASCADE, related_name="capital_accounts",
    )
    opening_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    capital_contributions = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    drawings = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    profit_share = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Populated from PartnerShare.total_share",
    )
    closing_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["partner__full_name"]
        unique_together = ["financial_year", "partner"]

    def __str__(self):
        return f"{self.partner.full_name} Capital: ${self.closing_balance}"

    def calculate_closing(self):
        self.closing_balance = (
            self.opening_balance + self.capital_contributions
            - self.drawings + self.profit_share
        )


# ---------------------------------------------------------------------------
# Working Paper Notes (Upgrade 6)
# ---------------------------------------------------------------------------
class WorkpaperNote(models.Model):
    """
    Account-level working paper notes attached to trial balance lines.
    Supports preparer notes, reviewer notes, carry-forward, and status tracking.
    """

    class NoteStatus(models.TextChoices):
        BLANK = "blank", "Blank"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"

    class NoteType(models.TextChoices):
        PREPARER = "preparer", "Preparer Note"
        REVIEWER = "reviewer", "Reviewer Note"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="workpaper_notes"
    )
    account_code = models.CharField(
        max_length=20,
        help_text="Trial balance account code this note is attached to",
    )
    account_name = models.CharField(max_length=255, blank=True, default="")
    note_type = models.CharField(
        max_length=10,
        choices=NoteType.choices,
        default=NoteType.PREPARER,
    )
    status = models.CharField(
        max_length=15,
        choices=NoteStatus.choices,
        default=NoteStatus.BLANK,
    )
    content = models.TextField(
        blank=True, default="",
        help_text="Working paper note content",
    )
    is_carried_forward = models.BooleanField(
        default=False,
        help_text="True if this note was carried forward from the prior year",
    )
    source_year_label = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Year label of the source note if carried forward",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="workpaper_notes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["account_code", "note_type"]
        unique_together = ["financial_year", "account_code", "note_type"]
        indexes = [
            models.Index(fields=["financial_year", "account_code"]),
        ]

    def __str__(self):
        return f"[{self.get_note_type_display()}] {self.account_code} - {self.get_status_display()}"


# ---------------------------------------------------------------------------
# Bulk Entity Import (Upgrade 7)
# ---------------------------------------------------------------------------
class EntityImportJob(models.Model):
    """
    Tracks a bulk entity import job from CSV/Excel.
    """

    class ImportStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VALIDATING = "validating", "Validating"
        VALIDATED = "validated", "Validated"
        IMPORTING = "importing", "Importing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to="imports/")
    original_filename = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=ImportStatus.choices,
        default=ImportStatus.PENDING,
    )
    # Column mapping (user-defined)
    column_mapping = models.JSONField(
        default=dict, blank=True,
        help_text="Maps spreadsheet columns to StatementHub fields",
    )
    # Results
    total_rows = models.IntegerField(default=0)
    created_count = models.IntegerField(default=0)
    skipped_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    validation_errors = models.JSONField(
        default=list, blank=True,
        help_text="List of validation errors: [{row, field, message}]",
    )
    # Metadata
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="entity_import_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Import: {self.original_filename} ({self.get_status_display()})"

    @property
    def progress_percentage(self):
        if self.total_rows == 0:
            return 0
        return int((self.created_count + self.skipped_count + self.error_count) / self.total_rows * 100)


# ---------------------------------------------------------------------------
# Tax Reference Data (configurable tax rates per FY)
# ---------------------------------------------------------------------------
class TaxReferenceData(models.Model):
    """
    Configurable tax rates and thresholds per financial year.
    Used by the Trust Tax Planning calculation engine.
    Never hardcode tax rates — always read from this table.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year_label = models.CharField(
        max_length=10,
        help_text='e.g. "FY2025". Blank = default for all years.',
        blank=True, default="",
    )
    key = models.CharField(
        max_length=100,
        help_text='e.g. "tax_free_threshold", "bracket_1_rate"',
    )
    value = models.CharField(
        max_length=255,
        help_text='Numeric value stored as string, e.g. "18200", "0.19"',
    )
    description = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["financial_year_label", "key"]
        unique_together = ["financial_year_label", "key"]
        verbose_name = "Tax Reference Data"
        verbose_name_plural = "Tax Reference Data"

    def __str__(self):
        return f"{self.key} = {self.value} ({self.financial_year_label or 'default'})"


# ---------------------------------------------------------------------------
# Trust Tax Planning Worksheet
# ---------------------------------------------------------------------------
class TaxPlanningWorksheet(models.Model):
    """
    One record per FinancialYear for Trust entities.
    Created automatically when a Trust financial year workspace is opened.
    """
    class WorksheetStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALISED = "finalised", "Finalised"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.OneToOneField(
        FinancialYear, on_delete=models.CASCADE, related_name="tax_planning_worksheet"
    )
    # Section 1 — auto-calculated from TB, stored for audit trail
    distributable_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Calculated from TB on each page load — stored for audit trail",
    )
    non_deductible_expenses = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    non_assessable_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    net_profit_before_distributions = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    capital_gains = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Subset of distributable income",
    )
    franked_dividends = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Subset of distributable income",
    )
    franking_credits = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Subset of distributable income",
    )
    # Section 5 — Recommendation & Notes
    recommendation_notes = models.TextField(
        blank=True, default="",
        help_text="Rich text — Section 5 content. Auto-saves on blur.",
    )
    # Status
    status = models.CharField(
        max_length=20, choices=WorksheetStatus.choices, default=WorksheetStatus.DRAFT,
    )
    finalised_at = models.DateTimeField(null=True, blank=True)
    finalised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="finalised_tax_plans",
    )
    last_updated_at = models.DateTimeField(auto_now=True)
    last_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="updated_tax_plans",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tax Planning Worksheet"
        verbose_name_plural = "Tax Planning Worksheets"

    def __str__(self):
        return f"Tax Planning — {self.financial_year}"

    @property
    def is_finalised(self):
        return self.status == self.WorksheetStatus.FINALISED


class TaxPlanningBeneficiaryRow(models.Model):
    """
    One record per beneficiary per TaxPlanningWorksheet.
    Recalculated whenever Proposed Distribution or Outside Income changes.
    """
    class BeneficiaryType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"
        TRUST = "trust", "Trust"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worksheet = models.ForeignKey(
        TaxPlanningWorksheet, on_delete=models.CASCADE, related_name="beneficiary_rows"
    )
    beneficiary = models.ForeignKey(
        EntityOfficer, on_delete=models.CASCADE, related_name="tax_planning_rows",
        help_text="From Directors/Trustees/Beneficiaries tab",
    )
    beneficiary_type = models.CharField(
        max_length=20, choices=BeneficiaryType.choices, default=BeneficiaryType.INDIVIDUAL,
        help_text="Denormalised from beneficiary at row creation",
    )
    # Manual entry fields
    outside_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Manual entry — default 0",
    )
    proposed_distribution = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Manual entry — must sum to distributable income",
    )
    # Calculated fields
    grossed_up_franking_credits = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    total_taxable_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    gross_tax_payable = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    medicare_levy = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Individuals only",
    )
    lito_offset = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Individuals only",
    )
    franking_credit_offset = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
    )
    net_tax_payable = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Floored at 0",
    )
    effective_tax_rate = models.DecimalField(
        max_digits=7, decimal_places=4, default=0,
        help_text="4 decimal places, displayed as %",
    )
    company_tax_rate_override = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True,
        help_text="Set if non-base-rate company (0.30). Null = use 0.25.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["beneficiary__full_name"]
        unique_together = ["worksheet", "beneficiary"]
        verbose_name = "Tax Planning Beneficiary Row"
        verbose_name_plural = "Tax Planning Beneficiary Rows"

    def __str__(self):
        return f"{self.beneficiary.full_name} — {self.proposed_distribution}"


class TaxPlanningScenario(models.Model):
    """
    Save up to 3 named distribution scenarios per financial year.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="tax_planning_scenarios"
    )
    scenario_name = models.CharField(max_length=100)
    distributions = models.JSONField(
        default=list,
        help_text='Array of {"beneficiary_id": "uuid", "proposed_amount": 0, "outside_income": 0}',
    )
    total_tax = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total tax payable for this scenario (cached)",
    )
    total_distributed = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total distributed for this scenario (cached)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_tax_scenarios",
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Tax Planning Scenario"
        verbose_name_plural = "Tax Planning Scenarios"

    def __str__(self):
        return f"{self.scenario_name} — {self.financial_year}"


# ---------------------------------------------------------------------------
# Bulk Journal Upload
# ---------------------------------------------------------------------------
class BulkJournalUpload(models.Model):
    """
    Tracks a bulk journal upload session. Each upload via 'Upload JNLs'
    creates one record here with a sequential reference (Bulk JNLS-001, etc.).
    The individual TrialBalanceLine adjustment rows link back via FK.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="bulk_journal_uploads"
    )
    reference_number = models.CharField(
        max_length=30, blank=True,
        help_text="Auto-generated sequential reference, e.g. Bulk JNLS-001",
    )
    filename = models.CharField(
        max_length=255, blank=True,
        help_text="Original uploaded filename",
    )
    description = models.CharField(
        max_length=500, blank=True, default="Bulk Journal Upload",
    )
    lines_count = models.IntegerField(
        default=0,
        help_text="Number of journal lines in this upload",
    )
    total_debit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total debit amount across all lines",
    )
    total_credit = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total credit amount across all lines",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="bulk_journal_uploads",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Bulk Journal Upload"
        verbose_name_plural = "Bulk Journal Uploads"

    def __str__(self):
        ref = self.reference_number or "DRAFT"
        return f"{ref} — {self.filename} ({self.lines_count} lines)"

    def save(self, *args, **kwargs):
        """Auto-generate reference number on first save."""
        if not self.reference_number and self.financial_year_id:
            last = (
                BulkJournalUpload.objects
                .filter(financial_year=self.financial_year)
                .exclude(reference_number="")
                .order_by("-reference_number")
                .first()
            )
            if last and last.reference_number:
                try:
                    num = int(last.reference_number.split("-")[-1]) + 1
                except (IndexError, ValueError):
                    num = 1
            else:
                num = 1
            self.reference_number = f"Bulk JNLS-{num:03d}"
        super().save(*args, **kwargs)

    @property
    def is_balanced(self):
        return self.total_debit == self.total_credit


# ---------------------------------------------------------------------------
# BAS Period (per-period status tracking for GST/BAS lodgement)
# ---------------------------------------------------------------------------
class BASPeriod(models.Model):
    """
    Tracks the status and audit snapshot for each BAS period within a
    financial year. One record per period, created lazily when the
    accountant first interacts with it.
    """

    class PeriodType(models.TextChoices):
        QUARTERLY = "quarterly", "Quarterly"
        MONTHLY = "monthly", "Monthly"

    class Status(models.TextChoices):
        EMPTY = "empty", "Empty"
        PARTIAL = "partial", "Partial"
        READY = "ready", "Ready"
        LODGED = "lodged", "Lodged"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="bas_periods"
    )
    period_type = models.CharField(
        max_length=10, choices=PeriodType.choices,
    )
    period_number = models.PositiveSmallIntegerField(
        help_text="1-4 for quarterly, 1-12 for monthly",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.EMPTY,
    )
    # Lodgement audit fields
    lodged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="lodged_bas_periods",
    )
    lodged_at = models.DateTimeField(null=True, blank=True)
    unlodged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="unlodged_bas_periods",
    )
    unlodged_at = models.DateTimeField(null=True, blank=True)
    # Snapshot of GST figures at time of lodgement
    snapshot_1a = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Snapshot of GST on Sales (label 1A) at time of lodgement",
    )
    snapshot_1b = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Snapshot of GST on Purchases (label 1B) at time of lodgement",
    )
    snapshot_net = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Snapshot of Net GST position at time of lodgement",
    )
    override_reason = models.TextField(
        blank=True, default="",
        help_text="Reason provided if lodging with incomplete coverage or warnings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["period_number"]
        unique_together = ["financial_year", "period_type", "period_number"]
        verbose_name = "BAS Period"
        verbose_name_plural = "BAS Periods"

    def __str__(self):
        return f"{self.financial_year} — {self.get_period_type_display()} P{self.period_number}"

    @property
    def label(self):
        """Human-readable period label, e.g. 'Q1 (Jul-Sep)' or 'Aug'."""
        if self.period_type == self.PeriodType.QUARTERLY:
            quarter_labels = {
                1: "Q1 (Jul\u2013Sep)",
                2: "Q2 (Oct\u2013Dec)",
                3: "Q3 (Jan\u2013Mar)",
                4: "Q4 (Apr\u2013Jun)",
            }
            return quarter_labels.get(self.period_number, f"Q{self.period_number}")
        else:
            import calendar
            # Month number in FY order: 1=Jul(7), 2=Aug(8), ... 6=Dec(12), 7=Jan(1), ...
            cal_month = (self.period_number + 6) % 12 or 12
            return calendar.month_abbr[cal_month]

    @property
    def short_label(self):
        """Short label for tabs, e.g. 'Q1' or 'Jul'."""
        if self.period_type == self.PeriodType.QUARTERLY:
            return f"Q{self.period_number}"
        else:
            import calendar
            cal_month = (self.period_number + 6) % 12 or 12
            return calendar.month_abbr[cal_month]


# ---------------------------------------------------------------------------
# Eva AI Compliance Reviewer Models
# ---------------------------------------------------------------------------
class EvaReview(models.Model):
    """A single Eva compliance review run against a financial year."""

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CLEARED = "cleared", "Cleared"
        FINDINGS_RAISED = "findings_raised", "Findings Raised"
        ERROR = "error", "Error"

    class ModelTier(models.TextChoices):
        HAIKU = "haiku", "Haiku (Pre-flight)"
        SONNET = "sonnet", "Sonnet (Standard)"
        OPUS = "opus", "Opus (Deep Review)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="eva_reviews"
    )
    triggered_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    model_used = models.CharField(
        max_length=10, choices=ModelTier.choices, default=ModelTier.SONNET
    )
    status = models.CharField(
        max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
    )
    raw_response = models.JSONField(default=dict, blank=True)
    applicable_checks = models.JSONField(
        default=list, blank=True,
        help_text="List of check names applicable to this entity type",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="triggered_eva_reviews",
    )
    error_message = models.TextField(blank=True, default="")
    error_acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="acknowledged_eva_errors",
    )
    error_acknowledged_at = models.DateTimeField(null=True, blank=True)
    is_rerun = models.BooleanField(default=False)
    duration_seconds = models.FloatField(null=True, blank=True)
    opus_override = models.BooleanField(
        default=False,
        help_text="True if manually escalated to Opus model",
    )

    class Meta:
        ordering = ["-triggered_at"]
        indexes = [
            models.Index(fields=["financial_year", "status"]),
        ]

    def __str__(self):
        return f"Eva Review for {self.financial_year} — {self.get_status_display()}"


class EvaFinding(models.Model):
    """An individual compliance finding raised by Eva during a review."""

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        ADVISORY = "advisory", "Advisory"

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class FindingStatus(models.TextChoices):
        OPEN = "open", "Open"
        ADDRESSED = "addressed", "Addressed"
        CLOSED = "closed", "Closed"
        REOPENED = "reopened", "Re-Opened"

    class Source(models.TextChoices):
        RISK_ENGINE = "risk_engine", "Risk Engine"
        EVA_ANALYSIS = "eva_analysis", "Eva Analysis"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eva_review = models.ForeignKey(
        EvaReview, on_delete=models.CASCADE, related_name="findings"
    )
    check_name = models.CharField(
        max_length=50,
        help_text="e.g. division_7a, sgc, ato_benchmarks, trust_distributions",
    )
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.ADVISORY
    )
    title = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Brief finding title e.g. 'Potential Division 7A Exposure'",
    )
    plain_english_explanation = models.TextField()
    recommendation = models.TextField()
    remediation_firm_procedure = models.TextField(
        blank=True, default="",
        help_text="Firm-specific procedure for addressing this finding",
    )
    remediation_authority = models.TextField(
        blank=True, default="",
        help_text="Authoritative guidance (ATO, legislation) for this finding",
    )
    remediation_synthesis = models.TextField(
        blank=True, default="",
        help_text="Synthesised remediation combining firm procedure + authority",
    )
    legislation_reference = models.CharField(max_length=255, blank=True, default="")
    knowledge_brain_citation = models.CharField(
        max_length=500, blank=True, default="",
        help_text="Knowledge Brain document cited, if applicable",
    )
    confidence = models.CharField(
        max_length=10, choices=Confidence.choices, default=Confidence.MEDIUM
    )
    status = models.CharField(
        max_length=15, choices=FindingStatus.choices, default=FindingStatus.OPEN
    )
    resolution_note = models.TextField(blank=True, default="")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resolved_eva_findings",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(
        max_length=15, choices=Source.choices, default=Source.EVA_ANALYSIS,
        help_text="Whether this finding originated from the risk engine or Eva's LLM analysis",
    )
    prior_finding = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="successor_findings",
        help_text="Link to the prior review's finding that this re-opens or supersedes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finding_key = models.CharField(
        max_length=255, blank=True, default="",
        db_index=True,
        help_text=(
            "Deterministic key for cross-review deduplication: "
            "{check_id}_{ACCOUNT_CODE_OR_CATEGORY}.  "
            "Used to detect whether a previously-addressed finding should be "
            "skipped on re-review."
        ),
    )
    related_findings = models.ManyToManyField(
        "self",
        blank=True,
        symmetrical=True,
        help_text="Other findings in this review that cover overlapping issues",
    )

    class Meta:
        ordering = ["severity", "check_name"]
        indexes = [
            models.Index(fields=["eva_review", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["eva_review", "finding_key"],
                name="unique_finding_key_per_review",
                condition=~models.Q(finding_key=""),
            ),
        ]

    def __str__(self):
        return f"{self.get_severity_display()}: {self.check_name} — {self.get_status_display()}"

    # ------------------------------------------------------------------
    # Finding-key helpers
    # ------------------------------------------------------------------
    @staticmethod
    def build_finding_key(check_id, account_codes=None, qualifier=None):
        """Return a deterministic finding_key for cross-review dedup.

        Format:  {check_id}_{qualifier_or_sorted_accounts}
        Examples:
            div7a_1200                     (single loan account)
            div7a_OTHER_EXPOSURES          (consolidated non-loan card)
            gst_reconciliation             (no sub-key)
            sgc_WAGES-5000_SUPER-2100      (multiple accounts)
        """
        parts = [str(check_id)]
        if qualifier:
            parts.append(str(qualifier))
        elif account_codes:
            parts.append("_".join(sorted(str(c) for c in account_codes)))
        return "_".join(parts)


# ---------------------------------------------------------------------------
# Eva Clarification Model
# ---------------------------------------------------------------------------
class EvaClarification(models.Model):
    """An interactive clarification question/answer on an EvaFinding."""
    class Outcome(models.TextChoices):
        PENDING = "pending", "Pending"
        DISMISSED = "dismissed", "Dismissed"
        CONFIRMED = "confirmed", "Confirmed"
        REDUCED = "reduced", "Severity Reduced"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    finding = models.ForeignKey(
        EvaFinding, on_delete=models.CASCADE, related_name="clarifications"
    )
    question_id = models.CharField(
        max_length=100,
        help_text="Identifier of the question from CLARIFICATION_QUESTIONS",
    )
    question_text = models.TextField(help_text="The question as shown to the accountant")
    answer_value = models.CharField(
        max_length=100,
        help_text="The selected option value (e.g. 'related_company')",
    )
    answer_label = models.CharField(
        max_length=255, blank=True, default="",
        help_text="The human-readable label of the selected option",
    )
    answer_detail = models.TextField(
        blank=True, default="",
        help_text="Optional free-text elaboration from the accountant",
    )
    outcome_hint = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Outcome hint from the option definition (dismiss/confirm/reduce_severity)",
    )
    outcome = models.CharField(
        max_length=15, choices=Outcome.choices, default=Outcome.PENDING,
    )
    outcome_message = models.TextField(
        blank=True, default="",
        help_text="Eva's explanation of how this answer affects the finding",
    )
    learning_note = models.TextField(
        blank=True, default="",
        help_text="Note stored for future reviews (e.g. borrower is a related company)",
    )
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="eva_clarifications",
    )
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["answered_at"]

    def __str__(self):
        return f"Clarification on {self.finding} — Q:{self.question_id} A:{self.answer_value}"


# ---------------------------------------------------------------------------
# Eva Finding Suppression
# ---------------------------------------------------------------------------
class EvaFindingSuppression(models.Model):
    """
    Prevents a resolved Eva finding from being re-raised on re-run.
    The fingerprint is a deterministic hash of:
    entity_id + financial_year_id + rule_category + sorted account references.
    It must NOT include dollar amounts or narrative text — only structural identifiers.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        'FinancialYear', on_delete=models.CASCADE, related_name='suppressed_findings'
    )
    fingerprint = models.CharField(max_length=64)  # SHA-256 hex digest
    rule_category = models.CharField(max_length=100)
    suppressed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
    )
    suppressed_at = models.DateTimeField(auto_now_add=True)
    accountant_note = models.TextField(blank=True)

    class Meta:
        unique_together = ('financial_year', 'fingerprint')

    def __str__(self):
        return f"Suppression {self.fingerprint[:12]}… on {self.financial_year}"

    @staticmethod
    def generate_fingerprint(entity_id, financial_year_id, rule_category, account_refs=None):
        """
        Generate a stable fingerprint from structural identifiers only.
        account_refs should be a list of account codes/numbers — sort before hashing.
        """
        payload = {
            'entity_id': str(entity_id),
            'financial_year_id': str(financial_year_id),
            'rule_category': str(rule_category),
            'account_refs': sorted([str(r) for r in (account_refs or [])]),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()


# ---------------------------------------------------------------------------
# Knowledge Brain Models
# ---------------------------------------------------------------------------
class KnowledgeDocument(models.Model):
    """A document in Eva's Knowledge Brain, synced from SharePoint."""

    class Category(models.TextChoices):
        FIRM_PROCEDURES = "firm_procedures", "Firm Procedures"
        FIRM_TECHNICAL = "firm_technical", "Firm Technical Positions"
        FIRM_TRAINING = "firm_training", "Firm Training"
        FIRM_PRECEDENTS = "firm_precedents", "Firm Precedents"
        ATO_RULINGS = "ato_rulings", "ATO Rulings"
        ATO_STATEMENTS = "ato_statements", "ATO Practice Statements"
        ATO_ALERTS = "ato_alerts", "ATO Alerts"
        ATO_BENCHMARKS = "ato_benchmarks", "ATO Benchmarks"
        LEGISLATION = "legislation", "Legislation"
        AASB_STANDARDS = "aasb_standards", "AASB Standards"
        CPA_MATERIALS = "cpa_materials", "CPA Materials"
        CA_ANZ_MATERIALS = "ca_anz_materials", "CA ANZ Materials"
        TREASURY = "treasury", "Treasury"
        APES_STANDARDS = "apes_standards", "APES Standards"
        TPB_GUIDANCE = "tpb_guidance", "TPB Guidance"
        CASE_LAW = "case_law", "Case Law"
        INDUSTRY_GUIDES = "industry_guides", "Industry Guides"
        CLIENT_PRECEDENTS = "client_precedents", "Client Precedents"
        OTHER = "other", "Other"

    class SyncStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SYNCED = "synced", "Synced"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=500)
    category = models.CharField(
        max_length=30, choices=Category.choices, default=Category.FIRM_PROCEDURES
    )
    sharepoint_path = models.CharField(
        max_length=1000, blank=True, default="",
        help_text="Full SharePoint path to the source document",
    )
    sharepoint_item_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="SharePoint item ID for API operations",
    )
    sharepoint_modified_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(
        max_length=10, choices=SyncStatus.choices, default=SyncStatus.PENDING
    )
    synced_at = models.DateTimeField(null=True, blank=True)
    chunk_count = models.IntegerField(default=0)
    file_type = models.CharField(
        max_length=10, blank=True, default="",
        help_text="File extension: docx, pdf, txt, xlsx, pptx",
    )
    file_size_bytes = models.IntegerField(default=0)
    is_archived = models.BooleanField(
        default=False,
        help_text="Archived documents are excluded from Eva's active retrieval",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["category", "sync_status"]),
            models.Index(fields=["sharepoint_item_id"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_category_display()})"


class KnowledgeChunk(models.Model):
    """A text chunk from a Knowledge Brain document with vector embedding."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    chunk_index = models.IntegerField(
        help_text="Position of this chunk within the document"
    )
    text = models.TextField(help_text="Raw text of this chunk (~512 tokens)")
    # Embedding stored as JSON array of floats (1536 dimensions).
    # When pgvector is installed, migrate to VectorField for similarity search.
    # For now, use JSONField for compatibility without pgvector extension.
    embedding = models.JSONField(
        default=list, blank=True,
        help_text="Vector embedding (1536 dimensions) as JSON array",
    )
    token_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "chunk_index"]
        indexes = [
            models.Index(fields=["document", "chunk_index"]),
        ]
        unique_together = [("document", "chunk_index")]

    def __str__(self):
        return f"Chunk {self.chunk_index} of {self.document.title}"


# ---------------------------------------------------------------------------
# Eva Chat Models
# ---------------------------------------------------------------------------
class EvaConversation(models.Model):
    """A chat conversation between an accountant and Eva within a financial year."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="eva_conversations"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="eva_conversations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)
    message_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-last_active_at"]
        indexes = [
            models.Index(fields=["financial_year", "user"]),
        ]

    def __str__(self):
        return f"Eva Chat — {self.financial_year} ({self.user})"


class EvaMessage(models.Model):
    """A single message in an Eva chat conversation."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        EvaConversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    model_used = models.CharField(
        max_length=10, blank=True, default="",
        help_text="AI model used: haiku/sonnet/opus (blank for user messages)",
    )
    retrieved_chunk_ids = models.JSONField(
        default=list, blank=True,
        help_text="List of KnowledgeChunk IDs used in this response",
    )
    tokens_used = models.IntegerField(default=0)
    token_count_prompt = models.IntegerField(
        default=0,
        help_text="Number of tokens in the prompt sent to the model",
    )
    token_count_response = models.IntegerField(
        default=0,
        help_text="Number of tokens in the model's response",
    )
    knowledge_chunks_cited = models.ManyToManyField(
        "KnowledgeChunk", blank=True,
        related_name="cited_in_messages",
        help_text="Knowledge Brain chunks cited in this response",
    )

    class InteractionType(models.TextChoices):
        GENERAL = "general", "General"
        TRUST_PLANNING = "trust_planning", "Trust Planning"
        FINALISATION = "finalisation", "Finalisation"

    interaction_type = models.CharField(
        max_length=20,
        choices=InteractionType.choices,
        default=InteractionType.GENERAL,
        help_text="Context of this interaction",
    )
    is_proactive = models.BooleanField(
        default=False,
        help_text="Whether this message was proactively generated by Eva (not in response to a user question)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
        ]

    def __str__(self):
        return f"{self.get_role_display()} message in {self.conversation}"


# ---------------------------------------------------------------------------
# Phase 5 — Eva Trust Tax Planning
# ---------------------------------------------------------------------------
class EvaTrustPlanningSession(models.Model):
    """An interactive trust distribution planning session with Eva."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        EvaConversation, on_delete=models.CASCADE,
        related_name="trust_planning_sessions",
    )
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE,
        related_name="trust_planning_sessions",
    )
    triggered_at = models.DateTimeField(auto_now_add=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="triggered_trust_planning_sessions",
    )
    net_distributable_income = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
    )
    beneficiary_incomes_provided = models.BooleanField(default=False)
    recommended_distribution = models.JSONField(
        default=dict, blank=True,
        help_text="Eva's recommended distribution allocation",
    )
    final_distribution = models.JSONField(
        default=dict, blank=True,
        help_text="User-confirmed final distribution",
    )
    resolution_pre_populated = models.BooleanField(
        default=False,
        help_text="Whether the trustee resolution was pre-populated from this session",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-triggered_at"]

    def __str__(self):
        return f"Trust Planning Session for {self.financial_year}"


# ---------------------------------------------------------------------------
# Phase 6 — Trust Distribution Tab
# ---------------------------------------------------------------------------
class TrustWorkspace(models.Model):
    """Master workspace for the 6-stage trust distribution workflow."""

    class StageStatus(models.TextChoices):
        NOT_STARTED = "not_started", "Not Started"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"

    class RiskRating(models.TextChoices):
        GREEN = "green", "Green"
        AMBER = "amber", "Amber"
        RED = "red", "Red"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.OneToOneField(
        FinancialYear, on_delete=models.CASCADE, related_name="trust_workspace",
    )
    # Stage statuses
    stage_1_status = models.CharField(
        max_length=15, choices=StageStatus.choices, default=StageStatus.NOT_STARTED,
        help_text="Income Calculation",
    )
    stage_2_status = models.CharField(
        max_length=15, choices=StageStatus.choices, default=StageStatus.NOT_STARTED,
        help_text="Beneficiary Profiling",
    )
    stage_3_status = models.CharField(
        max_length=15, choices=StageStatus.choices, default=StageStatus.NOT_STARTED,
        help_text="Distribution Modelling",
    )
    stage_4_status = models.CharField(
        max_length=15, choices=StageStatus.choices, default=StageStatus.NOT_STARTED,
        help_text="Section 100A Assessment",
    )
    stage_5_status = models.CharField(
        max_length=15, choices=StageStatus.choices, default=StageStatus.NOT_STARTED,
        help_text="Trust Elections",
    )
    stage_6_status = models.CharField(
        max_length=15, choices=StageStatus.choices, default=StageStatus.NOT_STARTED,
        help_text="Documents",
    )
    # Financial data
    net_distributable_income = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
    )
    income_streams = models.JSONField(
        default=dict, blank=True,
        help_text="Breakdown: ordinary, cgt_discount, cgt_non_discount, franked_dividends, franking_credits, tax_free",
    )
    confirmed_scenario = models.ForeignKey(
        "DistributionScenario", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    section_100a_overall_risk = models.CharField(
        max_length=10, choices=RiskRating.choices, blank=True, default="",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Trust Workspace for {self.financial_year}"

    def all_stages_completed(self):
        return all([
            self.stage_1_status == self.StageStatus.COMPLETED,
            self.stage_2_status == self.StageStatus.COMPLETED,
            self.stage_3_status == self.StageStatus.COMPLETED,
            self.stage_4_status == self.StageStatus.COMPLETED,
            self.stage_5_status == self.StageStatus.COMPLETED,
            self.stage_6_status == self.StageStatus.COMPLETED,
        ])


class BeneficiaryProfile(models.Model):
    """Tax profile for a beneficiary within a trust distribution workspace."""

    class BeneficiaryType(models.TextChoices):
        ADULT = "adult", "Adult Individual"
        MINOR = "minor", "Minor"
        COMPANY = "company", "Company"
        TRUST = "trust", "Trust"
        SMSF = "smsf", "SMSF"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trust_workspace = models.ForeignKey(
        TrustWorkspace, on_delete=models.CASCADE, related_name="beneficiary_profiles",
    )
    beneficiary = models.ForeignKey(
        "EntityOfficer", on_delete=models.CASCADE,
        related_name="trust_beneficiary_profiles",
    )
    beneficiary_type = models.CharField(
        max_length=10, choices=BeneficiaryType.choices, default=BeneficiaryType.ADULT,
    )
    other_income = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Beneficiary's other taxable income outside this trust",
    )
    marginal_rate = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True,
        help_text="Current marginal tax rate (e.g. 0.3250 for 32.5%)",
    )
    bracket_remaining = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Remaining capacity in current tax bracket",
    )
    franking_surplus = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Excess franking credits available",
    )
    include_in_distribution = models.BooleanField(default=True)
    exclusion_reason = models.TextField(blank=True, default="")
    tax_residency = models.CharField(max_length=20, blank=True, default="AU")

    class Meta:
        unique_together = ["trust_workspace", "beneficiary"]
        ordering = ["beneficiary__full_name"]

    def __str__(self):
        return f"{self.beneficiary} profile in {self.trust_workspace}"


class DistributionScenario(models.Model):
    """A named distribution scenario (up to 3 per workspace)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trust_workspace = models.ForeignKey(
        TrustWorkspace, on_delete=models.CASCADE, related_name="scenarios",
    )
    name = models.CharField(
        max_length=100, default="Scenario 1",
        help_text="User-friendly scenario name",
    )
    allocations = models.JSONField(
        default=dict, blank=True,
        help_text="JSON: {beneficiary_id: {stream: amount, ...}, ...}",
    )
    total_tax = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
    )
    tax_saved_vs_equal = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Tax saved compared to equal distribution",
    )
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.name} — {self.trust_workspace}"


class Section100AAssessment(models.Model):
    """Section 100A reimbursement agreement assessment per beneficiary."""

    class Answer(models.TextChoices):
        YES = "yes", "Yes"
        NO = "no", "No"
        UNSURE = "unsure", "Unsure"

    class RiskRating(models.TextChoices):
        GREEN = "green", "Green"
        AMBER = "amber", "Amber"
        RED = "red", "Red"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trust_workspace = models.ForeignKey(
        TrustWorkspace, on_delete=models.CASCADE, related_name="section_100a_assessments",
    )
    beneficiary = models.ForeignKey(
        "EntityOfficer", on_delete=models.CASCADE,
        related_name="section_100a_assessments",
    )
    q1 = models.CharField(max_length=10, choices=Answer.choices, blank=True, default="",
        help_text="Was the distribution made under a reimbursement agreement?")
    q2 = models.CharField(max_length=10, choices=Answer.choices, blank=True, default="",
        help_text="Did the beneficiary receive the economic benefit?")
    q3 = models.CharField(max_length=10, choices=Answer.choices, blank=True, default="",
        help_text="Was the distribution part of a pre-arranged plan?")
    q4 = models.CharField(max_length=10, choices=Answer.choices, blank=True, default="",
        help_text="Were funds redirected to another party?")
    q5 = models.CharField(max_length=10, choices=Answer.choices, blank=True, default="",
        help_text="Is the beneficiary a related party of the trustee?")
    q6 = models.CharField(max_length=10, choices=Answer.choices, blank=True, default="",
        help_text="Was there a tax benefit from the arrangement?")
    q7 = models.CharField(max_length=10, choices=Answer.choices, blank=True, default="",
        help_text="Is the arrangement consistent with an ordinary family dealing?")
    q8 = models.CharField(max_length=10, choices=Answer.choices, blank=True, default="",
        help_text="Does the arrangement fall within a safe harbour?")
    risk_rating = models.CharField(
        max_length=10, choices=RiskRating.choices, blank=True, default="",
        help_text="Calculated: GREEN (0 indicators), AMBER (1-2), RED (3+ or Q1+Q6 both YES)",
    )
    resolution_strategy = models.TextField(
        blank=True, default="",
        help_text="Mandatory for AMBER/RED ratings",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="reviewed_100a_assessments",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["trust_workspace", "beneficiary"]

    def calculate_risk_rating(self):
        """Calculate risk rating based on answers."""
        yes_count = sum(
            1 for q in [self.q1, self.q2, self.q3, self.q4, self.q5, self.q6, self.q7, self.q8]
            if q == self.Answer.YES
        )
        # Q7 and Q8 are protective — YES is good
        risk_indicators = sum(
            1 for q in [self.q1, self.q2, self.q3, self.q4, self.q5, self.q6]
            if q == self.Answer.YES
        )
        if risk_indicators >= 3 or (self.q1 == self.Answer.YES and self.q6 == self.Answer.YES):
            return self.RiskRating.RED
        elif risk_indicators >= 1:
            return self.RiskRating.AMBER
        return self.RiskRating.GREEN

    def save(self, *args, **kwargs):
        self.risk_rating = self.calculate_risk_rating()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"S100A Assessment: {self.beneficiary} — {self.risk_rating}"


class TrustElectionRecord(models.Model):
    """Family Trust Election (FTE) or Interposed Entity Election (IEE) record."""

    class ElectionType(models.TextChoices):
        FTE = "fte", "Family Trust Election"
        IEE = "iee", "Interposed Entity Election"

    class ElectionStatus(models.TextChoices):
        IN_PLACE = "in_place", "In Place"
        NOT_IN_PLACE = "not_in_place", "Not In Place"
        REQUIRED_NOT_YET_MADE = "required_not_yet_made", "Required — Not Yet Made"
        NOT_APPLICABLE = "not_applicable", "Not Applicable"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trust_workspace = models.ForeignKey(
        TrustWorkspace, on_delete=models.CASCADE, related_name="election_records",
    )
    election_type = models.CharField(
        max_length=5, choices=ElectionType.choices,
    )
    status = models.CharField(
        max_length=25, choices=ElectionStatus.choices, default=ElectionStatus.NOT_APPLICABLE,
    )
    effective_date = models.DateField(null=True, blank=True)
    test_individual = models.ForeignKey(
        "EntityOfficer", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="trust_election_test_individual",
        help_text="The test individual for the FTE",
    )
    related_entity = models.ForeignKey(
        "Entity", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="trust_election_related",
        help_text="Related entity for IEE",
    )
    election_document = models.FileField(
        upload_to="trust_elections/", blank=True,
        help_text="Uploaded election form document",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="confirmed_trust_elections",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["trust_workspace", "election_type"]

    def __str__(self):
        return f"{self.get_election_type_display()} — {self.get_status_display()}"


# ---------------------------------------------------------------------------
# Phase 7 — Governing Documents + OCR
# ---------------------------------------------------------------------------
class GoverningDocument(models.Model):
    """A governing document (trust deed, constitution, etc.) stored at entity level."""

    class DocumentType(models.TextChoices):
        TRUST_DEED = "trust_deed", "Trust Deed"
        COMPANY_CONSTITUTION = "company_constitution", "Company Constitution"
        PARTNERSHIP_AGREEMENT = "partnership_agreement", "Partnership Agreement"
        SMSF_DEED = "smsf_deed", "SMSF Deed"
        AMENDMENT = "amendment", "Amendment"
        SUPPLEMENTARY = "supplementary", "Supplementary Document"

    class ExtractionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        COMPLETED_WITH_WARNINGS = "completed_with_warnings", "Completed with Warnings"
        OCR_PENDING = "ocr_pending", "OCR Pending"
        FAILED = "failed", "Failed"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="governing_documents",
    )
    document_type = models.CharField(
        max_length=30, choices=DocumentType.choices,
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Whether this is the primary operative document",
    )
    file = models.FileField(upload_to="governing_documents/")
    original_filename = models.CharField(max_length=500, blank=True, default="")
    file_size_bytes = models.PositiveIntegerField(default=0)
    document_date = models.DateField(
        null=True, blank=True,
        help_text="Date of the document (execution date)",
    )
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="uploaded_governing_docs",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="archived_governing_docs",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    # OCR / Text extraction
    extracted_text = models.TextField(
        blank=True, default="",
        help_text="Full extracted text from the document",
    )
    extraction_status = models.CharField(
        max_length=30, choices=ExtractionStatus.choices,
        default=ExtractionStatus.PENDING,
    )
    low_confidence_pages = models.JSONField(
        default=list, blank=True,
        help_text="List of page numbers with low OCR confidence",
    )
    textract_job_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="AWS Textract job ID for async OCR processing",
    )

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["entity", "document_type", "status"]),
        ]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.entity}"


# ---------------------------------------------------------------------------
# Phase 8 — Legal Document Generation
# ---------------------------------------------------------------------------
class LegalDocumentTemplate(models.Model):
    """A Word template (.docx) for generating legal/compliance documents."""

    class DocumentType(models.TextChoices):
        # Legal documents
        DIV7A_LOAN_AGREEMENT = "div7a_loan_agreement", "Div 7A Loan Agreement"
        TRUST_DEED_CHANGE_TRUSTEE = "trust_deed_change_trustee", "Trust Deed — Change Trustee"
        TRUST_DEED_ADD_BENEFICIARY = "trust_deed_add_beneficiary", "Trust Deed — Add Beneficiary"
        TRUST_DEED_REMOVE_BENEFICIARY = "trust_deed_remove_beneficiary", "Trust Deed — Remove Beneficiary"
        TRUST_DEED_EXTEND_VESTING = "trust_deed_extend_vesting", "Trust Deed — Extend Vesting"
        TRUST_DEED_UPDATE_DISTRIBUTION = "trust_deed_update_distribution", "Trust Deed — Update Distribution"
        COMPANY_CONSTITUTION = "company_constitution", "Company Constitution"
        COMPANY_CONSTITUTION_SPECIAL = "company_constitution_special", "Company Constitution — Special Purpose"
        COMPANY_ESTABLISHMENT = "company_establishment", "Company Establishment Package"
        DISCRETIONARY_TRUST_DEED = "discretionary_trust_deed", "Discretionary Trust Deed"
        UNIT_TRUST_DEED = "unit_trust_deed", "Unit Trust Deed"
        UNIT_TRUST_DEED_ANCILLARIES = "unit_trust_deed_ancillaries", "Unit Trust Deed \u2014 Ancillary Documents"
        UNIT_TRANSFER = "unit_transfer", "Unit Transfer Package"
        PARTNERSHIP_AGREEMENT = "partnership_agreement", "Partnership Agreement"
        # Compliance documents
        DIVIDEND_STATEMENT = "dividend_statement", "Dividend Statement"
        DIVIDEND_MINUTES = "dividend_minutes", "Dividend Declaration Minutes"
        SOLVENCY_RESOLUTION = "solvency_resolution", "Solvency Resolution"
        DIRECTORS_DECLARATION = "directors_declaration", "Director's Declaration"
        DIRECTORS_DECLARATION_LARGE = "directors_declaration_large", "Director's Declaration — Large Proprietary"
        DIRECTORS_DECLARATION_GP = "directors_declaration_gp", "Director's Declaration — General Purpose"
        DIRECTORS_REPORT = "directors_report", "Director's Report"
        SHAREHOLDER_LOAN_ACK = "shareholder_loan_ack", "Shareholder Loan Acknowledgment"
        PARTNER_STATEMENT = "partner_statement", "Partner Statement"
        PARTNERSHIP_TAX_SUMMARY = "partnership_tax_summary", "Partnership Tax Summary"
        ENGAGEMENT_LETTER = "engagement_letter", "Client Engagement Letter"
        MANAGEMENT_REP_LETTER = "management_rep_letter", "Management Representation Letter"
        MANAGEMENT_REP_LETTER_TRUST = "management_rep_letter_trust", "Management Representation Letter — Trust"
        MANAGEMENT_REP_LETTER_PARTNERSHIP = "management_rep_letter_partnership", "Management Representation Letter — Partnership"
        CLIENT_COVER_LETTER = "client_cover_letter", "Client Cover Letter"
        DISTRIBUTION_MINUTES = "distribution_minutes", "Trust Distribution Minutes"
        SECTION_100A_SUMMARY = "section_100a_summary", "Section 100A Summary"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    document_type = models.CharField(
        max_length=50, choices=DocumentType.choices, unique=True,
    )
    entity_types = models.JSONField(
        default=list, blank=True,
        help_text="List of entity types this template applies to, e.g. ['company', 'trust']",
    )
    template_file = models.FileField(upload_to="legal_templates/")
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    solicitor_approved = models.BooleanField(default=False)
    solicitor_name = models.CharField(max_length=255, blank=True, default="")
    approval_date = models.DateField(null=True, blank=True)
    variable_schema = models.JSONField(
        default=dict, blank=True,
        help_text="JSON schema describing template variables and their types",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_legal_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} v{self.version}"


class LegalDocument(models.Model):
    """A generated legal/compliance document instance."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        GENERATED = "generated", "Generated"
        FINAL = "final", "Final"
        EXECUTED = "executed", "Executed"

    class FuseSignStatus(models.TextChoices):
        NOT_SENT = "not_sent", "Not Sent"
        SENT = "sent", "Sent for Signing"
        SIGNED = "signed", "Signed"
        DECLINED = "declined", "Declined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="legal_documents",
    )
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE,
        null=True, blank=True, related_name="legal_documents",
    )
    template = models.ForeignKey(
        LegalDocumentTemplate, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="generated_documents",
    )
    document_type = models.CharField(
        max_length=50, choices=LegalDocumentTemplate.DocumentType.choices,
    )
    title = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Human-readable document title",
    )
    version = models.PositiveIntegerField(default=1)
    context_data = models.JSONField(
        default=dict, blank=True,
        help_text="Structured context data used for rendering",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT,
    )
    parameters = models.JSONField(
        default=dict, blank=True,
        help_text="Template variable values used for generation",
    )
    generated_file = models.FileField(
        upload_to="legal_documents/", blank=True,
        help_text="Generated .docx file",
    )
    pdf_file = models.FileField(
        upload_to="legal_documents_pdf/", blank=True,
        help_text="PDF version of the generated document",
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="generated_legal_docs",
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    governing_document = models.ForeignKey(
        GoverningDocument, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="generated_legal_docs",
        help_text="Source governing document used for context",
    )
    disclaimer_acknowledged = models.BooleanField(default=False)
    disclaimer_acknowledged_at = models.DateTimeField(null=True, blank=True)
    auto_saved_to_governing_docs = models.BooleanField(
        default=False,
        help_text="Whether this was auto-saved as an amendment to governing docs",
    )
    # FuseSign integration
    fusesign_envelope_id = models.CharField(max_length=255, blank=True, default="")
    fusesign_status = models.CharField(
        max_length=15, choices=FuseSignStatus.choices, default=FuseSignStatus.NOT_SENT,
    )

    class Meta:
        ordering = ["-generated_at"]
        indexes = [
            models.Index(fields=["entity", "document_type"]),
            models.Index(fields=["financial_year", "document_type"]),
        ]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.entity} ({self.get_status_display()})"


# ---------------------------------------------------------------------------
# Phase 10 — Eva Client Summary
# ---------------------------------------------------------------------------
class EvaClientSummary(models.Model):
    """Auto-generated client summary when a financial year is locked."""

    class Format(models.TextChoices):
        BULLET = "bullet", "Bullet Point"
        NARRATIVE = "narrative", "Narrative"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="client_summaries",
    )
    format_type = models.CharField(
        max_length=15, choices=Format.choices, default=Format.BULLET,
    )
    # Five sections
    financial_highlights = models.TextField(blank=True, default="")
    compliance_status = models.TextField(blank=True, default="")
    tax_position = models.TextField(blank=True, default="")
    recommendations = models.TextField(blank=True, default="")
    year_on_year_comparison = models.TextField(blank=True, default="")
    # Full content
    full_content = models.TextField(
        blank=True, default="",
        help_text="Complete rendered summary content",
    )
    version = models.PositiveIntegerField(default=1)
    model_used = models.CharField(max_length=10, blank=True, default="")
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"Client Summary ({self.get_format_type_display()}) — {self.financial_year}"


# ---------------------------------------------------------------------------
# Phase 11 — Company Compliance Documents
# ---------------------------------------------------------------------------
class DividendEvent(models.Model):
    """A dividend declaration event for a company entity."""

    class DividendType(models.TextChoices):
        INTERIM = "interim", "Interim"
        FINAL = "final", "Final"
        SPECIAL = "special", "Special"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="dividend_events",
    )
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="dividend_events",
    )
    dividend_type = models.CharField(
        max_length=10, choices=DividendType.choices,
    )
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    franking_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=100.00,
        help_text="Percentage of dividend that is franked (0-100)",
    )
    company_tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=25.00,
        help_text="Company tax rate used for franking calculations",
    )
    record_date = models.DateField()
    payment_date = models.DateField()
    declaration_date = models.DateField()
    solvency_confirmed = models.BooleanField(
        default=False,
        help_text="Directors have confirmed solvency under s.254T",
    )
    franking_account_opening_balance = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
    )
    franking_account_closing_balance = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
    )
    resolution_type = models.CharField(
        max_length=50, blank=True, default="board_resolution",
        help_text="Type of resolution: board_resolution, circular_resolution",
    )
    meeting_location = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_dividend_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-declaration_date"]

    def __str__(self):
        return f"{self.get_dividend_type_display()} Dividend — {self.entity} ({self.declaration_date})"

    @property
    def franking_credit_per_dollar(self):
        """Calculate franking credit per dollar of dividend."""
        if self.company_tax_rate and self.franking_percentage:
            rate = self.company_tax_rate / 100
            return (rate / (1 - rate)) * (self.franking_percentage / 100)
        return 0


class DividendShareholderAllocation(models.Model):
    """Allocation of a dividend to an individual shareholder."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dividend_event = models.ForeignKey(
        DividendEvent, on_delete=models.CASCADE, related_name="allocations",
    )
    shareholder = models.ForeignKey(
        "EntityOfficer", on_delete=models.CASCADE,
        related_name="dividend_allocations",
    )
    shares_held = models.PositiveIntegerField(default=0)
    dividend_amount = models.DecimalField(max_digits=15, decimal_places=2)
    franking_credit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    withholding_tax = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Withholding tax for non-resident shareholders",
    )
    dividend_statement = models.ForeignKey(
        LegalDocument, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="dividend_allocations",
        help_text="Generated dividend statement document",
    )

    class Meta:
        unique_together = ["dividend_event", "shareholder"]

    def __str__(self):
        return f"{self.shareholder} — ${self.dividend_amount}"


# ---------------------------------------------------------------------------
# Franking Account Ledger
# ---------------------------------------------------------------------------
class FrankingAccountEntry(models.Model):
    """A single movement in a company entity's franking account ledger.

    Credits increase the balance (tax payments add franking credits).
    Debits decrease the balance (franking debits when paying franked dividends).
    Running balance is calculated at query time, not stored.
    """

    ENTRY_TYPE_CHOICES = [
        ("PAYMENT_OF_TAX", "Payment of Tax"),
        ("PAYG_INSTALMENT", "PAYG Instalment"),
        ("REFUND_OF_TAX", "Refund of Income Tax"),
        ("FRANKING_DEBIT_DIVIDEND", "Franking Debit — Dividend Paid"),
        ("FRANKING_CREDIT_RECEIVED", "Franking Credit Received"),
        ("OPENING_BALANCE", "Opening Balance"),
        ("OTHER_CREDIT", "Other Credit"),
        ("OTHER_DEBIT", "Other Debit"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        "Entity", on_delete=models.CASCADE, related_name="franking_entries",
    )
    financial_year = models.ForeignKey(
        "FinancialYear", on_delete=models.CASCADE, related_name="franking_entries",
    )
    date = models.DateField()
    description = models.CharField(max_length=255)
    entry_type = models.CharField(max_length=40, choices=ENTRY_TYPE_CHOICES)
    debit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    credit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["date", "sort_order", "created_at"]

    def __str__(self):
        amt = self.credit if self.credit else self.debit
        direction = "CR" if self.credit else "DR"
        return f"{self.date} — {self.description} (${amt} {direction})"


# ---------------------------------------------------------------------------
# Phase 12 — Engagement Letter Config
# ---------------------------------------------------------------------------
class EngagementLetterConfig(models.Model):
    """Entity-level engagement letter configuration (APES 305 compliant)."""

    class FeeBasis(models.TextChoices):
        FIXED = "fixed", "Fixed Fee"
        HOURLY = "hourly", "Hourly Rate"
        VALUE_BASED = "value_based", "Value-Based"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.OneToOneField(
        Entity, on_delete=models.CASCADE, related_name="engagement_letter_config",
    )
    services_engaged = models.JSONField(
        default=list, blank=True,
        help_text="List of services: ['tax_return', 'financial_statements', 'bas', ...]",
    )
    fee_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
    fee_basis = models.CharField(
        max_length=15, choices=FeeBasis.choices, default=FeeBasis.FIXED,
    )
    additional_terms = models.TextField(blank=True, default="")
    last_generated_fy = models.ForeignKey(
        FinancialYear, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Engagement Config — {self.entity}"



# ---------------------------------------------------------------------------
# BAS Period Commentary
# ---------------------------------------------------------------------------
class BASPeriodCommentary(models.Model):
    """AI-generated period commentary for a BAS period, transforming compliance
    data into client-ready advisory insights."""

    class Status(models.TextChoices):
        GENERATING = "generating", "Generating"
        DRAFT = "draft", "Draft"
        REVIEWED = "reviewed", "Reviewed"
        SENT = "sent", "Sent to Client"
        ERROR = "error", "Error"

    class Tone(models.TextChoices):
        PROFESSIONAL = "professional", "Professional"
        CONVERSATIONAL = "conversational", "Conversational"
        TECHNICAL = "technical", "Technical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.CASCADE, related_name="bas_commentaries",
    )
    bas_period = models.ForeignKey(
        BASPeriod, on_delete=models.CASCADE, related_name="commentaries",
        null=True, blank=True,
        help_text="Specific BAS period this commentary covers. Null for custom date ranges.",
    )
    # Period scoping
    period_start = models.DateField(
        help_text="Start date of the commentary period",
    )
    period_end = models.DateField(
        help_text="End date of the commentary period",
    )
    period_label = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Human-readable period label, e.g. 'Q1 (Jul-Sep 2025)'",
    )

    # Five-section commentary content
    section_snapshot = models.TextField(
        blank=True, default="",
        help_text="Section 1: Period Snapshot (2-3 sentences, revenue headline)",
    )
    section_revenue = models.TextField(
        blank=True, default="",
        help_text="Section 2: Revenue Analysis (2-3 significant movements)",
    )
    section_costs = models.TextField(
        blank=True, default="",
        help_text="Section 3: Cost & Margin Analysis (gross margin, opex trends)",
    )
    section_watch_items = models.TextField(
        blank=True, default="",
        help_text="Section 4: Items to Watch (max 4 items, plain English)",
    )
    section_actions = models.TextField(
        blank=True, default="",
        help_text="Section 5: Recommended Actions (1-3 specific actions)",
    )
    # Full rendered content
    full_content = models.TextField(
        blank=True, default="",
        help_text="Complete rendered commentary content (all sections combined)",
    )

    # Metadata
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.GENERATING,
    )
    tone = models.CharField(
        max_length=15, choices=Tone.choices, default=Tone.PROFESSIONAL,
    )
    version = models.PositiveIntegerField(default=1)
    model_used = models.CharField(
        max_length=10, blank=True, default="",
        help_text="AI model tier used: haiku/sonnet/opus",
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="generated_bas_commentaries",
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="reviewed_bas_commentaries",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_to_email = models.EmailField(blank=True, default="")

    # Context data snapshot for audit trail
    context_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text="Snapshot of the financial data used to generate this commentary",
    )
    # Error tracking
    error_message = models.TextField(blank=True, default="")

    # Celery task tracking (replaces in-memory _commentary_tasks dict)
    celery_task_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Celery task ID for tracking generation progress",
    )
    generation_started_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the background generation task started executing",
    )
    generation_completed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the background generation task finished (success or failure)",
    )
    generation_step = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Current step description for progress polling",
    )

    # Trend chaining — link to the prior period's commentary for comparison
    prior_commentary = models.ForeignKey(
        "self", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="subsequent_commentaries",
        help_text="Link to the prior period's commentary for trend chaining and comparison",
    )

    # Link to generated document
    legal_document = models.ForeignKey(
        LegalDocument, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="bas_commentaries",
        help_text="Generated Word document for this commentary",
    )

    class Meta:
        ordering = ["-generated_at"]
        verbose_name = "BAS Period Commentary"
        verbose_name_plural = "BAS Period Commentaries"
        indexes = [
            models.Index(fields=["financial_year", "status"]),
            models.Index(fields=["bas_period"]),
        ]

    def __str__(self):
        return f"BAS Commentary — {self.period_label or 'Custom'} ({self.get_status_display()})"

    @property
    def is_editable(self):
        return self.status in (self.Status.DRAFT, self.Status.REVIEWED)

    @property
    def section_count(self):
        """Number of non-empty sections."""
        count = 0
        for field in [self.section_snapshot, self.section_revenue, self.section_costs,
                      self.section_watch_items, self.section_actions]:
            if field and field.strip():
                count += 1
        return count



# ---------------------------------------------------------------------------
# Division 7A Detection Module — Div7AAssessment
# ---------------------------------------------------------------------------
class Div7AAssessment(models.Model):
    """
    One record per entity per financial year, created by the div7a_assessment
    Celery task. Stores the full Div 7A position: direct loan exposure, UPE
    exposure, s 109E payments, compliance status, and links to the
    consolidated EvaFinding card.
    """

    class OverallSeverity(models.TextChoices):
        CRITICAL = "CRITICAL", "Critical"
        ADVISORY = "ADVISORY", "Advisory"
        CLEAR = "CLEAR", "Clear"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.OneToOneField(
        FinancialYear, on_delete=models.CASCADE,
        related_name="div7a_assessment",
        help_text="Unique per FY — one Div 7A assessment per entity per year",
    )
    assessed_at = models.DateTimeField(
        auto_now=True,
        help_text="When the assessment last ran",
    )

    # --- Position Detection (Category A) ---
    direct_loan_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total debit balance across director/shareholder loan accounts",
    )
    direct_loan_accounts = models.JSONField(
        default=list, blank=True,
        help_text="Array of {account_code, account_name, balance, py_balance}",
    )
    upe_exposure = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total UPE amount from related trusts",
    )
    upe_details = models.JSONField(
        default=list, blank=True,
        help_text="Array of {trust_entity_id, trust_name, upe_amount, distribution_date, regime}",
    )
    s109e_payments = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total s 109E payments detected",
    )
    s109e_details = models.JSONField(
        default=list, blank=True,
        help_text="Array of {payee, amount, account_code, description}",
    )
    total_exposure = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="direct_loan_balance + upe_exposure + s109e_payments",
    )

    # --- Compliance Verification (Category B) ---
    has_complying_agreement = models.BooleanField(
        default=False,
        help_text="True if valid LegalDocument exists covering balance",
    )
    agreement_covers_balance = models.BooleanField(
        default=False,
        help_text="True if agreement.loan_amount >= total direct balance",
    )
    expected_interest = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Calculated benchmark interest for the year",
    )
    recorded_interest = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Actual interest income found in P&L",
    )
    interest_compliant = models.BooleanField(
        default=False,
        help_text="recorded_interest >= expected_interest * 0.95",
    )
    expected_myr = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Calculated minimum yearly repayment",
    )
    actual_repayments = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Credits on loan account during FY",
    )
    myr_compliant = models.BooleanField(
        null=True, blank=True,
        help_text="True if actual_repayments >= expected_myr",
    )

    # --- Escalation & Severity ---
    escalation_required = models.BooleanField(
        default=False,
        help_text="True if total_exposure > 200000",
    )
    rules_fired = models.JSONField(
        default=list, blank=True,
        help_text="Array of triggered rule IDs e.g. ['T2-D7A-01', 'T2-D7A-04']",
    )
    overall_severity = models.CharField(
        max_length=10, choices=OverallSeverity.choices,
        default=OverallSeverity.CLEAR,
    )

    # --- Link to consolidated finding card ---
    eva_finding = models.ForeignKey(
        EvaFinding, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="div7a_assessments",
        help_text="Link to consolidated finding card",
    )

    class Meta:
        ordering = ["-assessed_at"]
        verbose_name = "Div 7A Assessment"
        verbose_name_plural = "Div 7A Assessments"
        indexes = [
            models.Index(fields=["financial_year", "overall_severity"]),
        ]

    def __str__(self):
        return (
            f"Div 7A Assessment — {self.financial_year} "
            f"({self.get_overall_severity_display()})"
        )


# ---------------------------------------------------------------------------
# Division 7A Detection Module — Div7ACompliance
# ---------------------------------------------------------------------------
class Div7ACompliance(models.Model):
    """
    Tracks compliance status of each Div 7A loan arrangement.
    One record per loan per entity.
    """

    class ComplianceStatus(models.TextChoices):
        COMPLIANT = "COMPLIANT", "Compliant"
        NON_COMPLIANT = "NON_COMPLIANT", "Non-Compliant"
        EXPIRED = "EXPIRED", "Expired"
        PENDING = "PENDING", "Pending"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE,
        related_name="div7a_compliance_records",
    )
    borrower_name = models.CharField(
        max_length=255,
        help_text="Shareholder/associate/trust borrower name",
    )
    borrower_entity = models.ForeignKey(
        Entity, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="div7a_as_borrower",
        help_text="If borrower is another StatementHub entity",
    )
    loan_amount = models.DecimalField(
        max_digits=15, decimal_places=2,
        help_text="Original loan amount covered by agreement",
    )
    loan_start_date = models.DateField(
        help_text="Commencement date of complying agreement",
    )
    loan_start_year = models.IntegerField(
        help_text="FY loan commenced (e.g. 2024)",
    )
    loan_term = models.IntegerField(
        default=7,
        help_text="7 (unsecured) or 25 (secured)",
    )
    is_secured = models.BooleanField(
        default=False,
        help_text="True if secured over real property",
    )
    agreement_document = models.ForeignKey(
        LegalDocument, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="div7a_compliance_records",
    )
    status = models.CharField(
        max_length=15, choices=ComplianceStatus.choices,
        default=ComplianceStatus.PENDING,
    )
    last_reviewed = models.DateTimeField(
        auto_now=True,
        help_text="Last review date",
    )
    notes = models.TextField(
        blank=True, default="",
        help_text="Accountant notes",
    )

    class Meta:
        ordering = ["entity", "-loan_start_date"]
        verbose_name = "Div 7A Compliance Record"
        verbose_name_plural = "Div 7A Compliance Records"
        indexes = [
            models.Index(fields=["entity", "status"]),
        ]

    def __str__(self):
        return (
            f"Div 7A Loan — {self.borrower_name} "
            f"(${self.loan_amount:,.2f}, {self.get_status_display()})"
        )


# ---------------------------------------------------------------------------
# Going Concern Assessment
# ---------------------------------------------------------------------------
class GoingConcernAssessment(models.Model):
    """
    Consolidated going concern assessment per entity per financial year.
    Produced by the Going Concern detection module (core.risk_modules.going_concern).
    One record per entity per FY — mirrors the Div7AAssessment pattern.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.OneToOneField(
        FinancialYear, on_delete=models.CASCADE,
        related_name="going_concern_assessment",
    )
    assessed_at = models.DateTimeField(auto_now=True)

    # Financial position
    net_assets = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Total assets minus total liabilities",
    )
    cash_position = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Net cash/bank balance (including overdraft)",
    )
    cy_revenue = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Current year total revenue",
    )
    py_revenue = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Prior year total revenue",
    )
    revenue_decline_pct = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        help_text="Percentage decline (null if PY revenue = 0)",
    )
    cy_net_result = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="CY profit/loss",
    )
    py_net_result = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="PY profit/loss",
    )
    working_capital_ratio = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        help_text="Current assets / current liabilities (null if uncomputable)",
    )
    director_loan_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Net director loan debit (0 if credit)",
    )
    director_extraction_pct = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        help_text="Director loan / revenue percentage",
    )
    is_reliant_on_director = models.BooleanField(
        default=False,
        help_text="True if cash < 0 but director loan credit is funding operations",
    )
    is_startup = models.BooleanField(
        default=False,
        help_text="True if entity has < 2 years of financial data",
    )

    # Assessment results
    rules_fired = models.JSONField(
        default=list, blank=True,
        help_text="Array of rule IDs that triggered (GC-01 through GC-06)",
    )
    overall_severity = models.CharField(
        max_length=20, default="CLEAR",
        help_text="CRITICAL / ADVISORY / CLEAR",
    )
    eva_finding = models.ForeignKey(
        EvaFinding, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="going_concern_assessments",
        help_text="Link to consolidated finding card",
    )

    class Meta:
        verbose_name = "Going Concern Assessment"
        verbose_name_plural = "Going Concern Assessments"
        ordering = ["-assessed_at"]

    def __str__(self):
        return (
            f"Going Concern — {self.financial_year.entity.entity_name} "
            f"{self.financial_year.year_label}: {self.overall_severity}"
        )


# ---------------------------------------------------------------------------
# Work Paper Templates
# ---------------------------------------------------------------------------
class WorkPaperTemplate(models.Model):
    """
    An in-house work paper template (Excel or Word) that can be downloaded
    with entity name, ABN, and financial year pre-filled.

    Admins upload templates via the Django admin.  When an accountant clicks
    "Download" from the Work Papers tab the system opens the file, substitutes
    the merge fields, and streams the modified file — nothing is saved back to
    the platform.

    Supported merge fields (Excel named ranges or Word {{placeholders}}):
      - entity_name
      - abn
      - financial_year   (e.g. "FY2025")
      - fy_start_date    (e.g. "1 July 2024")
      - fy_end_date      (e.g. "30 June 2025")
    """

    class Category(models.TextChoices):
        BAS_RECONCILIATION = "bas_reconciliation", "BAS Reconciliation"
        JOURNAL_WORKPAPER = "journal_workpaper", "Journal Work Paper"
        ACCOUNT_RECONCILIATION = "account_reconciliation", "Account Reconciliation"
        DIVISION_7A = "division_7a", "Division 7A"
        LOAN_ACCOUNT = "loan_account", "Loan Account Schedule"
        DEPRECIATION = "depreciation", "Depreciation Schedule"
        STOCK_TAKE = "stock_take", "Stock Take / Inventory"
        PAYROLL = "payroll", "Payroll Reconciliation"
        TRUST_DISTRIBUTION = "trust_distribution", "Trust Distribution Work Paper"
        GENERAL = "general", "General"

    class FileFormat(models.TextChoices):
        XLSX = "xlsx", "Excel (.xlsx)"
        DOCX = "docx", "Word (.docx)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255,
        help_text="Human-readable name, e.g. \"BAS Reconciliation — Quarterly\"",
    )
    category = models.CharField(
        max_length=30,
        choices=Category.choices,
        default=Category.GENERAL,
    )
    description = models.TextField(
        blank=True,
        help_text="Brief description of what this work paper is used for.",
    )
    template_file = models.FileField(
        upload_to="workpaper_templates/",
        help_text="Upload the Excel (.xlsx) or Word (.docx) template file.",
    )
    file_format = models.CharField(
        max_length=4,
        choices=FileFormat.choices,
        default=FileFormat.XLSX,
        help_text="File format of the uploaded template.",
    )
    # Optional: restrict to specific entity types (blank = all)
    entity_types = models.JSONField(
        default=list,
        blank=True,
        help_text="List of entity types this template applies to (leave empty for all).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only active templates are shown in the Work Papers tab.",
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Controls display order within a category (lower = first).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_workpaper_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "sort_order", "name"]
        verbose_name = "Work Paper Template"
        verbose_name_plural = "Work Paper Templates"

    def __str__(self):
        return f"{self.get_category_display()} — {self.name}"


from .models_office_admin import *  # noqa: F401, F403

