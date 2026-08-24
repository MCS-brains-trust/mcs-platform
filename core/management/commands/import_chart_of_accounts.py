"""
Management command to import entity-type-specific chart of accounts
from the four Excel files provided by MC&S.

Usage:
    python manage.py import_chart_of_accounts
    python manage.py import_chart_of_accounts --clear   # Wipe and reimport
    python manage.py import_chart_of_accounts --json data/all_accounts.json  # From pre-parsed JSON
"""
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand
from core.models import ChartOfAccount


# Section name mapping from raw Excel to model choices
SECTION_MAP = {
    "Suspense": "suspense",
    "Revenue": "revenue",
    "Cost of Sales": "cost_of_sales",
    "Expenses": "expenses",
    "Other Expenses": "expenses",
    "Assets": "assets",
    "Liabilities": "liabilities",
    "Equity": "equity",
    "Capital Accounts": "capital_accounts",
    "P and L Appropriation": "pl_appropriation",
}


def canonical_template_code(entity_type, account_code, account_name):
    """The code this template row belongs under.

    data/ChartofAccounts*.xlsx carries only the four-digit HandiLedger form,
    so a re-import kept recreating "0620 Rents received" beside the "620
    Rents received" StatementHub actually uses -- the same account offered
    twice in every picker. A padded code therefore collapses onto an
    identically-named unpadded twin.

    Deliberately a de-duplication and NOT a renumbering. A padded code with
    no such twin is a distinct account -- the trust template's
    0200 Sales - Livestock, 0401 Proceeds - Hay -- and live trial balances
    already post to padded revenue codes, so those keep their code. So does
    0000, which strips to nothing. A twin under a different name is left
    alone too: same number, different account, and merging would quietly
    overwrite one with the other.
    """
    code = str(account_code or "").strip()
    base = code.split(".", 1)[0]
    if not (base.startswith("0") and base.isdigit()):
        return code
    stripped = base.lstrip("0")
    if not stripped:
        return code
    candidate = stripped + code[len(base):]
    twin = ChartOfAccount.objects.filter(
        entity_type=entity_type, account_code=candidate,
    ).first()
    if twin and (twin.account_name or "").strip().lower() == \
            str(account_name or "").strip().lower():
        return candidate
    return code


def parse_excel(filepath, entity_type, has_tax_col=True):
    """Parse a chart of accounts Excel file and return a list of account dicts."""
    import openpyxl

    wb = openpyxl.load_workbook(filepath)
    ws = wb.active
    accounts = []
    current_classification = ""
    current_section = ""
    order = 0

    for row in ws.iter_rows(min_row=5, values_only=True):
        vals = list(row)

        col_a = str(vals[0]).strip() if vals[0] else ""
        col_b = str(vals[1]).strip() if vals[1] else ""
        col_c = str(vals[2]).strip() if vals[2] else ""
        col_d = (
            str(vals[3]).strip()
            if has_tax_col and len(vals) > 3 and vals[3]
            else ""
        )

        if not col_a and not col_b and not col_c:
            continue

        # Detect misaligned rows (trust file): code in col_a, name in col_b, col_c empty
        is_misaligned = False
        if col_a and col_b and (not col_c or col_c == "None"):
            try:
                float(col_a.replace(".", ""))
                is_misaligned = True
            except ValueError:
                pass

        if is_misaligned:
            account_code = col_a
            account_name = col_b
            tax_code = ""
            classification = current_classification
        elif col_a and not col_b and (not col_c or col_c == "None"):
            # Section/classification header
            sl = col_a.lower().strip()
            if sl in ("revenue", "income"):
                current_section = "Revenue"
            elif sl in ("cost of sales", "cost of goods sold"):
                current_section = "Cost of Sales"
            elif sl in ("expenses", "expense"):
                current_section = "Expenses"
            elif sl in ("other expenses",):
                current_section = "Other Expenses"
            elif sl in ("equity", "capital"):
                current_section = "Equity"
            elif sl in ("assets", "current assets", "non-current assets", "fixed assets"):
                current_section = "Assets"
            elif sl in ("liabilities", "current liabilities", "non-current liabilities"):
                current_section = "Liabilities"
            elif sl in ("suspense",):
                current_section = "Suspense"
            elif "capital" in sl:
                current_section = "Capital Accounts"
            elif "appropriation" in sl:
                current_section = "P and L Appropriation"
            else:
                current_section = col_a
            continue
        elif col_b and col_b != "None":
            classification = col_a if col_a else current_classification
            if classification:
                current_classification = classification
            account_code = col_b
            account_name = col_c if col_c and col_c != "None" else ""
            tax_code = col_d if has_tax_col else ""
        else:
            continue

        if not account_name:
            continue

        # Clean up
        account_name = account_name.replace("**********", "").strip().strip("*").strip()
        if not account_name:
            continue

        order += 1
        accounts.append({
            "account_code": account_code,
            "account_name": account_name,
            "classification": current_classification,
            "section": current_section,
            "tax_code": tax_code,
            "entity_type": entity_type,
            "display_order": order,
        })

    return accounts


class Command(BaseCommand):
    help = "Import entity-type-specific chart of accounts from Excel files or pre-parsed JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing ChartOfAccount records before importing",
        )
        parser.add_argument(
            "--json",
            type=str,
            default="",
            help="Path to pre-parsed JSON file (skip Excel parsing)",
        )
        parser.add_argument(
            "--data-dir",
            type=str,
            default="data",
            help="Directory containing the Excel files (default: data/)",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            count = ChartOfAccount.objects.count()
            ChartOfAccount.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {count} existing records."))

        if options["json"]:
            self._import_from_json(options["json"])
        else:
            self._import_from_excel(options["data_dir"])

    def _import_from_json(self, json_path):
        with open(json_path) as f:
            data = json.load(f)

        total = 0
        for entity_type, accounts in data.items():
            created = 0
            for i, acct in enumerate(accounts):
                section_key = SECTION_MAP.get(acct["section"], "expenses")
                _, was_created = ChartOfAccount.objects.update_or_create(
                    entity_type=entity_type,
                    account_code=canonical_template_code(
                        entity_type, acct["account_code"], acct["account_name"]),
                    defaults={
                        "account_name": acct["account_name"],
                        "classification": acct.get("classification", ""),
                        "section": section_key,
                        "tax_code": acct.get("tax_code", ""),
                        "display_order": i + 1,
                        "is_active": True,
                    },
                )
                if was_created:
                    created += 1
            total += len(accounts)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {entity_type}: {len(accounts)} accounts ({created} new)"
                )
            )
        self.stdout.write(self.style.SUCCESS(f"\nTotal: {total} accounts imported."))

    def _import_from_excel(self, data_dir):
        base = Path(data_dir)
        files = {
            "company": (base / "ChartofAccountscompany.xlsx", True),
            "partnership": (base / "ChartofAccountspartnership.xlsx", True),
            "sole_trader": (base / "ChartofAccountssoletrader.xlsx", True),
            "trust": (base / "ChartofAccountstrust.xlsx", False),
        }

        total = 0
        for entity_type, (filepath, has_tax) in files.items():
            if not filepath.exists():
                self.stdout.write(self.style.ERROR(f"  File not found: {filepath}"))
                continue

            accounts = parse_excel(str(filepath), entity_type, has_tax_col=has_tax)
            created = 0
            for acct in accounts:
                section_key = SECTION_MAP.get(acct["section"], "expenses")
                _, was_created = ChartOfAccount.objects.update_or_create(
                    entity_type=entity_type,
                    account_code=canonical_template_code(
                        entity_type, acct["account_code"], acct["account_name"]),
                    defaults={
                        "account_name": acct["account_name"],
                        "classification": acct.get("classification", ""),
                        "section": section_key,
                        "tax_code": acct.get("tax_code", ""),
                        "display_order": acct.get("display_order", 0),
                        "is_active": True,
                    },
                )
                if was_created:
                    created += 1
            total += len(accounts)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {entity_type}: {len(accounts)} accounts ({created} new)"
                )
            )

        self.stdout.write(self.style.SUCCESS(f"\nTotal: {total} accounts imported."))
