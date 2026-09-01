# Merge migration: 0152_cashbook_gst_journal_lines and 0152_alter_activitylog_event_type
# both branched off 0151. They touch different models (JournalLine /
# AdjustingJournal vs ActivityLog), so joining the leaves is all that is needed.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0152_alter_activitylog_event_type'),
        ('core', '0153_journalline_gst_override'),
    ]

    operations = [
    ]
