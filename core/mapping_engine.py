"""
Auto-mapping engine for StatementHub.

Maps Access Ledger chart of accounts classifications to standard
financial statement line items (AccountMapping records).

The Classification column in the COA is the primary key for auto-mapping.
This module provides rules-based mapping that can be overridden manually.
"""
from .models import AccountMapping, template_entity_type


# Classification keyword → standard_code mapping rules
# Each rule is: (keywords_to_match, standard_code)
# Keywords are matched case-insensitively against the Classification field.
# First match wins.

CLASSIFICATION_RULES = [
    # =========================================================================
    # REVENUE
    # =========================================================================
    (["sales revenue", "other sales"], "IS-REV-001"),
    (["interest received", "interest income"], "IS-REV-003"),
    (["dividend"], "IS-REV-004"),
    (["rental income", "rent received", "lease income"], "IS-REV-005"),
    (["profit on sale"], "IS-REV-006"),
    (["capital gain"], "IS-REV-009"),
    (["distribution from"], "IS-REV-007"),
    (["government subsid", "assessable government"], "IS-REV-008"),

    # =========================================================================
    # COST OF SALES
    # =========================================================================
    (["cost of trading", "cost of sales", "cost of goods"], "IS-COS-001"),
    (["opening stock"], "IS-COS-002"),
    (["purchases"], "IS-COS-003"),
    (["closing stock"], "IS-COS-004"),

    # =========================================================================
    # EXPENSES
    # =========================================================================
    (["accountancy", "audit"], "IS-EXP-001"),
    (["advertising", "promotion"], "IS-EXP-002"),
    (["bad debt"], "IS-EXP-003"),
    (["bank fee", "bank charge"], "IS-EXP-004"),
    (["borrowing cost", "interest deduction", "hire purchase charge"], "IS-EXP-005"),
    (["contractor", "subcontractor", "commission"], "IS-EXP-006"),
    (["depreciation", "amortisation", "write-down to recoverable"], "IS-EXP-007"),
    (["salary", "wages", "casual labour", "director"], "IS-EXP-008"),
    (["superannuation"], "IS-EXP-017"),
    (["insurance"], "IS-EXP-009"),
    (["motor vehicle"], "IS-EXP-010"),
    (["rent expense", "occupancy"], "IS-EXP-011"),
    (["repair", "maintenance"], "IS-EXP-012"),
    (["telephone", "communication", "internet"], "IS-EXP-013"),
    (["travel", "accommodation"], "IS-EXP-014"),
    (["electricity", "heating", "gas", "water"], "IS-EXP-015"),
    (["loss on sale", "loss on disposal"], "IS-EXP-019"),
    (["income tax expense"], "IS-TAX-001"),

    # =========================================================================
    # CURRENT ASSETS
    # =========================================================================
    (["bank account", "other cash", "short term deposit"], "BS-CA-001"),
    (["trade debtor", "provision for doubtful"], "BS-CA-002"),
    (["finished goods", "raw material", "work in progress"], "BS-CA-003"),
    (["listed - ", "unlisted - share", "unlisted - debenture", "unlisted - government"], "BS-CA-004"),
    (["prepayment", "future income tax benefit"], "BS-CA-005"),
    (["loans to director", "loans to shareholder", "loans to holding", "loans to other related",
      "bills, promissory", "assets of trust"], "BS-CA-007"),

    # =========================================================================
    # NON-CURRENT ASSETS
    # =========================================================================
    (["freehold land", "building", "plant & equipment", "plant (other)",
      "lease improvement", "other assets accumulated", "other assets"], "BS-NCA-001"),
    (["motor vehicle"], "BS-NCA-001"),  # PPE subcategory
    (["investment property"], "BS-NCA-002"),
    (["goodwill", "patent", "trademark", "other intangible", "preliminary expense",
      "share issue", "debenture issue", "research accumulated"], "BS-NCA-003"),
    (["loans to related compan", "loans to other person"], "BS-NCA-006"),

    # =========================================================================
    # CURRENT LIABILITIES
    # =========================================================================
    (["trade creditor", "other creditor", "sundry"], "BS-CL-001"),
    (["bank loan", "bank overdraft", "other loan", "bills & promissory",
      "debenture"], "BS-CL-002"),
    (["provision for tax"], "BS-CL-003"),
    (["employee entitlement"], "BS-CL-004"),
    (["gst liability", "gst payable"], "BS-CL-006"),
    (["lease liability"], "BS-CL-007"),
    (["trust liability", "trust provision", "advance payment"], "BS-CL-005"),

    # =========================================================================
    # NON-CURRENT LIABILITIES
    # =========================================================================
    (["secured bank loan", "secured other loan", "unsecured - loans"], "BS-NCL-001"),
    (["deferred income tax"], "BS-NCL-002"),
    (["secured lease liability"], "BS-NCL-003"),
    (["secured loans, holding", "secured loans, related", "secured trust liability"], "BS-NCL-004"),

    # =========================================================================
    # EQUITY
    # =========================================================================
    (["issued capital", "share premium", "shareholders' equity"], "BS-EQ-001"),
    (["retained profit", "unappropriated"], "BS-EQ-002"),
    (["reserve", "revaluation"], "BS-EQ-003"),
    (["trust corpus", "trust capital", "opening balance - trust"], "BS-EQ-004"),
    (["undistributed income"], "BS-EQ-005"),
    (["opening balance - partner", "capital contribution", "salary undrawn"], "BS-EQ-006"),
    (["share of profit"], "BS-EQ-007"),
    (["drawing"], "BS-EQ-009"),
    (["dividend provided", "dividend paid"], "BS-EQ-010"),
    (["over-provision for tax", "under-provision for tax"], "IS-TAX-001"),
]


def auto_map_account(classification: str, account_code: str, account_name: str, entity_type: str) -> AccountMapping | None:
    """
    Attempt to automatically map an account to a standard line item
    based on its classification, code, and name.

    Returns the matched AccountMapping or None if no match found.
    """
    if not classification:
        classification = ""
    
    classification_lower = classification.lower()
    account_name_lower = account_name.lower()
    
    # Try classification-based matching first
    for keywords, standard_code in CLASSIFICATION_RULES:
        for keyword in keywords:
            if keyword.lower() in classification_lower:
                try:
                    mapping = AccountMapping.objects.get(standard_code=standard_code)
                    # Check entity applicability
                    if template_entity_type(entity_type) in mapping.applicable_entities or not mapping.applicable_entities:
                        return mapping
                except AccountMapping.DoesNotExist:
                    continue

    # Fallback: try matching on account name
    for keywords, standard_code in CLASSIFICATION_RULES:
        for keyword in keywords:
            if keyword.lower() in account_name_lower:
                try:
                    mapping = AccountMapping.objects.get(standard_code=standard_code)
                    if template_entity_type(entity_type) in mapping.applicable_entities or not mapping.applicable_entities:
                        return mapping
                except AccountMapping.DoesNotExist:
                    continue

    # Code-range based fallback
    try:
        code_num = int(account_code)
    except (ValueError, TypeError):
        return None

    # Revenue: 0000-0999
    if 0 <= code_num < 1000:
        if not classification_lower or classification_lower in ("revenue", "other"):
            try:
                return AccountMapping.objects.get(standard_code="IS-REV-002")  # Other income
            except AccountMapping.DoesNotExist:
                pass

    # Expenses: 1000-1999
    if 1000 <= code_num < 2000:
        if not any(kw in classification_lower for kw in ("depreciation", "salary", "motor", "rent")):
            try:
                return AccountMapping.objects.get(standard_code="IS-EXP-016")  # Other expenses
            except AccountMapping.DoesNotExist:
                pass

    # Equity: 4000-4199
    if 4000 <= code_num < 4200:
        equity_map = {
            "company": "BS-EQ-002",
            "trust": "BS-EQ-005",
            "partnership": "BS-EQ-006",
            "sole_trader": "BS-EQ-008",
        }
        code = equity_map.get(template_entity_type(entity_type))
        if code:
            try:
                return AccountMapping.objects.get(standard_code=code)
            except AccountMapping.DoesNotExist:
                pass

    # P&L Appropriation: 9000-9999
    if 9000 <= code_num < 10000:
        if "tax" in classification_lower or "tax" in account_name_lower:
            try:
                return AccountMapping.objects.get(standard_code="IS-TAX-001")
            except AccountMapping.DoesNotExist:
                pass

    return None


def auto_map_trial_balance(financial_year) -> dict:
    """
    Auto-map all unmapped trial balance lines for a financial year.
    Returns a dict with counts of mapped and unmapped lines.
    """
    from .models import TrialBalanceLine, ClientAccountMapping

    entity = financial_year.entity
    entity_type = entity.entity_type
    lines = financial_year.trial_balance_lines.filter(mapped_line_item__isnull=True)

    mapped_count = 0
    unmapped_count = 0

    for line in lines:
        # First check if there's a ClientAccountMapping for this entity+code
        try:
            client_mapping = ClientAccountMapping.objects.get(
                entity=entity, client_account_code=line.account_code
            )
            if client_mapping.mapped_line_item:
                line.mapped_line_item = client_mapping.mapped_line_item
                line.save(update_fields=["mapped_line_item"])
                mapped_count += 1
                continue
        except ClientAccountMapping.DoesNotExist:
            pass

        # Try auto-mapping based on classification/name
        # The classification is stored in the account_name or we derive from code
        mapping = auto_map_account(
            classification="",  # We don't store classification separately yet
            account_code=line.account_code,
            account_name=line.account_name,
            entity_type=entity_type,
        )

        if mapping:
            line.mapped_line_item = mapping
            line.save(update_fields=["mapped_line_item"])

            # Also create/update the ClientAccountMapping for future use
            ClientAccountMapping.objects.update_or_create(
                entity=entity,
                client_account_code=line.account_code,
                defaults={
                    "client_account_name": line.account_name,
                    "mapped_line_item": mapping,
                },
            )
            mapped_count += 1
        else:
            unmapped_count += 1

    return {"mapped": mapped_count, "unmapped": unmapped_count}
