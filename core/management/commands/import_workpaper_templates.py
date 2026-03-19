"""
Management command: import_workpaper_templates

Bulk-registers the 19 MC&S workpaper .docx templates into the WorkPaperTemplate
database table.  The converted files (with platform merge fields injected) must
be present in the directory specified by --source (default: ./workpaper_templates/).

Usage (on the server):
    python manage.py import_workpaper_templates --source /path/to/converted/files/

The command is idempotent: it updates existing records (matched by name) rather
than creating duplicates.
"""
import os
import shutil

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from core.models import WorkPaperTemplate


WORKPAPER_REGISTRY = [
    {
        "filename": "WP01_Div7A_Loan_Calculation.docx",
        "name": "Division 7A — Loan Calculation & Minimum Repayment Schedule",
        "category": WorkPaperTemplate.Category.DIVISION_7A,
        "description": (
            "Calculates the minimum yearly repayment for a Div 7A loan, "
            "the annual repayment schedule, deemed dividend exposure, and "
            "distributable surplus check. Eva-triggered evidence workpaper."
        ),
        "entity_types": ["company", "trust"],
        "sort_order": 10,
    },
    {
        "filename": "WP02_Div7A_UPE_Confirmation.docx",
        "name": "Division 7A — UPE Sub-Trust or Repayment Confirmation",
        "category": WorkPaperTemplate.Category.DIVISION_7A,
        "description": (
            "Confirms the arrangement type for an unpaid present entitlement (UPE): "
            "sub-trust (Option 1) or loan conversion (Option 2). "
            "Eva-triggered evidence workpaper."
        ),
        "entity_types": ["company", "trust"],
        "sort_order": 20,
    },
    {
        "filename": "WP19_Div7A_Unfranked_Dividend_Calculation.docx",
        "name": "Division 7A — Unfranked Dividend Calculation",
        "category": WorkPaperTemplate.Category.DIVISION_7A,
        "description": (
            "Calculates the unfranked dividend amount required to clear a Div 7A "
            "loan balance and documents the distributable surplus position."
        ),
        "entity_types": ["company", "trust"],
        "sort_order": 30,
    },
    {
        "filename": "WP03_SGC_Shortfall_Calculation.docx",
        "name": "Superannuation Guarantee — Shortfall Calculation",
        "category": WorkPaperTemplate.Category.PAYROLL,
        "description": (
            "Calculates any SGC shortfall for employees and contractors, including "
            "quarterly timing analysis and SGC exposure assessment."
        ),
        "entity_types": [],
        "sort_order": 10,
    },
    {
        "filename": "WP04_Contractor_SG_Classification.docx",
        "name": "Contractor SG Classification Assessment",
        "category": WorkPaperTemplate.Category.PAYROLL,
        "description": (
            "Classifies up to five contractors as employees or genuine contractors "
            "for SG purposes using the ATO multi-factor test."
        ),
        "entity_types": [],
        "sort_order": 20,
    },
    {
        "filename": "WP14_Wages_Payroll_Reconciliation.docx",
        "name": "Wages & Payroll Reconciliation",
        "category": WorkPaperTemplate.Category.PAYROLL,
        "description": (
            "Reconciles wages expense per the trial balance to STP/payroll records "
            "and PAYG withholding to the BAS/IAS. Documents any variances."
        ),
        "entity_types": [],
        "sort_order": 30,
    },
    {
        "filename": "WP05_GST_Reconciliation_Schedule.docx",
        "name": "GST Reconciliation Schedule",
        "category": WorkPaperTemplate.Category.ACCOUNT_RECONCILIATION,
        "description": (
            "Reconciles GST collected and GST paid per the trial balance to the "
            "BAS lodgements for the financial year. Documents sales and input tax "
            "credit variances."
        ),
        "entity_types": [],
        "sort_order": 10,
    },
    {
        "filename": "WP13_Bank_Reconciliation_Summary.docx",
        "name": "Bank Reconciliation Summary",
        "category": WorkPaperTemplate.Category.ACCOUNT_RECONCILIATION,
        "description": (
            "Summarises the bank reconciliation position for all accounts at "
            "year-end, confirming the closing balance per the bank statement "
            "agrees to the trial balance."
        ),
        "entity_types": [],
        "sort_order": 20,
    },
    {
        "filename": "WP18_Prepayments_Accruals_Schedule.docx",
        "name": "Prepayments & Accruals Schedule",
        "category": WorkPaperTemplate.Category.ACCOUNT_RECONCILIATION,
        "description": (
            "Schedules prepayment and accrual balances at year-end, reconciles "
            "them to the trial balance, and documents any variances."
        ),
        "entity_types": [],
        "sort_order": 30,
    },
    {
        "filename": "WP06_Section100A_Risk_Assessment.docx",
        "name": "Section 100A Risk Assessment",
        "category": WorkPaperTemplate.Category.TRUST_DISTRIBUTION,
        "description": (
            "Assesses the risk that a trust distribution arrangement constitutes "
            "a reimbursement agreement under s100A ITAA 1936. "
            "Eva-triggered evidence workpaper."
        ),
        "entity_types": ["trust"],
        "sort_order": 10,
    },
    {
        "filename": "WP07_Trust_Distribution_Reconciliation.docx",
        "name": "Trust Distribution Reconciliation",
        "category": WorkPaperTemplate.Category.TRUST_DISTRIBUTION,
        "description": (
            "Reconciles the trust's distributable income to the amounts resolved "
            "in the distribution minutes and confirms beneficiary entitlements."
        ),
        "entity_types": ["trust"],
        "sort_order": 20,
    },
    {
        "filename": "WP08_Going_Concern_Assessment.docx",
        "name": "Going Concern Assessment",
        "category": WorkPaperTemplate.Category.GENERAL,
        "description": (
            "Documents the accountant's assessment of whether the entity can "
            "continue as a going concern, including cash flow projections and "
            "mitigating factors."
        ),
        "entity_types": [],
        "sort_order": 10,
    },
    {
        "filename": "WP09_Related_Party_Transaction_Register.docx",
        "name": "Related Party Transaction Register",
        "category": WorkPaperTemplate.Category.GENERAL,
        "description": (
            "Records all related party transactions during the financial year, "
            "confirms arm's length terms, and documents disclosure requirements."
        ),
        "entity_types": [],
        "sort_order": 20,
    },
    {
        "filename": "WP10_ATO_Benchmark_Comparison.docx",
        "name": "ATO Industry Benchmark Comparison",
        "category": WorkPaperTemplate.Category.GENERAL,
        "description": (
            "Compares the entity's key financial ratios to ATO industry benchmarks "
            "and documents explanations for any material variances."
        ),
        "entity_types": [],
        "sort_order": 30,
    },
    {
        "filename": "WP11_TPAR_Contractor_Register.docx",
        "name": "TPAR Contractor Payment Register",
        "category": WorkPaperTemplate.Category.GENERAL,
        "description": (
            "Registers all contractor payments for TPAR reporting purposes, "
            "confirms ABNs, and documents the TPAR lodgement status."
        ),
        "entity_types": [],
        "sort_order": 40,
    },
    {
        "filename": "WP12_Thin_Capitalisation_Assessment.docx",
        "name": "Thin Capitalisation Assessment",
        "category": WorkPaperTemplate.Category.GENERAL,
        "description": (
            "Assesses whether the entity is subject to thin capitalisation rules "
            "and calculates the maximum allowable debt deduction."
        ),
        "entity_types": ["company"],
        "sort_order": 50,
    },
    {
        "filename": "WP15_Fixed_Asset_Movement_Schedule.docx",
        "name": "Fixed Asset Movement Schedule",
        "category": WorkPaperTemplate.Category.DEPRECIATION,
        "description": (
            "Schedules additions, disposals, and depreciation for all fixed assets "
            "during the year and reconciles the closing WDV to the trial balance."
        ),
        "entity_types": [],
        "sort_order": 10,
    },
    {
        "filename": "WP16_Loans_Borrowings_Movement_Schedule.docx",
        "name": "Loans & Borrowings Movement Schedule",
        "category": WorkPaperTemplate.Category.LOAN_ACCOUNT,
        "description": (
            "Schedules all loan and borrowing movements during the year, "
            "reconciles closing balances to the trial balance, and documents "
            "interest calculations."
        ),
        "entity_types": [],
        "sort_order": 10,
    },
    {
        "filename": "WP17_FBT_Exposure_Summary.docx",
        "name": "FBT Exposure Summary",
        "category": WorkPaperTemplate.Category.GENERAL,
        "description": (
            "Summarises the entity's FBT exposure across all benefit categories "
            "and documents the FBT liability or nil position."
        ),
        "entity_types": [],
        "sort_order": 60,
    },
]


class Command(BaseCommand):
    help = "Bulk-register MC&S workpaper .docx templates into the WorkPaperTemplate table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=None,
            help="Directory containing the converted .docx files. "
                 "Defaults to workpaper_templates/ inside the project root.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be done without making any changes.",
        )

    def handle(self, *args, **options):
        source_dir = options["source"]
        if source_dir is None:
            source_dir = os.path.join(settings.BASE_DIR, "workpaper_templates")

        if not os.path.isdir(source_dir):
            raise CommandError(
                f"Source directory not found: {source_dir}\n"
                f"Run the conversion script first, then copy files to this directory."
            )

        dry_run = options["dry_run"]
        created = 0
        updated = 0
        skipped = 0

        for entry in WORKPAPER_REGISTRY:
            filename = entry["filename"]
            src_path = os.path.join(source_dir, filename)

            if not os.path.exists(src_path):
                self.stdout.write(
                    self.style.WARNING(f"  MISSING: {filename} — skipping")
                )
                skipped += 1
                continue

            existing = WorkPaperTemplate.objects.filter(name=entry["name"]).first()

            if dry_run:
                action = "UPDATE" if existing else "CREATE"
                self.stdout.write(f"  [{action}] {entry['name']}")
                continue

            # Determine file format from extension
            ext = filename.rsplit(".", 1)[-1].lower()
            file_format = WorkPaperTemplate.FileFormat.DOCX if ext == "docx" else WorkPaperTemplate.FileFormat.XLSX

            # Build the media destination path
            media_dest_rel = f"workpaper_templates/{filename}"
            media_dest_abs = os.path.join(settings.MEDIA_ROOT, media_dest_rel)
            os.makedirs(os.path.dirname(media_dest_abs), exist_ok=True)
            shutil.copy2(src_path, media_dest_abs)

            if existing:
                existing.category = entry["category"]
                existing.description = entry["description"]
                existing.entity_types = entry["entity_types"]
                existing.file_format = file_format
                existing.sort_order = entry["sort_order"]
                existing.is_active = True
                existing.template_file.name = media_dest_rel
                existing.save()
                self.stdout.write(self.style.SUCCESS(f"  Updated: {entry['name']}"))
                updated += 1
            else:
                WorkPaperTemplate.objects.create(
                    name=entry["name"],
                    category=entry["category"],
                    description=entry["description"],
                    entity_types=entry["entity_types"],
                    file_format=file_format,
                    sort_order=entry["sort_order"],
                    is_active=True,
                    template_file=media_dest_rel,
                )
                self.stdout.write(self.style.SUCCESS(f"  Created: {entry['name']}"))
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created: {created}, Updated: {updated}, Skipped: {skipped}"
            )
        )
