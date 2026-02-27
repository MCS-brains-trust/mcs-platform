"""
StatementHub — Eva AI Practice Intelligence Models
===================================================
Implements the data models from Eva Spec v2.0:
  - KnowledgeDocument & KnowledgeChunk (Knowledge Brain / pgvector RAG)
  - EvaConversation & EvaMessage (Chat Interface)
  - EvaReview & EvaFinding (Finalisation Gate)
"""
import uuid
from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# Knowledge Brain — Document & Chunk models for RAG
# ---------------------------------------------------------------------------
class KnowledgeDocument(models.Model):
    """
    A document in Eva's Knowledge Brain, synced from SharePoint.
    Each document is chunked and embedded for semantic retrieval.
    """

    class Category(models.TextChoices):
        FIRM_PROCEDURES = "firm_procedures", "Firm Procedures"
        FIRM_TECHNICAL = "firm_technical", "Firm Technical Positions"
        FIRM_TRAINING = "firm_training", "Firm Training Materials"
        FIRM_PRECEDENTS = "firm_precedents", "Firm Precedents"
        ATO_RULINGS = "ato_rulings", "ATO Rulings"
        ATO_STATEMENTS = "ato_statements", "ATO Practice Statements"
        ATO_ALERTS = "ato_alerts", "ATO Alerts"
        ATO_BENCHMARKS = "ato_benchmarks", "ATO Benchmarks"
        LEGISLATION = "legislation", "Legislation"
        AASB_STANDARDS = "aasb_standards", "AASB Standards"
        CPA_MATERIALS = "cpa_materials", "CPA Australia Materials"
        CA_ANZ_MATERIALS = "ca_anz_materials", "CA ANZ Materials"
        TREASURY = "treasury", "Treasury"

    class SyncStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SYNCED = "synced", "Synced"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(
        max_length=500,
        help_text="Document title (from SharePoint filename or metadata)",
    )
    category = models.CharField(max_length=30, choices=Category.choices)
    sharepoint_path = models.CharField(
        max_length=1000,
        help_text="Full SharePoint path to the source document",
    )
    sharepoint_item_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="SharePoint/Graph API item ID for change detection",
    )
    sharepoint_modified_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last modification timestamp in SharePoint",
    )
    sync_status = models.CharField(
        max_length=10, choices=SyncStatus.choices, default=SyncStatus.PENDING,
    )
    sync_error = models.TextField(
        blank=True, default="",
        help_text="Error message from last failed sync attempt",
    )
    synced_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of last successful sync",
    )
    chunk_count = models.IntegerField(
        default=0,
        help_text="Number of KnowledgeChunk records created from this document",
    )
    file_type = models.CharField(
        max_length=10, blank=True, default="",
        help_text="File extension: docx, pdf, txt, xlsx, pptx",
    )
    file_size_bytes = models.IntegerField(
        default=0,
        help_text="File size in bytes",
    )
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
            models.Index(fields=["is_archived", "sync_status"]),
        ]

    def __str__(self):
        return f"{self.title} [{self.get_category_display()}]"


class KnowledgeChunk(models.Model):
    """
    An individual text chunk from a KnowledgeDocument, with its vector embedding.
    Used for semantic similarity search (RAG) via pgvector.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks",
    )
    chunk_index = models.IntegerField(
        help_text="Position of this chunk within the document (0-indexed)",
    )
    text = models.TextField(
        help_text="Raw text content of this chunk (~512 tokens)",
    )
    # Note: The embedding field uses pgvector's VectorField.
    # If pgvector is not yet installed, this field stores embeddings as JSON
    # and will be migrated to VectorField once CREATE EXTENSION vector is run.
    embedding = models.JSONField(
        null=True, blank=True,
        help_text="Vector embedding (1536 dimensions) stored as JSON array. "
                  "Will be migrated to pgvector VectorField in production.",
    )
    token_count = models.IntegerField(
        default=0,
        help_text="Actual token count of this chunk",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "chunk_index"]
        indexes = [
            models.Index(fields=["document", "chunk_index"]),
        ]

    def __str__(self):
        return f"Chunk {self.chunk_index} of {self.document.title}"


# ---------------------------------------------------------------------------
# Eva Chat Interface — Conversation & Message models
# ---------------------------------------------------------------------------
class EvaConversation(models.Model):
    """
    A chat conversation between an accountant and Eva within a financial year workspace.
    One conversation per financial year per session.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        "core.FinancialYear", on_delete=models.CASCADE, related_name="eva_conversations",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
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
        return f"Eva Chat — {self.financial_year} ({self.message_count} messages)"


class EvaMessage(models.Model):
    """
    A single message in an Eva conversation (either from the user or Eva).
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Eva"

    class ModelUsed(models.TextChoices):
        HAIKU = "haiku", "Haiku"
        SONNET = "sonnet", "Sonnet"
        OPUS = "opus", "Opus"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        EvaConversation, on_delete=models.CASCADE, related_name="messages",
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField(help_text="Message text content")
    model_used = models.CharField(
        max_length=10, choices=ModelUsed.choices, blank=True, default="",
        help_text="AI model used for this response (blank for user messages)",
    )
    retrieved_chunk_ids = models.JSONField(
        default=list, blank=True,
        help_text="List of KnowledgeChunk IDs used in this response",
    )
    tokens_used = models.IntegerField(
        default=0,
        help_text="Total tokens consumed for this response",
    )
    is_proactive = models.BooleanField(
        default=False,
        help_text="True if this is a proactive suggestion from Eva (Stage 2)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
        ]

    def __str__(self):
        preview = self.content[:80] + "..." if len(self.content) > 80 else self.content
        return f"[{self.get_role_display()}] {preview}"


# ---------------------------------------------------------------------------
# Eva Finalisation Gate — Review & Finding models
# ---------------------------------------------------------------------------
class EvaReview(models.Model):
    """
    A structured compliance review triggered by 'Ask Eva to Review'.
    One active review per financial year at a time (replaced on re-run).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        FINDINGS_RAISED = "findings_raised", "Findings Raised"
        CLEARED = "cleared", "Cleared"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    financial_year = models.ForeignKey(
        "core.FinancialYear", on_delete=models.CASCADE, related_name="eva_reviews",
    )
    triggered_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When Eva finished the review (findings or cleared)",
    )
    model_used = models.CharField(
        max_length=10,
        choices=EvaMessage.ModelUsed.choices,
        default=EvaMessage.ModelUsed.SONNET,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name="triggered_eva_reviews",
    )
    opus_override = models.BooleanField(
        default=False,
        help_text="True if manually escalated to Opus model",
    )
    checks_completed = models.IntegerField(
        default=0,
        help_text="Number of compliance checks completed so far",
    )
    checks_total = models.IntegerField(
        default=8,
        help_text="Total number of compliance checks to run",
    )
    error_message = models.TextField(
        blank=True, default="",
        help_text="Error details if status is ERROR",
    )
    is_rerun = models.BooleanField(
        default=False,
        help_text="True if this review was triggered as a re-run after findings were addressed",
    )

    class Meta:
        ordering = ["-triggered_at"]
        indexes = [
            models.Index(fields=["financial_year", "status"]),
        ]

    def __str__(self):
        return f"Eva Review — {self.financial_year} ({self.get_status_display()})"

    @property
    def finding_count(self):
        return self.findings.count()

    @property
    def open_finding_count(self):
        return self.findings.filter(status=EvaFinding.Status.OPEN).count()

    @property
    def critical_finding_count(self):
        return self.findings.filter(
            severity=EvaFinding.Severity.CRITICAL,
            status=EvaFinding.Status.OPEN,
        ).count()


class EvaFinding(models.Model):
    """
    A specific compliance finding raised by Eva during a Finalisation Gate review.
    Each finding must be addressed with a mandatory resolution note before
    the financial year can be finalised.
    """

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        ADVISORY = "advisory", "Advisory"

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ADDRESSED = "addressed", "Addressed"

    # The 8 compliance check areas from the spec
    class CheckName(models.TextChoices):
        DIVISION_7A = "division_7a", "Division 7A"
        SUPERANNUATION = "superannuation", "Superannuation Guarantee"
        ATO_BENCHMARKS = "ato_benchmarks", "ATO Industry Benchmarks"
        TRUST_DISTRIBUTIONS = "trust_distributions", "Trust Distributions"
        GOING_CONCERN = "going_concern", "Going Concern"
        RELATED_PARTY = "related_party", "Related Party Transactions"
        TPAR = "tpar", "TPAR Obligations"
        THIN_CAPITALISATION = "thin_capitalisation", "Thin Capitalisation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eva_review = models.ForeignKey(
        EvaReview, on_delete=models.CASCADE, related_name="findings",
    )
    check_name = models.CharField(
        max_length=30, choices=CheckName.choices,
        help_text="Which compliance check area this finding relates to",
    )
    severity = models.CharField(max_length=10, choices=Severity.choices)
    title = models.CharField(
        max_length=500,
        help_text="Brief, plain-English finding title",
    )
    explanation = models.TextField(
        help_text="2-3 sentence plain-English description of what Eva found",
    )
    recommendation = models.TextField(
        help_text="Specific recommended action for the accountant",
    )
    legislation_reference = models.CharField(
        max_length=255, blank=True, default="",
        help_text="e.g. 'ITAA 1936 s.109D'",
    )
    knowledge_brain_citation = models.CharField(
        max_length=500, blank=True, default="",
        help_text="Firm Knowledge Brain document cited, if applicable",
    )
    confidence = models.CharField(
        max_length=10, choices=Confidence.choices, default=Confidence.HIGH,
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN,
    )
    resolution_note = models.TextField(
        blank=True, default="",
        help_text="Mandatory accountant response documenting how this finding was addressed",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="resolved_eva_findings",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-severity", "check_name"]
        indexes = [
            models.Index(fields=["eva_review", "status"]),
            models.Index(fields=["eva_review", "severity"]),
        ]

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title}"
