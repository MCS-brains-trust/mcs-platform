# Data migration: populate finding_key for existing EvaFinding records.

from django.db import migrations


def populate_finding_keys(apps, schema_editor):
    """Set finding_key on existing EvaFinding rows using check_name as base."""
    EvaFinding = apps.get_model("core", "EvaFinding")
    for finding in EvaFinding.objects.filter(finding_key=""):
        # Best-effort: use check_name + extract account code from title if div7a
        key = finding.check_name or "unknown"
        if finding.check_name == "div7a" and finding.title:
            # Try to extract account code from title like "Div 7A — Loan (1200)"
            import re
            m = re.search(r"\((\d{3,6})\)", finding.title)
            if m:
                key = f"div7a_{m.group(1)}"
            elif "Other Exposures" in finding.title:
                key = "div7a_OTHER_EXPOSURES"
        finding.finding_key = key
        finding.save(update_fields=["finding_key"])

    # Deduplicate: if multiple findings in the same review got the same key,
    # append a pk fragment to make them unique before 0083 adds the constraint.
    from django.db.models import Count

    duplicates = (
        EvaFinding.objects
        .exclude(finding_key="")
        .values("eva_review_id", "finding_key")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )
    for dup in duplicates:
        findings = EvaFinding.objects.filter(
            eva_review_id=dup["eva_review_id"],
            finding_key=dup["finding_key"],
        ).order_by("created_at")
        # Keep the first one unchanged, suffix the rest with pk fragment
        for finding in findings[1:]:
            finding.finding_key = f"{finding.finding_key}_{str(finding.pk)[:8]}"
            finding.save(update_fields=["finding_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0081_evafinding_finding_key"),
    ]

    operations = [
        migrations.RunPython(populate_finding_keys, migrations.RunPython.noop),
    ]
