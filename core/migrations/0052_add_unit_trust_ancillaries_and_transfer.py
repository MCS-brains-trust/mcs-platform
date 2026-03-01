"""
Add two new document_type choices to LegalDocumentTemplate and LegalDocument:
  - unit_trust_deed_ancillaries: Unit Trust Deed — Ancillary Documents
  - unit_transfer: Unit Transfer Package
"""

import django.db.models.deletion
from django.db import migrations, models


# Full updated choices list (original 28 + 2 new entries)
DOCUMENT_TYPE_CHOICES = [
    ("div7a_loan_agreement", "Div 7A Loan Agreement"),
    ("trust_deed_change_trustee", "Trust Deed — Change Trustee"),
    ("trust_deed_add_beneficiary", "Trust Deed — Add Beneficiary"),
    ("trust_deed_remove_beneficiary", "Trust Deed — Remove Beneficiary"),
    ("trust_deed_extend_vesting", "Trust Deed — Extend Vesting"),
    ("trust_deed_update_distribution", "Trust Deed — Update Distribution"),
    ("company_constitution", "Company Constitution"),
    ("company_constitution_special", "Company Constitution — Special Purpose"),
    ("discretionary_trust_deed", "Discretionary Trust Deed"),
    ("unit_trust_deed", "Unit Trust Deed"),
    ("unit_trust_deed_ancillaries", "Unit Trust Deed — Ancillary Documents"),
    ("unit_transfer", "Unit Transfer Package"),
    ("partnership_agreement", "Partnership Agreement"),
    ("dividend_statement", "Dividend Statement"),
    ("dividend_minutes", "Dividend Declaration Minutes"),
    ("solvency_resolution", "Solvency Resolution"),
    ("directors_declaration", "Director's Declaration"),
    ("directors_declaration_large", "Director's Declaration — Large Proprietary"),
    ("directors_declaration_gp", "Director's Declaration — General Purpose"),
    ("directors_report", "Director's Report"),
    ("shareholder_loan_ack", "Shareholder Loan Acknowledgment"),
    ("partner_statement", "Partner Statement"),
    ("partnership_tax_summary", "Partnership Tax Summary"),
    ("engagement_letter", "Client Engagement Letter"),
    ("management_rep_letter", "Management Representation Letter"),
    ("management_rep_letter_trust", "Management Representation Letter — Trust"),
    ("management_rep_letter_partnership", "Management Representation Letter — Partnership"),
    ("client_cover_letter", "Client Cover Letter"),
    ("distribution_minutes", "Trust Distribution Minutes"),
    ("section_100a_summary", "Section 100A Summary"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0051_remaining_phase_models_and_fields"),
    ]

    operations = [
        # LegalDocumentTemplate.document_type (unique CharField)
        migrations.AlterField(
            model_name="legaldocumenttemplate",
            name="document_type",
            field=models.CharField(
                choices=DOCUMENT_TYPE_CHOICES,
                max_length=50,
                unique=True,
            ),
        ),
        # LegalDocument.document_type (non-unique CharField)
        migrations.AlterField(
            model_name="legaldocument",
            name="document_type",
            field=models.CharField(
                choices=DOCUMENT_TYPE_CHOICES,
                max_length=50,
            ),
        ),
    ]
