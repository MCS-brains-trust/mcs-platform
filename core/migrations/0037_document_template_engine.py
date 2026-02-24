"""
Migration: Add DocumentTemplate model and seed 3 trust document templates.

This replaces the hardcoded python-docx generation with a JSON-driven,
PostgreSQL-stored, admin-configurable template engine.
"""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_trust_templates(apps, schema_editor):
    """Seed the 3 trust document templates with full JSON structures."""
    DocumentTemplate = apps.get_model("core", "DocumentTemplate")

    # =========================================================================
    # 1. Distribution Minutes
    # =========================================================================
    DocumentTemplate.objects.create(
        id=uuid.uuid4(),
        name="Trust Distribution Minutes",
        document_category="distribution_minutes",
        entity_type="trust",
        description="Minutes of meeting of the trustee(s) resolving to distribute trust income to beneficiaries for the financial year.",
        version=1,
        is_active=True,
        structure={
            "metadata": {
                "page_setup": {
                    "orientation": "portrait",
                    "margin_top": 2.54,
                    "margin_bottom": 2.54,
                    "margin_left": 2.54,
                    "margin_right": 2.54,
                }
            },
            "styles": {
                "font_name": "Times New Roman",
                "font_size_body": 11,
                "font_size_heading": 14,
                "font_size_subheading": 12,
                "font_size_small": 9,
                "table_header_bg": "333333",
                "table_header_fg": "FFFFFF",
            },
            "sections": [
                {
                    "type": "heading",
                    "text": "MINUTES OF MEETING OF THE TRUSTEE",
                    "level": 1,
                    "alignment": "center",
                    "bold": True,
                },
                {
                    "type": "heading",
                    "text": "{{trust_name}}",
                    "level": 2,
                    "alignment": "center",
                    "bold": True,
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "paragraph",
                    "text": "A meeting of the trustee(s) of {{trust_name}} was held on {{minutes_date}}.",
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "PRESENT",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "{{trustee_name}}",
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "CHAIRPERSON",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "{{chairperson_name}} acted as Chairperson of the meeting.",
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "BUSINESS",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "The Chairperson advised that the purpose of the meeting was to consider the distribution of the net income of the trust for the year ended {{financial_year_end}}.",
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "RESOLUTION",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "After due consideration, it was RESOLVED that the net income of the trust for the year ended {{financial_year_end}} be distributed to the following beneficiaries in the following proportions:",
                },
                {"type": "spacer", "lines": 1},
                {
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
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "paragraph",
                    "text": "There being no further business, the meeting was declared closed.",
                },
                {"type": "spacer", "lines": 2},
                {
                    "type": "signature_block",
                    "name_field": "chairperson_name",
                    "title_field": "",
                    "date_field": "minutes_date",
                    "label": "Chairperson",
                },
            ],
        },
    )

    # =========================================================================
    # 2. Trust Election (s97)
    # =========================================================================
    DocumentTemplate.objects.create(
        id=uuid.uuid4(),
        name="Trust Election (s97 Streaming)",
        document_category="trust_election",
        entity_type="trust",
        description="Trustee resolution for streaming election under s97 ITAA 1936, including capital gains and franked dividend allocation.",
        version=1,
        is_active=True,
        structure={
            "metadata": {
                "page_setup": {
                    "orientation": "portrait",
                    "margin_top": 2.54,
                    "margin_bottom": 2.54,
                    "margin_left": 2.54,
                    "margin_right": 2.54,
                }
            },
            "styles": {
                "font_name": "Times New Roman",
                "font_size_body": 11,
                "font_size_heading": 14,
                "font_size_subheading": 12,
                "font_size_small": 9,
                "table_header_bg": "333333",
                "table_header_fg": "FFFFFF",
            },
            "sections": [
                {
                    "type": "heading",
                    "text": "TRUSTEE RESOLUTION — STREAMING ELECTION",
                    "level": 1,
                    "alignment": "center",
                    "bold": True,
                },
                {
                    "type": "heading",
                    "text": "{{trust_name}}",
                    "level": 2,
                    "alignment": "center",
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "Section 97 Income Tax Assessment Act 1936",
                    "alignment": "center",
                    "italic": True,
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "paragraph",
                    "text": "The trustee(s) of {{trust_name}}, being {{trustee_name}}, hereby resolve as follows in respect of the income year ended {{financial_year_end}}:",
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "1. DISTRIBUTABLE INCOME",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "The total distributable income of the trust for the year ended {{financial_year_end}} is {{distributable_income}}.",
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "2. DISTRIBUTION TO BENEFICIARIES",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "The trustee(s) resolve to distribute the net income of the trust to the following beneficiaries:",
                },
                {
                    "type": "table",
                    "columns": [
                        {"header": "Beneficiary", "field": "name", "width_cm": 4, "alignment": "left"},
                        {"header": "Type", "field": "type", "width_cm": 2.5, "alignment": "center"},
                        {"header": "Distribution", "field": "distribution", "width_cm": 3, "alignment": "right"},
                        {"header": "Net Tax", "field": "net_tax", "width_cm": 3, "alignment": "right"},
                        {"header": "Eff. Rate", "field": "effective_rate", "width_cm": 2, "alignment": "right"},
                        {"header": "Notes", "field": "notes", "width_cm": 3, "alignment": "left"},
                    ],
                    "data_source": "beneficiary_rows",
                    "totals": {
                        "label": "TOTAL",
                        "distribution": "{{total_distributed}}",
                        "net_tax": "{{total_tax_payable}}",
                    },
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "conditional",
                    "field": "has_streaming",
                    "children": [
                        {
                            "type": "heading",
                            "text": "3. STREAMING ELECTION",
                            "level": 2,
                            "bold": True,
                        },
                        {
                            "type": "paragraph",
                            "text": "Pursuant to section 97 of the Income Tax Assessment Act 1936, the trustee(s) hereby make the following streaming election in respect of specific categories of income:",
                        },
                        {
                            "type": "conditional",
                            "field": "has_capital_gains",
                            "children": [
                                {
                                    "type": "paragraph",
                                    "text": "Total Capital Gains: {{capital_gains_total}}",
                                    "bold": True,
                                },
                            ],
                        },
                        {
                            "type": "conditional",
                            "field": "has_franked_dividends",
                            "children": [
                                {
                                    "type": "paragraph",
                                    "text": "Total Franked Dividends: {{franked_dividends_total}}",
                                    "bold": True,
                                },
                                {
                                    "type": "paragraph",
                                    "text": "Total Franking Credits: {{franking_credits_total}}",
                                    "bold": True,
                                },
                            ],
                        },
                        {"type": "spacer", "lines": 1},
                        {
                            "type": "paragraph",
                            "text": "The streaming allocation to each beneficiary is as follows:",
                        },
                        {
                            "type": "table",
                            "columns": [
                                {"header": "Beneficiary", "field": "name", "width_cm": 4, "alignment": "left"},
                                {"header": "Capital Gains", "field": "capital_gains", "width_cm": 3, "alignment": "right"},
                                {"header": "Franked Dividends", "field": "franked_dividends", "width_cm": 3, "alignment": "right"},
                                {"header": "Franking Credits", "field": "franking_credits", "width_cm": 3, "alignment": "right"},
                                {"header": "Other Income", "field": "other_income", "width_cm": 3, "alignment": "right"},
                            ],
                            "data_source": "streaming_rows",
                        },
                    ],
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "paragraph",
                    "text": "This resolution is made on {{resolution_date}} and is effective for the income year ended {{financial_year_end}}.",
                },
                {"type": "spacer", "lines": 2},
                {
                    "type": "signature_block",
                    "name_field": "chairperson_name",
                    "title_field": "",
                    "date_field": "resolution_date",
                    "label": "Trustee",
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "disclaimer",
                    "heading": "DISCLAIMER",
                    "text": "This election is made in accordance with the provisions of the Income Tax Assessment Act 1936 and the Income Tax Assessment Act 1997. The trustee(s) confirm that this resolution was made prior to the end of the income year or within the period allowed by the Commissioner of Taxation.",
                },
            ],
        },
    )

    # =========================================================================
    # 3. Tax Planning Summary
    # =========================================================================
    DocumentTemplate.objects.create(
        id=uuid.uuid4(),
        name="Tax Planning Summary",
        document_category="tax_planning_summary",
        entity_type="trust",
        description="Comprehensive tax planning summary showing distributable income, beneficiary allocations, estimated tax, and accountant's recommendation.",
        version=1,
        is_active=True,
        structure={
            "metadata": {
                "page_setup": {
                    "orientation": "portrait",
                    "margin_top": 2.54,
                    "margin_bottom": 2.54,
                    "margin_left": 2.54,
                    "margin_right": 2.54,
                }
            },
            "styles": {
                "font_name": "Times New Roman",
                "font_size_body": 11,
                "font_size_heading": 14,
                "font_size_subheading": 12,
                "font_size_small": 9,
                "table_header_bg": "333333",
                "table_header_fg": "FFFFFF",
            },
            "sections": [
                {
                    "type": "heading",
                    "text": "TAX PLANNING SUMMARY",
                    "level": 1,
                    "alignment": "center",
                    "bold": True,
                },
                {
                    "type": "heading",
                    "text": "{{trust_name}}",
                    "level": 2,
                    "alignment": "center",
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "For the year ended {{financial_year_end}}",
                    "alignment": "center",
                    "italic": True,
                },
                {
                    "type": "paragraph",
                    "text": "Scenario: {{scenario_name}}",
                    "alignment": "center",
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "1. DISTRIBUTABLE INCOME",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "paragraph",
                    "text": "The distributable income of {{trust_name}} for the year ended {{financial_year_end}} is {{distributable_income}}, comprising the following components:",
                },
                {
                    "type": "key_value_table",
                    "items": [
                        {"label": "Net Accounting Profit (per TB)", "value": "{{distributable_income}}"},
                        {"label": "Add: Non-Deductible Expenses", "value": "{{non_deductible_expenses}}"},
                        {"label": "Less: Non-Assessable Income", "value": "{{non_assessable_income}}"},
                        {"label": "Capital Gains", "value": "{{capital_gains}}"},
                        {"label": "Franked Dividends", "value": "{{franked_dividends}}"},
                        {"label": "Franking Credits", "value": "{{franking_credits}}"},
                    ],
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "2. BENEFICIARY TAX POSITION",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "table",
                    "columns": [
                        {"header": "Beneficiary", "field": "name", "width_cm": 4, "alignment": "left"},
                        {"header": "Type", "field": "type", "width_cm": 2.5, "alignment": "center"},
                        {"header": "Distribution", "field": "distribution", "width_cm": 3, "alignment": "right"},
                        {"header": "Net Tax", "field": "net_tax", "width_cm": 3, "alignment": "right"},
                        {"header": "Eff. Rate", "field": "effective_rate", "width_cm": 2, "alignment": "right"},
                        {"header": "Notes", "field": "notes", "width_cm": 3, "alignment": "left"},
                    ],
                    "data_source": "beneficiary_rows",
                    "totals": {
                        "label": "TOTAL",
                        "distribution": "{{total_distributed}}",
                        "net_tax": "{{total_tax_payable}}",
                    },
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "3. SUMMARY",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "key_value_table",
                    "items": [
                        {"label": "Total Distributable Income", "value": "{{distributable_income}}"},
                        {"label": "Total Proposed Distributions", "value": "{{total_distributed}}"},
                        {"label": "Undistributed Balance", "value": "{{undistributed_balance}}"},
                        {"label": "Total Estimated Tax", "value": "{{total_tax_payable}}"},
                        {"label": "Weighted Effective Tax Rate", "value": "{{weighted_effective_rate}}"},
                    ],
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "heading",
                    "text": "4. ACCOUNTANT'S RECOMMENDATION",
                    "level": 2,
                    "bold": True,
                },
                {
                    "type": "conditional",
                    "field": "has_recommendation",
                    "children": [
                        {
                            "type": "paragraph",
                            "text": "{{accountant_recommendation}}",
                        },
                    ],
                    "else_children": [
                        {
                            "type": "paragraph",
                            "text": "(No recommendation recorded.)",
                            "italic": True,
                        },
                    ],
                },
                {"type": "spacer", "lines": 1},
                {
                    "type": "disclaimer",
                    "heading": "DISCLAIMER",
                    "text": "This tax planning summary is based on the information provided and current tax legislation as at the date of preparation. It is intended as a guide only and does not constitute formal tax advice. Actual tax outcomes may differ based on individual circumstances, changes in legislation, or ATO rulings. Please contact our office to discuss any questions.",
                },
            ],
        },
    )


def reverse_seed(apps, schema_editor):
    DocumentTemplate = apps.get_model("core", "DocumentTemplate")
    DocumentTemplate.objects.filter(
        document_category__in=["distribution_minutes", "trust_election", "tax_planning_summary"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0036_add_trust_document_types"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(help_text="Human-readable template name, e.g. 'Trust Distribution Minutes v2'", max_length=255)),
                ("document_category", models.CharField(
                    choices=[
                        ("distribution_minutes", "Distribution Minutes"),
                        ("trust_election", "Trust Election (s97)"),
                        ("tax_planning_summary", "Tax Planning Summary"),
                        ("financial_statements", "Financial Statements"),
                        ("beneficiary_statement", "Beneficiary Statement"),
                        ("partner_statement", "Partner Statement"),
                        ("other", "Other"),
                    ],
                    help_text="The type of document this template generates.",
                    max_length=30,
                )),
                ("entity_type", models.CharField(
                    blank=True,
                    choices=[
                        ("individual", "Individual"),
                        ("company", "Company"),
                        ("trust", "Trust"),
                        ("smsf", "SMSF"),
                        ("partnership", "Partnership"),
                    ],
                    help_text="Restrict to a specific entity type, or leave blank for all.",
                    max_length=20,
                )),
                ("description", models.TextField(blank=True, help_text="Internal notes about this template.")),
                ("structure", models.JSONField(default=dict, help_text="JSON template definition (metadata, styles, sections with merge fields).")),
                ("version", models.PositiveIntegerField(default=1, help_text="Auto-incremented on each save via admin.")),
                ("is_active", models.BooleanField(default=True, help_text="Only one active template per document_category + entity_type.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("superseded_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="supersedes",
                    to="core.documenttemplate",
                    help_text="Points to the newer version that replaced this one.",
                )),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_templates",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["document_category", "entity_type", "-version"],
            },
        ),
        migrations.AddConstraint(
            model_name="documenttemplate",
            constraint=models.UniqueConstraint(
                fields=["document_category", "entity_type", "version"],
                name="unique_template_version",
            ),
        ),
        migrations.RunPython(seed_trust_templates, reverse_seed),
    ]
