"""
Data migration: remove stale Eva findings that should no longer appear.

Deletes EvaFinding records matching:
  - Going Concern (check disabled — directors sign ASIC minutes annually)
  - Year-on-Year Variances (comparative_consistency check removed)
  - Superannuation Overpayment (SGC module only flags underpayment)
"""

from django.db import migrations


def clean_stale_findings(apps, schema_editor):
    EvaFinding = apps.get_model("core", "EvaFinding")

    # 1. Going Concern findings
    gc_qs = EvaFinding.objects.filter(check_name__icontains="going_concern")
    gc_title_qs = EvaFinding.objects.filter(title__icontains="Going Concern")
    gc_count = (gc_qs | gc_title_qs).distinct().delete()[0]

    # 2. Year-on-Year Variance findings
    var_qs = EvaFinding.objects.filter(title__icontains="Year-on-Year Variances")
    cc_qs = EvaFinding.objects.filter(check_name__icontains="comparative_consistency")
    var_count = (var_qs | cc_qs).distinct().delete()[0]

    # 3. Superannuation Overpayment findings
    overpay_count = EvaFinding.objects.filter(
        title__icontains="Overpayment"
    ).delete()[0]

    if gc_count or var_count or overpay_count:
        print(
            f"\n  Cleaned stale Eva findings: "
            f"{gc_count} Going Concern, "
            f"{var_count} Variance, "
            f"{overpay_count} Overpayment"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0071_entity_is_small_business_entity_and_more"),
    ]

    operations = [
        migrations.RunPython(clean_stale_findings, migrations.RunPython.noop),
    ]
