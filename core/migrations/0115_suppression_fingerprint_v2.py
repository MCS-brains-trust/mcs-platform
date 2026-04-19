"""
Add fingerprint_version and requires_review to EvaFindingSuppression.
Backfill empty finding_key on EvaFinding.

Schema changes:
  - EvaFindingSuppression.fingerprint_version  PositiveSmallIntegerField  default=2
  - EvaFindingSuppression.requires_review      BooleanField               default=False

Data migrations:
  1. All existing EvaFindingSuppression rows set to fingerprint_version=1,
     requires_review=True so legacy coarse fingerprints no longer suppress
     findings until a partner re-confirms them.
  2. All EvaFinding rows with empty finding_key backfilled with check_name
     so every finding has a non-empty key for suppression fingerprints.
"""

from django.db import migrations, models


def mark_legacy_suppressions(apps, schema_editor):
    EvaFindingSuppression = apps.get_model("core", "EvaFindingSuppression")
    EvaFindingSuppression.objects.all().update(
        fingerprint_version=1,
        requires_review=True,
    )


def reverse_mark_legacy(apps, schema_editor):
    EvaFindingSuppression = apps.get_model("core", "EvaFindingSuppression")
    EvaFindingSuppression.objects.all().update(
        fingerprint_version=2,
        requires_review=False,
    )


def backfill_finding_keys(apps, schema_editor):
    EvaFinding = apps.get_model("core", "EvaFinding")
    empty_qs = EvaFinding.objects.filter(finding_key__in=["", None])
    count = empty_qs.count()
    if count:
        # Backfill each row: finding_key = check_name
        for finding in empty_qs.iterator():
            finding.finding_key = finding.check_name
            finding.save(update_fields=["finding_key"])
        print(f"\n  Backfilled finding_key on {count} EvaFinding rows")


def reverse_backfill(apps, schema_editor):
    # No-op: we cannot distinguish backfilled from originally-set keys
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0114_trust_workspace_tax_scenario_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="evafindingsuppression",
            name="fingerprint_version",
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text="1 = legacy coarse fingerprint, 2 = finding_key-based fingerprint",
            ),
        ),
        migrations.AddField(
            model_name="evafindingsuppression",
            name="requires_review",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True if this suppression predates the fingerprint v2 "
                    "composition and must be re-confirmed by a partner before "
                    "it applies to a new compliance run."
                ),
            ),
        ),
        migrations.RunPython(
            mark_legacy_suppressions,
            reverse_mark_legacy,
        ),
        migrations.RunPython(
            backfill_finding_keys,
            reverse_backfill,
        ),
    ]
