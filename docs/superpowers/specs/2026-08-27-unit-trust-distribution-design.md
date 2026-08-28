# Unit trusts: entity type, unit register, and distribution by units

**Date:** 2026-08-27
**Status:** Approved design, ready for implementation planning
**Driver:** Minli Enterprise Unit Trust — the platform's only unit trust today

## The problem

A unit trust distributes income by unit holding. There is no discretion and
nothing to plan: the register fixes each holder's share, and the distribution
follows arithmetically.

StatementHub does not model this. Unit trusts are stored as `entity_type =
"trust"` and are only recognised as unit trusts where something happens to check
for `unit_holder` officers (`core/views_trust.py:575 _entity_has_unitholders`).
Units themselves are never persisted anywhere: `EntityOfficer` carries
`shares_held` for company shareholders but has no units field, and the unit-trust
deed generator (`core/legal_doc_contexts.py:794`) models unit counts, ranges and
certificates only as throwaway parameters at document-generation time.

The consequence is visible on Minli. Its two unit holders — Double Water
International Pty Ltd and Penman Property Nominees Pty Ltd — each sit on a
hand-typed `distribution_percentage` of 50.00. Nothing records that this is
50/50 *because of the units held*, nothing prevents the figures drifting from
the register, and the Tax Planning tab still presents
`TaxPlanningBeneficiaryRow.proposed_distribution` as "Manual entry — must sum to
distributable income", inviting an allocation the deed does not permit.

The account structure a unit trust needs already exists. Minli's chart carries
4000.01/.02 (Opening balance), 4110.01/.02 (Funds loaned to trust) and
4053.01/.02 (Physical distribution), one pair per holder — the per-beneficiary
capital accounts from PR #80, already doing unit-trust duty under
discretionary-trust names.

## Decisions taken

Settled with Elio before this document was written:

| Question | Decision |
|---|---|
| Unit classes | Single class only; every unit ranks equally |
| Selectability | A true new `EntityType` value, not a sub-type flag |
| Mid-year transfers | Holdings are static within a financial year |
| Tax Planning tab | Kept, with the distribution locked to units |
| Streaming | Every stream splits by units |
| Terminology reach | UI, statements and chart — current year forward |
| Coexistence with `trust` | Inherit trust behaviour by default, diverge explicitly |

## Scope

**In scope:** the `trust_unit` entity type and its compatibility layer; a unit
register on `EntityOfficer`; unit-proportional allocation across all income
streams; the Tax Planning tab in locked mode; "Unit Holder" terminology through
UI, statements and chart; migration of Minli.

**Out of scope:** multiple unit classes; mid-year unit transfers and weighted
allocation; unit issue/redemption workflows; unit certificates and the register
as a legal document (the deed generator already covers document production);
changing any behaviour of discretionary trusts.

## Design

### 1. Entity type and compatibility

Add `UNIT_TRUST = "trust_unit", "Unit Trust"` to `Entity.EntityType`.

**Why `trust_unit` and not `unit_trust`.** The value `trust_unit` is already an
established convention across roughly twenty sites — `eva_engine.py`,
`eva_div7a.py`, `eva_trust_planning.py`, `views_office_admin.py`, two
`TRUST_TYPES` sets in `views.py`, and several templates. `views_trust.py:577`
already tests `entity.entity_type == 'trust_unit'` inside
`_entity_has_unitholders`. The value was simply never added to `EntityType`, so
all of that code is dormant. Adopting the existing name activates it and keeps
one convention; a new `unit_trust` value would leave it dead and create a
second. The display label is "Unit Trust" regardless.

`trust_discretionary` and `trust_hybrid` are dormant in the same places. They
stay out of scope: neither is needed here, and each new type repeats the
regression risk described below.

The hazard is that 77 sites branch on `entity_type == "trust"` — 58 in Python,
19 in templates — covering FS generation, distribution, tax, and chart seeding.
A sibling type value makes unit trusts fall out of all of them at once.

So unit trusts inherit trust behaviour by default:

- `TRUST_LIKE_TYPES = ("trust", "trust_unit")` in `core/models.py`.
- `Entity.is_trust_like` property, for the 19 template sites.
- Every `entity_type == "trust"` test becomes one of these two forms.

Three lookups are keyed by entity type and must resolve `trust_unit` to the
existing `trust` data rather than gaining copies of it:

- `ChartOfAccount` — 468 template rows for `trust`.
- `CapitalAccountTemplate.TRUST_ENTITY_TYPES` — currently the single-element
  list `[("trust", "Trust")]`.
- `GlobalAccountMappingHint` — cross-entity mapping hints.

Resolution rather than cloning is deliberate: cloned template rows drift the
moment the xlsx source is edited, and the chart template's xlsx source is
already known to diverge from the database.

A data migration moves Minli to `trust_unit`, identified by the presence of
`unit_holder` officers. No other entity qualifies.

**Failure direction.** A site missed in the sweep leaves a unit trust behaving
like a discretionary trust — wrong, but safe and visible. The reverse (a unit
trust with no chart, no FS) would not be.

### 2. The unit register

Add to `EntityOfficer`:

- `units_held` — positive integer, null for non-holders.
- Validation in `clean()`: only rows whose role or roles include `unit_holder`
  may carry units, mirroring the existing rule that restricts
  `distribution_percentage` to unit holders and beneficiaries.

Derived values:

- `Entity.total_units` — sum of `units_held` across active unit holders.
- `EntityOfficer.unit_percentage` — `units_held / total_units * 100`, exact to
  the model's 4 decimal places.

For unit holders, `distribution_percentage` stops being an *input* but remains a
*stored field*: it is recomputed from the register on save and rendered
read-only in the UI. Keeping it populated is what makes inherit-by-default work
— every existing consumer that reads `distribution_percentage` continues to
function without knowing units exist. `OfficerDistributionHistory` continues to
record changes, so a change in units still leaves an audit trail.

Distribution is refused while `total_units` is zero, so a partly-configured
register cannot silently distribute nothing to everyone.

**Minli's migration.** Its holders currently sit on a typed 50.00 each. The
migration seeds `units_held` so the derived percentage reproduces the existing
split exactly rather than inventing a unit count that contradicts the deed.

### 3. Allocation

Each income stream is allocated in the unit proportion:

- capital gains
- franked dividends, with franking credits following them
- foreign income
- other income

`BeneficiaryAllocation` rows are created and refreshed from the register, with
`percentage` read-only and `fixed_amount` unused for unit trusts.

**Rounding.** Proportional splits do not divide evenly — three holders sharing
$100,000 in thirds must still total exactly $100,000. Allocation uses the
largest-remainder method so every stream ties to its total to the cent. This
applies per stream, not only to the aggregate, or the streams will not reconcile
against the distribution statement.

### 4. Tax Planning tab

For a unit trust:

- Rows are created automatically from the unit holders.
- `proposed_distribution` is derived from units and read-only.
- `outside_income` remains a manual entry — it describes the holder's own
  position, not the trust's allocation.
- The tax computation is untouched: gross tax, Medicare levy, LITO, franking
  credit offset, net tax payable, effective rate.
- Scenario save, apply and delete are hidden. There is nothing to model.
- Changing a holder's units recalculates the tab.

For a discretionary trust the tab is unchanged in every respect.

### 5. Terminology

A label resolver keyed on entity type returns "Unit Holder"/"Unit Holders" for
unit trusts and "Beneficiary"/"Beneficiaries" for discretionary trusts. Applied
to the officer tab, the Tax Planning tab, FS notes, and distribution minutes.

Chart account names follow the same rule, so a unit trust's capital accounts
read `Opening balance - Unit Holder — Double Water International Pty Ltd`.

This is an overlay applied at seed time, not a second set of template rows. The
`trust` chart template supplies names that already contain the word
"Beneficiary" (code 4000 is `Opening balance - Beneficiary`, 4100 is
`Beneficiary current account`). Seeding a unit trust resolves to those same
template rows and then substitutes the terminology, so the single template stays
the source of truth for codes, sections and classifications.

**Interaction with PR #83/#84.** Chart names now flow onto trial balance rows at
import and rollover, and the `backfill_tb_account_names` command rewrites
existing rows from the chart. Renaming a unit trust's chart entries therefore
propagates into the trial balance for draft years, which is the desired
behaviour. Finalised years are protected by the same guard the backfill uses:
Minli FY2024 and FY2025 keep the "Beneficiary" wording that appears on the
statements already issued to the client.

Existing unit trusts are renamed by a management command scoped to draft years,
following the pattern of `backfill_tb_account_names`.

## Testing

The feature is small; the regression surface is not. A defect in the 77-site
sweep reaches four discretionary trusts and one unit trust, so the trust
regression tests carry more weight than the unit-trust feature tests.

**Regression — a discretionary trust is unchanged:**

- allocates manually, and streams by choice
- says "Beneficiary" throughout
- seeds its chart from the `trust` template
- generates financial statements as before

**Feature — unit trusts:**

- `unit_percentage` derives correctly, including a three-way split
- every stream allocates in unit proportion, franking credits following franked
  dividends
- each stream's allocations tie exactly to the stream total (the largest-
  remainder case)
- `proposed_distribution` and `distribution_percentage` reject writes
- distribution is refused when `total_units` is zero
- a unit trust seeds its chart and capital accounts from the `trust` template
- terminology resolves to "Unit Holder" in UI, statements and chart

**Migration:**

- Minli becomes `trust_unit`, and its derived percentages still read 50/50
- no other entity is reclassified

## Risks

| Risk | Mitigation |
|---|---|
| A missed site in the 77-site sweep | Inherit-by-default means the failure is a unit trust behaving like a trust; discretionary regression tests catch the dangerous direction |
| Rounding leaves cents unallocated | Largest-remainder per stream, asserted by test |
| Chart rename reaching finalised years | Draft-year guard, same as `backfill_tb_account_names` |
| Only one unit trust exists, so unit-trust bugs surface slowly | Feature tests stand in for production exposure |

## Open question for implementation

Minli's unit counts are being back-solved from the existing 50/50 percentage.
If the deed states actual unit numbers, they should be used instead — worth
confirming against the trust deed before the migration runs.
