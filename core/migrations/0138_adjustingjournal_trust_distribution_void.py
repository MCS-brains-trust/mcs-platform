"""Add the trust-distribution structural flag and the 'voided' journal status.

Part of the un-post feature:
  * ``AdjustingJournal.is_trust_distribution`` — structural signal replacing
    the fragile ``description__startswith="Trust distribution"`` match at the
    post gate / idempotency guard / un-post action.
  * ``AdjustingJournal.status`` gains the ``'voided'`` choice (state-only;
    ``"voided"`` fits the existing max_length=20, no column change).

The backfill flags pre-existing distribution journals using the description
prefix — the ONLY signal available for legacy rows. This is the single place
the prefix is used; all runtime call sites key off the flag.
"""

from django.db import migrations, models


def backfill_distribution_flag(apps, schema_editor):
    AdjustingJournal = apps.get_model("core", "AdjustingJournal")
    AdjustingJournal.objects.filter(
        description__startswith="Trust distribution"
    ).update(is_trust_distribution=True)


def unset_distribution_flag(apps, schema_editor):
    AdjustingJournal = apps.get_model("core", "AdjustingJournal")
    AdjustingJournal.objects.filter(is_trust_distribution=True).update(
        is_trust_distribution=False
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0137_prune_9000_capital_account_templates"),
    ]

    operations = [
        migrations.AddField(
            model_name="adjustingjournal",
            name="is_trust_distribution",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True for the system-generated trust distribution journal "
                    "posted from the trust workspace. Structural signal used by "
                    "the post gate, the idempotency guard and the un-post action "
                    "instead of matching on the description text."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="adjustingjournal",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("posted", "Posted"),
                    ("voided", "Voided"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            backfill_distribution_flag, unset_distribution_flag
        ),
    ]
