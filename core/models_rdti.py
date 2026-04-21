"""
R&D Tax Incentive (RDTI) Drafter — Data Models
Spec: R&DTI Drafter MVP v0.3 (Elio Scarton, MC & S Accountants, April 2026)

Hierarchy mirrors the actual AusIndustry application structure:
  FinancialYear (existing)
  └── RdtiApplication (one per FY per entity)
      └── RdtiProject (one project may span multiple FYs)
          ├── RdtiCoreActivity (belongs to project + application)
          │   ├── RdtiSupportingActivity (belongs to core activity)
          │   └── RdtiExpenditureYear (5-year breakdown)
          └── RdtiDraftVersion (versioned per narrative field, polymorphic)
          └── RdtiFlag (compliance flags per field)
"""
import uuid
from django.db import models
from django.conf import settings


# ---------------------------------------------------------------------------
# ANZSIC / ANZSRC code choices (abbreviated — full lists via fixtures)
# ---------------------------------------------------------------------------

ANZSIC_DIVISIONS = [
    ("A", "A — Agriculture, Forestry and Fishing"),
    ("B", "B — Mining"),
    ("C", "C — Manufacturing"),
    ("D", "D — Electricity, Gas, Water and Waste Services"),
    ("E", "E — Construction"),
    ("F", "F — Wholesale Trade"),
    ("G", "G — Retail Trade"),
    ("H", "H — Accommodation and Food Services"),
    ("I", "I — Transport, Postal and Warehousing"),
    ("J", "J — Information Media and Telecommunications"),
    ("K", "K — Financial and Insurance Services"),
    ("L", "L — Rental, Hiring and Real Estate Services"),
    ("M", "M — Professional, Scientific and Technical Services"),
    ("N", "N — Administrative and Support Services"),
    ("O", "O — Public Administration and Safety"),
    ("P", "P — Education and Training"),
    ("Q", "Q — Health Care and Social Assistance"),
    ("R", "R — Arts and Recreation Services"),
    ("S", "S — Other Services"),
]

ANZSRC_DIVISIONS = [
    ("01", "01 — Mathematical Sciences"),
    ("02", "02 — Physical Sciences"),
    ("03", "03 — Chemical Sciences"),
    ("04", "04 — Earth Sciences"),
    ("05", "05 — Environmental Sciences"),
    ("06", "06 — Biological Sciences"),
    ("07", "07 — Agricultural and Veterinary Sciences"),
    ("08", "08 — Information and Computing Sciences"),
    ("09", "09 — Engineering"),
    ("10", "10 — Technology"),
    ("11", "11 — Medical and Health Sciences"),
    ("12", "12 — Built Environment and Design"),
    ("13", "13 — Education"),
    ("14", "14 — Economics"),
    ("15", "15 — Commerce, Management, Tourism and Services"),
    ("16", "16 — Studies in Human Society"),
    ("17", "17 — Psychology and Cognitive Sciences"),
    ("18", "18 — Law and Legal Studies"),
    ("19", "19 — Studies in Creative Arts and Writing"),
    ("20", "20 — Language, Communication and Culture"),
    ("21", "21 — History and Archaeology"),
    ("22", "22 — Philosophy and Religious Studies"),
]


# ---------------------------------------------------------------------------
# RdtiApplication — one per FinancialYear per entity
# ---------------------------------------------------------------------------

class RdtiApplication(models.Model):
    """Top-level R&D Tax Incentive application for a given financial year."""

    class Status(models.TextChoices):
        INTAKE = "intake", "Intake in Progress"
        DRAFTING = "drafting", "Drafting"
        REVIEW = "review", "Under Review"
        READY = "ready", "Ready to Lodge"
        LODGED = "lodged", "Lodged"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.OneToOneField(
        "core.FinancialYear",
        on_delete=models.CASCADE,
        related_name="rdti_application",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.INTAKE
    )

    # Structured fields — entity / company level
    abn = models.CharField(max_length=11, blank=True, verbose_name="ABN")
    acn = models.CharField(max_length=9, blank=True, verbose_name="ACN")
    company_name = models.CharField(max_length=255, blank=True)
    contact_name = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    aggregated_turnover = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Aggregated turnover for the income year (determines refundable vs non-refundable offset)",
    )
    employee_count = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Number of employees (FTE) during the R&D period",
    )
    anzsic_division = models.CharField(
        max_length=2, choices=ANZSIC_DIVISIONS, blank=True,
        verbose_name="ANZSIC Division",
    )
    anzsic_code = models.CharField(
        max_length=10, blank=True,
        help_text="Full 4-digit ANZSIC code",
    )

    # Beneficiary confirmation
    ip_owned_by_entity = models.BooleanField(
        null=True, blank=True,
        help_text="IP arising from R&D is owned by the R&D entity",
    )
    entity_bears_financial_burden = models.BooleanField(
        null=True, blank=True,
        help_text="The R&D entity bears the financial burden of the R&D activities",
    )
    entity_controls_activities = models.BooleanField(
        null=True, blank=True,
        help_text="The R&D entity controls the R&D activities",
    )

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rdti_applications_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    lodged_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, help_text="Internal consultant notes")

    class Meta:
        verbose_name = "RDTI Application"
        verbose_name_plural = "RDTI Applications"
        ordering = ["-created_at"]

    def __str__(self):
        return f"RDTI {self.financial_year.year_label} — {self.financial_year.entity.entity_name}"

    @property
    def is_refundable(self):
        """Returns True if the entity qualifies for the refundable offset (turnover < $20M)."""
        if self.aggregated_turnover is None:
            return None
        return self.aggregated_turnover < 20_000_000

    @property
    def flag_counts(self):
        """Returns dict of red/amber/green flag counts across all activities."""
        flags = RdtiFlag.objects.filter(application=self)
        return {
            "red": flags.filter(severity="red").count(),
            "amber": flags.filter(severity="amber").count(),
            "green": flags.filter(severity="green").count(),
        }


# ---------------------------------------------------------------------------
# RdtiProject — one project may span multiple FYs
# ---------------------------------------------------------------------------

class RdtiProject(models.Model):
    """A research project. One project may contain multiple core activities."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(
        RdtiApplication, on_delete=models.CASCADE, related_name="projects"
    )

    # Project identification
    project_title = models.CharField(max_length=255)
    project_start_date = models.DateField(null=True, blank=True)
    project_end_date = models.DateField(
        null=True, blank=True,
        help_text="Expected end date (may be in a future FY)",
    )
    anzsrc_division = models.CharField(
        max_length=2, choices=ANZSRC_DIVISIONS, blank=True,
        verbose_name="ANZSRC Division (Field of Research)",
    )
    anzsrc_code = models.CharField(
        max_length=10, blank=True,
        help_text="Full 6-digit ANZSRC code",
    )

    # Project-level narrative fields (4,000 chars each)
    objectives = models.TextField(
        blank=True,
        help_text="Project objectives (AusIndustry field, 4,000 char limit)",
    )
    documents_kept = models.TextField(
        blank=True,
        help_text="Documents kept to demonstrate R&D activities (4,000 char limit)",
    )
    plant_and_facilities = models.TextField(
        blank=True,
        help_text="Plant and facilities used in the R&D activities (4,000 char limit)",
    )
    beneficiary_description = models.TextField(
        blank=True,
        help_text="Beneficiary description: IP ownership, control, financial burden (4,000 char limit)",
    )

    # Intake raw answers (Phase 1 intake)
    intake_business_problem = models.TextField(blank=True)
    intake_existing_knowledge = models.TextField(blank=True)
    intake_uncertainty = models.TextField(blank=True)
    intake_who_could_have_known = models.TextField(blank=True)
    intake_expenditure_estimate = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "RDTI Project"
        verbose_name_plural = "RDTI Projects"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.project_title} ({self.application.financial_year.year_label})"


# ---------------------------------------------------------------------------
# RdtiCoreActivity — the heart of the application
# ---------------------------------------------------------------------------

class RdtiCoreActivity(models.Model):
    """
    A Core R&D Activity. Each has 8 narrative fields (4,000 chars each)
    plus structured fields (dates, evidence checklist, expenditure breakdown).
    """

    class PerformedBy(models.TextChoices):
        ENTITY = "entity", "The R&D entity itself"
        ON_BEHALF = "on_behalf", "On behalf of another entity"
        JOINTLY = "jointly", "Jointly with another entity"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        RdtiProject, on_delete=models.CASCADE, related_name="core_activities"
    )
    application = models.ForeignKey(
        RdtiApplication, on_delete=models.CASCADE, related_name="core_activities"
    )

    # Activity identification
    activity_title = models.CharField(max_length=255)
    activity_start_date = models.DateField(null=True, blank=True)
    activity_end_date = models.DateField(null=True, blank=True)
    performed_by = models.CharField(
        max_length=20, choices=PerformedBy.choices,
        default=PerformedBy.ENTITY,
    )

    # The 8 narrative fields (each 4,000 char limit)
    description = models.TextField(
        blank=True,
        help_text="Description of core R&D activity (4,000 char limit)",
    )
    outcome_not_known_in_advance = models.TextField(
        blank=True,
        help_text="How the company determined the outcome could not be known in advance — sources investigated (4,000 char limit)",
    )
    competent_professional = models.TextField(
        blank=True,
        help_text="Why a competent professional could not have known or determined the outcome (4,000 char limit)",
    )
    hypothesis = models.TextField(
        blank=True,
        help_text="Hypothesis (overarching + sub-hypotheses) (4,000 char limit)",
    )
    experiment = models.TextField(
        blank=True,
        help_text="Experiment and how it tested the hypothesis (4,000 char limit)",
    )
    evaluation_method = models.TextField(
        blank=True,
        help_text="Evaluation method — metrics and criteria (4,000 char limit)",
    )
    conclusions = models.TextField(
        blank=True,
        help_text="Conclusions — findings including what didn't work (4,000 char limit)",
    )
    new_knowledge = models.TextField(
        blank=True,
        help_text="New knowledge produced — domain-level, not project-level (4,000 char limit)",
    )

    # Evidence checklist (multi-select stored as comma-separated)
    EVIDENCE_CHOICES = [
        ("lab_notebooks", "Laboratory notebooks / research diaries"),
        ("project_plans", "Project plans and progress reports"),
        ("technical_reports", "Technical reports and analysis"),
        ("test_results", "Test results and experimental data"),
        ("meeting_minutes", "Meeting minutes and correspondence"),
        ("financial_records", "Financial records and invoices"),
        ("ip_records", "IP records (patents, design registrations)"),
        ("software_commits", "Software version control / commit history"),
        ("photographs", "Photographs or video evidence"),
        ("third_party_reports", "Third-party expert reports"),
    ]
    evidence_kept = models.JSONField(
        default=list, blank=True,
        help_text="List of evidence types kept for this activity",
    )

    # Sources investigated (multi-select)
    SOURCES_CHOICES = [
        ("academic_literature", "Academic literature / journals"),
        ("patent_databases", "Patent databases"),
        ("industry_reports", "Industry reports and standards"),
        ("expert_consultation", "Expert consultation"),
        ("prior_internal_work", "Prior internal R&D work"),
        ("commercial_products", "Commercial products / solutions investigated"),
        ("government_databases", "Government / regulatory databases"),
        ("conference_proceedings", "Conference proceedings"),
    ]
    sources_investigated = models.JSONField(
        default=list, blank=True,
        help_text="List of sources investigated before commencing the activity",
    )

    # Intake raw answers (Phase 2 intake)
    intake_technical_question = models.TextField(blank=True)
    intake_prior_search = models.TextField(blank=True)
    intake_why_unpredictable = models.TextField(blank=True)
    intake_hypothesis_raw = models.TextField(blank=True)
    intake_experiments_run = models.TextField(blank=True)
    intake_measurement = models.TextField(blank=True)
    intake_learnings = models.TextField(blank=True)
    intake_records_kept = models.TextField(blank=True)

    # Draft status tracking
    draft_complete = models.BooleanField(
        default=False,
        help_text="All 8 narrative fields have been drafted and are within character limits",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "RDTI Core Activity"
        verbose_name_plural = "RDTI Core Activities"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.activity_title} — {self.project.project_title}"

    def get_narrative_fields(self):
        """Return list of (field_name, label, value) tuples for all 8 narrative fields."""
        return [
            ("description", "Description of Core R&D Activity", self.description),
            ("outcome_not_known_in_advance", "How Outcome Could Not Be Known in Advance", self.outcome_not_known_in_advance),
            ("competent_professional", "Why a Competent Professional Could Not Have Known", self.competent_professional),
            ("hypothesis", "Hypothesis", self.hypothesis),
            ("experiment", "Experiment", self.experiment),
            ("evaluation_method", "Evaluation Method", self.evaluation_method),
            ("conclusions", "Conclusions", self.conclusions),
            ("new_knowledge", "New Knowledge Produced", self.new_knowledge),
        ]

    @property
    def char_counts(self):
        """Returns dict of field_name -> character count for all narrative fields."""
        fields = self.get_narrative_fields()
        return {name: len(value) for name, label, value in fields}

    @property
    def fields_over_limit(self):
        """Returns list of field names that exceed 4,000 characters."""
        return [name for name, count in self.char_counts.items() if count > 4000]


# ---------------------------------------------------------------------------
# RdtiSupportingActivity
# ---------------------------------------------------------------------------

class RdtiSupportingActivity(models.Model):
    """A Supporting Activity that directly supports a Core Activity."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    core_activity = models.ForeignKey(
        RdtiCoreActivity, on_delete=models.CASCADE, related_name="supporting_activities"
    )
    application = models.ForeignKey(
        RdtiApplication, on_delete=models.CASCADE, related_name="supporting_activities"
    )

    activity_title = models.CharField(max_length=255)
    description = models.TextField(
        blank=True,
        help_text="Description of supporting activity (4,000 char limit)",
    )
    direct_relation = models.TextField(
        blank=True,
        help_text="How this activity directly relates to the core activity (4,000 char limit)",
    )

    # Intake raw answers
    intake_description = models.TextField(blank=True)
    intake_relation = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "RDTI Supporting Activity"
        verbose_name_plural = "RDTI Supporting Activities"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.activity_title} (supports: {self.core_activity.activity_title})"


# ---------------------------------------------------------------------------
# RdtiExpenditureYear — 5-year breakdown per core activity
# ---------------------------------------------------------------------------

class RdtiExpenditureYear(models.Model):
    """Annual expenditure breakdown for a Core Activity (up to 5 years)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    core_activity = models.ForeignKey(
        RdtiCoreActivity, on_delete=models.CASCADE, related_name="expenditure_years"
    )
    financial_year_label = models.CharField(
        max_length=20,
        help_text="e.g. FY2025",
    )
    labour_expenditure = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    contractor_expenditure = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    overhead_expenditure = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    other_expenditure = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    class Meta:
        ordering = ["financial_year_label"]
        unique_together = [["core_activity", "financial_year_label"]]

    def __str__(self):
        return f"{self.core_activity.activity_title} — {self.financial_year_label}"

    @property
    def total(self):
        total = 0
        for f in ["labour_expenditure", "contractor_expenditure", "overhead_expenditure", "other_expenditure"]:
            val = getattr(self, f)
            if val:
                total += val
        return total


# ---------------------------------------------------------------------------
# RdtiDraftVersion — versioned history per narrative field
# ---------------------------------------------------------------------------

class RdtiDraftVersion(models.Model):
    """
    Versioned draft for any narrative field.
    Polymorphic: links to either a CoreActivity or SupportingActivity or Project.
    """

    class TargetType(models.TextChoices):
        PROJECT = "project", "Project"
        CORE_ACTIVITY = "core_activity", "Core Activity"
        SUPPORTING_ACTIVITY = "supporting_activity", "Supporting Activity"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(
        RdtiApplication, on_delete=models.CASCADE, related_name="draft_versions"
    )
    target_type = models.CharField(max_length=30, choices=TargetType.choices)
    target_id = models.UUIDField(help_text="UUID of the target object")
    field_name = models.CharField(
        max_length=100,
        help_text="Name of the narrative field (e.g. 'hypothesis', 'new_knowledge')",
    )
    version_number = models.PositiveIntegerField(default=1)
    content = models.TextField()
    char_count = models.PositiveIntegerField(default=0)
    prompt_version = models.CharField(
        max_length=50, blank=True,
        help_text="Version identifier of the prompt used to generate this draft",
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rdti_drafts_generated",
    )
    is_current = models.BooleanField(
        default=True,
        help_text="Whether this is the currently active version for this field",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "RDTI Draft Version"
        verbose_name_plural = "RDTI Draft Versions"
        ordering = ["-version_number"]

    def __str__(self):
        return f"{self.target_type}/{self.field_name} v{self.version_number}"

    def save(self, *args, **kwargs):
        self.char_count = len(self.content)
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# RdtiFlag — compliance flags per field
# ---------------------------------------------------------------------------

class RdtiFlag(models.Model):
    """
    A compliance flag raised by the validator layer.
    Red = blocks submission-ready status.
    Amber = flag for consultant review.
    Green = field meets quality threshold.
    """

    class Severity(models.TextChoices):
        RED = "red", "Red — Blocks Submission"
        AMBER = "amber", "Amber — Review Required"
        GREEN = "green", "Green — Meets Quality Threshold"

    class FlagType(models.TextChoices):
        HYPOTHESIS_OBJECTIVE = "hypothesis_objective", "Hypothesis reads as objective"
        HYPOTHESIS_NO_MEASURABLE = "hypothesis_no_measurable", "Hypothesis has no measurable outcome"
        EXPERIMENT_NO_HYPOTHESIS = "experiment_no_hypothesis", "Experiment doesn't reference hypothesis"
        NEW_KNOWLEDGE_PROJECT = "new_knowledge_project", "New knowledge describes project delivery, not domain knowledge"
        EXCLUDED_CATEGORY = "excluded_category", "Activity description includes excluded-category language"
        OVER_CHAR_LIMIT = "over_char_limit", "Field exceeds 4,000 character limit"
        MISSING_MANDATORY = "missing_mandatory", "Missing mandatory structured field"
        HYPOTHESIS_NO_SUBSTRUCTURE = "hypothesis_no_substructure", "Hypothesis lacks sub-structure for complex activity"
        COMPETENT_PROF_GENERIC = "competent_prof_generic", "Competent professional section lacks specific technical detail"
        SOURCES_GENERIC = "sources_generic", "Sources investigated section is generic"
        CONCLUSIONS_NO_FAILURES = "conclusions_no_failures", "Conclusions don't mention what didn't work"
        EVALUATION_NO_QUANTITATIVE = "evaluation_no_quantitative", "Evaluation method lacks quantitative criteria"
        CROSS_FIELD_INCONSISTENCY = "cross_field_inconsistency", "Cross-field inconsistency detected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(
        RdtiApplication, on_delete=models.CASCADE, related_name="flags"
    )
    target_type = models.CharField(max_length=30)
    target_id = models.UUIDField()
    field_name = models.CharField(max_length=100)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    flag_type = models.CharField(max_length=50, choices=FlagType.choices)
    message = models.TextField(help_text="Human-readable explanation of the flag")
    suggestion = models.TextField(
        blank=True,
        help_text="Suggested fix or improvement",
    )
    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "RDTI Flag"
        verbose_name_plural = "RDTI Flags"
        ordering = ["severity", "-created_at"]

    def __str__(self):
        return f"[{self.severity.upper()}] {self.flag_type} — {self.field_name}"
