"""
Add legal_doc_prompt_dismissed boolean field to Entity.

Master Spec 4.6.3: When a new company or trust entity is created, the
entity detail page surfaces a prompt to generate the relevant legal
document package. This field tracks whether the user has dismissed that
prompt.

Existing entities default to False (prompt not dismissed), but the prompt
only appears when no document of the relevant type has been generated yet,
so existing entities with documents already generated will not see it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0054_add_company_establishment"),
    ]

    operations = [
        migrations.AddField(
            model_name="entity",
            name="legal_doc_prompt_dismissed",
            field=models.BooleanField(
                default=False,
                help_text="Set to True when the user dismisses the post-creation legal document prompt.",
            ),
        ),
    ]
