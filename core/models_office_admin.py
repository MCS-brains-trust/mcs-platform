"""
MCS Platform - Office Admin Models
Models for the reception/office admin dashboard:
Correspondence, ASIC/ATO tracking, Debtors, and Daily Tasks.
"""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Correspondence
# ---------------------------------------------------------------------------
class Correspondence(models.Model):
    """
    Tracks all incoming and outgoing correspondence for the practice.
    Used by reception to log mail, ATO notices, client documents, etc.
    """

    class Direction(models.TextChoices):
        INCOMING = "incoming", "Incoming"
        OUTGOING = "outgoing", "Outgoing"

    class CorrespondenceType(models.TextChoices):
        ATO_NOTICE = "ato_notice", "ATO Notice"
        NOA_REFUND = "noa_refund", "NOA — Refund"
        NOA_PAYABLE = "noa_payable", "NOA — Payable"
        TAX_DOCUMENTS = "tax_documents", "Tax Documents"
        LETTER_PAYABLE = "letter_payable", "Letter — Payable"
        DOCUMENT_REQUEST = "document_request", "Document Request"
        FUSESIGN_BUNDLE = "fusesign_bundle", "FuseSign Bundle"
        CLIENT_LETTER = "client_letter", "Client Letter"
        ASIC_NOTICE = "asic_notice", "ASIC Notice"
        BANK_STATEMENT = "bank_statement", "Bank Statement"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        SENT = "sent", "Sent"
        PENDING = "pending", "Pending"
        AWAITING = "awaiting", "Awaiting Reply"
        ACTIONED = "actioned", "Actioned"
        FILED = "filed", "Filed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        "Entity", on_delete=models.CASCADE, related_name="correspondence",
        null=True, blank=True,
        help_text="The entity this correspondence relates to (optional for general mail).",
    )
    direction = models.CharField(max_length=10, choices=Direction.choices)
    correspondence_type = models.CharField(
        max_length=30, choices=CorrespondenceType.choices, default=CorrespondenceType.OTHER,
    )
    subject = models.CharField(
        max_length=500, blank=True,
        help_text="Brief description of the correspondence.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True, help_text="Additional notes or context.")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="assigned_correspondence",
        help_text="Staff member this correspondence has been passed to.",
    )
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="logged_correspondence",
    )
    date_received = models.DateField(default=timezone.now)
    date_actioned = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_received", "-created_at"]
        verbose_name_plural = "correspondence"

    def __str__(self):
        entity_name = self.entity.entity_name if self.entity else "General"
        return f"{self.get_direction_display()} — {entity_name} — {self.get_correspondence_type_display()}"


# ---------------------------------------------------------------------------
# ASIC Return Tracker
# ---------------------------------------------------------------------------
class ASICReturn(models.Model):
    """
    Tracks ASIC annual returns, business name renewals, and related deadlines.
    """

    class ReturnType(models.TextChoices):
        ANNUAL_RETURN = "annual_return", "ASIC Annual Return"
        BUSINESS_NAME = "business_name", "Business Name Renewal"
        COMPANY_CHANGE = "company_change", "Company Change"
        DEREGISTRATION = "deregistration", "Deregistration"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        LODGED = "lodged", "Lodged"
        BURNING = "burning", "Burning"
        OVERDUE = "overdue", "Overdue"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        "Entity", on_delete=models.CASCADE, related_name="asic_returns",
    )
    return_type = models.CharField(
        max_length=20, choices=ReturnType.choices, default=ReturnType.ANNUAL_RETURN,
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Fee amount if applicable.",
    )
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="logged_asic_returns",
    )
    date_lodged = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "status"]

    def __str__(self):
        return f"{self.entity.entity_name} — {self.get_return_type_display()} — {self.get_status_display()}"

    @property
    def is_burning(self):
        """Return True if the due date is within 7 days or past due."""
        if not self.due_date:
            return False
        days_until = (self.due_date - timezone.now().date()).days
        return days_until <= 7 and self.status not in ("lodged", "completed")


# ---------------------------------------------------------------------------
# NOA Tracker (Notice of Assessment)
# ---------------------------------------------------------------------------
class NOARecord(models.Model):
    """
    Tracks Notices of Assessment from the ATO — refunds to send to clients
    or payable amounts to notify clients about.
    """

    class NOAType(models.TextChoices):
        REFUND = "refund", "Refund"
        PAYABLE = "payable", "Payable"
        NIL = "nil", "Nil"

    class Status(models.TextChoices):
        TO_SEND = "to_send", "To Send"
        SENT = "sent", "Sent to Client"
        FORWARDED = "forwarded", "Forwarded to Accountant"
        ACTIONED = "actioned", "Actioned"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        "Entity", on_delete=models.CASCADE, related_name="noa_records",
    )
    noa_type = models.CharField(max_length=10, choices=NOAType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TO_SEND)
    date_received = models.DateField(default=timezone.now)
    date_sent = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="logged_noas",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_received"]
        verbose_name = "NOA Record"
        verbose_name_plural = "NOA Records"

    def __str__(self):
        return f"{self.entity.entity_name} — {self.get_noa_type_display()} ${self.amount}"


# ---------------------------------------------------------------------------
# Debtor Record
# ---------------------------------------------------------------------------
class DebtorRecord(models.Model):
    """
    Tracks outstanding debtor balances and escalation status.
    Synced from Xero Practice Manager or manually entered.
    """

    class EscalationStage(models.TextChoices):
        CURRENT = "current", "Current"
        FIRST_STATEMENT = "1st_statement", "1st Statement"
        SECOND_STATEMENT = "2nd_statement", "2nd Statement"
        SOFT_LETTER = "soft_letter", "Soft Letter"
        FIRM_LETTER = "firm_letter", "Firm Letter"
        FINAL_NOTICE = "final_notice", "Final Notice"
        WRITE_OFF = "write_off", "Write Off"

    class Status(models.TextChoices):
        CURRENT = "current", "Current"
        OVERDUE = "overdue", "Overdue"
        PAYMENT_PLAN = "payment_plan", "Payment Plan"
        PAID = "paid", "Paid"
        WRITTEN_OFF = "written_off", "Written Off"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        "Entity", on_delete=models.CASCADE, related_name="debtor_records",
    )
    invoice_number = models.CharField(max_length=50, blank=True)
    amount_outstanding = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    days_overdue = models.IntegerField(default=0)
    escalation_stage = models.CharField(
        max_length=20, choices=EscalationStage.choices, default=EscalationStage.CURRENT,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CURRENT)
    last_contact_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="logged_debtors",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-days_overdue", "-amount_outstanding"]

    def __str__(self):
        return f"{self.entity.entity_name} — ${self.amount_outstanding} — {self.days_overdue} days"


# ---------------------------------------------------------------------------
# Payment Plan
# ---------------------------------------------------------------------------
class PaymentPlan(models.Model):
    """
    Tracks active payment plans for clients on debit arrangements.
    """

    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        FORTNIGHTLY = "fortnightly", "Fortnightly"
        MONTHLY = "monthly", "Monthly"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        DEFAULTED = "defaulted", "Defaulted"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        "Entity", on_delete=models.CASCADE, related_name="payment_plans",
    )
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    instalment_amount = models.DecimalField(max_digits=12, decimal_places=2)
    frequency = models.CharField(max_length=15, choices=Frequency.choices, default=Frequency.MONTHLY)
    next_payment_date = models.DateField(null=True, blank=True)
    remaining_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_payment_date"]

    def __str__(self):
        return f"{self.entity.entity_name} — ${self.instalment_amount}/{self.get_frequency_display()}"


# ---------------------------------------------------------------------------
# Daily Task / Checklist
# ---------------------------------------------------------------------------
class DailyTask(models.Model):
    """
    Recurring and one-off tasks for the office admin daily checklist.
    """

    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        ONE_OFF = "one_off", "One-off"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.DAILY)
    scheduled_time = models.TimeField(null=True, blank=True, help_text="Suggested time for this task.")
    display_order = models.IntegerField(default=0, help_text="Order in the checklist.")
    is_active = models.BooleanField(default=True, help_text="Whether this task appears in the checklist.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "scheduled_time"]

    def __str__(self):
        return f"{self.title} ({self.get_frequency_display()})"


class DailyTaskCompletion(models.Model):
    """
    Records completion of a daily task for a specific date.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(DailyTask, on_delete=models.CASCADE, related_name="completions")
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    completed_date = models.DateField(default=timezone.now)
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["task", "completed_date"]
        ordering = ["-completed_date"]

    def __str__(self):
        return f"{self.task.title} — {self.completed_date}"
