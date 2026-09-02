# Section 100A: what it would take to make the module work

**Status:** scoping only. No code has been written.
**Date:** 2026-09-02
**Context:** `core/risk_modules/section100a.py` (500 lines, 5 rules)

---

## Summary

The Section 100A risk module runs, produces a finding, and detects nothing. Four
of its five rules cannot fire on any trust on the platform, and the fifth fires
on every trust regardless of the facts. Two of the four are not merely inert —
they are wrong, and would produce nonsense if their data ever arrived.

The earlier working theory was that the module is starved because nothing
populates `TrustDistribution`. That is true, and it is not the binding
constraint. Feeding it the posted distribution journal — which is available and
joins cleanly — would still leave three of the four detection rules unable to
fire, because each needs a *second* input that does not exist either.

This document sets out what each rule needs, what the platform actually holds,
and what a working module would cost. It makes a recommendation but decides
nothing.

---

## Current state, rule by rule

`should_run()` restricts the module to `entity_type == "trust"` — discretionary
trusts only, deliberately (a unit trust distributes by register and makes no
discretionary distribution). Six financial years across four trusts qualify.

| Rule | Severity | Needs | Live state | Can it fire? |
|---|---|---|---|---|
| S100A-01 Low-tax beneficiary | ADVISORY | allocations, `BeneficiaryProfile.marginal_rate`, a controller rate | 0 allocations; `marginal_rate` is `None` on all 12 profiles; `EntityRelationship` has 0 rows | **No** |
| S100A-02 Circular money flow | CRITICAL | allocations, `EntityRelationship`, beneficiary→Entity link | 0 allocations; 0 relationships; no such link exists | **No** |
| S100A-03 UPE to related entity | ADVISORY | allocations, beneficiary→Entity link | 0 allocations; no such link exists | **No** |
| S100A-04 Resolution date | CRITICAL | `workspace.confirmed_scenario` | `None` on every workspace | **Fires always — false positive** |
| S100A-05 Four-factor summary | ADVISORY→CRITICAL | `Section100AAssessment` rows | 0 rows platform-wide | No |

### The three blocking data gaps

**1. `BeneficiaryAllocation` is empty (0 rows platform-wide).**
Only `views_upgrades.trust_distribution` creates them, and that page is orphaned
— nothing in the codebase links to `/years/<pk>/distribution/`. Rules 01, 02 and
03 all open with `if not self.allocations: return`.

*Fixable now.* The posted distribution journal holds the same facts and joins
cleanly: `AdjustingJournal.live_trust_distribution(fy)` → journal lines →
`EntityChartOfAccount.beneficiary_officer` → `EntityOfficer`. Verified against
all six posted distributions; every line resolved to a named officer.

**2. `EntityRelationship` has 0 rows.**
`_get_controller_marginal_rate()` and S100A-02 both begin by querying
relationships of type `trustee_of` / `director_of` pointing at the trust. With
none, `_get_controller_marginal_rate()` returns `None`, so S100A-01 returns
early before reaching any allocation, and S100A-02 returns early at
`if not controller_entities`.

*Not fixable by wiring.* This is a data-entry gap. The model exists
(`TRUSTEE_OF`, `DIRECTOR_OF`, `SHAREHOLDER_OF`, …) and is simply unpopulated.

**3. `BeneficiaryProfile.marginal_rate` is `None` on all 12 live profiles.**
S100A-01 skips any beneficiary without one. The field is a manual input on the
Trust tab that nobody fills in.

*Partly derivable.* The Tax Planning worksheet already computes each
beneficiary's tax position from `outside_income + proposed_distribution`
(`tax_engine.calculate_beneficiary_tax`), which yields an **effective** rate.
A true marginal rate needs the beneficiary's other income and the applicable
bracket — the same input the worksheet takes. Deriving it is feasible but is a
modelling decision, not a wiring change.

### The two rules that are wrong, not just idle

S100A-02 and S100A-03 both do:

```python
ben_entity = getattr(alloc.beneficiary, 'entity', None)
```

`alloc.beneficiary` is an `EntityOfficer`, and **`EntityOfficer.entity` is a
foreign key to the trust itself** (`related_name="officers"`). There is no field
linking an officer to the beneficiary's own `Entity`.

So if allocations ever arrived, S100A-03 would search the trust's own trial
balance for the trust's own name and report a UPE against itself, and S100A-02
would test whether the trust is related to itself. Both would also fail their
`ben_entity.entity_type == "company"` branch, since the trust is a trust.

Nor could a name-based fallback rescue it: the platform holds 11 entities in
total, and **none** of the eight beneficiary officers of the four discretionary
trusts exists as an Entity.

### A fourth gap, outside the module

`Section100AAssessment` — the per-beneficiary eight-question four-factor
assessment that feeds S100A-05 — has **0 rows platform-wide**, yet Stage 3 is
marked `completed` on all six trust-years. `section_100a_api` only *lists*
existing assessments; nothing creates them, and the stage can be marked complete
without any. So the stage reports done while holding no assessment at all.

---

## What a working module requires

Grouped by what each unlocks. Costs are relative, not estimates.

### Tier 1 — honesty (no new data, no schema)

1. **Fix S100A-04's false positive.** Read the posted distribution journal
   instead of `confirmed_scenario`, which is a vestigial field.
2. **Source allocations from the journal**, via the officer join above, so the
   data path is correct even where rules remain blocked downstream.
3. **Report what cannot be assessed.** Where `marginal_rate` or
   `EntityRelationship` is missing, say so explicitly rather than returning
   early and silently passing. Silence currently reads as "no risk found".
4. **Stop Stage 3 completing with no assessments**, or state on the finding
   that the four-factor test was never performed.

Outcome: no false alarms, and an explicit statement of what is unknown. **No
rule becomes able to detect anything.**

### Tier 2 — make S100A-01 able to fire

5. **Capture or derive beneficiary marginal rates.** Either surface the existing
   `marginal_rate` field in the Trust tab as a required input before Stage 2
   completes, or derive it from the Tax Planning worksheet's per-beneficiary
   position. The derivation needs a decision about marginal vs effective rate.
6. **Populate `EntityRelationship`** for trustees and directors, or replace
   `_get_controller_marginal_rate`'s relationship lookup with something grounded
   in `EntityOfficer.role` (`trustee`, `director`), which *is* populated.

Item 6 has a cheap version: officers already carry roles, so "the controller" is
derivable from the officer register without touching `EntityRelationship` at
all. That would be my preference over a data-entry campaign.

### Tier 3 — make S100A-02 and S100A-03 correct

7. **Add an officer→entity link.** A nullable `EntityOfficer.linked_entity` FK,
   a migration, and a way to maintain it. Only then can "is this beneficiary a
   related company?" be asked at all.
8. **Populate it.** None of the current beneficiaries exist as entities, so this
   is not a backfill — it is ongoing data capture as corporate beneficiaries are
   onboarded.
9. **Then rewrite both rules** against the real link, and re-examine their trial
   balance heuristics, which currently match on substring of account name.

Tier 3 is the expensive one, and it is the one that buys the CRITICAL rule
(circular money flow) the ATO cares most about.

---

## Options

**A. Tier 1 only.** Small, shippable, honest. Removes a false CRITICAL from
every discretionary trust and replaces silent passes with an explicit
"cannot assess". Does not detect anything.

**B. Tier 1 + Tier 2.** Adds genuine detection for the low-tax-beneficiary
pattern, which is the most common Section 100A exposure in practice. Needs a
decision on marginal-rate sourcing and a change to how the controller is
identified. Moderate.

**C. Tier 1 + 2 + 3.** A working module. Requires a schema addition, ongoing
data capture, and rewriting two rules. Large.

**D. Retire what cannot work.** Ship Tier 1 and remove S100A-02 and S100A-03
until the data model supports them, rather than carrying two rules that are
wrong. Can be combined with A or B.

### Recommendation

**A + D now, B next if Section 100A detection is wanted.**

Reasoning: a compliance rule that cannot fire is worse than no rule, because the
finding card reports a clean assessment. Removing the false positive and stating
what is unknown is strictly better than the present state and costs little. B is
where the real value is, and it is a contained piece of work once the
marginal-rate question is decided. C should not be started until a corporate
beneficiary actually exists on the platform to justify the schema.

I would not implement Tier 1's "wire the journal into allocations" on its own
and call the thread closed — it would look like progress while changing nothing
that fires.

---

## Open questions for the decision

1. **Marginal vs effective rate** for S100A-01 — the rule compares a
   beneficiary's rate against the controller's with a threshold
   (`MARGINAL_RATE_DIFF`). Which figure is intended?
2. **Is the "controller" the trustee, the appointor, or the highest-rate
   individual?** The current code tries all three and lands on none.
3. **Should Stage 3 be blockable?** Marking it complete with zero assessments is
   what makes S100A-05 silent.
4. **Do corporate beneficiaries need to be entities on the platform?** That is
   the real precondition for Tier 3, and it is a client-onboarding question as
   much as a technical one.
