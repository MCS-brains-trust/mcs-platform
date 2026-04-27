"""
StatementHub — Capital Account Auto-Provisioning Engine
========================================================
Provisions per-beneficiary capital accounts from CapitalAccountTemplate
records when a unit holder or beneficiary officer is created on a trust entity.
"""
import logging
from django.db import transaction

logger = logging.getLogger(__name__)


def provision_capital_accounts(officer_id):
    """
    Create capital accounts for a trust beneficiary/unit holder from templates.

    For each active CapitalAccountTemplate matching the entity's type, creates
    an EntityChartOfAccount linked to the officer — unless one already exists.

    Args:
        officer_id: UUID primary key of the EntityOfficer record.
    """
    from core.models import (
        EntityOfficer, EntityChartOfAccount, CapitalAccountTemplate,
        ClientAccountMapping,
    )

    try:
        officer = EntityOfficer.objects.select_related("entity").get(pk=officer_id)
    except EntityOfficer.DoesNotExist:
        logger.warning("provision_capital_accounts: officer %s not found", officer_id)
        return

    # Only provision for unit holders and beneficiaries
    if officer.role not in EntityOfficer.DISTRIBUTION_ROLES:
        return

    entity = officer.entity

    # Only provision for trust entities
    if entity.entity_type != "trust":
        return

    templates = CapitalAccountTemplate.objects.filter(
        entity_type=entity.entity_type,
        is_active=True,
    ).order_by("sort_order")

    if not templates.exists():
        logger.info("No capital account templates for entity_type=%s", entity.entity_type)
        return

    # Determine account code suffix from display_order
    order = officer.display_order
    suffix = "" if order == 0 else f".{order:02d}"

    # Base account code range for capital accounts (9000 series)
    base_code_start = 9000

    created_count = 0
    with transaction.atomic():
        for tpl in templates:
            # Check if already provisioned
            exists = EntityChartOfAccount.objects.filter(
                entity=entity,
                beneficiary_officer=officer,
                capital_template_item=tpl,
            ).exists()
            if exists:
                continue

            # Generate account code: base + sort_order + suffix
            base_code = str(base_code_start + tpl.sort_order)
            account_code = f"{base_code}{suffix}"

            # Handle potential code collision — append officer order
            if EntityChartOfAccount.objects.filter(entity=entity, account_code=account_code).exists():
                account_code = f"{base_code}.{officer.display_order:02d}"
                if EntityChartOfAccount.objects.filter(entity=entity, account_code=account_code).exists():
                    # Last resort: use UUID fragment
                    account_code = f"{base_code}.{str(officer.pk)[:4]}"

            account_name = f"{tpl.account_name} — {officer.full_name}"

            eca = EntityChartOfAccount.objects.create(
                entity=entity,
                account_code=account_code,
                account_name=account_name,
                classification=tpl.classification,
                section=EntityChartOfAccount.StatementSection.CAPITAL_ACCOUNTS,
                maps_to=None,
                is_active=True,
                is_custom=False,
                beneficiary_officer=officer,
                capital_template_item=tpl,
                auto_provisioned=True,
                display_order=tpl.sort_order,
            )
            created_count += 1

            # Never auto-create a ClientAccountMapping for 4199 Profit
            # Distribution accounts — they are suppressed separately and
            # must not be netted into beneficiary loans.
            if (
                eca.beneficiary_officer is not None
                and not (eca.account_code or "").startswith("4199")
            ):
                ClientAccountMapping.objects.update_or_create(
                    entity=eca.entity,
                    client_account_code=eca.account_code,
                    defaults={
                        'client_account_name': eca.account_name,
                        'beneficiary_officer': eca.beneficiary_officer,
                    }
                )

    if created_count:
        logger.info(
            "Provisioned %d capital accounts for officer %s (%s) on entity %s",
            created_count, officer.full_name, officer.role, entity.entity_name,
        )

    return created_count


def cease_officer_accounts(officer_id):
    """
    Mark all capital accounts linked to a ceased officer as ceased.

    Args:
        officer_id: UUID primary key of the EntityOfficer record.
    """
    from core.models import EntityOfficer, EntityChartOfAccount

    try:
        officer = EntityOfficer.objects.get(pk=officer_id)
    except EntityOfficer.DoesNotExist:
        return

    updated = EntityChartOfAccount.objects.filter(
        beneficiary_officer=officer,
    ).update(is_ceased=True)

    if updated:
        logger.info(
            "Ceased %d capital accounts for officer %s",
            updated, officer.full_name,
        )
    return updated
