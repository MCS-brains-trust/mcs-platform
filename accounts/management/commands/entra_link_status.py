# accounts/management/commands/entra_link_status.py
"""Who can sign in through Entra, and who cannot yet.

Exits 1 while any ACTIVE user is unlinked, so the cutover step that removes
password login can be gated on a fact rather than on recollection. Inactive
users are excluded: a departed staff member never needs to sign in again and
must not block the removal.
"""
import sys

from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    help = "Report which active users have been linked to a Microsoft Entra identity."

    def handle(self, *args, **options):
        users = User.objects.filter(is_active=True).order_by("email")
        linked = 0
        for user in users:
            if user.azure_object_id:
                linked += 1
                self.stdout.write(f"  LINKED   {user.email}  oid={user.azure_object_id}")
            else:
                self.stdout.write(f"  unlinked {user.email}")

        total = users.count()
        self.stdout.write(f"linked {linked} of {total}")
        if linked != total:
            self.stdout.write(
                "Password login MUST stay enabled: unlinked users would be locked out."
            )
            sys.exit(1)
        self.stdout.write("All active users are linked; password login can be removed.")
