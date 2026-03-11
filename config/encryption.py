"""
Field-level encryption utilities for sensitive data.

Uses Fernet symmetric encryption. Prefers a dedicated FIELD_ENCRYPTION_KEY
environment variable; falls back to deriving from SECRET_KEY for backward
compatibility during migration.

All PII, OAuth tokens, and secrets are encrypted at rest in the database.
"""
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

_encryption_logger = logging.getLogger(__name__)


def _get_fernet():
    """
    Get a Fernet cipher using a dedicated encryption key.
    Uses FIELD_ENCRYPTION_KEY if set (preferred), otherwise falls back to
    deriving from SECRET_KEY for backward compatibility.
    """
    explicit_key = os.environ.get("FIELD_ENCRYPTION_KEY", "") or getattr(
        settings, "FIELD_ENCRYPTION_KEY", ""
    )
    if explicit_key:
        return Fernet(
            explicit_key.encode() if isinstance(explicit_key, str) else explicit_key
        )

    # Fallback: derive from SECRET_KEY (backward-compatible, not recommended)
    _encryption_logger.warning(
        "FIELD_ENCRYPTION_KEY not set — deriving encryption key from SECRET_KEY. "
        "Set FIELD_ENCRYPTION_KEY in your environment for production use."
    )
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_value(value):
    """Encrypt a string value. Returns base64-encoded ciphertext."""
    if not value:
        return ""
    f = _get_fernet()
    return f.encrypt(value.encode()).decode()


def decrypt_value(value):
    """Decrypt a base64-encoded ciphertext. Returns plaintext string."""
    if not value:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(value.encode()).decode()
    except (InvalidToken, Exception):
        # If decryption fails, the value may be stored in plaintext (pre-migration).
        # Return as-is for backward compatibility during migration period.
        return value


class EncryptedCharField(models.TextField):
    """
    A TextField that transparently encrypts data at rest.
    Values are encrypted before saving and decrypted when reading.
    Uses TEXT column to accommodate Fernet ciphertext which is always
    longer than the original plaintext.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_value(value) if value else value

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value) if value else value

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # Report as our custom field for migrations
        path = "config.encryption.EncryptedCharField"
        return name, path, args, kwargs


class EncryptedTextField(models.TextField):
    """
    A TextField that transparently encrypts data at rest.
    Used for longer values like OAuth tokens.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_value(value) if value else value

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value) if value else value

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        path = "config.encryption.EncryptedTextField"
        return name, path, args, kwargs
