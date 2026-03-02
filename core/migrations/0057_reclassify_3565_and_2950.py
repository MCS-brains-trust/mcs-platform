"""
Data migration: Reclassify account 3565 (Loan - Director) to Non-Current
Liabilities and account 2950 (Formation/Preliminary Expenses) to Non-Current
Assets across all existing records.

Updates:
  - ChartOfAccount.maps_to  (master template)
  - EntityChartOfAccount.maps_to  (per-entity copies)
  - TrialBalanceLine.mapped_line_item  (existing trial balance lines)
"""
from django.db import migrations


def reclassify_accounts(apps, schema_editor):
    AccountMapping = apps.get_model("core", "AccountMapping")
    ChartOfAccount = apps.get_model("core", "ChartOfAccount")
    EntityChartOfAccount = apps.get_model("core", "EntityChartOfAccount")
    TrialBalanceLine = apps.get_model("core", "TrialBalanceLine")

    # ── Account 3565: Loan - Director → BS-NCL-001 (Borrowings non-current) ──
    try:
        ncl_001 = AccountMapping.objects.get(standard_code="BS-NCL-001")
    except AccountMapping.DoesNotExist:
        ncl_001 = None

    if ncl_001:
        # Master template
        updated = ChartOfAccount.objects.filter(account_code="3565").update(maps_to=ncl_001)
        print(f"  ChartOfAccount 3565 → BS-NCL-001: {updated} rows")

        # Entity-level accounts (exact code and subaccounts like 3565.01, 3565.02)
        updated = EntityChartOfAccount.objects.filter(account_code="3565").update(maps_to=ncl_001)
        print(f"  EntityChartOfAccount 3565 → BS-NCL-001: {updated} rows")

        # Also update subaccounts (3565.xx)
        from django.db.models import Q
        updated = EntityChartOfAccount.objects.filter(
            account_code__startswith="3565."
        ).update(maps_to=ncl_001)
        print(f"  EntityChartOfAccount 3565.xx → BS-NCL-001: {updated} rows")

        # Trial balance lines
        updated = TrialBalanceLine.objects.filter(account_code="3565").update(
            mapped_line_item=ncl_001
        )
        print(f"  TrialBalanceLine 3565 → BS-NCL-001: {updated} rows")

        updated = TrialBalanceLine.objects.filter(
            account_code__startswith="3565."
        ).update(mapped_line_item=ncl_001)
        print(f"  TrialBalanceLine 3565.xx → BS-NCL-001: {updated} rows")

    # ── Account 2950: Formation/Preliminary Expenses → BS-NCA-007 (Other NCA) ──
    try:
        nca_007 = AccountMapping.objects.get(standard_code="BS-NCA-007")
    except AccountMapping.DoesNotExist:
        nca_007 = None

    if nca_007:
        # Master template
        updated = ChartOfAccount.objects.filter(account_code="2950").update(maps_to=nca_007)
        print(f"  ChartOfAccount 2950 → BS-NCA-007: {updated} rows")

        # Entity-level accounts
        updated = EntityChartOfAccount.objects.filter(account_code="2950").update(maps_to=nca_007)
        print(f"  EntityChartOfAccount 2950 → BS-NCA-007: {updated} rows")

        # Subaccounts
        updated = EntityChartOfAccount.objects.filter(
            account_code__startswith="2950."
        ).update(maps_to=nca_007)
        print(f"  EntityChartOfAccount 2950.xx → BS-NCA-007: {updated} rows")

        # Trial balance lines
        updated = TrialBalanceLine.objects.filter(account_code="2950").update(
            mapped_line_item=nca_007
        )
        print(f"  TrialBalanceLine 2950 → BS-NCA-007: {updated} rows")

        updated = TrialBalanceLine.objects.filter(
            account_code__startswith="2950."
        ).update(mapped_line_item=nca_007)
        print(f"  TrialBalanceLine 2950.xx → BS-NCA-007: {updated} rows")


def reverse_reclassify(apps, schema_editor):
    """Reverse: move accounts back to their original current classifications."""
    AccountMapping = apps.get_model("core", "AccountMapping")
    ChartOfAccount = apps.get_model("core", "ChartOfAccount")
    EntityChartOfAccount = apps.get_model("core", "EntityChartOfAccount")
    TrialBalanceLine = apps.get_model("core", "TrialBalanceLine")

    # 3565 back to BS-CL-002 (Borrowings current)
    try:
        cl_002 = AccountMapping.objects.get(standard_code="BS-CL-002")
    except AccountMapping.DoesNotExist:
        cl_002 = None

    if cl_002:
        ChartOfAccount.objects.filter(account_code="3565").update(maps_to=cl_002)
        EntityChartOfAccount.objects.filter(account_code="3565").update(maps_to=cl_002)
        EntityChartOfAccount.objects.filter(account_code__startswith="3565.").update(maps_to=cl_002)
        TrialBalanceLine.objects.filter(account_code="3565").update(mapped_line_item=cl_002)
        TrialBalanceLine.objects.filter(account_code__startswith="3565.").update(mapped_line_item=cl_002)

    # 2950 back to BS-CA-006 (Prepayments)
    try:
        ca_006 = AccountMapping.objects.get(standard_code="BS-CA-006")
    except AccountMapping.DoesNotExist:
        ca_006 = None

    if ca_006:
        ChartOfAccount.objects.filter(account_code="2950").update(maps_to=ca_006)
        EntityChartOfAccount.objects.filter(account_code="2950").update(maps_to=ca_006)
        EntityChartOfAccount.objects.filter(account_code__startswith="2950.").update(maps_to=ca_006)
        TrialBalanceLine.objects.filter(account_code="2950").update(mapped_line_item=ca_006)
        TrialBalanceLine.objects.filter(account_code__startswith="2950.").update(mapped_line_item=ca_006)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0056_merge_0055_bankaccountmapping_0055_entity_legal_doc"),
    ]

    operations = [
        migrations.RunPython(reclassify_accounts, reverse_reclassify),
    ]
