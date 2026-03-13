# Generated manually to resolve migration conflict between
# 0088_engagement_letter and 0089_alter_activitylog_event_type
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0088_engagement_letter"),
        ("core", "0089_alter_activitylog_event_type"),
    ]

    operations = [
    ]
