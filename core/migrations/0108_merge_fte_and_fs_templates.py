# Merge migration: resolves the two leaf nodes
#   0103_familytrustelectoraldocument  (FTE working document model)
#   0107_force_regenerate_fs_templates (FS template regeneration)
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0103_familytrustelectoraldocument"),
        ("core", "0107_force_regenerate_fs_templates"),
    ]

    operations = []
