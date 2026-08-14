"""The deterministic subject of the Tier 2 accounting flows.

Tier 1 crawls real production data on purpose — real chart-of-accounts shapes break
document generation in ways synthetic data never would. Tier 2 asserts exact figures
against a blessed baseline, which needs the opposite property: a subject that does not
move when the production copy is refreshed or a client edits their books.

So the flows run against this fixture entity. Fixed UUIDs let a spec build a URL
without a lookup, and make a failure traceable to a known row.
"""
from __future__ import annotations

import dataclasses
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
#
# Includes two P&L accounts (Sales, Administration) alongside the balance-sheet ones
# so a roll-forward spec has a real, non-zero net profit to close into retained
# earnings and real P&L accounts to check "carries as comparative, not opening"
# against -- a fixture with balance-sheet accounts only cannot exercise either
# promise (see Task 9's fix round 1: both the net-profit-closing formula and the
# P&L-comparative check evaluate to a trivial 0 == 0 without this). Cash at Bank is
# $20,000 higher than the balance-sheet-only version of this fixture (70,000 instead
# of 50,000) precisely to absorb that $20,000 net profit (Sales 30,000 − Admin
# 10,000) and keep the whole trial balance balanced.
PRIOR_YEAR_TB = [
    # (code, name, debit, credit)
    ("1-1000", "Cash at Bank", Decimal("70000.00"), Decimal("0.00")),
    ("1-2000", "Plant and Equipment", Decimal("20000.00"), Decimal("0.00")),
    ("1-2100", "Accumulated Depreciation", Decimal("0.00"), Decimal("4000.00")),
    ("2-1000", "Trade Creditors", Decimal("0.00"), Decimal("6000.00")),
    ("3-1000", "Retained Earnings", Decimal("0.00"), Decimal("60000.00")),
    ("4-1000", "Sales", Decimal("0.00"), Decimal("30000.00")),
    ("6-1000", "Administration", Decimal("10000.00"), Decimal("0.00")),
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
    ("4-1000", "Sales", "revenue"),
    ("6-1000", "Administration", "expenses"),
    ("6-1200", "Depreciation", "expenses"),
]


@dataclasses.dataclass(frozen=True)
class FixtureProfile:
    """One deterministic Tier 2 subject.

    `retained_profits_code` is the account the year's result must close into. It
    differs per entity type, and so does the section it lives in: a trust's sits in
    pl_appropriation, a partnership's and a sole trader's in capital_accounts. The
    specs read it rather than hardcoding an account, which is what lets one flow
    serve every type.
    """

    key: str
    ids: dict
    entity_kwargs: dict
    chart: list  # (code, name, section)
    prior_year_tb: list  # (code, name, debit, credit)
    retained_profits_code: str
    client_name: str
    depreciation_asset: dict | None = None
    # Bank-statement-reconciliation account code, for profiles whose flow needs to
    # know which chart account a bank statement is uploaded against. Optional
    # because the four roll-forward/year-end profiles have no such flow.
    bank_account_code: str | None = None


COMPANY = FixtureProfile(
    key="company",
    ids=FIXTURE_IDS,
    # Deliberately unsuffixed, unlike the profiles added later: this profile's
    # checkpoints are already blessed, so nothing it seeds may change.
    client_name="E2E Fixture Client",
    entity_kwargs={
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
    chart=CHART_OF_ACCOUNTS,
    prior_year_tb=PRIOR_YEAR_TB,
    retained_profits_code="3-1000",
    depreciation_asset={
        "pk": FIXTURE_IDS["asset"],
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

TRUST_IDS = {
    "client": "e2e00001-0000-4000-8000-000000000001",
    "entity": "e2e00001-0000-4000-8000-000000000002",
    "prior_fy": "e2e00001-0000-4000-8000-000000000003",
    "current_fy": "e2e00001-0000-4000-8000-000000000004",
}

# Codes and sections taken from E & J Chiaravalle Family Trust, verified by query
# against sh_e2e_template. Two things to note, both deliberate:
#
#   * Real charts use the flat `assets`/`liabilities` sections, not the
#     current_/non_current_ pair the company fixture uses. Not one of the ~5,000
#     chart rows in the production copy is a hyphenated MYOB-style code either.
#   * This trust's 4199 sits in pl_appropriation, where the partnership's and the
#     sole trader's sit in capital_accounts. That difference is the point of this
#     fixture: the year's result can only reach it if pl_appropriation is treated
#     as a balance-sheet section.
TRUST_CHART = [
    ("2000", "Cash at bank", "assets"),
    ("2860", "Plant & equipment (cost)", "assets"),
    ("2869", "Less: Accumulated depreciation", "assets"),
    ("3048", "Trade creditors", "liabilities"),
    ("4000.01", "Opening balance - Beneficiary — Beneficiary One", "capital_accounts"),
    ("4000.02", "Opening balance - Beneficiary — Beneficiary Two", "capital_accounts"),
    ("4005.01", "Distribution for year — Beneficiary One", "capital_accounts"),
    ("4005.02", "Distribution for year — Beneficiary Two", "capital_accounts"),
    ("4199", "Undistributed income", "pl_appropriation"),
    ("0105", "Sales", "revenue"),
    ("1510", "Accountancy", "expenses"),
]

# Same arithmetic as every other profile in this file -- 100,000 a side, a 20,000
# net profit (Sales 30,000 less Accountancy 10,000) -- so a failure on one entity
# type can be compared against another directly. The distribution accounts exist in
# the chart but carry no prior balance: they are movement accounts.
TRUST_PRIOR_TB = [
    ("2000", "Cash at bank", Decimal("70000.00"), Decimal("0.00")),
    ("2860", "Plant & equipment (cost)", Decimal("20000.00"), Decimal("0.00")),
    ("2869", "Less: Accumulated depreciation", Decimal("0.00"), Decimal("4000.00")),
    ("3048", "Trade creditors", Decimal("0.00"), Decimal("6000.00")),
    (
        "4000.01",
        "Opening balance - Beneficiary — Beneficiary One",
        Decimal("0.00"),
        Decimal("30000.00"),
    ),
    (
        "4000.02",
        "Opening balance - Beneficiary — Beneficiary Two",
        Decimal("0.00"),
        Decimal("30000.00"),
    ),
    ("0105", "Sales", Decimal("0.00"), Decimal("30000.00")),
    ("1510", "Accountancy", Decimal("10000.00"), Decimal("0.00")),
]

# NOTE: the trust is the one profile whose seeded chart is bigger than the list
# above. core/signals.py's handle_trust_entity_created fires on Entity creation and
# calls EntityChartOfAccount.seed_from_template, which adds the 466-row master trust
# template -- so on the E2E database this entity ends up with 469 accounts, not 11.
#
# That is left in place deliberately. A trust created through the real UI gets those
# accounts too, so suppressing them would make the fixture model a state that cannot
# exist in production. The chart list above is still applied afterwards and wins on
# every code it names (update_or_create), which is what keeps 4199 pointing at
# Undistributed income in pl_appropriation. Verified on 2026-08-13 that the template
# contributes no rival retained-profits candidate: 4199 is the only account in this
# entity's whole chart matching undistributed/unappropriated/retained or sitting in
# pl_appropriation.
#
# The prior-year trial balance -- which is what the roll-forward flow actually reads
# -- stays at the 8 deterministic lines below regardless.
TRUST = FixtureProfile(
    key="trust",
    ids=TRUST_IDS,
    client_name="E2E Fixture Client (trust)",
    entity_kwargs={
        "entity_name": "E2E Fixture Family Trust",
        "entity_type": "trust",
        "abn": "11000000560",
        "financial_year_end": "06-30",
        "reporting_framework": "SPFR",
        "is_gst_registered": True,
        "include_comparative_figures": True,
    },
    chart=TRUST_CHART,
    prior_year_tb=TRUST_PRIOR_TB,
    retained_profits_code="4199",
)

PARTNERSHIP_IDS = {
    "client": "e2e00002-0000-4000-8000-000000000001",
    "entity": "e2e00002-0000-4000-8000-000000000002",
    "prior_fy": "e2e00002-0000-4000-8000-000000000003",
    "current_fy": "e2e00002-0000-4000-8000-000000000004",
}

# Codes taken from D.P Vaughan & D Vriend. Share-of-profit (4003.x) and drawings
# (4054.x) exist in the chart but carry no prior balance -- they are movement
# accounts, and leaving them empty keeps the arithmetic identical across profiles.
PARTNERSHIP_CHART = [
    ("2000", "Cash at bank", "assets"),
    ("2860", "Plant & equipment (cost)", "assets"),
    ("2869", "Less: Accumulated depreciation", "assets"),
    ("3048", "Trade creditors", "liabilities"),
    ("4000.01", "Opening balance - Partner — Partner One", "capital_accounts"),
    ("4000.02", "Opening balance - Partner — Partner Two", "capital_accounts"),
    ("4003.01", "Share of profit — Partner One", "capital_accounts"),
    ("4003.02", "Share of profit — Partner Two", "capital_accounts"),
    ("4054.01", "Drawings - Partner One", "capital_accounts"),
    ("4054.02", "Drawings - Partner Two", "capital_accounts"),
    ("4199", "Unappropriated profits", "capital_accounts"),
    ("0105", "Sales", "revenue"),
    ("1510", "Accountancy", "expenses"),
]

PARTNERSHIP_PRIOR_TB = [
    ("2000", "Cash at bank", Decimal("70000.00"), Decimal("0.00")),
    ("2860", "Plant & equipment (cost)", Decimal("20000.00"), Decimal("0.00")),
    ("2869", "Less: Accumulated depreciation", Decimal("0.00"), Decimal("4000.00")),
    ("3048", "Trade creditors", Decimal("0.00"), Decimal("6000.00")),
    (
        "4000.01",
        "Opening balance - Partner — Partner One",
        Decimal("0.00"),
        Decimal("30000.00"),
    ),
    (
        "4000.02",
        "Opening balance - Partner — Partner Two",
        Decimal("0.00"),
        Decimal("30000.00"),
    ),
    ("0105", "Sales", Decimal("0.00"), Decimal("30000.00")),
    ("1510", "Accountancy", Decimal("10000.00"), Decimal("0.00")),
]

PARTNERSHIP = FixtureProfile(
    key="partnership",
    ids=PARTNERSHIP_IDS,
    client_name="E2E Fixture Client (partnership)",
    entity_kwargs={
        "entity_name": "E2E Fixture Partners",
        "entity_type": "partnership",
        "abn": "11000000592",
        "financial_year_end": "06-30",
        "reporting_framework": "SPFR",
        "is_gst_registered": True,
        "include_comparative_figures": True,
    },
    chart=PARTNERSHIP_CHART,
    prior_year_tb=PARTNERSHIP_PRIOR_TB,
    retained_profits_code="4199",
)

SOLE_TRADER_IDS = {
    "client": "e2e00003-0000-4000-8000-000000000001",
    "entity": "e2e00003-0000-4000-8000-000000000002",
    "prior_fy": "e2e00003-0000-4000-8000-000000000003",
    "current_fy": "e2e00003-0000-4000-8000-000000000004",
}

# Codes taken from Daniel Habteslassie. This chart deliberately has no sub-coded
# capital accounts -- the real one does not either -- so it tests "does the year's
# result reach the type-correct account" with the sub-account variable removed.
# Note the plant pairing is 2850/2859 here, not 2860/2869: the convention is cost
# at N, accumulated depreciation at N+9, and this entity's plant sits at 2850.
SOLE_TRADER_CHART = [
    ("2000", "Cash at bank", "assets"),
    ("2850", "Plant & equipment - At cost", "assets"),
    ("2859", "Less: Accumulated depreciation", "assets"),
    ("3048", "Trade creditors", "liabilities"),
    ("4010", "Capital contribution", "capital_accounts"),
    ("4049", "Share of profit", "capital_accounts"),
    ("4080", "Drawings", "capital_accounts"),
    ("4199", "Undistributed income", "capital_accounts"),
    ("0105", "Sales", "revenue"),
    ("1510", "Accountancy", "expenses"),
]

SOLE_TRADER_PRIOR_TB = [
    ("2000", "Cash at bank", Decimal("70000.00"), Decimal("0.00")),
    ("2850", "Plant & equipment - At cost", Decimal("20000.00"), Decimal("0.00")),
    ("2859", "Less: Accumulated depreciation", Decimal("0.00"), Decimal("4000.00")),
    ("3048", "Trade creditors", Decimal("0.00"), Decimal("6000.00")),
    ("4010", "Capital contribution", Decimal("0.00"), Decimal("60000.00")),
    ("0105", "Sales", Decimal("0.00"), Decimal("30000.00")),
    ("1510", "Accountancy", Decimal("10000.00"), Decimal("0.00")),
]

SOLE_TRADER = FixtureProfile(
    key="sole_trader",
    ids=SOLE_TRADER_IDS,
    client_name="E2E Fixture Client (sole_trader)",
    entity_kwargs={
        "entity_name": "E2E Fixture Sole Trader",
        "entity_type": "sole_trader",
        "abn": "11000000641",
        "financial_year_end": "06-30",
        "reporting_framework": "SPFR",
        "is_gst_registered": True,
        "include_comparative_figures": True,
    },
    chart=SOLE_TRADER_CHART,
    prior_year_tb=SOLE_TRADER_PRIOR_TB,
    retained_profits_code="4199",
)

BANK_BAS_IDS = {
    "client": "b1a5c0de-0000-4000-8000-000000000001",
    "entity": "b1a5c0de-0000-4000-8000-000000000002",
    "current_fy": "b1a5c0de-0000-4000-8000-000000000003",
    # Not in the brief's illustrative dict, but seed_fixture_entity always creates a
    # prior FinancialYear row (it is the one that carries prior_year_tb, even an
    # empty one) -- omitting this key is a KeyError, not an inert no-op. The bank-
    # to-BAS flow itself never reads this year; it exists only so the seeder's shared
    # code path has somewhere to put the (empty) prior_year_tb below.
    "prior_fy": "b1a5c0de-0000-4000-8000-000000000004",
}

BANK_BAS_CHART = [
    ("2000", "Cash at bank", "current_assets"),
    # "revenue", not the brief's "income" -- EntityChartOfAccount.StatementSection
    # (core/models.py) has no "income" choice. sqlite has no CHECK constraint so a
    # bad value here would save silently, but the hardened Postgres E2E copy would
    # reject it -- same trap CHART_OF_ACCOUNTS's comment above already warns about.
    ("0510", "Sales", "revenue"),
    ("1520", "Office supplies", "expenses"),
    ("1530", "Bank fees and charges", "expenses"),
    ("1540", "Food supplies", "expenses"),
    ("4199", "Retained profits", "equity"),
]

BANK_BAS = FixtureProfile(
    key="bank_bas",
    ids=BANK_BAS_IDS,
    client_name="E2E Bank BAS Client",
    entity_kwargs={
        "entity_name": "E2E Bank BAS Pty Ltd",
        "entity_type": "company",
        # Valid check digits — core/validators.py rejects malformed identifiers,
        # and a fixture that could not be saved through the UI is a trap.
        "abn": "51824753556",
        "acn": "004085616",
        "financial_year_end": "06-30",
        "reporting_framework": "SPFR",
        "company_size": "small_proprietary",
        # The whole point of this profile: without it there is no BAS to compute.
        "is_gst_registered": True,
        "include_comparative_figures": False,
        # bas_frequency is a real Entity field (confirmed against Entity._meta,
        # not the getattr fallback in core/views_bas.py) with choices
        # quarterly/monthly and default quarterly -- set explicitly rather than
        # relying on the model default, so this fixture states its own assumption.
        "bas_frequency": "quarterly",
    },
    chart=BANK_BAS_CHART,
    # No prior year trial balance. This flow never rolls forward, and an unnecessary
    # prior year would only add figures that later assertions could trip over. The
    # prior FinancialYear row itself still gets created (see BANK_BAS_IDS above) --
    # it is just empty, which is a valid, trivially-balanced (0 == 0) trial balance.
    prior_year_tb=[],
    retained_profits_code="4199",
    # Read by e2e_seed_fixture_entity to add the one extra manifest key this
    # profile's flow needs: which chart account the bank statement's opening/
    # closing balances reconcile against. No other profile's flow needs this, so it
    # is not a field every profile must populate.
    bank_account_code="2000",
)

PROFILES = {
    "company": COMPANY,
    "trust": TRUST,
    "partnership": PARTNERSHIP,
    "sole_trader": SOLE_TRADER,
    "bank_bas": BANK_BAS,
}


@transaction.atomic
def seed_fixture_entity(profile: FixtureProfile | str = "company") -> dict:
    """Create or reset one fixture entity. Idempotent."""
    if isinstance(profile, str):
        profile = PROFILES[profile]
    from core.models import (
        Client,
        DepreciationAsset,
        Entity,
        EntityChartOfAccount,
        FinancialYear,
        TrialBalanceLine,
    )

    client, _ = Client.objects.update_or_create(
        pk=profile.ids["client"],
        defaults={"name": profile.client_name, "is_active": True},
    )

    entity, _ = Entity.objects.update_or_create(
        pk=profile.ids["entity"],
        defaults={"client": client, **profile.entity_kwargs},
    )

    for code, name, section in profile.chart:
        EntityChartOfAccount.objects.update_or_create(
            entity=entity,
            account_code=code,
            defaults={"account_name": name, "section": section, "is_active": True},
        )

    prior = _upsert_year(
        FinancialYear,
        pk=profile.ids["prior_fy"],
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
        pk=profile.ids["current_fy"],
        entity=entity,
        year_label="2026",
        start=datetime.date(2025, 7, 1),
        end=datetime.date(2026, 6, 30),
        status=FinancialYear.Status.DRAFT,
        prior_year=prior,
    )

    TrialBalanceLine.objects.filter(financial_year=prior).delete()
    for code, name, debit, credit in profile.prior_year_tb:
        TrialBalanceLine.objects.create(
            financial_year=prior,
            account_code=code,
            account_name=name,
            debit=debit,
            credit=credit,
            closing_balance=debit - credit,
            source="tb_import",
        )

    if profile.depreciation_asset:
        asset_kwargs = dict(profile.depreciation_asset)
        DepreciationAsset.objects.update_or_create(
            pk=asset_kwargs.pop("pk"),
            defaults={"financial_year": current, **asset_kwargs},
        )

    return dict(profile.ids)


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
