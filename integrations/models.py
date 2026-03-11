"""
Integrations app models.

Stores OAuth2 connections to external accounting platforms (Xero, MYOB, QuickBooks).
Each Entity can have one active connection to one provider at a time.
"""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from config.encryption import EncryptedCharField, EncryptedTextField


class AccountingConnection(models.Model):
    """
    An OAuth2 connection between a StatementHub entity and an external
    accounting platform. Stores tokens, tenant/realm IDs, and connection
    metadata needed to pull trial balance data.
    """

    class Provider(models.TextChoices):
        XERO = "xero", "Xero"
        MYOB = "myob", "MYOB"
        QUICKBOOKS = "quickbooks", "QuickBooks Online"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Token Expired"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        "core.Entity",
        on_delete=models.CASCADE,
        related_name="accounting_connections",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    # OAuth2 tokens
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Provider-specific identifiers
    # Xero: tenant_id (organisation UUID)
    # MYOB: company_file_uri
    # QuickBooks: realm_id (company ID)
    tenant_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Xero tenant ID, MYOB company file URI, or QBO realm ID",
    )
    tenant_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Display name of the connected organisation",
    )

    # Audit
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="accounting_connections",
    )
    connected_at = models.DateTimeField(auto_now_add=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-connected_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "provider"],
                condition=models.Q(status="active"),
                name="unique_active_connection_per_entity_provider",
            )
        ]

    def __str__(self):
        return f"{self.entity.entity_name} \u2192 {self.get_provider_display()} ({self.get_status_display()})"

    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at

    @property
    def needs_refresh(self):
        """Token needs refresh if it expires within 5 minutes."""
        if not self.token_expires_at:
            return True
        return timezone.now() >= (self.token_expires_at - timezone.timedelta(minutes=5))


class ImportLog(models.Model):
    """
    Log of each trial balance import from an external provider.
    Tracks what was pulled and any issues encountered.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        AccountingConnection,
        on_delete=models.CASCADE,
        related_name="import_logs",
    )
    financial_year = models.ForeignKey(
        "core.FinancialYear",
        on_delete=models.CASCADE,
        related_name="cloud_imports",
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    lines_imported = models.IntegerField(default=0)
    lines_unmapped = models.IntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    as_at_date = models.DateField(
        null=True, blank=True,
        help_text="The date the trial balance was pulled as at",
    )
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]

    def __str__(self):
        return (
            f"{self.connection.get_provider_display()} import \u2192 "
            f"{self.financial_year} ({self.lines_imported} lines)"
        )


class XPMConnection(models.Model):
    """
    A practice-level OAuth2 connection to Xero Practice Manager.
    Unlike AccountingConnection (per-entity), this is a single connection
    for the entire practice that syncs client data, contacts, relationships,
    and notes between XPM and StatementHub.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Token Expired"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    # OAuth2 tokens
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Xero tenant
    tenant_id = models.CharField(max_length=255, blank=True, default="")
    tenant_name = models.CharField(max_length=255, blank=True, default="")

    # Audit
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="xpm_connections",
    )
    connected_at = models.DateTimeField(auto_now_add=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    # Sync stats
    clients_synced = models.IntegerField(default=0)
    contacts_synced = models.IntegerField(default=0)
    relationships_synced = models.IntegerField(default=0)
    notes_synced = models.IntegerField(default=0)

    class Meta:
        ordering = ["-connected_at"]

    def __str__(self):
        return f"XPM Connection ({self.get_status_display()}) - {self.tenant_name}"

    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at

    @property
    def needs_refresh(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= (self.token_expires_at - timezone.timedelta(minutes=5))


class XPMSyncLog(models.Model):
    """Log of each XPM sync operation."""

    class SyncType(models.TextChoices):
        FULL = "full", "Full Sync"
        CLIENTS = "clients", "Clients Only"
        CONTACTS = "contacts", "Contacts Only"
        NOTES = "notes", "Notes Only"

    class SyncStatus(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial (with errors)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        XPMConnection,
        on_delete=models.CASCADE,
        related_name="sync_logs",
    )
    sync_type = models.CharField(max_length=20, choices=SyncType.choices)
    status = models.CharField(
        max_length=20, choices=SyncStatus.choices, default=SyncStatus.RUNNING
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )

    # Stats
    clients_created = models.IntegerField(default=0)
    clients_updated = models.IntegerField(default=0)
    entities_created = models.IntegerField(default=0)
    contacts_synced = models.IntegerField(default=0)
    relationships_synced = models.IntegerField(default=0)
    notes_synced = models.IntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"XPM Sync ({self.get_sync_type_display()}) - {self.get_status_display()}"

    @property
    def duration(self):
        if self.completed_at and self.started_at:
            return self.completed_at - self.started_at
        return None


class XeroGlobalConnection(models.Model):
    """
    A practice-level OAuth2 connection to Xero using accounting scopes.
    One connection gives access to all client organisations (tenants)
    the advisor account has access to. Tokens are stored here and
    shared across all entity-level imports.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Token Expired"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    # OAuth2 tokens
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Audit
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="xero_global_connections",
    )
    connected_at = models.DateTimeField(auto_now_add=True)
    last_tenant_refresh = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-connected_at"]

    def __str__(self):
        return f"Xero Global ({self.get_status_display()}) - {self.tenants.count()} tenants"

    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at

    @property
    def needs_refresh(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= (self.token_expires_at - timezone.timedelta(minutes=5))


class XeroTenant(models.Model):
    """
    A cached Xero tenant (organisation) accessible via the global connection.
    Each tenant represents one client's Xero file that the advisor has access to.
    Tenants can be linked to StatementHub entities for trial balance imports.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        XeroGlobalConnection,
        on_delete=models.CASCADE,
        related_name="tenants",
    )
    tenant_id = models.CharField(
        max_length=255,
        help_text="Xero tenant/organisation UUID",
    )
    tenant_name = models.CharField(
        max_length=255,
        help_text="Organisation name in Xero",
    )

    # Link to StatementHub entity (optional — set when user maps tenant to entity)
    entity = models.ForeignKey(
        "core.Entity",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="xero_tenants",
    )

    # Metadata
    tenant_type = models.CharField(
        max_length=50, blank=True, default="",
        help_text="ORGANISATION or PRACTICE",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant_name"]
        unique_together = [("connection", "tenant_id")]

    def __str__(self):
        linked = f" → {self.entity.entity_name}" if self.entity else ""
        return f"{self.tenant_name}{linked}"


class QBGlobalConnection(models.Model):
    """
    A practice-level OAuth2 connection to QuickBooks Online.
    Unlike Xero, each QBO company requires a separate OAuth flow (each
    company has its own realmId). This model stores the latest tokens
    and acts as the parent for all QBTenant records.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Token Expired"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    # OAuth2 tokens (updated with each new company connection)
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Audit
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="qb_global_connections",
    )
    connected_at = models.DateTimeField(auto_now_add=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-connected_at"]

    def __str__(self):
        return f"QB Global ({self.get_status_display()}) - {self.tenants.count()} companies"

    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at

    @property
    def needs_refresh(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= (self.token_expires_at - timezone.timedelta(minutes=5))


class QBTenant(models.Model):
    """
    A QuickBooks Online company (realm) accessible via the global connection.
    Each company has its own realmId and its own set of tokens.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        QBGlobalConnection,
        on_delete=models.CASCADE,
        related_name="tenants",
    )
    realm_id = models.CharField(
        max_length=255,
        help_text="QuickBooks Online company/realm ID",
    )
    company_name = models.CharField(
        max_length=255,
        help_text="Company name in QuickBooks",
    )

    # Each QBO company has its own tokens
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Link to StatementHub entity
    entity = models.ForeignKey(
        "core.Entity",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="qb_tenants",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name"]
        unique_together = [("connection", "realm_id")]

    def __str__(self):
        linked = f" → {self.entity.entity_name}" if self.entity else ""
        return f"{self.company_name} ({self.realm_id}){linked}"

    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at

    @property
    def needs_refresh(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= (self.token_expires_at - timezone.timedelta(minutes=5))


class MYOBGlobalConnection(models.Model):
    """
    A practice-level OAuth2 connection to MYOB Business.
    Stores the OAuth tokens for accessing MYOB company files.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Token Expired"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    # OAuth2 tokens
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Audit
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="myob_global_connections",
    )
    connected_at = models.DateTimeField(auto_now_add=True)
    last_file_refresh = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-connected_at"]

    def __str__(self):
        return f"MYOB Global ({self.get_status_display()}) - {self.company_files.count()} files"

    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at

    @property
    def needs_refresh(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= (self.token_expires_at - timezone.timedelta(minutes=2))


class MYOBCompanyFile(models.Model):
    """
    A MYOB company file accessible via the global connection.
    Each company file has its own URI and may require separate credentials.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        MYOBGlobalConnection,
        on_delete=models.CASCADE,
        related_name="company_files",
    )
    file_uri = models.CharField(
        max_length=500,
        help_text="MYOB company file URI",
    )
    file_name = models.CharField(
        max_length=255,
        help_text="Company file name in MYOB",
    )
    file_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="MYOB company file ID",
    )

    # Company file credentials (encrypted at rest)
    cf_username = EncryptedCharField(
        blank=True, default="Administrator",
        help_text="Company file username",
    )
    cf_password = EncryptedCharField(
        blank=True, default="",
        help_text="Company file password (encrypted at rest)",
    )

    # Link to StatementHub entity
    entity = models.ForeignKey(
        "core.Entity",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="myob_company_files",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["file_name"]
        unique_together = [("connection", "file_uri")]

    def __str__(self):
        linked = f" → {self.entity.entity_name}" if self.entity else ""
        return f"{self.file_name}{linked}"
