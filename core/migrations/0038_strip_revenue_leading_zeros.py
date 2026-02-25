"""
Data migration: Strip leading '0' from all revenue account codes.

Affects:
1. ChartOfAccount (master COA templates) — section='revenue'
2. EntityChartOfAccount (per-entity COAs) — section='revenue'
3. TrialBalanceLine — where the linked entity account is revenue
4. ClientAccountMapping — where the mapped entity account is revenue

For codes like '0500' → '500', '0510' → '510', etc.
Only strips a single leading zero; codes like '00500' → '0500'.
"""

from django.db import migrations


def strip_revenue_leading_zeros(apps, schema_editor):
    ChartOfAccount = apps.get_model('core', 'ChartOfAccount')
    EntityChartOfAccount = apps.get_model('core', 'EntityChartOfAccount')
    TrialBalanceLine = apps.get_model('core', 'TrialBalanceLine')
    ClientAccountMapping = apps.get_model('core', 'ClientAccountMapping')

    # 1. Master COA templates — revenue accounts with leading '0'
    master_updated = 0
    for acct in ChartOfAccount.objects.filter(section='revenue', account_code__startswith='0'):
        old_code = acct.account_code
        new_code = old_code.lstrip('0') or '0'  # safety: don't make it empty
        if new_code != old_code:
            acct.account_code = new_code
            acct.save(update_fields=['account_code'])
            master_updated += 1

    # 2. Per-entity COAs — revenue accounts with leading '0'
    entity_updated = 0
    for acct in EntityChartOfAccount.objects.filter(section='revenue', account_code__startswith='0'):
        old_code = acct.account_code
        new_code = old_code.lstrip('0') or '0'
        if new_code != old_code:
            # Also update any TrialBalanceLines that reference this account code
            TrialBalanceLine.objects.filter(
                financial_year__entity=acct.entity,
                account_code=old_code,
            ).update(account_code=new_code)

            # Also update any ClientAccountMappings that map to this entity account
            ClientAccountMapping.objects.filter(
                entity=acct.entity,
                entity_account=acct,
            ).update(client_account_code=new_code)

            acct.account_code = new_code
            acct.save(update_fields=['account_code'])
            entity_updated += 1

    print(f"  Stripped leading zeros: {master_updated} master COA, {entity_updated} entity COA revenue accounts")


def restore_revenue_leading_zeros(apps, schema_editor):
    """Reverse: add leading '0' back to revenue codes that are 3 digits (500 → 0500)."""
    ChartOfAccount = apps.get_model('core', 'ChartOfAccount')
    EntityChartOfAccount = apps.get_model('core', 'EntityChartOfAccount')
    TrialBalanceLine = apps.get_model('core', 'TrialBalanceLine')
    ClientAccountMapping = apps.get_model('core', 'ClientAccountMapping')

    for acct in ChartOfAccount.objects.filter(section='revenue'):
        if len(acct.account_code) == 3 and acct.account_code.isdigit():
            acct.account_code = '0' + acct.account_code
            acct.save(update_fields=['account_code'])

    for acct in EntityChartOfAccount.objects.filter(section='revenue'):
        if len(acct.account_code) == 3 and acct.account_code.isdigit():
            old_code = acct.account_code
            new_code = '0' + old_code
            TrialBalanceLine.objects.filter(
                financial_year__entity=acct.entity,
                account_code=old_code,
            ).update(account_code=new_code)
            ClientAccountMapping.objects.filter(
                entity=acct.entity,
                entity_account=acct,
            ).update(client_account_code=new_code)
            acct.account_code = new_code
            acct.save(update_fields=['account_code'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0037_document_template_engine'),
    ]

    operations = [
        migrations.RunPython(
            strip_revenue_leading_zeros,
            restore_revenue_leading_zeros,
        ),
    ]
