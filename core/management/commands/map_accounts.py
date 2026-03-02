"""
Management command to map ChartOfAccount records to AccountMapping line items.
Maps all 1,410 detailed accounts to the 70 financial statement line items
based on account name, section, and classification keywords.
"""
import os
import sys
import django

from django.core.management.base import BaseCommand
from core.models import ChartOfAccount, AccountMapping


# ---------------------------------------------------------------------------
# Mapping rules: account_name keywords -> AccountMapping standard_code
# Rules are checked in order; first match wins.
# ---------------------------------------------------------------------------

# REVENUE section
REVENUE_RULES = [
    # Interest
    (["interest received", "interest income"], "IS-REV-003"),
    # Dividends
    (["dividend"], "IS-REV-004"),
    # Rental / Lease
    (["rent received", "rents received", "lease income", "hire income"], "IS-REV-005"),
    # Distributions
    (["distribution from"], "IS-REV-007"),
    # Government subsidies
    (["government", "jobkeeper", "subsidies received"], "IS-REV-008"),
    # Capital gains
    (["capital gain", "profit on sale"], "IS-REV-009"),
    # Gain on disposal
    (["gain on disposal"], "IS-REV-006"),
    # Other income
    (["other income", "other non-operating", "rebates", "insurance recoveries",
     "foreign exchange", "bad debts recovered", "discounts received",
     "commissions received", "inventory movement", "royalties received",
     "attributed foreign", "net foreign", "fbt employee"], "IS-REV-002"),
    # Main revenue (sales, fees, gross receipts, etc.)
    (["sales", "gross receipts", "professional fees", "income from labour",
     "income under voluntary", "income where abn"], "IS-REV-001"),
]

# COST OF SALES section (within Expenses)
COS_RULES = [
    (["opening stock", "opening finished", "opening raw", "opening work in progress"], "IS-COS-002"),
    (["closing stock", "closing finished", "closing raw", "closing work in progress"], "IS-COS-004"),
    (["purchases", "direct costs", "direct labour", "materials & supplies",
     "materials", "crates & packing", "packaging"], "IS-COS-003"),
    (["cost of sales", "cost of goods"], "IS-COS-001"),
]

# EXPENSES section
EXPENSE_RULES = [
    # Accounting & professional
    (["accountancy", "audit", "bookkeeping", "legal fees", "consultants fees",
     "professional fees", "disbursements", "filing fees"], "IS-EXP-001"),
    # Advertising
    (["advertising", "promotion"], "IS-EXP-002"),
    # Bad debts
    (["bad debts"], "IS-EXP-003"),
    # Bank charges
    (["bank fees", "bank charges", "merchant fees", "terms charges"], "IS-EXP-004"),
    # Borrowing costs
    (["borrowing expenses", "borrowing costs"], "IS-EXP-005"),
    # Contractors
    (["contractor", "subcontractor", "contract payments", "agency fees",
     "management agent", "management fees", "service fees"], "IS-EXP-006"),
    # Depreciation
    (["depreciation", "amortisation", "amortization", "write-down to recoverable"], "IS-EXP-007"),
    # Employee benefits
    (["salaries", "wages", "directors fees", "directors salaries",
     "staff amenities", "staff training", "staff uniforms",
     "long service leave", "payroll tax", "workcover", "employment expenses",
     "casual labour", "protective clothing", "uniform", "prepaid wages"], "IS-EXP-008"),
    # Superannuation
    (["superannuation"], "IS-EXP-017"),
    # Insurance
    (["insurance"], "IS-EXP-009"),
    # Motor vehicle
    (["m/v car", "m/v commercial", "m/v other", "motor vehicle"], "IS-EXP-010"),
    # Occupancy
    (["rates & land", "rates & taxes", "light & power", "electricity",
     "water", "cleaning", "rubbish removal", "pest control",
     "security", "environmental"], "IS-EXP-011"),
    # Rent
    (["rent on land", "rent and outgoings", "lease payments",
     "sundry rental"], "IS-EXP-018"),
    # Repairs & maintenance
    (["repairs & maintenance", "replacements"], "IS-EXP-012"),
    # Telephone
    (["telephone", "internet"], "IS-EXP-013"),
    # Travel
    (["travel", "accommodation", "conference", "seminar"], "IS-EXP-014"),
    # Utilities (already covered by occupancy for electricity/water)
    (["utilities"], "IS-EXP-015"),
    # Loss on disposal
    (["loss on disposal", "loss on sale"], "IS-EXP-019"),
    # Dividends / distributions / tax related in expenses
    (["dividend deductions", "partnership/trust distribution",
     "royalty deductions", "royalty payments"], "IS-EXP-016"),
    # Other expenses (catch-all for expenses)
    (["sundry expenses", "donations", "entertainment", "subscriptions",
     "licences", "postage", "printing", "stationery", "office expenses",
     "computer expenses", "courier", "storage", "tools", "minor equipment",
     "expensed equipment", "catering", "paper products", "bar code",
     "delivery", "debt collection", "commissions", "discounts allowed",
     "fees & charges", "fines", "livestock", "research", "sales tax",
     "wine equalisation", "luxury car tax"], "IS-EXP-016"),
]

# ASSETS section
ASSET_RULES = [
    # Cash
    (["cash at bank", "cash on hand", "bank account", "short term deposit"], "BS-CA-001"),
    # Trade receivables
    (["trade debtors", "provision doubtful", "provision for doubtful"], "BS-CA-002"),
    # Inventories
    (["finished goods", "raw materials", "work in progress", "stock on hand",
     "land held for resale"], "BS-CA-003"),
    # Financial assets (current)
    (["bills of exchange", "debentures in other", "government & semi",
     "shares in associated", "shares in other", "shares in related",
     "shares in subsidiary", "options in other", "other investments"], "BS-CA-004"),
    # Prepayments
    (["prepayments", "prepaid borrowing", "prepaid wages",
     "share issue expenses", "debenture issue"], "BS-CA-006"),
    # Formation / preliminary expenses — always non-current
    (["formation costs", "formation expenses",
     "preliminary expenses"], "BS-NCA-007"),
    # Loans to related parties
    (["loans to directors", "loans to holding", "loans to shareholders",
     "loans to other persons", "loans other related", "loan -",
     "interest in partnership", "right of indemnity"], "BS-CA-007"),
    # PPE
    (["buildings", "freehold land", "motor vehicles", "plant & equipment",
     "plant (other)", "office equipment", "fixtures & fittings",
     "floor coverings", "lease improvements", "leasehold improvements",
     "accumulated depreciation", "less: accumulated depreciation",
     "land development"], "BS-NCA-001"),
    # Investment property
    (["investment property"], "BS-NCA-002"),
    # Intangible assets
    (["goodwill", "patents", "trade marks", "trademarks",
     "other intangibles", "less: other intangibles amortisation",
     "less: accumulated amortisation", "research & development"], "BS-NCA-003"),
    # Deferred tax
    (["future income tax", "deferred tax asset"], "BS-NCA-005"),
    # Other assets
    (["other assets", "other - assets", "other", "rental bond"], "BS-NCA-007"),
]

# LIABILITIES section
LIABILITY_RULES = [
    # Trade payables
    (["trade creditors", "creditors - other", "other creditors",
     "sundry", "advance payments", "deferred gst"], "BS-CL-001"),
    # Borrowings (current)
    (["bank overdrafts", "bank loans", "bills of exchange",
     "hire purchase", "debentures", "other loans",
     "loan - on deck",
     "cba - lal", "cba better", "capital finance",
     "toyota finance", "macquarie porsche", "ondeck",
     "less: unexpired interest", "less deferred interest",
     "henneken"], "BS-CL-002"),
    # Director loan — always non-current
    (["loan - director"], "BS-NCL-001"),
    # Current tax
    (["taxation", "creditors - ato", "creditors - bas",
     "abn withholding", "tfn withholding", "payg withholding",
     "luxury car tax", "wine equalisation"], "BS-CL-003"),
    # Employee provisions
    (["employee entitlements", "employees entitlements",
     "wages payable", "superannuation payable",
     "super contributions", "payroll tax liability",
     "amounts withheld from salary"], "BS-CL-004"),
    # GST
    (["gst clearing", "gst payable", "input tax credit"], "BS-CL-006"),
    # Lease liabilities
    (["lease liabilities"], "BS-CL-007"),
    # Loans from related parties
    (["loans from holding", "loans from related",
     "loans from other persons", "liability as trustee",
     "fleet card", "altitude black", "fwgp mastercard"], "BS-CL-008"),
    # Dividends payable
    (["dividends"], "BS-CL-005"),
    # Sundry provisions
    (["sundry provisions"], "BS-CL-005"),
]

# EQUITY section
EQUITY_RULES = [
    # Issued capital
    (["issued & paid up", "subscribed units", "contribution by settlor",
     "gifts to trust"], "BS-EQ-001"),
    # Trust corpus
    (["trust corpus", "trust capital"], "BS-EQ-004"),
    # Retained earnings / undistributed
    (["retained profits", "undistributed income", "unappropriated"], "BS-EQ-002"),
    # Reserves
    (["general reserve", "assets revaluation", "capital profit",
     "capital profits"], "BS-EQ-003"),
    # Drawings
    (["drawings"], "BS-EQ-009"),
    # Dividends paid
    (["dividends provided", "dividends paid"], "BS-EQ-010"),
    # Proprietor's equity (sole trader)
    (["proprietor"], "BS-EQ-008"),
]

# CAPITAL ACCOUNTS section (partnerships/trusts)
CAPITAL_ACCOUNT_RULES = [
    # Opening balance
    (["opening balance"], "BS-EQ-002"),
    # Share of profit / distribution
    (["share of profit", "distribution for year"], "BS-EQ-005"),
    # Drawings
    (["drawings"], "BS-EQ-009"),
    # Capital contribution
    (["capital contribution"], "BS-EQ-006"),
    # Other items
    (["advance maintenance", "funds loaned", "income tax withheld",
     "interest on loan", "interest received on loan",
     "salary undrawn", "physical distribution"], "BS-EQ-007"),
    # Undistributed
    (["undistributed", "unappropriated"], "BS-EQ-005"),
]

# P&L APPROPRIATION section
PL_APPROP_RULES = [
    (["income tax expense", "income tax income",
     "over-provision of tax", "under-provision of tax"], "IS-TAX-001"),
    (["dividends provided", "dividends paid"], "BS-EQ-010"),
    (["retained profits", "undistributed income"], "BS-EQ-002"),
    (["other income"], "IS-REV-002"),
]

SECTION_RULES = {
    # Keys must match ChartOfAccount.StatementSection values (lowercase)
    "revenue": REVENUE_RULES,
    "cost_of_sales": COS_RULES,
    "expenses": EXPENSE_RULES,
    "assets": ASSET_RULES,
    "liabilities": LIABILITY_RULES,
    "equity": EQUITY_RULES,
    "capital_accounts": CAPITAL_ACCOUNT_RULES,
    "pl_appropriation": PL_APPROP_RULES,
    "suspense": [],  # Suspense stays unmapped
}


def match_account(account_name, section, entity_type):
    """
    Match an account name to an AccountMapping standard_code.
    Returns the standard_code or None if no match.
    """
    name_lower = account_name.lower().strip()
    rules = SECTION_RULES.get(section, [])

    for keywords, code in rules:
        for kw in keywords:
            if kw.lower() in name_lower:
                return code

    # Section-level fallbacks (keys match DB values)
    if section == "revenue":
        return "IS-REV-001"
    elif section == "cost_of_sales":
        return "IS-COS-001"
    elif section == "expenses":
        return "IS-EXP-016"  # Other expenses
    elif section == "assets":
        return "BS-NCA-007"  # Other non-current assets
    elif section == "liabilities":
        return "BS-CL-005"  # Other current liabilities
    elif section == "equity":
        if entity_type == "trust":
            return "BS-EQ-005"
        elif entity_type == "partnership":
            return "BS-EQ-007"
        elif entity_type == "sole_trader":
            return "BS-EQ-008"
        return "BS-EQ-002"
    elif section == "capital_accounts":
        if entity_type == "partnership":
            return "BS-EQ-007"
        return "BS-EQ-005"
    elif section == "pl_appropriation":
        return "BS-EQ-002"

    return None


class Command(BaseCommand):
    help = "Map all ChartOfAccount records to AccountMapping line items"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be mapped without saving",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Build lookup: standard_code -> AccountMapping
        mappings = {m.standard_code: m for m in AccountMapping.objects.all()}
        self.stdout.write(f"Found {len(mappings)} AccountMapping line items")

        accounts = ChartOfAccount.objects.all()
        self.stdout.write(f"Found {accounts.count()} ChartOfAccount records")

        mapped_count = 0
        unmapped = []
        updated = []

        for acc in accounts:
            code = match_account(acc.account_name, acc.section, acc.entity_type)
            if code and code in mappings:
                mapping = mappings[code]
                # Check entity type is applicable
                applicable = mapping.applicable_entities or []
                if applicable and acc.entity_type not in applicable:
                    # Try fallback for entity-specific equity
                    if acc.entity_type == "trust":
                        fallback = "BS-EQ-005"
                    elif acc.entity_type == "partnership":
                        fallback = "BS-EQ-007"
                    elif acc.entity_type == "sole_trader":
                        fallback = "BS-EQ-008"
                    else:
                        fallback = None
                    if fallback and fallback in mappings:
                        mapping = mappings[fallback]
                    else:
                        # Use the mapping anyway (it'll work)
                        pass

                if not dry_run:
                    acc.maps_to = mapping
                    updated.append(acc)
                mapped_count += 1
            else:
                unmapped.append(
                    f"  {acc.entity_type:15s} | {acc.account_code:10s} | "
                    f"{acc.section:20s} | {acc.account_name}"
                )

        if not dry_run and updated:
            ChartOfAccount.objects.bulk_update(updated, ["maps_to"], batch_size=200)

        self.stdout.write(self.style.SUCCESS(
            f"\n{'[DRY RUN] ' if dry_run else ''}Mapped {mapped_count} / {accounts.count()} accounts"
        ))

        if unmapped:
            self.stdout.write(self.style.WARNING(
                f"\n{len(unmapped)} accounts could not be mapped:"
            ))
            for line in unmapped:
                self.stdout.write(line)
        else:
            self.stdout.write(self.style.SUCCESS("All accounts mapped successfully!"))

        # Summary by entity type
        self.stdout.write("\n--- Summary by entity type ---")
        for et in ["company", "trust", "sole_trader", "partnership"]:
            total = sum(1 for a in accounts if a.entity_type == et)
            et_mapped = sum(
                1 for a in accounts
                if a.entity_type == et and match_account(a.account_name, a.section, a.entity_type)
            )
            self.stdout.write(f"  {et:15s}: {et_mapped}/{total} mapped")
