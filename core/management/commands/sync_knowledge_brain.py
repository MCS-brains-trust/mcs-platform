"""
Management command to sync the Eva Knowledge Brain from SharePoint.
Can be run manually or scheduled via cron/Celery Beat (every 2 hours).

Usage:
    python manage.py sync_knowledge_brain
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sync Eva Knowledge Brain documents from SharePoint via Microsoft Graph API"

    def handle(self, *args, **options):
        from core.eva_service import sync_knowledge_brain

        self.stdout.write("Starting Eva Knowledge Brain sync...")
        try:
            stats = sync_knowledge_brain()
            if "error" in stats:
                self.stdout.write(self.style.ERROR(f"Sync error: {stats['error']}"))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"Sync complete. "
                    f"Added: {stats.get('added', 0)}, "
                    f"Updated: {stats.get('updated', 0)}, "
                    f"Errors: {stats.get('errors', 0)}, "
                    f"Total chunks: {stats.get('total_chunks', 0)}"
                ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Sync failed: {e}"))
            raise
