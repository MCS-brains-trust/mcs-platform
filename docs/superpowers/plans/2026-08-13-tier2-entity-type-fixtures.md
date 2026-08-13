# Tier 2 Entity-Type Fixtures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Tier 2 a deterministic fixture entity for trust, partnership and sole trader, and run the roll-forward flow against each, so the type-specific equity handling is exercised end to end.

**Architecture:** `core/e2e_fixture_data.py` becomes profile-driven — a `FixtureProfile` dataclass with a `PROFILES` registry, replacing the module-level constants that hardcode one company. The company profile is ported unchanged so its blessed baseline stays valid. On the Playwright side, the roll-forward flow is extracted from `tier2/roll_forward.spec.ts` into `tier2/roll_forward_flow.ts` as `describeRollForward(profile)`, and each entity type gets a thin spec file with its own Django instance and database branch.

**Tech Stack:** Django 5 / Python 3.12, Playwright 1.49 + TypeScript, PostgreSQL 18 (E2E cluster on :5433).

**Spec:** `docs/superpowers/specs/2026-08-12-tier2-entity-type-fixtures-design.md`

## Global Constraints

- **Never touch production.** Production runs on this same host. Every Django management command in this plan uses `--settings=config.settings_e2e`; every database query targets port **5433**. Production's Celery worker is live on the default queue, so a task enqueued with default settings would execute against production.
- **Django tests need the sqlite override.** The default database is live Postgres and `manage.py test` cannot create a test database on it. Always:
  `DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test <label> -v1`
- **There is a large pre-existing failure baseline** in the wider Django test suite. Only run the specific test labels named in each task; do not judge success on a full-suite run.
- **`tier2/known_failures.json` must stay empty.** An entry there is a claim that a specific application defect is confirmed and accepted. Fix defects in application code and pin them with a Django unit test instead.
- **The escalation rule.** A contained failure (a missing name match, a bad classification, an off-by-one) is fixed under this plan. If a failure instead shows that a whole capability is absent — for example roll-forward having no handling for beneficiary or partner sub-accounts at all — that is an unimplemented feature, not a defect. **Stop, write up what building it would involve, and take it to the user.** Do not absorb it into this scope.
- **Blessing is manual and order-sensitive.** Playwright wipes `test-results/` at the start of every run, and the bless scripts read observed figures from there. Run one project, bless it, then run the other.
- **Account codes and sections are taken from real exemplars**, verified by query against `sh_e2e_template` on 2026-08-12. Do not invent codes.
- **Watch host contention.** `playwright.config.ts` sets `workers: 4` locally. This plan takes Tier 2 from three spec files to six, and each provisions its own Django instance and its own ~471 MB database branch. Disk is fine (31 GB free, ~2.8 GB needed), but the host has 7 GB of RAM and is also running production. Run the first full-tier pass with `--workers=2` and watch `free -g`; raise it only if there is clear headroom.

---

## Reference: verified facts

These were established by read-only query and are relied on by the tasks below.

**Section values in use.** Real charts use flat `assets` / `liabilities`, not the `current_assets` / `current_liabilities` the company fixture uses. Valid `EntityChartOfAccount.StatementSection` values are: `suspense`, `revenue`, `cost_of_sales`, `expenses`, `current_assets`, `non_current_assets`, `assets`, `current_liabilities`, `non_current_liabilities`, `liabilities`, `equity`, `capital_accounts`, `pl_appropriation`.

**Codes shared by all three exemplars:** `2000` Cash at bank (`assets`), `3048` Trade creditors (`liabilities`), `0105` Sales (`revenue`), `1510` Accountancy (`expenses`).

**Plant pairing convention:** cost at *N*, accumulated depreciation at *N+9*. Partnership and trust use `2860` / `2869`; sole trader uses `2850` / `2859`.

**The retained-profits account differs by type, and so does its section:**

| Type | Code | Name | Section |
|---|---|---|---|
| Trust | `4199` | Undistributed income | `pl_appropriation` |
| Partnership | `4199` | Unappropriated profits | `capital_accounts` |
| Sole trader | `4199` | Undistributed income | `capital_accounts` |

The trust's `pl_appropriation` section is the single most likely source of a defect in this work — `_is_balance_sheet_account` must treat it as a balance-sheet section for the year's result to land there.

**Valid fixture ABNs** (checked against `core.validators.is_valid_abn`): `11000000560`, `11000000592`, `11000000641`. ACN applies to companies only.

**Existing port allocation:** 8201 `yearend_close`, 8202 `roll_forward`, 8209 `instance.smoke`. Free: 8203, 8204, 8205.

---

## File Structure

**Modified:**
- `core/e2e_fixture_data.py` — becomes profile-driven; gains the three new profiles
- `core/management/commands/e2e_seed_fixture_entity.py` — gains `--profile`
- `e2e/tier2/roll_forward.spec.ts` — reduced to a thin caller of the shared flow
- `e2e/tier2/figures.baseline.json` — gains namespaced checkpoints

**Created:**
- `e2e/tier2/roll_forward_flow.ts` — `describeRollForward(profile)`, the extracted flow
- `e2e/tier2/roll_forward_trust.spec.ts`
- `e2e/tier2/roll_forward_partnership.spec.ts`
- `e2e/tier2/roll_forward_sole_trader.spec.ts`
- `core/tests_e2e_fixture_profiles.py` — unit tests for profile seeding

---

### Task 1: Introduce `FixtureProfile`, porting the company unchanged

This is a pure refactor. Its whole purpose is to change no seeded data, so the test asserts exactly that.

**Files:**
- Modify: `core/e2e_fixture_data.py`
- Test: `core/tests_e2e_fixture_profiles.py`

**Interfaces:**
- Produces: `FixtureProfile` dataclass; `PROFILES: dict[str, FixtureProfile]`; `seed_fixture_entity(profile: FixtureProfile | str = "company") -> dict`

- [ ] **Step 1: Write the failing test**

Create `core/tests_e2e_fixture_profiles.py`:

```python
"""The fixture profiles seed exactly what each entity type's real chart looks like.

Task 1's test is a refactor guard: the company profile must seed byte-identical
rows to the pre-refactor fixture, because its Tier 2 baseline is already blessed.
"""
from decimal import Decimal

from django.test import TestCase

from core.e2e_fixture_data import PROFILES, seed_fixture_entity


class CompanyProfileUnchangedTests(TestCase):
    def test_the_company_profile_seeds_the_same_seven_trial_balance_lines(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["company"])
        lines = {
            line.account_code: line
            for line in TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"])
        }
        self.assertEqual(len(lines), 7)
        self.assertEqual(lines["1-1000"].debit, Decimal("70000.00"))
        self.assertEqual(lines["3-1000"].credit, Decimal("60000.00"))
        self.assertEqual(lines["4-1000"].credit, Decimal("30000.00"))
        self.assertEqual(lines["6-1000"].debit, Decimal("10000.00"))
        self.assertEqual(
            sum(line.debit for line in lines.values()),
            sum(line.credit for line in lines.values()),
        )

    def test_the_company_profile_seeds_its_eight_chart_accounts(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["company"])
        codes = set(
            EntityChartOfAccount.objects.filter(
                entity_id=ids["entity"]
            ).values_list("account_code", flat=True)
        )
        self.assertEqual(
            codes,
            {"1-1000", "1-2000", "1-2100", "2-1000", "3-1000", "4-1000", "6-1000", "6-1200"},
        )

    def test_seeding_is_idempotent(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["company"])
        seed_fixture_entity(PROFILES["company"])
        self.assertEqual(
            TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"]).count(), 7
        )

    def test_the_default_argument_is_still_the_company(self):
        ids_default = seed_fixture_entity()
        ids_explicit = seed_fixture_entity(PROFILES["company"])
        self.assertEqual(ids_default["entity"], ids_explicit["entity"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_e2e_fixture_profiles -v1
```

Expected: FAIL with `ImportError: cannot import name 'PROFILES'`.

- [ ] **Step 3: Refactor `core/e2e_fixture_data.py`**

Add the dataclass above the existing constants, keeping the existing `FIXTURE_IDS`, `PRIOR_YEAR_TB` and `CHART_OF_ACCOUNTS` values as the company profile's data:

```python
import dataclasses


@dataclasses.dataclass(frozen=True)
class FixtureProfile:
    """One deterministic Tier 2 subject.

    `retained_profits_code` is the account the year's result must close into. It
    differs per entity type, and so does the section it lives in -- a trust's sits
    in pl_appropriation, a partnership's and a sole trader's in capital_accounts.
    """

    key: str
    ids: dict
    entity_kwargs: dict
    chart: list          # (code, name, section)
    prior_year_tb: list  # (code, name, debit, credit)
    retained_profits_code: str
    depreciation_asset: dict | None = None


COMPANY = FixtureProfile(
    key="company",
    ids=FIXTURE_IDS,
    entity_kwargs={
        "entity_name": "E2E Fixture Holdings Pty Ltd",
        "entity_type": "company",
        "abn": "51824753556",
        "acn": "004085616",
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
        "method": "P",
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

PROFILES = {"company": COMPANY}
```

Change the signature and body of `seed_fixture_entity` to read from the profile. The client name, entity `pk`, chart loop, year upserts, TB loop and depreciation asset all now read `profile.*` instead of the module constants:

```python
@transaction.atomic
def seed_fixture_entity(profile: "FixtureProfile | str" = "company") -> dict:
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
        defaults={"name": f"E2E Fixture Client ({profile.key})", "is_active": True},
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
    # ... years unchanged, reading profile.ids["prior_fy"] / ["current_fy"] ...

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
```

**Important:** the client name gains a ` (company)` suffix above. If any existing spec asserts on the literal string `E2E Fixture Client`, keep the company's name unsuffixed instead and only suffix the new profiles. Check with:
`grep -rn "E2E Fixture Client" /opt/statementhub/e2e /opt/statementhub/core`

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_e2e_fixture_profiles core.tests_e2e_fixture_data -v1
```

Expected: PASS. `core.tests_e2e_fixture_data` is the pre-existing suite for this module and must stay green — it is the real refactor guard.

- [ ] **Step 5: Verify the company's Tier 2 flow is untouched**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/roll_forward.spec.ts
```

Expected: all 6 tests pass against the **existing** blessed baseline, with no blessing step. If any figure moved, the refactor changed seeded data — fix that before continuing.

- [ ] **Step 6: Commit**

```bash
cd /opt/statementhub && git add core/e2e_fixture_data.py core/tests_e2e_fixture_profiles.py
git commit -m "refactor: make the Tier 2 fixture profile-driven

No seeded data changes. The company profile is ported verbatim so its blessed
baseline stays valid; PROFILES is the registry the entity-type fixtures land in."
```

---

### Task 2: Add `--profile` to the seed command

**Files:**
- Modify: `core/management/commands/e2e_seed_fixture_entity.py`

**Interfaces:**
- Consumes: `PROFILES`, `seed_fixture_entity(profile)` from Task 1
- Produces: `.e2e/fixture_entity.json` (company, unchanged path) and `.e2e/fixture_entity_<key>.json` per profile

- [ ] **Step 1: Modify the command**

```python
    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="/opt/statementhub/.e2e",
            help="Where to write the id manifests consumed by Playwright.",
        )
        parser.add_argument(
            "--profile",
            action="append",
            choices=sorted(PROFILES),
            help="Seed only this profile (repeatable). Default: all.",
        )

    def handle(self, *args, **options):
        assert_e2e_database()
        keys = options["profile"] or sorted(PROFILES)
        for key in keys:
            ids = seed_fixture_entity(PROFILES[key])
            # The company keeps the original unsuffixed path, because
            # tier2/roll_forward.spec.ts and yearend_close.spec.ts already read it.
            name = "fixture_entity.json" if key == "company" else f"fixture_entity_{key}.json"
            output = atomic_write_text(
                f"{options['output_dir']}/{name}", json.dumps(ids, indent=2) + "\n"
            )
            self.stdout.write(self.style.SUCCESS(f"{key} seeded → {output}"))
        workbooks = write_tb_workbooks("/opt/statementhub/.e2e/tb")
        self.stdout.write(self.style.SUCCESS(f"tb workbooks written: {len(workbooks)}"))
```

Add `from core.e2e_fixture_data import PROFILES, seed_fixture_entity` to the imports.

**Note:** the `--output` argument is replaced by `--output-dir`. Check for callers first:
`grep -rn "e2e_seed_fixture_entity" /opt/statementhub/e2e /opt/statementhub/scripts`
If any caller passes `--output`, keep `--output` as an alias for the company's path.

- [ ] **Step 2: Verify against the E2E database**

```bash
cd /opt/statementhub && venv/bin/python manage.py e2e_seed_fixture_entity --settings=config.settings_e2e --profile company
```

Expected: `company seeded → /opt/statementhub/.e2e/fixture_entity.json`, then the tb workbooks line.

- [ ] **Step 3: Commit**

```bash
git add core/management/commands/e2e_seed_fixture_entity.py
git commit -m "e2e: seed fixture profiles selectively with --profile"
```

---

### Task 3: Add the trust profile

The trust is deliberately first: its `4199` sits in `pl_appropriation` rather than `capital_accounts`, which is the likeliest defect in this work.

**Files:**
- Modify: `core/e2e_fixture_data.py`
- Test: `core/tests_e2e_fixture_profiles.py`

**Interfaces:**
- Produces: `PROFILES["trust"]`, ids prefixed `e2e00001-`

- [ ] **Step 1: Write the failing test**

Append to `core/tests_e2e_fixture_profiles.py`:

```python
class TrustProfileTests(TestCase):
    """Modelled on E & J Chiaravalle Family Trust: beneficiary sub-accounts at
    .01/.02, and 4199 Undistributed income sitting in pl_appropriation."""

    def test_the_prior_year_trial_balance_balances(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["trust"])
        lines = TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"])
        self.assertEqual(
            sum(line.debit for line in lines), sum(line.credit for line in lines)
        )
        self.assertEqual(sum(line.debit for line in lines), Decimal("100000.00"))

    def test_both_beneficiaries_have_their_own_sub_coded_capital_account(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["trust"])
        codes = set(
            EntityChartOfAccount.objects.filter(
                entity_id=ids["entity"], section="capital_accounts"
            ).values_list("account_code", flat=True)
        )
        self.assertIn("4000.01", codes)
        self.assertIn("4000.02", codes)
        self.assertIn("4005.01", codes)
        self.assertIn("4005.02", codes)

    def test_undistributed_income_is_in_the_pl_appropriation_section(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["trust"])
        account = EntityChartOfAccount.objects.get(
            entity_id=ids["entity"], account_code="4199"
        )
        self.assertEqual(account.account_name, "Undistributed income")
        self.assertEqual(account.section, "pl_appropriation")
        self.assertEqual(PROFILES["trust"].retained_profits_code, "4199")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_e2e_fixture_profiles.TrustProfileTests -v1
```

Expected: FAIL with `KeyError: 'trust'`.

- [ ] **Step 3: Add the profile**

```python
TRUST_IDS = {
    "client": "e2e00001-0000-4000-8000-000000000001",
    "entity": "e2e00001-0000-4000-8000-000000000002",
    "prior_fy": "e2e00001-0000-4000-8000-000000000003",
    "current_fy": "e2e00001-0000-4000-8000-000000000004",
}

# Codes and sections taken from E & J Chiaravalle Family Trust, verified by query
# against sh_e2e_template. Real charts use the flat `assets`/`liabilities` sections,
# not current_/non_current_, and the trust's 4199 sits in pl_appropriation.
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

# Same arithmetic as every other profile in this file -- 100,000 each side, a
# 20,000 net profit -- so a failure can be compared across entity types directly.
TRUST_PRIOR_TB = [
    ("2000", "Cash at bank", Decimal("70000.00"), Decimal("0.00")),
    ("2860", "Plant & equipment (cost)", Decimal("20000.00"), Decimal("0.00")),
    ("2869", "Less: Accumulated depreciation", Decimal("0.00"), Decimal("4000.00")),
    ("3048", "Trade creditors", Decimal("0.00"), Decimal("6000.00")),
    ("4000.01", "Opening balance - Beneficiary — Beneficiary One", Decimal("0.00"), Decimal("30000.00")),
    ("4000.02", "Opening balance - Beneficiary — Beneficiary Two", Decimal("0.00"), Decimal("30000.00")),
    ("0105", "Sales", Decimal("0.00"), Decimal("30000.00")),
    ("1510", "Accountancy", Decimal("10000.00"), Decimal("0.00")),
]

TRUST = FixtureProfile(
    key="trust",
    ids=TRUST_IDS,
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

PROFILES = {"company": COMPANY, "trust": TRUST}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_e2e_fixture_profiles -v1
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/e2e_fixture_data.py core/tests_e2e_fixture_profiles.py
git commit -m "e2e: add the trust fixture profile"
```

---

### Task 4: Add the partnership profile

**Files:**
- Modify: `core/e2e_fixture_data.py`
- Test: `core/tests_e2e_fixture_profiles.py`

**Interfaces:**
- Produces: `PROFILES["partnership"]`, ids prefixed `e2e00002-`

- [ ] **Step 1: Write the failing test**

```python
class PartnershipProfileTests(TestCase):
    """Modelled on D.P Vaughan & D Vriend: partner sub-accounts at .01/.02 and
    4199 Unappropriated profits in capital_accounts."""

    def test_the_prior_year_trial_balance_balances(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["partnership"])
        lines = TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"])
        self.assertEqual(
            sum(line.debit for line in lines), sum(line.credit for line in lines)
        )
        self.assertEqual(sum(line.debit for line in lines), Decimal("100000.00"))

    def test_both_partners_have_opening_balance_and_share_of_profit_accounts(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["partnership"])
        codes = set(
            EntityChartOfAccount.objects.filter(
                entity_id=ids["entity"]
            ).values_list("account_code", flat=True)
        )
        for code in ("4000.01", "4000.02", "4003.01", "4003.02", "4054.01", "4054.02"):
            self.assertIn(code, codes)

    def test_unappropriated_profits_is_the_retained_profits_account(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["partnership"])
        account = EntityChartOfAccount.objects.get(
            entity_id=ids["entity"], account_code="4199"
        )
        self.assertEqual(account.account_name, "Unappropriated profits")
        self.assertEqual(account.section, "capital_accounts")
        self.assertEqual(PROFILES["partnership"].retained_profits_code, "4199")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_e2e_fixture_profiles.PartnershipProfileTests -v1
```

Expected: FAIL with `KeyError: 'partnership'`.

- [ ] **Step 3: Add the profile**

```python
PARTNERSHIP_IDS = {
    "client": "e2e00002-0000-4000-8000-000000000001",
    "entity": "e2e00002-0000-4000-8000-000000000002",
    "prior_fy": "e2e00002-0000-4000-8000-000000000003",
    "current_fy": "e2e00002-0000-4000-8000-000000000004",
}

# Codes taken from D.P Vaughan & D Vriend. Drawings and share-of-profit accounts
# exist in the chart but carry no prior balance: they are movement accounts, and
# leaving them at zero keeps the arithmetic identical across all four profiles.
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
    ("4000.01", "Opening balance - Partner — Partner One", Decimal("0.00"), Decimal("30000.00")),
    ("4000.02", "Opening balance - Partner — Partner Two", Decimal("0.00"), Decimal("30000.00")),
    ("0105", "Sales", Decimal("0.00"), Decimal("30000.00")),
    ("1510", "Accountancy", Decimal("10000.00"), Decimal("0.00")),
]

PARTNERSHIP = FixtureProfile(
    key="partnership",
    ids=PARTNERSHIP_IDS,
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
```

Extend the registry: `PROFILES = {"company": COMPANY, "trust": TRUST, "partnership": PARTNERSHIP}`

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_e2e_fixture_profiles -v1
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/e2e_fixture_data.py core/tests_e2e_fixture_profiles.py
git commit -m "e2e: add the partnership fixture profile"
```

---

### Task 5: Add the sole trader profile

**Files:**
- Modify: `core/e2e_fixture_data.py`
- Test: `core/tests_e2e_fixture_profiles.py`

**Interfaces:**
- Produces: `PROFILES["sole_trader"]`, ids prefixed `e2e00003-`

- [ ] **Step 1: Write the failing test**

```python
class SoleTraderProfileTests(TestCase):
    """Modelled on Daniel Habteslassie: no sub-coded capital accounts at all, and
    a 2850/2859 plant pairing rather than 2860/2869."""

    def test_the_prior_year_trial_balance_balances(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["sole_trader"])
        lines = TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"])
        self.assertEqual(
            sum(line.debit for line in lines), sum(line.credit for line in lines)
        )
        self.assertEqual(sum(line.debit for line in lines), Decimal("100000.00"))

    def test_no_capital_account_is_sub_coded(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["sole_trader"])
        codes = EntityChartOfAccount.objects.filter(
            entity_id=ids["entity"], section="capital_accounts"
        ).values_list("account_code", flat=True)
        self.assertTrue(codes)
        for code in codes:
            self.assertNotIn(".", code)

    def test_undistributed_income_is_the_retained_profits_account(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["sole_trader"])
        account = EntityChartOfAccount.objects.get(
            entity_id=ids["entity"], account_code="4199"
        )
        self.assertEqual(account.account_name, "Undistributed income")
        self.assertEqual(account.section, "capital_accounts")
        self.assertEqual(PROFILES["sole_trader"].retained_profits_code, "4199")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_e2e_fixture_profiles.SoleTraderProfileTests -v1
```

Expected: FAIL with `KeyError: 'sole_trader'`.

- [ ] **Step 3: Add the profile**

```python
SOLE_TRADER_IDS = {
    "client": "e2e00003-0000-4000-8000-000000000001",
    "entity": "e2e00003-0000-4000-8000-000000000002",
    "prior_fy": "e2e00003-0000-4000-8000-000000000003",
    "current_fy": "e2e00003-0000-4000-8000-000000000004",
}

# Codes taken from Daniel Habteslassie. This chart deliberately has no sub-coded
# capital accounts -- the real one does not either -- so it isolates the
# type-correct retained-profits question from the sub-account question.
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

PROFILES = {
    "company": COMPANY,
    "trust": TRUST,
    "partnership": PARTNERSHIP,
    "sole_trader": SOLE_TRADER,
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/claude-0/-opt-statementhub/19a1599b-63b8-4db9-9e11-41cdb9d787f8/scratchpad/t2.sqlite3" venv/bin/python manage.py test core.tests_e2e_fixture_profiles -v1
```

Expected: PASS, all four profiles.

- [ ] **Step 5: Seed all profiles into the E2E database**

```bash
cd /opt/statementhub && venv/bin/python manage.py e2e_seed_fixture_entity --settings=config.settings_e2e
```

Expected: four `… seeded → …` lines.

- [ ] **Step 6: Commit**

```bash
git add core/e2e_fixture_data.py core/tests_e2e_fixture_profiles.py
git commit -m "e2e: add the sole trader fixture profile"
```

---

### Task 6: Extract the roll-forward flow

Pure refactor of the TypeScript, mirroring Task 1's shape: the company's behaviour must not change.

**Files:**
- Create: `e2e/tier2/roll_forward_flow.ts`
- Modify: `e2e/tier2/roll_forward.spec.ts`

**Interfaces:**
- Produces: `describeRollForward(opts: RollForwardOptions): void` and `interface RollForwardOptions`

- [ ] **Step 1: Create the flow module**

Move everything from `roll_forward.spec.ts` between the imports and the end of the file into `roll_forward_flow.ts`, wrapped in an exported function. The options carry everything that differs per entity type:

```ts
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';
import { dumpFigures, recordObserved, compareToBaseline } from '../fixtures/figures';
import { E2E_STATE_DIR, VENV_PYTHON } from '../fixtures/paths';

export interface RollForwardOptions {
  /** Profile key: 'company' | 'trust' | 'partnership' | 'sole_trader'. */
  profile: string;
  /** Dedicated port and database branch for this file's Django instance. */
  port: number;
  /** Manifest written by e2e_seed_fixture_entity. */
  manifest: string;
  /** The account the year's result must close into. */
  retainedProfitsCode: string;
  /** Every account expected to carry a prior closing balance into the new year. */
  balanceSheetCodes: string[];
  /** Accounts expected to carry a comparative only, never an opening balance. */
  plCodes: string[];
  /** Expected row count in the rolled year's trial balance. */
  expectedRolledRows: number;
  /** Expected opening balance on retainedProfitsCode after the roll. */
  expectedRetainedOpening: string;
  /**
   * Checkpoint prefix. The company passes '' so its already-blessed checkpoint
   * names ('after_roll_forward') are unchanged; others pass 'trust:' etc.
   */
  checkpointPrefix: string;
  /**
   * The COMPLETE amended prior-year trial balance, written to a workbook and
   * re-uploaded to prove the reroll diff catches drift. `writeAmendedPriorTbWorkbook`
   * builds a whole TB rather than a delta, so this is the whole thing.
   *
   * Follow the company's existing shape: raise one expense and one liability by the
   * same amount, so the TB still balances and the net profit moves -- which is what
   * makes the retained-profits line change and the diff report something.
   */
  amendedPriorTb: Array<[string, string, number, number]>;
  /** The account the amendment is expected to move in the reroll diff. */
  amendedAccountCode: string;
}

export function describeRollForward(opts: RollForwardOptions): void {
  test.describe.configure({ mode: 'serial' });

  const IDS = JSON.parse(fs.readFileSync(`${E2E_STATE_DIR}/${opts.manifest}`, 'utf-8'));
  const PRIOR_FY = IDS.prior_fy;
  const CURRENT_FY = IDS.current_fy;

  let instance: Instance;

  test.beforeAll(async () => {
    instance = await startInstance(`roll_forward_${opts.profile}`, opts.port);
  });

  test.afterAll(async () => {
    await instance?.stop();
  });

  // ... the existing helpers (seniorPage, uploadTb, submitReview,
  // openRerollModalAndGetDiff) and the existing tests move here verbatim,
  // with hardcoded '3-1000' / 7 / '-80000.00' replaced by the opts fields
  // and every checkpoint string prefixed with opts.checkpointPrefix.
}
```

**The account-code regex must go.** This is the single most important change in this task, and moving the final test verbatim would silently break every new profile.

The current final test classifies balance-sheet accounts by matching the first character of the account code:

```ts
const priorBsClosing = new Map<string, string>(
  prior.trial_balance
    .filter((r: any) => /^[123]/.test(r.account_code))   // <-- WRONG for HandiLedger
    .map((r: any) => [r.account_code, r.closing_balance]),
);
```

That works only because the company fixture uses MYOB-style `1-`/`2-`/`3-` codes. Against the HandiLedger codes every real entity uses, it misclassifies in both directions:

| Code | Really is | `/^[123]/` says |
|---|---|---|
| `1510` Accountancy | expense | **balance sheet** ✗ |
| `4000.01` Opening balance - Beneficiary | capital | **P&L** ✗ |
| `4199` Undistributed income | capital | **P&L** ✗ |

Replace both uses of the regex — the `priorBsClosing` filter above and the `netPlResult` reducer below it — with explicit membership tests against the options:

```ts
const bsCodes = new Set(opts.balanceSheetCodes);
const priorBsClosing = new Map<string, string>(
  prior.trial_balance
    .filter((r: any) => bsCodes.has(r.account_code))
    .map((r: any) => [r.account_code, r.closing_balance]),
);

const netPlResult = prior.trial_balance
  .filter((r: any) => !bsCodes.has(r.account_code))
  .reduce((sum: number, r: any) => sum + parseFloat(r.closing_balance), 0);
```

For the company, `balanceSheetCodes` is exactly the set the regex used to select, so its behaviour is unchanged — which Step 3 verifies. Update the surrounding comment, which currently explains the regex.

**The amendment test.** The final test amends the prior year by uploading a modified TB workbook. `writeAmendedPriorTbWorkbook` (lines 119–156 of the current spec) hardcodes the company's seven rows into an embedded Python script. Change its signature to `writeAmendedPriorTbWorkbook(path: string, rows: Array<[string, string, number, number]>)` and interpolate the rows instead of hardcoding them:

```ts
const rowsLiteral = JSON.stringify(rows);
const script = `
import os, tempfile, json
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Trial Balance"
ws.append(("Account Code", "Account Name", "Debit", "Credit"))
for row in json.loads(${JSON.stringify(rowsLiteral)}):
    ws.append(tuple(row))
target = ${JSON.stringify(path)}
` + /* the existing atomic-write tail, unchanged */ '';
```

The atomic temp-file-then-`os.replace` tail must stay exactly as it is — its comment explains that concurrently booting instances must never catch a half-written xlsx.

The company's rows are passed verbatim from its existing hardcoded list, so its behaviour does not change: Administration rises 10,000 → 11,000 and Trade Creditors 6,000 → 7,000, everything else untouched.

- [ ] **Step 2: Reduce `roll_forward.spec.ts` to a caller**

```ts
import { describeRollForward } from './roll_forward_flow';

describeRollForward({
  profile: 'company',
  port: 8202,
  manifest: 'fixture_entity.json',
  retainedProfitsCode: '3-1000',
  balanceSheetCodes: ['1-1000', '1-2000', '1-2100', '2-1000', '3-1000'],
  plCodes: ['4-1000', '6-1000'],
  expectedRolledRows: 7,
  expectedRetainedOpening: '-80000.00',
  checkpointPrefix: '',
  // Verbatim from the current writeAmendedPriorTbWorkbook: Administration
  // 10,000 -> 11,000 and Trade Creditors 6,000 -> 7,000.
  amendedPriorTb: [
    ['1-1000', 'Cash at Bank', 70000.0, 0.0],
    ['1-2000', 'Plant and Equipment', 20000.0, 0.0],
    ['1-2100', 'Accumulated Depreciation', 0.0, 4000.0],
    ['2-1000', 'Trade Creditors', 0.0, 7000.0],
    ['3-1000', 'Retained Earnings', 0.0, 60000.0],
    ['4-1000', 'Sales', 0.0, 30000.0],
    ['6-1000', 'Administration', 11000.0, 0.0],
  ],
  amendedAccountCode: '6-1000',
});
```

Keep the existing file header comment — it documents two production fixes and the ordering convention, and it is still true.

- [ ] **Step 3: Verify the company is unchanged**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/roll_forward.spec.ts
```

Expected: the same 6 tests pass against the existing blessed baseline, with no blessing. Any figure change means the extraction altered behaviour.

- [ ] **Step 4: Commit**

```bash
cd /opt/statementhub && git add e2e/tier2/roll_forward_flow.ts e2e/tier2/roll_forward.spec.ts
git commit -m "e2e: extract the roll-forward flow so entity types can share it"
```

---

### Task 7: Trust roll-forward spec

**This is the task most likely to trigger the escalation rule.** Read the Global Constraints entry on it before starting.

**Files:**
- Create: `e2e/tier2/roll_forward_trust.spec.ts`

- [ ] **Step 1: Write the spec**

```ts
/**
 * Roll-forward for a trust.
 *
 * Modelled on E & J Chiaravalle Family Trust. Two things differ from the company:
 * the beneficiary capital accounts are sub-coded (.01/.02), and 4199 Undistributed
 * income sits in the pl_appropriation section rather than capital_accounts -- so
 * _is_balance_sheet_account has to treat pl_appropriation as a balance-sheet
 * section for the year's result to land there at all.
 */
import { describeRollForward } from './roll_forward_flow';

describeRollForward({
  profile: 'trust',
  port: 8203,
  manifest: 'fixture_entity_trust.json',
  retainedProfitsCode: '4199',
  balanceSheetCodes: ['2000', '2860', '2869', '3048', '4000.01', '4000.02'],
  plCodes: ['0105', '1510'],
  // Six balance-sheet accounts + two P&L comparatives + 4199 taking the result.
  expectedRolledRows: 9,
  expectedRetainedOpening: '-20000.00',
  checkpointPrefix: 'trust:',
  // Same shape as the company's: Accountancy 10,000 -> 11,000, Trade creditors
  // 6,000 -> 7,000. Still balanced at 101,000, and the net profit moves to 19,000.
  amendedPriorTb: [
    ['2000', 'Cash at bank', 70000.0, 0.0],
    ['2860', 'Plant & equipment (cost)', 20000.0, 0.0],
    ['2869', 'Less: Accumulated depreciation', 0.0, 4000.0],
    ['3048', 'Trade creditors', 0.0, 7000.0],
    ['4000.01', 'Opening balance - Beneficiary — Beneficiary One', 0.0, 30000.0],
    ['4000.02', 'Opening balance - Beneficiary — Beneficiary Two', 0.0, 30000.0],
    ['0105', 'Sales', 0.0, 30000.0],
    ['1510', 'Accountancy', 11000.0, 0.0],
  ],
  amendedAccountCode: '1510',
});
```

**Note on `expectedRolledRows`:** 9 assumes `4199` is created as a new line, since the prior-year TB has no `4199` row. If the flow instead carries only the eight prior rows, the correct expectation is 8 and the year's result has gone somewhere else — investigate before changing the number. Never change an expectation to match observed output without understanding why it moved.

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/roll_forward_trust.spec.ts
```

Expected on a first run: some tests fail. That is the point of the exercise.

- [ ] **Step 3: Triage each failure**

For each failure, decide which of three things it is:

1. **A fixture error** (a wrong code, an unbalanced TB, a bad section value) — fix the fixture.
2. **A contained application defect** — fix it in `core/views.py`, pin it with a test in `core/tests_rollforward_retained_profits.py`, and commit fix + test together in the shape of PR #32.
3. **An absent capability** — stop and take it to the user per the escalation rule.

The most likely defect: `_is_balance_sheet_account` not treating `pl_appropriation` as a balance-sheet section, so the trust's `4199` is classified as P&L and the year's result has nowhere to close into. If that is what happens, it is category 2.

- [ ] **Step 4: Re-run until green**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/roll_forward_trust.spec.ts
```

Expected: all tests pass except the baseline comparison, which reports `trust:after_roll_forward: not in the baseline yet`. That one is expected and is blessed in Task 10.

- [ ] **Step 5: Commit**

```bash
cd /opt/statementhub && git add e2e/tier2/roll_forward_trust.spec.ts
git commit -m "e2e: roll-forward flow for a trust fixture"
```

---

### Task 8: Partnership roll-forward spec

**Files:**
- Create: `e2e/tier2/roll_forward_partnership.spec.ts`

- [ ] **Step 1: Write the spec**

```ts
/**
 * Roll-forward for a partnership.
 *
 * Modelled on D.P Vaughan & D Vriend. Partner capital accounts are sub-coded
 * (.01/.02) as the trust's beneficiary accounts are, but 4199 Unappropriated
 * profits sits in capital_accounts rather than pl_appropriation -- so this file
 * isolates the sub-account question from the section question the trust raises.
 */
import { describeRollForward } from './roll_forward_flow';

describeRollForward({
  profile: 'partnership',
  port: 8204,
  manifest: 'fixture_entity_partnership.json',
  retainedProfitsCode: '4199',
  balanceSheetCodes: ['2000', '2860', '2869', '3048', '4000.01', '4000.02'],
  plCodes: ['0105', '1510'],
  expectedRolledRows: 9,
  expectedRetainedOpening: '-20000.00',
  checkpointPrefix: 'partnership:',
  amendedPriorTb: [
    ['2000', 'Cash at bank', 70000.0, 0.0],
    ['2860', 'Plant & equipment (cost)', 20000.0, 0.0],
    ['2869', 'Less: Accumulated depreciation', 0.0, 4000.0],
    ['3048', 'Trade creditors', 0.0, 7000.0],
    ['4000.01', 'Opening balance - Partner — Partner One', 0.0, 30000.0],
    ['4000.02', 'Opening balance - Partner — Partner Two', 0.0, 30000.0],
    ['0105', 'Sales', 0.0, 30000.0],
    ['1510', 'Accountancy', 11000.0, 0.0],
  ],
  amendedAccountCode: '1510',
});
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/roll_forward_partnership.spec.ts
```

- [ ] **Step 3: Triage each failure**

Same three categories as Task 7. Note that the share-of-profit (`4003.01`/`4003.02`) and drawings (`4054.01`/`4054.02`) accounts are in the chart but carry no prior balance, so they should not appear in the rolled trial balance at all. If they do appear with zero balances, that is a fixture/expectation question, not necessarily a defect — check what the company profile does with `6-1200` Depreciation, which is in its chart but not its prior TB, and match that behaviour.

- [ ] **Step 4: Re-run until green**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/roll_forward_partnership.spec.ts
```

Expected: green except `partnership:after_roll_forward: not in the baseline yet`.

- [ ] **Step 5: Commit**

```bash
cd /opt/statementhub && git add e2e/tier2/roll_forward_partnership.spec.ts
git commit -m "e2e: roll-forward flow for a partnership fixture"
```

---

### Task 9: Sole trader roll-forward spec

**Files:**
- Create: `e2e/tier2/roll_forward_sole_trader.spec.ts`

- [ ] **Step 1: Write the spec**

```ts
/**
 * Roll-forward for a sole trader.
 *
 * Modelled on Daniel Habteslassie. No sub-coded capital accounts -- the real chart
 * has none -- so this is the cleanest test of "does the year's result reach the
 * type-correct retained-profits account", with the sub-account variable removed.
 */
import { describeRollForward } from './roll_forward_flow';

describeRollForward({
  profile: 'sole_trader',
  port: 8205,
  manifest: 'fixture_entity_sole_trader.json',
  retainedProfitsCode: '4199',
  balanceSheetCodes: ['2000', '2850', '2859', '3048', '4010'],
  plCodes: ['0105', '1510'],
  // Five balance-sheet accounts + two P&L comparatives + 4199 taking the result.
  expectedRolledRows: 8,
  expectedRetainedOpening: '-20000.00',
  checkpointPrefix: 'sole_trader:',
  amendedPriorTb: [
    ['2000', 'Cash at bank', 70000.0, 0.0],
    ['2850', 'Plant & equipment - At cost', 20000.0, 0.0],
    ['2859', 'Less: Accumulated depreciation', 0.0, 4000.0],
    ['3048', 'Trade creditors', 0.0, 7000.0],
    ['4010', 'Capital contribution', 0.0, 60000.0],
    ['0105', 'Sales', 0.0, 30000.0],
    ['1510', 'Accountancy', 11000.0, 0.0],
  ],
  amendedAccountCode: '1510',
});
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/roll_forward_sole_trader.spec.ts
```

- [ ] **Step 3: Triage each failure**

Same three categories as Task 7.

- [ ] **Step 4: Re-run until green**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/roll_forward_sole_trader.spec.ts
```

Expected: green except `sole_trader:after_roll_forward: not in the baseline yet`.

- [ ] **Step 5: Commit**

```bash
cd /opt/statementhub && git add e2e/tier2/roll_forward_sole_trader.spec.ts
git commit -m "e2e: roll-forward flow for a sole trader fixture"
```

---

### Task 10: Bless the baselines and verify the whole tier

**Files:**
- Modify: `e2e/tier2/figures.baseline.json`

- [ ] **Step 1: Run the whole tier in one go**

Blessing must happen in the same breath as the run that produced the figures, because Playwright wipes `test-results/` at the start of every run.

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- --workers=2
```

Expected: every test passes except the three `<type>:after_roll_forward: not in the baseline yet` reports.

`--workers=2` is deliberate — see the host-contention constraint. Six spec files each boot their own Django instance and database branch on a host that is also running production.

- [ ] **Step 2: Read the observed figures before blessing**

```bash
cd /opt/statementhub/e2e && for f in test-results/observed-figures/*roll_forward*.json; do
  echo "== $f"; python3 -c "
import json,sys
d=json.load(open('$f'))
print(d['checkpoint'])
for l in d['figures'].get('trial_balance',[]):
    print(' ', l['account_code'], l['account_name'], l['opening_balance'])
"; done
```

Confirm for each: the balance-sheet accounts carry their prior closing balances, `4199` holds `-20000.00`, the P&L accounts have a zero opening, and the openings sum to zero. **Do not bless figures you have not checked.**

- [ ] **Step 3: Bless**

```bash
cd /opt/statementhub/e2e && npm run bless:figures
git diff e2e/tier2/figures.baseline.json
```

Expected: three checkpoints added, the four existing ones byte-identical. Verify with:

```bash
cd /opt/statementhub && python3 -c "
import json, subprocess
old = json.loads(subprocess.run(['git','show','HEAD:e2e/tier2/figures.baseline.json'],
                                capture_output=True, text=True).stdout)
new = json.load(open('e2e/tier2/figures.baseline.json'))
for k in sorted(set(old) & set(new)):
    print(('UNCHANGED ' if old[k] == new[k] else '*** CHANGED *** ') + k)
for k in sorted(set(new) - set(old)):
    print('ADDED ' + k)
"
```

Any `*** CHANGED ***` on an existing checkpoint means something regressed — investigate before committing.

- [ ] **Step 4: Confirm the full tier is green**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 && npm run check:tier2-failures
```

Expected: all tests pass, and the known-failures check reports no discrepancy against the empty list.

- [ ] **Step 5: Update the suite documentation**

In `e2e/README.md`, update the "Current coverage" section: Tier 2 now covers roll-forward across four entity types, not two flows against one entity.

- [ ] **Step 6: Commit**

```bash
cd /opt/statementhub && git add e2e/tier2/figures.baseline.json e2e/README.md
git commit -m "e2e: bless the entity-type roll-forward baselines

Tier 2 now exercises roll-forward against company, trust, partnership and sole
trader fixtures. known_failures.json stays empty."
```

---

## Verification

The work is complete when all of these hold:

1. `npm run test:tier2` is fully green and `npm run check:tier2-failures` reports no discrepancy.
2. `e2e/tier2/known_failures.json` is still empty.
3. The company's four pre-existing baseline checkpoints are byte-identical to their pre-refactor values.
4. `core.tests_e2e_fixture_profiles`, `core.tests_e2e_fixture_data`, `core.tests_rollforward_classification` and `core.tests_rollforward_retained_profits` all pass.
5. Every application defect found is fixed in `core/views.py` and pinned by a Django test, one commit each.
6. Anything that turned out to be an absent capability was escalated to the user, not built.
