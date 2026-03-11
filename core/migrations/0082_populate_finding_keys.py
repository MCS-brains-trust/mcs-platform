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


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0081_evafinding_finding_key"),
    ]

    operations = [
        migrations.RunPython(populate_finding_keys, migrations.RunPython.noop),
    ]
