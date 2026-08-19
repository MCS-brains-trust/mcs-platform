# accounts/tests_entra_link_status.py
"""The gate on removing password login.

Task 8 of the SSO plan deletes the password flow. That is only safe once every
active user can get in through Entra, and "every" is a fact about the database,
not a memory of who said they signed in. Non-zero exit = do not proceed.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User


class EntraLinkStatusTests(TestCase):
    def _user(self, name, oid=None, is_active=True):
        return User.objects.create_user(
            username=name, email=f"{name}@mcands.com.au", password="x" * 14,
            azure_object_id=oid, is_active=is_active,
        )

    def test_reports_unlinked_users_and_exits_nonzero(self):
        self._user("linked", oid="aaaa-1")
        self._user("unlinked")
        out = StringIO()
        with self.assertRaises(SystemExit) as raised:
            call_command("entra_link_status", stdout=out)
        self.assertEqual(raised.exception.code, 1)
        printed = out.getvalue()
        self.assertIn("linked 1 of 2", printed)
        self.assertIn("unlinked@mcands.com.au", printed)

    def test_exits_zero_when_everyone_is_linked(self):
        self._user("a", oid="aaaa-1")
        self._user("b", oid="aaaa-2")
        out = StringIO()
        call_command("entra_link_status", stdout=out)
        self.assertIn("linked 2 of 2", out.getvalue())

    def test_inactive_users_do_not_hold_the_gate(self):
        self._user("a", oid="aaaa-1")
        self._user("departed", is_active=False)
        out = StringIO()
        call_command("entra_link_status", stdout=out)
        self.assertIn("linked 1 of 1", out.getvalue())
