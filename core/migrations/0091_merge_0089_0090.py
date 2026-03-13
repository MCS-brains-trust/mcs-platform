# Generated manually to resolve migration conflict between
# 0089_engagement_letter and 0090_alter_activitylog_event_type
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0089_engagement_letter"),
        ("core", "0090_alter_activitylog_event_type"),
    ]

    operations = [
    ]
