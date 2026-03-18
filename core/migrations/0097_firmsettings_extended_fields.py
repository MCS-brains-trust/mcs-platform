"""
Migration: 0097_firmsettings_extended_fields

Adds the following fields to FirmSettings as specified in the
DocumentContextBuilder Spec v1.0:
  - tax_agent_number
  - bas_agent_number
  - asic_agent_number
  - signatory_name
  - signatory_designation
  - professional_body
  - membership_number
  - practice_independence_maintained
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0096_merge_0095_firmsettings_0095_rename"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmsettings",
            name="tax_agent_number",
            field=models.CharField(
                blank=True,
                default="",
                help_text="TPB Tax Agent registration number. Required on all engagement letters (APES 305).",
                max_length=20,
                verbose_name="Tax Agent Number",
            ),
        ),
        migrations.AddField(
            model_name="firmsettings",
            name="bas_agent_number",
            field=models.CharField(
                blank=True,
                default="",
                help_text="TPB BAS Agent registration number (if applicable).",
                max_length=20,
                verbose_name="BAS Agent Number",
            ),
        ),
        migrations.AddField(
            model_name="firmsettings",
            name="asic_agent_number",
            field=models.CharField(
                blank=True,
                default="",
                help_text="ASIC registered agent number (if applicable).",
                max_length=20,
                verbose_name="ASIC Agent Number",
            ),
        ),
        migrations.AddField(
            model_name="firmsettings",
            name="signatory_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Name of the signing partner/director for document sign-off blocks.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="firmsettings",
            name="signatory_designation",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Designation shown under signatory name (e.g. 'CPA, Registered Tax Agent').",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="firmsettings",
            name="professional_body",
            field=models.CharField(
                blank=True,
                default="CPA Australia",
                help_text="Professional body membership (e.g. 'CPA Australia', 'CAANZ'). Drives dispute resolution clause wording.",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="firmsettings",
            name="membership_number",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Professional body membership number.",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="firmsettings",
            name="practice_independence_maintained",
            field=models.BooleanField(
                default=True,
                help_text="Whether the practice maintains independence under APES 110. Drives independence statement in compilation reports.",
            ),
        ),
    ]
