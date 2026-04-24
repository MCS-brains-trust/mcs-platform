"""
Add Entity.include_comparative_figures boolean field.

When set to False, prior-year comparative columns are suppressed from:
  - The on-screen trial balance (financial_year_detail view)
  - The trial balance PDF download
  - The trial balance Word/Excel download
  - The on-screen financial statements preview
  - The generated financial statement documents (docgen + fs_template_service)
  - The document context builder (show_prior_year_column)

Defaults to True so all existing entities retain their current behaviour.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0119_entity_service_scope_evafinding_domain"),
    ]

    operations = [
        migrations.AddField(
            model_name="entity",
            name="include_comparative_figures",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Include prior-year comparative figures in the trial balance "
                    "and financial statement documents. Uncheck for first-year "
                    "entities or when comparatives are not required."
                ),
            ),
        ),
    ]
