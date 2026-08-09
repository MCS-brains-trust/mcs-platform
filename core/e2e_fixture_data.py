"""The deterministic subject of the Tier 2 accounting flows.

Tier 1 crawls real production data on purpose — real chart-of-accounts shapes break
document generation in ways synthetic data never would. Tier 2 asserts exact figures
against a blessed baseline, which needs the opposite property: a subject that does not
move when the production copy is refreshed or a client edits their books.

So the flows run against this fixture entity. Fixed UUIDs let a spec build a URL
without a lookup, and make a failure traceable to a known row.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import transaction

FIXTURE_IDS = {
    "client": "e2e00000-0000-4000-8000-000000000001",
    "entity": "e2e00000-0000-4000-8000-000000000002",
    "prior_fy": "e2e00000-0000-4000-8000-000000000003",
    "current_fy": "e2e00000-0000-4000-8000-000000000004",
    "asset": "e2e00000-0000-4000-8000-000000000005",
}

# Prior-year closing balances. Balanced, and deliberately small and round so a spec
# failure reports a number a human can reason about.
PRIOR_YEAR_TB = [
    # (code, name, debit, credit)
    ("1-1000", "Cash at Bank", Decimal("50000.00"), Decimal("0.00")),
    ("1-2000", "Plant and Equipment", Decimal("20000.00"), Decimal("0.00")),
    ("1-2100", "Accumulated Depreciation", Decimal("0.00"), Decimal("4000.00")),
    ("2-1000", "Trade Creditors", Decimal("0.00"), Decimal("6000.00")),
    ("3-1000", "Retained Earnings", Decimal("0.00"), Decimal("60000.00")),
]

# Section values are EntityChartOfAccount.StatementSection choices (core/models.py) —
# confirmed against the real model, not guessed, since a bad choice value fails silently
# on sqlite (no CHECK constraint) but rejects on the hardened Postgres E2E copy.
CHART_OF_ACCOUNTS = [
    ("1-1000", "Cash at Bank", "current_assets"),
    ("1-2000", "Plant and Equipment", "non_current_assets"),
    ("1-2100", "Accumulated Depreciation", "non_current_assets"),
    ("2-1000", "Trade Creditors", "current_liabilities"),
    ("3-1000", "Retained Earnings", "equity"),
    ("6-1200", "Depreciation", "expenses"),
]


@transaction.atomic
def seed_fixture_entity() -> dict:
    """Create or reset the fixture entity. Idempotent."""
    from core.models import (
        Client,
        DepreciationAsset,
        Entity,
        EntityChartOfAccount,
        FinancialYear,
        TrialBalanceLine,
    )

    client, _ = Client.objects.update_or_create(
        pk=FIXTURE_IDS["client"],
        defaults={"name": "E2E Fixture Client", "is_active": True},
    )

    entity, _ = Entity.objects.update_or_create(
        pk=FIXTURE_IDS["entity"],
        defaults={
            "client": client,
            "entity_name": "E2E Fixture Holdings Pty Ltd",
            "entity_type": "company",
            # Valid check digits: core/validators.py rejects malformed identifiers
            # on the entity form, and a fixture that could not be saved through the
            # UI would be a trap for later specs.
            "abn": "51824753556",
            "acn": "004085616",
            # Model field is month-day format (max_length=5, e.g. "06-30" for June),
            # not a prose date — confirmed against Entity.financial_year_end's
            # help_text and default in core/models.py.
            "financial_year_end": "06-30",
            "reporting_framework": "SPFR",
            "company_size": "small_proprietary",
            "is_gst_registered": True,
            "include_comparative_figures": True,
        },
    )

    for code, name, section in CHART_OF_ACCOUNTS:
        EntityChartOfAccount.objects.update_or_create(
            entity=entity,
            account_code=code,
            defaults={"account_name": name, "section": section, "is_active": True},
        )

    prior = _upsert_year(
        FinancialYear,
        pk=FIXTURE_IDS["prior_fy"],
        entity=entity,
        year_label="2025",
        start=datetime.date(2024, 7, 1),
        end=datetime.date(2025, 6, 30),
        status=FinancialYear.Status.FINALISED,
        prior_year=None,
    )
    # is_locked is derived from status == FINALISED (see FinancialYear.is_locked),
    # already true above; locked_at/finalised_at are also set because roll-forward
    # code and the FY detail views read them directly, not just the property.
    if prior.locked_at is None:
        prior.locked_at = datetime.datetime(2025, 8, 1, tzinfo=datetime.timezone.utc)
        prior.finalised_at = prior.locked_at
        prior.save(update_fields=["locked_at", "finalised_at"])

    current = _upsert_year(
        FinancialYear,
        pk=FIXTURE_IDS["current_fy"],
        entity=entity,
        year_label="2026",
        start=datetime.date(2025, 7, 1),
        end=datetime.date(2026, 6, 30),
        status=FinancialYear.Status.DRAFT,
        prior_year=prior,
    )

    TrialBalanceLine.objects.filter(financial_year=prior).delete()
    for code, name, debit, credit in PRIOR_YEAR_TB:
        TrialBalanceLine.objects.create(
            financial_year=prior,
            account_code=code,
            account_name=name,
            debit=debit,
            credit=credit,
            closing_balance=debit - credit,
            source="tb_import",
        )

    DepreciationAsset.objects.update_or_create(
        pk=FIXTURE_IDS["asset"],
        defaults={
            "financial_year": current,
            "category": "Plant and Equipment",
            "asset_name": "Fixture Forklift",
            "purchase_date": datetime.date(2024, 7, 1),
            "total_cost": Decimal("20000.00"),
            "private_use_pct": Decimal("0.00"),
            "opening_wdv": Decimal("16000.00"),
            "depreciable_value": Decimal("20000.00"),
            "method": "P",  # prime cost
            "rate": Decimal("20.00"),
            "depreciation_amount": Decimal("4000.00"),
            "private_depreciation": Decimal("0.00"),
            "closing_wdv": Decimal("12000.00"),
            "display_order": 1,
            "asset_account_code": "1-2000",
            "asset_account_name": "Plant and Equipment",
            "accum_dep_code": "1-2100",
            "accum_dep_name": "Accumulated Depreciation",
            "dep_expense_code": "6-1200",
            "dep_expense_name": "Depreciation",
        },
    )

    return dict(FIXTURE_IDS)


def _upsert_year(model, *, pk, entity, year_label, start, end, status, prior_year):
    obj, _ = model.objects.update_or_create(
        pk=pk,
        defaults={
            "entity": entity,
            "year_label": year_label,
            "start_date": start,
            "end_date": end,
            "status": status,
            "prior_year": prior_year,
            "period_type": "annual",
        },
    )
    return obj
