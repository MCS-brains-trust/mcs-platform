from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge migration: resolves conflict between
      0100_alter_knowledgechunk_embedding_vector (server-only)
      0100_yearend_commentary
    """

    dependencies = [
        ('core', '0100_alter_knowledgechunk_embedding_vector'),
        ('core', '0100_yearend_commentary'),
    ]

    operations = [
    ]
