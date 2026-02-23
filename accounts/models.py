"""MCS Platform - Custom User Model with Role-Based Access Control, Invitations, and TOTP 2FA"""
import uuid
import secrets
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from datetime import timedelta
from config.encryption import EncryptedCharField


class User(AbstractUser):
    """
    Custom user model for MCS Platform.
    Extends Django AbstractUser with role-based access, client assignments, and TOTP 2FA.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        SENIOR_ACCOUNTANT = "senior_accountant", "Senior Accountant"
        ACCOUNTANT = "accountant", "Accountant"
        OFFICE_ADMIN = "office_admin", "Office Admin"
        READ_ONLY = "read_only", "Read Only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.ACCOUNTANT,
    )
    phone = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    # TOTP 2FA fields
    totp_secret = EncryptedCharField(max_length=255, blank=True, default="")
    totp_confirmed = models.BooleanField(
        default=False,
        help_text="Whether the user has confirmed their TOTP setup by entering a valid code.",
    )

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        name = self.get_full_name() or self.username
        return f"{name} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_office_admin(self):
        return self.role == self.Role.OFFICE_ADMIN

    @property
    def is_senior(self):
        return self.role in (self.Role.ADMIN, self.Role.SENIOR_ACCOUNTANT)

    @property
    def can_view_all_entities(self):
        """Admin and Office Admin can see all entities firm-wide."""
        return self.role in (self.Role.ADMIN, self.Role.SENIOR_ACCOUNTANT, self.Role.OFFICE_ADMIN)

    @property
    def can_finalise(self):
        return self.role in (self.Role.ADMIN, self.Role.SENIOR_ACCOUNTANT)

    @property
    def can_do_accounting(self):
        """Can perform accounting work: journals, trial balances, risk engine, documents."""
        return self.role in (
            self.Role.ADMIN,
            self.Role.SENIOR_ACCOUNTANT,
            self.Role.ACCOUNTANT,
        )

    @property
    def can_edit(self):
        """Can create/edit entities, notes, associates, details."""
        return self.role in (
            self.Role.ADMIN,
            self.Role.SENIOR_ACCOUNTANT,
            self.Role.ACCOUNTANT,
            self.Role.OFFICE_ADMIN,
        )

    @property
    def has_2fa(self):
        """Whether the user has completed TOTP 2FA setup."""
        return bool(self.totp_secret) and self.totp_confirmed


def _default_token():
    return secrets.token_urlsafe(48)


def _default_expiry():
    return timezone.now() + timedelta(days=7)


class Invitation(models.Model):
    """
    Invitation to join StatementHub. Sent via email with a unique signup link.
    The invited user sets their password and configures TOTP 2FA during signup.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    role = models.CharField(
        max_length=20,
        choices=User.Role.choices,
        default=User.Role.ACCOUNTANT,
    )
    token = models.CharField(max_length=128, unique=True, default=_default_token)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations_sent",
    )
    created_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitation",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_default_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invitation for {self.first_name} {self.last_name} ({self.email}) - {self.status}"

    @property
    def is_valid(self):
        """Whether the invitation can still be accepted."""
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()

    def mark_expired(self):
        if self.status == self.Status.PENDING and self.expires_at <= timezone.now():
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status"])
