"""
Fix double-encrypted fields across all models.

A manual encryption migration script double-encrypted values, so the DB
contains encrypt(encrypt(plaintext)).  The ORM's from_db_value decrypts
once on read, yielding encrypt(plaintext) — still a Fernet token starting
with "gAAAAA".

This command:
  1. Reads raw ciphertext from the DB (bypassing ORM decryption)
  2. Decrypts once — if the result still starts with "gAAAAA", decrypts again
  3. Re-encrypts the plaintext once and writes it back via raw SQL
     (bypassing ORM's get_prep_value which would encrypt again)

Usage:
    python manage.py fix_double_encrypted_fields          # dry-run
    python manage.py fix_double_encrypted_fields --fix     # apply fixes
"""
import logging

from django.core.management.base import BaseCommand
from django.db import connection

from config.encryption import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

# (db_table, pk_column, [(field_name, ...)])
ENCRYPTED_FIELDS = [
    # core.Entity
    ("core_entity", "id", ["tfn", "contact_phone"]),
    # accounts.User
    ("accounts_user", "id", ["totp_secret"]),
    # integrations models
    ("integrations_accountingconnection", "id", ["access_token", "refresh_token"]),
    ("integrations_xpmconnection", "id", ["access_token", "refresh_token"]),
    ("integrations_xeroglobalconnection", "id", ["access_token", "refresh_token"]),
    ("integrations_qbglobalconnection", "id", ["access_token", "refresh_token"]),
    ("integrations_qbtenant", "id", ["access_token", "refresh_token"]),
    ("integrations_myobglobalconnection", "id", ["access_token", "refresh_token"]),
    ("integrations_myobcompanyfile", "id", ["cf_username", "cf_password"]),
]


def _is_fernet_token(value):
    """Check if value looks like a Fernet token."""
    return bool(value) and value.startswith("gAAAAA")


class Command(BaseCommand):
    help = "Fix double-encrypted fields caused by a migration script that encrypted already-encrypted values."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Actually fix the records (default is dry-run).",
        )

    def handle(self, *args, **options):
        do_fix = options["fix"]
        mode = "FIX" if do_fix else "DRY-RUN"
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Fix Double-Encrypted Fields  [{mode}]")
        self.stdout.write(f"{'='*60}\n")

        total_checked = 0
        total_fixed = 0
        total_skipped = 0
        errors = []

        for table, pk_col, fields in ENCRYPTED_FIELDS:
            # Check if table exists
            with connection.cursor() as cursor:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                    row_count = cursor.fetchone()[0]
                except Exception:
                    self.stdout.write(f"  [{table}] table does not exist — skipping")
                    continue

            if row_count == 0:
                self.stdout.write(f"  [{table}] 0 rows — skipping")
                continue

            self.stdout.write(f"\n  [{table}] checking {row_count} rows...")

            for field in fields:
                field_fixed = 0
                field_checked = 0

                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT {pk_col}, {field} FROM {table} WHERE {field} IS NOT NULL AND {field} != ''"  # noqa: S608
                    )
                    rows = cursor.fetchall()

                for row_pk, raw_value in rows:
                    field_checked += 1
                    total_checked += 1

                    if not raw_value or not _is_fernet_token(raw_value):
                        continue

                    # Decrypt once
                    try:
                        once = decrypt_value(raw_value)
                    except Exception as e:
                        errors.append(f"{table}.{field} pk={row_pk}: first decrypt failed: {e}")
                        continue

                    if not _is_fernet_token(once):
                        # Single-encrypted — correct, nothing to do
                        continue

                    # Still a Fernet token after one decryption => double-encrypted
                    try:
                        plaintext = decrypt_value(once)
                    except Exception as e:
                        errors.append(f"{table}.{field} pk={row_pk}: second decrypt failed: {e}")
                        continue

                    # Sanity: if STILL a Fernet token, it could be triple+ encrypted
                    depth = 2
                    check = plaintext
                    while _is_fernet_token(check) and depth < 10:
                        try:
                            check = decrypt_value(check)
                            depth += 1
                            plaintext = check
                        except Exception:
                            break

                    if _is_fernet_token(plaintext):
                        errors.append(
                            f"{table}.{field} pk={row_pk}: still Fernet after {depth} "
                            f"decryptions — skipping"
                        )
                        total_skipped += 1
                        continue

                    # Re-encrypt once (single layer) for storage
                    correct_ciphertext = encrypt_value(plaintext)

                    preview = plaintext[:4] + "***" if len(plaintext) > 4 else "***"
                    self.stdout.write(
                        f"    {field} pk={row_pk}: double-encrypted "
                        f"(depth={depth}, plaintext={preview})"
                    )

                    if do_fix:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f"UPDATE {table} SET {field} = %s WHERE {pk_col} = %s",  # noqa: S608
                                [correct_ciphertext, str(row_pk)],
                            )
                        field_fixed += 1
                        total_fixed += 1
                    else:
                        field_fixed += 1
                        total_fixed += 1

                if field_checked:
                    status = "would fix" if not do_fix else "fixed"
                    self.stdout.write(
                        f"    {field}: {field_checked} checked, "
                        f"{field_fixed} {status}"
                    )

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Total fields checked: {total_checked}")
        self.stdout.write(f"  Total double-encrypted: {total_fixed}")
        self.stdout.write(f"  Total skipped (errors): {total_skipped}")
        if errors:
            self.stdout.write(f"\n  Errors:")
            for err in errors:
                self.stdout.write(f"    - {err}")
        if not do_fix and total_fixed > 0:
            self.stdout.write(
                f"\n  Run with --fix to apply changes."
            )
        self.stdout.write(f"{'='*60}\n")
