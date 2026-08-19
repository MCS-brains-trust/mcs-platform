"""The Entra link column on accounts.User.

Nullable AND unique: during the dual-run cutover most rows are unlinked, so
several NULLs must coexist, while two users can never claim one Entra identity.
"""
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User


class AzureObjectIdFieldTests(TestCase):
    def test_defaults_to_none(self):
        user = User.objects.create_user(
            username="unlinked", email="unlinked@mcands.com.au", password="x" * 14,
        )
        self.assertIsNone(user.azure_object_id)

    def test_many_unlinked_users_coexist(self):
        for i in range(3):
            User.objects.create_user(
                username=f"u{i}", email=f"u{i}@mcands.com.au", password="x" * 14,
            )
        self.assertEqual(User.objects.filter(azure_object_id__isnull=True).count(), 3)

    def test_two_users_cannot_share_one_entra_identity(self):
        oid = "00000000-1111-2222-3333-444444444444"
        User.objects.create_user(
            username="first", email="first@mcands.com.au", password="x" * 14,
            azure_object_id=oid,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(
                username="second", email="second@mcands.com.au", password="x" * 14,
                azure_object_id=oid,
            )
