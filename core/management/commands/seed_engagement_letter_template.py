"""
Management command to seed the engagement letter .docx template into
LegalDocumentTemplate.  Run once on the server after deploying.

Usage:
    python3 manage.py seed_engagement_letter_template \
        --file /path/to/Engagement_Letter_TEMPLATE_BW_v2.docx
"""
import os
from django.core.management.base import BaseCommand, CommandError
from django.core.files import File
from core.models import LegalDocumentTemplate


class Command(BaseCommand):
    help = "Seed the engagement letter .docx template into LegalDocumentTemplate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            required=True,
            help="Absolute path to the .docx template file.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Replace existing template if one already exists.",
        )

    def handle(self, *args, **options):
        docx_path = options["file"]
        force = options["force"]

        if not os.path.isfile(docx_path):
            raise CommandError(f"File not found: {docx_path}")

        existing = LegalDocumentTemplate.objects.filter(
            document_type="engagement_letter"
        ).first()

        if existing and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"Engagement letter template already exists (id={existing.pk}, "
                    f"v{existing.version}). Use --force to replace it."
                )
            )
            return

        if existing and force:
            # Deactivate old version
            existing.is_active = False
            existing.save(update_fields=["is_active"])
            self.stdout.write(f"Deactivated existing template v{existing.version}.")
            new_version = existing.version + 1
            # Delete the old record so unique constraint doesn't block us
            existing.delete()
        else:
            new_version = 1

        with open(docx_path, "rb") as f:
            template = LegalDocumentTemplate(
                name="Client Engagement Letter (APES 305)",
                document_type="engagement_letter",
                entity_types=["company", "trust", "partnership", "sole_trader", "individual"],
                version=new_version,
                is_active=True,
                solicitor_approved=False,
                variable_schema={
                    "client_name": "string",
                    "client_address": "string",
                    "engagement_date": "string",
                    "fee_amount": "string",
                    "fee_basis": "string",
                    "services_engaged": "list",
                    "show_service_compilation": "boolean",
                    "show_service_tax_return": "boolean",
                    "show_service_bas": "boolean",
                    "show_service_asic": "boolean",
                    "show_service_trust_distribution": "boolean",
                    "show_service_div7a": "boolean",
                    "show_service_fbt": "boolean",
                    "show_service_tpar": "boolean",
                    "show_service_payroll_tax": "boolean",
                    "show_fixed_fee_clause": "boolean",
                    "show_hourly_clause": "boolean",
                    "show_estimate_clause": "boolean",
                    "show_additional_terms": "boolean",
                    "prior_year_letter_existed": "boolean",
                    "show_client_portal": "boolean",
                    "client_portal_url": "string",
                    "show_fusesign": "boolean",
                    "practice_name": "string",
                    "practice_tax_agent_number": "string",
                    "practice_professional_body": "string",
                    "practice_signatory_name": "string",
                    "practice_signatory_designation": "string",
                    "tpb_registration_statement": "string",
                    "apes_305_reference": "string",
                    "is_company": "boolean",
                    "is_trust": "boolean",
                    "is_partnership": "boolean",
                    "signing_directors": "list",
                    "trustees": "list",
                    "partners": "list",
                    "trustee_names_list": "string",
                    "trust_name": "string",
                    "entity_abn": "string",
                    "div7a_benchmark_rate": "string",
                },
            )
            template.template_file.save(
                os.path.basename(docx_path),
                File(f),
                save=False,
            )
            template.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Engagement letter template seeded successfully: "
                f"'{template.name}' v{template.version} (id={template.pk})"
            )
        )
