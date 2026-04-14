"""
One-shot command to verify and fix the Distribution Minutes DocumentTemplate.

Ensures the beneficiary table conditional + data_source uses the correct
field names matching the resolver output.
"""
import json
from django.core.management.base import BaseCommand
from core.models import DocumentTemplate


EXPECTED_CONDITIONAL = {
    "type": "conditional",
    "field": "has_beneficiaries",
    "children": [
        {
            "type": "table",
            "columns": [
                {"header": "Beneficiary", "field": "name", "width_cm": 5, "alignment": "left"},
                {"header": "Type", "field": "type", "width_cm": 3, "alignment": "center"},
                {"header": "Distribution", "field": "distribution", "width_cm": 4, "alignment": "right"},
                {"header": "Share %", "field": "percentage", "width_cm": 3, "alignment": "right"},
            ],
            "data_source": "beneficiary_rows",
            "totals": {
                "label": "TOTAL",
                "distribution": "{{total_distributed}}",
                "percentage": "100.00%",
            },
        },
    ],
    "else_children": [
        {
            "type": "paragraph",
            "text": "(No beneficiary distributions have been recorded.)",
            "italic": True,
        },
    ],
}


class Command(BaseCommand):
    help = "Verify and fix the Distribution Minutes document template."

    def handle(self, *args, **options):
        tpl = DocumentTemplate.get_active("distribution_minutes", "trust")
        if not tpl:
            self.stderr.write("ERROR: No active distribution_minutes template found.")
            return

        structure = tpl.structure
        sections = structure.get("sections", [])

        self.stdout.write(f"Template: {tpl.name} (pk={tpl.pk}, v{tpl.version})")
        self.stdout.write(f"Total sections: {len(sections)}")

        # Find the conditional section with has_beneficiaries
        found = False
        for i, section in enumerate(sections):
            if section.get("type") == "conditional" and section.get("field") == "has_beneficiaries":
                found = True
                self.stdout.write(f"\nFound conditional at section index {i}:")
                self.stdout.write(json.dumps(section, indent=2))

                # Check if it matches expected
                children = section.get("children", [])
                if not children:
                    self.stdout.write("\nWARNING: No children in conditional — fixing...")
                    sections[i] = EXPECTED_CONDITIONAL
                    tpl.structure = structure
                    tpl.save(update_fields=["structure"])
                    self.stdout.write("FIXED: Replaced conditional with expected structure.")
                else:
                    table = children[0] if children else {}
                    ds = table.get("data_source", "")
                    cols = table.get("columns", [])
                    self.stdout.write(f"\n  data_source: {ds}")
                    self.stdout.write(f"  columns: {[c.get('field') for c in cols]}")
                    if ds != "beneficiary_rows":
                        self.stdout.write(f"\n  MISMATCH: data_source is '{ds}', expected 'beneficiary_rows'. Fixing...")
                        sections[i] = EXPECTED_CONDITIONAL
                        tpl.structure = structure
                        tpl.save(update_fields=["structure"])
                        self.stdout.write("  FIXED.")
                    else:
                        self.stdout.write("\n  OK: data_source and field names match expected values.")
                break

        if not found:
            self.stdout.write("\nERROR: No conditional with field='has_beneficiaries' found in sections.")
            self.stdout.write("Section types present: " + str([s.get("type") for s in sections]))
            # Insert the expected conditional before the last few sections
            # Find "There being no further business" paragraph
            for i, section in enumerate(sections):
                if section.get("type") == "paragraph" and "no further business" in section.get("text", "").lower():
                    self.stdout.write(f"\nInserting conditional before section {i}...")
                    sections.insert(i, {"type": "spacer", "lines": 1})
                    sections.insert(i + 1, EXPECTED_CONDITIONAL)
                    tpl.structure = structure
                    tpl.save(update_fields=["structure"])
                    self.stdout.write("FIXED: Inserted beneficiary conditional + table.")
                    break
