# Unit Trust Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `trust_unit` a selectable entity type whose income is distributed strictly in proportion to unit holdings, with "Unit Holder" terminology throughout and no discretionary planning.

**Architecture:** A new `EntityType` value, `trust_unit`, inherits all trust behaviour by default via a `TRUST_LIKE_TYPES` constant, then diverges explicitly at the four places that must differ: allocation, the tax planning tab, terminology, and the unit register. Units live on `EntityOfficer.units_held`; `distribution_percentage` remains a stored field but is recomputed from units rather than typed, which is what lets every existing consumer keep working unchanged.

**Tech Stack:** Django 5, Python 3.12, PostgreSQL (production) / SQLite (tests), Django templates, `manage.py` management commands.

**Spec:** `docs/superpowers/specs/2026-08-27-unit-trust-distribution-design.md`

## Global Constraints

- **Stored value is `trust_unit`, display label is "Unit Trust".** Never `unit_trust` — roughly twenty existing sites already reference `trust_unit`.
- **Discretionary trusts must not change behaviour.** Every task that touches shared code needs a regression test proving `entity_type="trust"` still behaves as before. This is the primary risk in the whole plan.
- **Run tests with a SQLite override.** The managed Postgres has no `postgres` maintenance database, so `manage.py test` cannot create a test DB against it:
  ```bash
  DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test <labels>
  ```
- **View tests need three things** or they fail for reasons unrelated to your change:
  1. `staticfiles/` must exist in the worktree (`ln -sfn /opt/statementhub/staticfiles staticfiles`), or templates raise `Missing staticfiles manifest entry for 'css/style.css'`.
  2. POST/GET through `self.client` needs `secure=True`, because `SECURE_SSL_REDIRECT` is on and a plain request gets a bare 301.
  3. After `force_login`, set `session["2fa_verified"] = True` by hand — `Require2FAMiddleware` gates everything.
- **Known pre-existing failure:** `core.tests_jt_client_picker.JtClientPickerTests.test_results_render_one_row_per_client` fails on `main`. It is not yours. Compare failure *sets*, not counts.
- **`.env` must be symlinked into the worktree** (`ln -sf /opt/statementhub/.env .env`) or Django refuses to start.
- **Money is `Decimal`.** Never float. Import `from decimal import Decimal, ROUND_HALF_UP`.
- **Never edit files in `/opt/statementhub` directly** — that tree is what gunicorn serves. Work in the worktree.

---

## File Structure

**Created:**
- `core/unit_allocation.py` — the allocation arithmetic. Pure functions, no ORM, so the rounding rules can be tested in isolation.
- `core/entity_terminology.py` — the label resolver. One responsibility: entity type in, noun out.
- `core/management/commands/rename_unit_trust_chart_terms.py` — one-off chart rename for existing unit trusts.
- `core/tests_unit_allocation.py`, `core/tests_unit_register.py`, `core/tests_trust_like_types.py`, `core/tests_unit_trust_tax_planning.py`, `core/tests_entity_terminology.py`

**Modified:**
- `core/models.py` — `EntityType`, `TRUST_LIKE_TYPES`, `Entity.is_trust_like`, `Entity.total_units`, `EntityOfficer.units_held`, `EntityOfficer.unit_percentage`, `CapitalAccountTemplate.TRUST_ENTITY_TYPES`
- `core/views.py`, `core/views_trust.py`, `core/views_tax_planning.py`, `core/fs_template_service.py`, `core/docgen.py`, `core/document_context_builder.py`, `core/beneficiary_account_service.py`, `core/package_service.py`, and the other sweep sites
- `templates/core/entity_officers.html`, `entity_officer_form.html`, `financial_year_detail.html`, and the other template sweep sites

---

### Task 1: The `trust_unit` entity type and the compatibility constant

**Files:**
- Modify: `core/models.py` (`Entity.EntityType` ~line 144, `Entity` class body)
- Create: `core/tests_trust_like_types.py`
- Create: `core/migrations/XXXX_add_trust_unit_entity_type.py` (generated)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `core.models.TRUST_LIKE_TYPES: tuple[str, ...]` — `("trust", "trust_unit")`
  - `Entity.EntityType.UNIT_TRUST` — value `"trust_unit"`, label `"Unit Trust"`
  - `Entity.is_trust_like -> bool` (property)
  - `Entity.is_unit_trust -> bool` (property)

- [ ] **Step 1: Write the failing test**

Create `core/tests_trust_like_types.py`:

```python
"""trust_unit is a real entity type that inherits trust behaviour."""
from django.test import TestCase

from core.models import TRUST_LIKE_TYPES, Entity


class TrustLikeTypesTests(TestCase):
    def test_unit_trust_is_a_selectable_entity_type(self):
        self.assertIn(("trust_unit", "Unit Trust"), Entity.EntityType.choices)

    def test_trust_like_types_covers_both_trust_kinds(self):
        self.assertEqual(TRUST_LIKE_TYPES, ("trust", "trust_unit"))

    def test_discretionary_trust_is_trust_like(self):
        e = Entity.objects.create(entity_name="Vincent Family Trust", entity_type="trust")
        self.assertTrue(e.is_trust_like)
        self.assertFalse(e.is_unit_trust)

    def test_unit_trust_is_both_trust_like_and_a_unit_trust(self):
        e = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        self.assertTrue(e.is_trust_like)
        self.assertTrue(e.is_unit_trust)

    def test_company_is_neither(self):
        e = Entity.objects.create(entity_name="DJLH", entity_type="company")
        self.assertFalse(e.is_trust_like)
        self.assertFalse(e.is_unit_trust)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_trust_like_types -v 2
```
Expected: FAIL — `ImportError: cannot import name 'TRUST_LIKE_TYPES'`.

- [ ] **Step 3: Add the entity type and the constant**

In `core/models.py`, add the choice to `Entity.EntityType` (keep it adjacent to `TRUST` so the dropdown groups sensibly):

```python
    class EntityType(models.TextChoices):
        COMPANY = "company", "Company"
        TRUST = "trust", "Trust"
        UNIT_TRUST = "trust_unit", "Unit Trust"
        PARTNERSHIP = "partnership", "Partnership"
        SOLE_TRADER = "sole_trader", "Sole Trader"
        SMSF = "smsf", "SMSF"
```

Above the `Entity` class, add the module-level constant:

```python
# Entity types that behave as trusts. A unit trust inherits every trust
# behaviour by default and diverges only where the deed requires it, so new
# trust logic should test membership here rather than equality with "trust".
TRUST_LIKE_TYPES = ("trust", "trust_unit")
```

In the `Entity` class body, add the two properties:

```python
    @property
    def is_trust_like(self):
        """True for any trust, discretionary or unit. Use in templates."""
        return self.entity_type in TRUST_LIKE_TYPES

    @property
    def is_unit_trust(self):
        """True only where income must follow the unit register."""
        return self.entity_type == self.EntityType.UNIT_TRUST
```

- [ ] **Step 4: Generate and inspect the migration**

```bash
python3 manage.py makemigrations core --name add_trust_unit_entity_type
```
Confirm it is an `AlterField` on `entity.entity_type` choices only — no data operations. Choices changes do not alter the database column, but Django records them.

- [ ] **Step 5: Run test to verify it passes**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_trust_like_types -v 2
```
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add core/models.py core/tests_trust_like_types.py core/migrations/
git commit -m "feat(entity): add trust_unit entity type and TRUST_LIKE_TYPES"
```

---

### Task 2: Type-keyed lookups resolve `trust_unit` to `trust`

**Files:**
- Modify: `core/models.py` — `CapitalAccountTemplate.TRUST_ENTITY_TYPES` (~line 645), `GlobalAccountMappingHint.record_mapping` (~line 6758)
- Modify: `core/management/commands/map_accounts.py:278,326`
- Modify: `core/models.py` — `EntityChartOfAccount` seeding (~line 1217, the `seed_from_template` path)
- Create: `core/tests_unit_trust_chart_seeding.py`

**Interfaces:**
- Consumes: `TRUST_LIKE_TYPES`, `Entity.is_unit_trust` (Task 1)
- Produces: `core.models.template_entity_type(entity_type: str) -> str` — maps `"trust_unit"` to `"trust"`, returns every other value unchanged.

Chart templates hold 468 rows for `trust` and none for `trust_unit`. Without this task a new unit trust seeds an empty chart of accounts.

- [ ] **Step 1: Write the failing test**

Create `core/tests_unit_trust_chart_seeding.py`:

```python
"""A unit trust seeds its chart from the trust template, not from nothing."""
from django.test import TestCase

from core.models import (
    ChartOfAccount,
    Entity,
    EntityChartOfAccount,
    template_entity_type,
)


class TemplateEntityTypeTests(TestCase):
    def test_unit_trust_resolves_to_trust(self):
        self.assertEqual(template_entity_type("trust_unit"), "trust")

    def test_other_types_pass_through(self):
        for value in ("trust", "company", "partnership", "sole_trader", "smsf"):
            self.assertEqual(template_entity_type(value), value)


class UnitTrustChartSeedingTests(TestCase):
    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="620",
            account_name="Rents received", section="revenue",
        )
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="4000",
            account_name="Opening balance - Beneficiary", section="equity",
        )

    def test_unit_trust_seeds_from_the_trust_template(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        EntityChartOfAccount.seed_from_template(entity)

        codes = set(
            EntityChartOfAccount.objects.filter(entity=entity)
            .values_list("account_code", flat=True)
        )
        self.assertEqual(codes, {"620", "4000"})

    def test_discretionary_trust_seeding_is_unchanged(self):
        entity = Entity.objects.create(entity_name="Vincent", entity_type="trust")
        EntityChartOfAccount.seed_from_template(entity)

        codes = set(
            EntityChartOfAccount.objects.filter(entity=entity)
            .values_list("account_code", flat=True)
        )
        self.assertEqual(codes, {"620", "4000"})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_trust_chart_seeding -v 2
```
Expected: FAIL — `cannot import name 'template_entity_type'`.

- [ ] **Step 3: Add the resolver and apply it**

In `core/models.py`, beside `TRUST_LIKE_TYPES`:

```python
def template_entity_type(entity_type):
    """Entity type to use when looking up type-keyed template data.

    A unit trust has no templates of its own by design: it uses the trust
    chart, the trust capital accounts and the trust mapping hints. Resolving
    here keeps one source of truth — cloned template rows would drift from the
    xlsx source the moment either was edited.
    """
    if entity_type == Entity.EntityType.UNIT_TRUST:
        return Entity.EntityType.TRUST
    return entity_type
```

Apply it at every type-keyed lookup. In `EntityChartOfAccount.seed_from_template` (~line 1217), change the template query to:

```python
        templates = ChartOfAccount.objects.filter(
            entity_type=template_entity_type(entity.entity_type),
            is_active=True,
        )
```

In `GlobalAccountMappingHint.record_mapping` (~line 6758), normalise on the way in:

```python
        entity_type = template_entity_type(entity_type)
```

Widen the capital account template choices (~line 645):

```python
    TRUST_ENTITY_TYPES = [
        ("trust", "Trust"),
        ("trust_unit", "Unit Trust"),
    ]
```

and resolve at its lookup sites so a unit trust reads the `trust` rows.

In `core/management/commands/map_accounts.py`, lines 278 and 326, replace the `entity_type == "trust"` tests with `template_entity_type(entity_type) == "trust"`.

- [ ] **Step 4: Run test to verify it passes**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_trust_chart_seeding -v 2
```
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/management/commands/map_accounts.py core/tests_unit_trust_chart_seeding.py
git commit -m "feat(entity): resolve trust_unit to trust for type-keyed templates"
```

---

### Task 3: Sweep the Python trust branch sites

**Files:**
- Modify: `core/package_service.py:462`, `core/views_compliance_docs.py:594`, `core/views_partnership_docs.py:700`, `core/package_pdf_renderer.py:184`, `core/views.py:2761,2788,2951,4975,5011,7068,7497`, `core/fs_template_service.py:1249,3287,3521`, `core/document_context_builder.py:766,780,1538,1625,2061`, `core/docgen.py:1204,1958,1965,2119,2158,2933,3111,3386`, `core/legal_doc_contexts.py:90`, `core/beneficiary_account_service.py:139`, `core/management/commands/generate_fs_templates.py:794,1268,1300`, `core/risk_modules/section100a.py:59`
- Create: `core/tests_trust_sweep_regression.py`

**Interfaces:**
- Consumes: `TRUST_LIKE_TYPES`, `template_entity_type` (Tasks 1–2)
- Produces: no new API. Behavioural guarantee: every listed site treats `trust_unit` as it treats `trust`.

Leave `core/legal_doc_contexts.py:888,1262,1325` alone — those test whether a *counterparty* entity is a trust for document wording, and a unit trust genuinely is one, so widening them is correct; but they are cosmetic and carry no test. Handle them in the same sweep for consistency.

Do **not** change `core/risk_modules/section100a.py:59` to include `trust_unit`. Section 100A concerns discretionary distributions; a fixed unit trust has none, and `core/views_trust.py:57` already auto-skips that stage for unit trusts. Narrowing it deliberately is the point — add a comment saying so.

- [ ] **Step 1: Write the failing test**

Create `core/tests_trust_sweep_regression.py`:

```python
"""The sweep must widen trust behaviour to unit trusts without altering trusts."""
from datetime import date

from django.test import TestCase

from core.models import Entity, FinancialYear
from core.beneficiary_account_service import ...  # import the function at line 139
from core.risk_modules.section100a import Section100AModule


class TrustSweepRegressionTests(TestCase):
    def setUp(self):
        self.discretionary = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
        )
        self.unit = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )

    def test_beneficiary_accounts_apply_to_both_trust_kinds(self):
        # The service at core/beneficiary_account_service.py:139 gates on
        # entity_type; a unit trust needs the same capital accounts.
        self.assertTrue(_beneficiary_accounts_apply(self.discretionary))
        self.assertTrue(_beneficiary_accounts_apply(self.unit))

    def test_section_100a_stays_discretionary_only(self):
        # A fixed unit trust makes no discretionary distribution, so Section
        # 100A does not apply. This is a deliberate narrowing, not an omission.
        fy_d = FinancialYear.objects.create(
            entity=self.discretionary,
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        fy_u = FinancialYear.objects.create(
            entity=self.unit,
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        self.assertTrue(Section100AModule(fy_d).applies())
        self.assertFalse(Section100AModule(fy_u).applies())
```

Replace `_beneficiary_accounts_apply` and the `Section100AModule` constructor call with the real names at those line numbers — read them first, they are the point of the test.

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_trust_sweep_regression -v 2
```
Expected: FAIL on the unit-trust assertions — the sites still test equality with `"trust"`.

- [ ] **Step 3: Sweep the sites**

For each file and line listed above, replace the equality test with membership. The mechanical shape is:

```python
# before
if entity.entity_type == "trust":
# after
if entity.entity_type in TRUST_LIKE_TYPES:
```

and for the `elif entity_type == "trust":` variants that take a bare string:

```python
elif entity_type in TRUST_LIKE_TYPES:
```

Import the constant in each module: `from core.models import TRUST_LIKE_TYPES`.

For dict-building sites like `core/views.py:5011` and `core/document_context_builder.py:766`, the key stays `is_trust` and only its expression widens:

```python
        "is_trust": entity.entity_type in TRUST_LIKE_TYPES,
```

Work file by file and run the full suite after each file rather than at the end — a regression is far cheaper to locate that way.

- [ ] **Step 4: Run the test and the full suite**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_trust_sweep_regression -v 2
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core review integrations 2>&1 | grep -E "^(FAIL|ERROR|Ran|OK|FAILED)"
```
Expected: the new tests PASS; the full suite shows only `core.tests_jt_client_picker` failing.

- [ ] **Step 5: Commit**

```bash
git add core/ && git commit -m "refactor(trust): trust branch sites accept unit trusts via TRUST_LIKE_TYPES"
```

---

### Task 4: Sweep the template trust branch sites

**Files:**
- Modify: `templates/core/entity_officer_form.html:99,122`, `templates/core/entity_detail.html:16,449`, `templates/core/entity_officers.html:27,47,77,109,145`, `templates/core/financial_year_detail.html:179,1480,1596,1612`, `templates/core/entity_coa_form.html:116`, `templates/partials/entity_list_rows.html:18`
- Create: `core/tests_unit_trust_templates.py`

**Interfaces:**
- Consumes: `Entity.is_trust_like` (Task 1)
- Produces: no new API.

The sites at `entity_detail.html:92`, `governing_docs_tab.html:123`, and `financial_year_detail.html:846,1712` already spell out `trust_unit` explicitly and need no change — they become live for the first time once Task 1 lands.

- [ ] **Step 1: Write the failing test**

Create `core/tests_unit_trust_templates.py`:

```python
"""A unit trust reaches the same screens a discretionary trust does."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Entity


class UnitTrustTemplateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ut", email="ut@example.com", password="secret123",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()
        self.unit = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )

    def test_officers_page_renders_for_a_unit_trust(self):
        response = self.client.get(
            reverse("core:entity_officers", kwargs={"pk": self.unit.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unit Holder")
```

Confirm the URL name against `core/urls.py` before running.

- [ ] **Step 2: Run test to verify it fails**

```bash
ln -sfn /opt/statementhub/staticfiles staticfiles
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_trust_templates -v 2
```
Expected: FAIL — the page renders the non-trust branch, so "Unit Holder" is absent.

- [ ] **Step 3: Sweep the templates**

Replace each listed `{% if entity.entity_type == 'trust' %}` with:

```django
{% if entity.is_trust_like %}
```

For the badge expressions at `entity_detail.html:16` and `entity_list_rows.html:18`, add a unit-trust arm rather than widening, so the two types are visually distinguishable:

```django
{% elif entity.entity_type == 'trust' %}info{% elif entity.entity_type == 'trust_unit' %}info{% elif ... %}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_trust_templates -v 2
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/ core/tests_unit_trust_templates.py
git commit -m "feat(ui): trust templates recognise unit trusts"
```

---

### Task 5: The unit register on EntityOfficer

**Files:**
- Modify: `core/models.py` — `EntityOfficer` (~line 426), `Entity` (~line 138)
- Create: `core/tests_unit_register.py`
- Create: migration `XXXX_entityofficer_units_held.py`

**Interfaces:**
- Consumes: `Entity.is_unit_trust` (Task 1)
- Produces:
  - `EntityOfficer.units_held: int | None`
  - `EntityOfficer.unit_percentage -> Decimal` — 0 when the entity has no units on issue
  - `Entity.total_units -> int`

- [ ] **Step 1: Write the failing test**

Create `core/tests_unit_register.py`:

```python
"""Units are the register; the percentage is derived from them."""
from decimal import Decimal

from django.test import TestCase

from core.models import Entity, EntityOfficer


class UnitRegisterTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )

    def _holder(self, name, units):
        return EntityOfficer.objects.create(
            entity=self.entity, full_name=name,
            role="unit_holder", roles=["unit_holder"], units_held=units,
        )

    def test_total_units_sums_active_holders(self):
        self._holder("Double Water International Pty Ltd", 50)
        self._holder("Penman Property Nominees Pty Ltd", 50)
        self.assertEqual(self.entity.total_units, 100)

    def test_percentage_is_derived_from_units(self):
        a = self._holder("Double Water International Pty Ltd", 50)
        b = self._holder("Penman Property Nominees Pty Ltd", 50)
        self.assertEqual(a.unit_percentage, Decimal("50.0000"))
        self.assertEqual(b.unit_percentage, Decimal("50.0000"))

    def test_uneven_split_derives_exactly(self):
        a = self._holder("A", 1)
        self._holder("B", 2)
        self.assertEqual(a.unit_percentage, Decimal("33.3333"))

    def test_distribution_percentage_is_stored_from_units_on_save(self):
        # Stored, not a pure property: existing consumers read this field.
        a = self._holder("A", 75)
        self._holder("B", 25)
        a.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("75.00"))

    def test_percentage_is_zero_when_no_units_on_issue(self):
        a = EntityOfficer.objects.create(
            entity=self.entity, full_name="A",
            role="unit_holder", roles=["unit_holder"],
        )
        self.assertEqual(a.unit_percentage, Decimal("0"))

    def test_non_unit_holders_may_not_hold_units(self):
        officer = EntityOfficer(
            entity=self.entity, full_name="T", role="trustee", units_held=10,
        )
        officer.clean()
        self.assertIsNone(officer.units_held)

    def test_ceased_holders_are_excluded_from_total(self):
        from datetime import date
        self._holder("A", 50)
        ceased = self._holder("B", 50)
        ceased.date_ceased = date(2025, 1, 1)
        ceased.save()
        self.assertEqual(self.entity.total_units, 50)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_register -v 2
```
Expected: FAIL — `EntityOfficer() got unexpected keyword argument 'units_held'`.

- [ ] **Step 3: Add the field and the derivations**

On `EntityOfficer`, beside `shares_held`:

```python
    units_held = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=(
            "Units held in a unit trust. The register is authoritative: "
            "distribution_percentage is derived from this, not typed."
        ),
    )
```

Extend `EntityOfficer.clean()`, which already nulls `distribution_percentage` for ineligible roles:

```python
    def clean(self):
        super().clean()
        # Only unit holders and beneficiaries may have distribution_percentage
        if self.role not in self.DISTRIBUTION_ROLES:
            self.distribution_percentage = None
        # Units belong to the unit register alone.
        if self.role != self.OfficerRole.UNIT_HOLDER and "unit_holder" not in (self.roles or []):
            self.units_held = None
```

Add the derivation property:

```python
    @property
    def unit_percentage(self):
        """This holder's share of units on issue, as a percentage."""
        total = self.entity.total_units
        if not total or not self.units_held:
            return Decimal("0")
        return (Decimal(self.units_held) / Decimal(total) * 100).quantize(
            Decimal("0.0001")
        )
```

In `EntityOfficer.save()`, recompute the stored percentage before the existing history tracking runs, so the audit trail records the derived value:

```python
        if self.units_held is not None:
            self.distribution_percentage = self.unit_percentage.quantize(Decimal("0.01"))
```

On `Entity`:

```python
    @property
    def total_units(self):
        """Units on issue across active unit holders."""
        return self.officers.filter(
            date_ceased__isnull=True, units_held__isnull=False,
        ).aggregate(t=models.Sum("units_held"))["t"] or 0
```

- [ ] **Step 4: Generate the migration**

```bash
python3 manage.py makemigrations core --name entityofficer_units_held
```

- [ ] **Step 5: Run test to verify it passes**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_register -v 2
```
Expected: PASS, 7 tests.

Note the ordering trap: `test_distribution_percentage_is_stored_from_units_on_save` depends on B existing when A is saved. A is saved first, when total units is 75, so A's stored percentage is 100.00 until B arrives. Either save A again after B, or accept that the stored field is refreshed by Task 6's form. Resolve this explicitly rather than leaving the test flaky — the simplest fix is a `recalculate_unit_percentages()` classmethod on `EntityOfficer` that the form calls after any register change, and have the test call it.

- [ ] **Step 6: Commit**

```bash
git add core/models.py core/migrations/ core/tests_unit_register.py
git commit -m "feat(entity): unit register on EntityOfficer with derived percentage"
```

---

### Task 6: Officer form — units in, percentage read-only

**Files:**
- Modify: `core/forms.py` (the `EntityOfficer` form) and `templates/core/entity_officer_form.html`
- Modify: `templates/core/entity_officers.html` — show a Units column for unit trusts
- Create: tests appended to `core/tests_unit_register.py`

**Interfaces:**
- Consumes: `EntityOfficer.units_held`, `unit_percentage`, `Entity.is_unit_trust` (Tasks 1, 5)
- Produces: `EntityOfficer.recalculate_unit_percentages(entity)` — classmethod, rewrites every holder's stored `distribution_percentage` from the register. Called after any officer save on a unit trust.

- [ ] **Step 1: Write the failing test**

```python
class UnitRegisterFormTests(TestCase):
    def test_saving_a_holder_recalculates_every_percentage(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        a = EntityOfficer.objects.create(
            entity=entity, full_name="A", role="unit_holder",
            roles=["unit_holder"], units_held=50,
        )
        EntityOfficer.objects.create(
            entity=entity, full_name="B", role="unit_holder",
            roles=["unit_holder"], units_held=50,
        )
        EntityOfficer.recalculate_unit_percentages(entity)

        a.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("50.00"))
```

- [ ] **Step 2: Run it and watch it fail**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_register -v 2
```
Expected: FAIL — `recalculate_unit_percentages` does not exist.

- [ ] **Step 3: Implement**

```python
    @classmethod
    def recalculate_unit_percentages(cls, entity):
        """Rewrite stored percentages from the unit register.

        Called after any register change. Percentages are stored rather than
        computed on read so that every existing consumer of
        distribution_percentage keeps working without knowing units exist.
        """
        holders = list(cls.objects.filter(
            entity=entity, date_ceased__isnull=True, units_held__isnull=False,
        ))
        for holder in holders:
            holder.distribution_percentage = holder.unit_percentage.quantize(
                Decimal("0.01")
            )
            holder.save(update_fields=["distribution_percentage"])
```

In the officer form, expose `units_held` only when `entity.is_unit_trust`, and render `distribution_percentage` as `readonly` with helper text "Derived from units held". Call `recalculate_unit_percentages` in the view after a successful save.

- [ ] **Step 4: Run tests**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_register -v 2
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/forms.py core/views.py templates/core/ core/tests_unit_register.py
git commit -m "feat(ui): unit holdings editable, distribution percentage read-only"
```

---

### Task 7: Unit-proportional allocation with exact rounding

**Files:**
- Create: `core/unit_allocation.py`
- Create: `core/tests_unit_allocation.py`

**Interfaces:**
- Consumes: nothing (pure functions, no ORM — this is why it is its own module)
- Produces: `allocate_by_units(total: Decimal, holdings: list[tuple[Any, int]]) -> dict[Any, Decimal]`

- [ ] **Step 1: Write the failing test**

Create `core/tests_unit_allocation.py`:

```python
"""Unit allocation must tie to the cent, including the awkward splits."""
from decimal import Decimal

from django.test import SimpleTestCase

from core.unit_allocation import allocate_by_units


class AllocateByUnitsTests(SimpleTestCase):
    def test_even_split(self):
        result = allocate_by_units(Decimal("1000.00"), [("a", 50), ("b", 50)])
        self.assertEqual(result, {"a": Decimal("500.00"), "b": Decimal("500.00")})

    def test_thirds_tie_to_the_cent(self):
        result = allocate_by_units(
            Decimal("100000.00"), [("a", 1), ("b", 1), ("c", 1)]
        )
        self.assertEqual(sum(result.values()), Decimal("100000.00"))
        self.assertEqual(sorted(result.values()), [
            Decimal("33333.33"), Decimal("33333.33"), Decimal("33333.34"),
        ])

    def test_uneven_holdings(self):
        result = allocate_by_units(Decimal("900.00"), [("a", 2), ("b", 1)])
        self.assertEqual(result, {"a": Decimal("600.00"), "b": Decimal("300.00")})

    def test_a_loss_allocates_too(self):
        result = allocate_by_units(Decimal("-1000.00"), [("a", 50), ("b", 50)])
        self.assertEqual(sum(result.values()), Decimal("-1000.00"))

    def test_negative_thirds_tie_to_the_cent(self):
        result = allocate_by_units(Decimal("-100.00"), [("a", 1), ("b", 1), ("c", 1)])
        self.assertEqual(sum(result.values()), Decimal("-100.00"))

    def test_zero_total_allocates_zero(self):
        result = allocate_by_units(Decimal("0.00"), [("a", 1), ("b", 1)])
        self.assertEqual(sum(result.values()), Decimal("0.00"))

    def test_no_units_on_issue_is_refused(self):
        with self.assertRaises(ValueError):
            allocate_by_units(Decimal("100.00"), [("a", 0)])

    def test_empty_register_is_refused(self):
        with self.assertRaises(ValueError):
            allocate_by_units(Decimal("100.00"), [])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_allocation -v 2
```
Expected: FAIL — `No module named 'core.unit_allocation'`.

- [ ] **Step 3: Implement**

Create `core/unit_allocation.py`:

```python
"""Splitting an amount across a unit register, exactly.

A unit trust's income follows the register arithmetically, which makes rounding
the only interesting part: three holders sharing $100,000 in thirds must still
total $100,000, not $99,999.99. Allocation therefore runs on integer cents and
distributes the remainder by largest fractional part, so the parts always sum
to the whole.

Kept free of the ORM so the arithmetic can be tested on its own.
"""
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal("0.01")


def allocate_by_units(total, holdings):
    """Split ``total`` across ``holdings`` in proportion to units held.

    ``holdings`` is a list of ``(key, units)`` pairs. Returns ``{key: Decimal}``
    whose values sum exactly to ``total`` rounded to the cent.

    Raises ValueError when no units are on issue — distributing a fixed trust's
    income with an empty register is not a rounding question, it is a
    misconfiguration, and it must not silently allocate nothing.
    """
    if not holdings:
        raise ValueError("cannot allocate across an empty unit register")

    total_units = sum(units for _, units in holdings)
    if total_units <= 0:
        raise ValueError("cannot allocate with no units on issue")

    cents = int(
        (Decimal(total) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    sign = -1 if cents < 0 else 1
    cents = abs(cents)

    whole_parts = {}
    remainders = []
    allocated = 0
    for key, units in holdings:
        exact = cents * units
        whole, remainder = divmod(exact, total_units)
        whole_parts[key] = whole
        remainders.append((remainder, key))
        allocated += whole

    # Largest remainder takes the leftover cents, one each.
    leftover = cents - allocated
    for _, key in sorted(remainders, key=lambda pair: pair[0], reverse=True)[:leftover]:
        whole_parts[key] += 1

    return {
        key: (Decimal(sign * value) / 100).quantize(CENTS)
        for key, value in whole_parts.items()
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_allocation -v 2
```
Expected: PASS, 8 tests.

Note: sorting remainders by value alone is unstable when two remainders tie, which makes the *distribution* of leftover cents arbitrary but the *total* always correct. If a deterministic tie-break matters for reproducibility, sort by `(-remainder, str(key))`.

- [ ] **Step 5: Commit**

```bash
git add core/unit_allocation.py core/tests_unit_allocation.py
git commit -m "feat(trust): exact unit-proportional allocation"
```

---

### Task 8: Allocate every stream by units

**Files:**
- Modify: `core/views_trust.py` — the distribution calculation path
- Create: `core/tests_unit_trust_streams.py`

**Interfaces:**
- Consumes: `allocate_by_units` (Task 7), `Entity.total_units`, `EntityOfficer.units_held` (Task 5)
- Produces: `core.views_trust.allocate_unit_trust_distribution(distribution) -> list[BeneficiaryAllocation]` — creates or refreshes one allocation per unit holder.

- [ ] **Step 1: Write the failing test**

Create `core/tests_unit_trust_streams.py`:

```python
"""Every stream splits by units; franking credits follow franked dividends."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    BeneficiaryAllocation, Entity, EntityOfficer, FinancialYear, TrustDistribution,
)
from core.views_trust import allocate_unit_trust_distribution


class UnitTrustStreamTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        for name, units in [("A", 75), ("B", 25)]:
            EntityOfficer.objects.create(
                entity=self.entity, full_name=name,
                role="unit_holder", roles=["unit_holder"], units_held=units,
            )
        self.distribution = TrustDistribution.objects.create(
            financial_year=self.fy,
            distributable_income=Decimal("100000.00"),
            capital_gains=Decimal("20000.00"),
        )

    def test_each_stream_splits_in_unit_proportion(self):
        allocate_unit_trust_distribution(self.distribution)

        a = BeneficiaryAllocation.objects.get(
            distribution=self.distribution, beneficiary__full_name="A",
        )
        self.assertEqual(a.percentage, Decimal("75.0000"))
        self.assertEqual(a.allocated_capital_gains, Decimal("15000.00"))

    def test_allocations_tie_to_the_stream_total(self):
        allocate_unit_trust_distribution(self.distribution)
        total = sum(
            row.allocated_capital_gains
            for row in BeneficiaryAllocation.objects.filter(distribution=self.distribution)
        )
        self.assertEqual(total, Decimal("20000.00"))

    def test_rerunning_refreshes_rather_than_duplicates(self):
        allocate_unit_trust_distribution(self.distribution)
        allocate_unit_trust_distribution(self.distribution)
        self.assertEqual(
            BeneficiaryAllocation.objects.filter(distribution=self.distribution).count(),
            2,
        )

    def test_empty_register_is_refused(self):
        EntityOfficer.objects.filter(entity=self.entity).update(units_held=None)
        with self.assertRaises(ValueError):
            allocate_unit_trust_distribution(self.distribution)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_trust_streams -v 2
```
Expected: FAIL — `cannot import name 'allocate_unit_trust_distribution'`.

- [ ] **Step 3: Implement**

In `core/views_trust.py`:

```python
def allocate_unit_trust_distribution(distribution):
    """Allocate a unit trust's distribution strictly by the unit register.

    Every stream splits in the same proportion — there is no streaming choice
    in a fixed trust — and franking credits follow the franked dividends they
    attach to. Rows are refreshed in place so re-running is idempotent.
    """
    entity = distribution.financial_year.entity
    holders = list(
        EntityOfficer.objects.filter(
            entity=entity, date_ceased__isnull=True, units_held__isnull=False,
        ).order_by("display_order", "full_name")
    )
    holdings = [(h.pk, h.units_held) for h in holders]

    streams = {
        "allocated_capital_gains": distribution.capital_gains,
        "allocated_franked_dividends": distribution.franked_dividends,
        "allocated_foreign_income": distribution.foreign_income,
        "allocated_other_income": distribution.other_income,
    }
    split = {
        field: allocate_by_units(amount, holdings)
        for field, amount in streams.items()
    }
    entitlement = allocate_by_units(distribution.distributable_income, holdings)

    rows = []
    for holder in holders:
        row, _ = BeneficiaryAllocation.objects.update_or_create(
            distribution=distribution,
            beneficiary=holder,
            defaults={
                "percentage": holder.unit_percentage,
                "fixed_amount": None,
                "total_allocated": entitlement[holder.pk],
                **{field: values[holder.pk] for field, values in split.items()},
            },
        )
        rows.append(row)
    return rows
```

Read `TrustDistribution` and `BeneficiaryAllocation` field names at `core/models.py:3033` and `:3112` before writing this — `foreign_income`, `other_income` and `total_allocated` must match the real field names, and franking credits may be a separate field that needs the same treatment.

- [ ] **Step 4: Run test to verify it passes**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_trust_streams -v 2
```
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add core/views_trust.py core/tests_unit_trust_streams.py
git commit -m "feat(trust): allocate every unit trust stream by the register"
```

---

### Task 9: Tax Planning tab in locked mode

**Files:**
- Modify: `core/views_tax_planning.py` — `tax_planning_tab:98`, `_sync_beneficiary_rows:49`, `tax_planning_save:231`
- Modify: `templates/` — the tax planning template (hide scenario controls, render distribution read-only)
- Create: `core/tests_unit_trust_tax_planning.py`

**Interfaces:**
- Consumes: `TRUST_LIKE_TYPES`, `Entity.is_unit_trust` (Task 1), `EntityOfficer.unit_percentage` (Task 5), `allocate_by_units` (Task 7)
- Produces: no new public API.

Today `tax_planning_tab` rejects anything but `entity_type == "trust"`, and `_sync_beneficiary_rows` matches only `role="beneficiary"`. That combination is why Minli FY2026 has a worksheet with **zero rows** — verified against production on 2026-08-27.

- [ ] **Step 1: Write the failing test**

Create `core/tests_unit_trust_tax_planning.py`:

```python
"""The tab opens for a unit trust, and its distribution follows the register."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Entity, EntityOfficer, FinancialYear, TaxPlanningWorksheet,
)


class UnitTrustTaxPlanningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tp", email="tp@example.com", password="secret123",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        for name, units in [("A Pty Ltd", 50), ("B Pty Ltd", 50)]:
            EntityOfficer.objects.create(
                entity=self.entity, full_name=name,
                role="unit_holder", roles=["unit_holder"], units_held=units,
            )

    def test_tab_opens_for_a_unit_trust(self):
        response = self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)

    def test_a_row_is_created_for_every_unit_holder(self):
        self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(worksheet.beneficiary_rows.count(), 2)

    def test_proposed_distribution_is_derived_from_units(self):
        worksheet = TaxPlanningWorksheet.objects.create(
            financial_year=self.fy, distributable_income=Decimal("100000.00"),
        )
        self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        amounts = sorted(
            worksheet.beneficiary_rows.values_list("proposed_distribution", flat=True)
        )
        self.assertEqual(amounts, [Decimal("50000.00"), Decimal("50000.00")])

    def test_a_posted_distribution_override_is_rejected(self):
        # There is no planning: the register decides.
        worksheet = TaxPlanningWorksheet.objects.create(
            financial_year=self.fy, distributable_income=Decimal("100000.00"),
        )
        self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}), secure=True,
        )
        row = worksheet.beneficiary_rows.first()
        self.client.post(
            reverse("core:tax_planning_save", kwargs={"pk": self.fy.pk}),
            data={f"proposed_distribution_{row.pk}": "90000.00"},
            secure=True,
        )
        row.refresh_from_db()
        self.assertEqual(row.proposed_distribution, Decimal("50000.00"))


class DiscretionaryTrustTaxPlanningUnchangedTests(TestCase):
    """The tab must behave exactly as before for a discretionary trust."""

    def test_manual_distribution_is_still_accepted(self):
        # Build a discretionary trust with a beneficiary, post an allocation,
        # and assert it saves — the behaviour unit trusts are giving up.
        ...
```

Fill in the final test properly against the existing behaviour — it is the regression guard and must not be left as an ellipsis.

- [ ] **Step 2: Run test to verify it fails**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_trust_tax_planning -v 2
```
Expected: FAIL — the tab redirects with "Tax Planning is only available for Trust entities."

- [ ] **Step 3: Implement**

Widen the gate at `tax_planning_tab:98`:

```python
    if entity.entity_type not in TRUST_LIKE_TYPES:
        messages.error(request, "Tax Planning is only available for Trust entities.")
        return redirect("core:financial_year_detail", pk=pk)
```

Widen row creation in `_sync_beneficiary_rows:56` so unit holders qualify:

```python
    beneficiaries = EntityOfficer.objects.filter(entity=entity).filter(
        models_Q(role="beneficiary") | models_Q(roles__contains="beneficiary")
        | models_Q(role="unit_holder") | models_Q(roles__contains="unit_holder")
    ).filter(date_ceased__isnull=True)
```

After syncing, for a unit trust only, derive the distributions:

```python
def _apply_unit_distributions(worksheet):
    """A unit trust's proposed distribution is the register, not a proposal."""
    entity = worksheet.financial_year.entity
    if not entity.is_unit_trust:
        return
    rows = list(worksheet.beneficiary_rows.select_related("beneficiary"))
    holdings = [
        (row.pk, row.beneficiary.units_held or 0) for row in rows
    ]
    if not any(units for _, units in holdings):
        return
    split = allocate_by_units(worksheet.distributable_income, holdings)
    for row in rows:
        row.proposed_distribution = split[row.pk]
        row.save(update_fields=["proposed_distribution"])
```

Call it from `tax_planning_tab` immediately after `_sync_beneficiary_rows(worksheet)`, and again after the Section 1 recalculation, since `distributable_income` changes there.

In `tax_planning_save`, ignore any posted `proposed_distribution` when the entity is a unit trust — accept `outside_income` as normal.

In the template, when `entity.is_unit_trust`: render the distribution cell as text rather than an input, add a Units column, and hide the scenario save/apply/delete controls.

- [ ] **Step 4: Run test and full suite**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core.tests_unit_trust_tax_planning -v 2
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core review integrations 2>&1 | grep -E "^(FAIL|ERROR|Ran|OK|FAILED)"
```
Expected: new tests PASS; suite shows only `core.tests_jt_client_picker`.

- [ ] **Step 5: Commit**

```bash
git add core/views_tax_planning.py templates/ core/tests_unit_trust_tax_planning.py
git commit -m "feat(tax): tax planning tab reads the unit register"
```

---

### Task 10: "Unit Holder" terminology

**Files:**
- Create: `core/entity_terminology.py`, `core/tests_entity_terminology.py`
- Modify: templates and document builders that render the beneficiary noun

**Interfaces:**
- Consumes: `Entity.is_unit_trust` (Task 1)
- Produces:
  - `beneficiary_noun(entity, plural=False) -> str`
  - a template filter of the same name registered in `core/templatetags/`

- [ ] **Step 1: Write the failing test**

```python
"""A unit trust has unit holders; a discretionary trust has beneficiaries."""
from django.test import TestCase

from core.entity_terminology import beneficiary_noun
from core.models import Entity


class BeneficiaryNounTests(TestCase):
    def test_unit_trust(self):
        e = Entity(entity_name="Minli", entity_type="trust_unit")
        self.assertEqual(beneficiary_noun(e), "Unit Holder")
        self.assertEqual(beneficiary_noun(e, plural=True), "Unit Holders")

    def test_discretionary_trust(self):
        e = Entity(entity_name="Vincent", entity_type="trust")
        self.assertEqual(beneficiary_noun(e), "Beneficiary")
        self.assertEqual(beneficiary_noun(e, plural=True), "Beneficiaries")
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `No module named 'core.entity_terminology'`.

- [ ] **Step 3: Implement**

```python
"""What this entity calls the people who receive its income."""


def beneficiary_noun(entity, plural=False):
    """"Unit Holder" for a unit trust, "Beneficiary" otherwise.

    A unit trust's income recipients are unit holders — the word matters on
    financial statements and distribution minutes, not only on screen.
    """
    if getattr(entity, "is_unit_trust", False):
        return "Unit Holders" if plural else "Unit Holder"
    return "Beneficiaries" if plural else "Beneficiary"
```

Register a template filter wrapping it, then apply it at the officer tab, the tax planning tab, FS note headings and distribution minutes.

- [ ] **Step 4: Run tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/entity_terminology.py core/templatetags/ core/tests_entity_terminology.py templates/
git commit -m "feat(ui): unit trusts say Unit Holder, not Beneficiary"
```

---

### Task 11: Chart terminology for unit trusts

**Files:**
- Modify: `core/models.py` — `EntityChartOfAccount.seed_from_template`
- Modify: `core/beneficiary_account_service.py` — per-holder sub-account naming
- Create: `core/management/commands/rename_unit_trust_chart_terms.py`
- Create: `core/tests_unit_trust_chart_terms.py`

**Interfaces:**
- Consumes: `template_entity_type` (Task 2), `beneficiary_noun` (Task 10)
- Produces: management command `rename_unit_trust_chart_terms` with `--dry-run`, `--entity`, and no finalised-year writes.

The `trust` template's own names contain the word (4000 is `Opening balance - Beneficiary`, 4100 is `Beneficiary current account`), so seeding a unit trust substitutes the terminology as an overlay on the shared template.

**This interacts with PR #83/#84.** Chart names now flow onto trial balance rows, so renaming chart entries propagates into the TB on the next import or rollover. Finalised years must be excluded, exactly as `backfill_tb_account_names` does — Minli FY2024 and FY2025 carry "Beneficiary" on statements already issued.

- [ ] **Step 1: Write the failing test**

```python
class UnitTrustChartTermsTests(TestCase):
    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="4000",
            account_name="Opening balance - Beneficiary", section="equity",
        )

    def test_seeding_a_unit_trust_renames_the_term(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        EntityChartOfAccount.seed_from_template(entity)
        account = EntityChartOfAccount.objects.get(entity=entity, account_code="4000")
        self.assertEqual(account.account_name, "Opening balance - Unit Holder")

    def test_seeding_a_discretionary_trust_does_not(self):
        entity = Entity.objects.create(entity_name="Vincent", entity_type="trust")
        EntityChartOfAccount.seed_from_template(entity)
        account = EntityChartOfAccount.objects.get(entity=entity, account_code="4000")
        self.assertEqual(account.account_name, "Opening balance - Beneficiary")
```

Add a command test asserting `--dry-run` writes nothing and that finalised years are untouched.

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Implement**

In `seed_from_template`, after resolving the template rows, substitute for unit trusts:

```python
            name = tpl.account_name
            if entity.is_unit_trust:
                name = name.replace("Beneficiary", "Unit Holder").replace(
                    "beneficiary", "unit holder"
                )
```

Write the management command on the shape of `core/management/commands/backfill_tb_account_names.py`: default dry-run reporting, `--entity` filter, and a hard skip of finalised years.

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/beneficiary_account_service.py core/management/commands/rename_unit_trust_chart_terms.py core/tests_unit_trust_chart_terms.py
git commit -m "feat(chart): unit trust capital accounts read Unit Holder"
```

---

### Task 12: Migrate Minli

**Files:**
- Create: `core/migrations/XXXX_minli_to_trust_unit.py`
- Create: `core/tests_unit_trust_migration.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no API.

**Blocking question — confirm before writing this migration.** Minli's holders currently sit on a typed `distribution_percentage` of 50.00 each with no unit count recorded. The migration should seed the real numbers from the trust deed. If they are unavailable, seed 50 and 50 so the derived percentage reproduces today's split exactly, and note in the commit message that the counts are a reconstruction, not the deed.

- [ ] **Step 1: Write the failing test**

```python
class MinliMigrationTests(TestCase):
    """Only entities with unit holders become unit trusts."""

    def test_a_trust_with_unit_holders_is_reclassified(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust")
        EntityOfficer.objects.create(
            entity=entity, full_name="A", role="unit_holder", roles=["unit_holder"],
            distribution_percentage=Decimal("50.00"),
        )
        reclassify_unit_trusts(Entity.objects.all())
        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust_unit")

    def test_a_trust_with_beneficiaries_is_left_alone(self):
        entity = Entity.objects.create(entity_name="Vincent", entity_type="trust")
        EntityOfficer.objects.create(
            entity=entity, full_name="B", role="beneficiary", roles=["beneficiary"],
        )
        reclassify_unit_trusts(Entity.objects.all())
        entity.refresh_from_db()
        self.assertEqual(entity.entity_type, "trust")
```

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Write the data migration**

A `RunPython` migration that reclassifies trusts holding `unit_holder` officers, seeds `units_held` from the confirmed deed figures, and calls the percentage recalculation. Give it a working `reverse_code` that sets the type back to `trust` and nulls `units_held`, so the migration is reversible.

- [ ] **Step 4: Run the tests and the full suite**

```bash
DATABASE_URL="sqlite:////tmp/utplan.sqlite3" python3 manage.py test core review integrations 2>&1 | grep -E "^(FAIL|ERROR|Ran|OK|FAILED)"
```

- [ ] **Step 5: Verify against production, read-only, before deploying**

```bash
python3 manage.py migrate --plan | tail -20
```
Then after deployment, confirm Minli reads `trust_unit`, its two holders show 50/50 derived, and the tax planning tab renders two rows where it previously rendered none.

- [ ] **Step 6: Commit**

```bash
git add core/migrations/ core/tests_unit_trust_migration.py
git commit -m "feat(entity): reclassify Minli as a unit trust and seed its register"
```

---

## Self-Review Notes

**Spec coverage.** Every spec section maps to a task: entity type and compatibility → 1–4; unit register → 5–6; allocation → 7–8; tax planning → 9; terminology → 10–11; migration → 12; testing is embedded in each task rather than deferred.

**Known gaps carried deliberately:**
- Task 3's test file names two symbols (`_beneficiary_accounts_apply`, the `Section100AModule` constructor) that must be read from the real source before the test compiles. Flagged in the task rather than guessed, because guessing them would produce a test that fails for the wrong reason.
- Task 9's discretionary-trust regression test is left as a described stub. It must be written against the existing save path — it is the single most important test in this plan, and writing it blind would be worse than writing it with the code open.
- Task 12 is blocked on the deed's actual unit numbers.

**Ordering.** Tasks 1–4 must land before anything else; 5 before 6–9; 7 before 8 and 9. Tasks 10 and 11 can proceed in parallel with 8–9. Task 12 is last.
