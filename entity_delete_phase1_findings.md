# Entity Deletion — Phase 1 Audit Findings

**Date:** 2026-04-29
**Audit basis:** Static analysis of `C:\Users\Elio\mcs-platform` (master branch, same code as `/opt/statementhub`).
**Phase 1.5 (live DB census) requires server access — script provided at the end for Elio to run.**

---

## ROOT CAUSE (one-line summary)

`OfficerDistributionHistory.officer = ForeignKey(EntityOfficer, on_delete=PROTECT)` at `core/models.py:582-583` is the **only** PROTECT clause in Entity's downstream tree. When a user bulk-deletes an Entity, Django cascades to `EntityOfficer` (CASCADE), then tries to cascade-delete those officers — which fails because `OfficerDistributionHistory` rows protect them. The `client_bulk_action` view at `core/views.py:10470` does not catch `ProtectedError`, so the user gets HTTP 500.

The traceback labels the blocker as `EntityOfficer.entity` because that's the cascade path Django was traversing. The actual culprit is one level down at `OfficerDistributionHistory.officer`.

---

## 1.1 — FKs pointing at `Entity` (full inventory)

Found via grep across `core/models.py`, `core/models_office_admin.py`, `integrations/models.py`, `review/models.py`. Tests / fixture files excluded.

### Self-referential / reverse: 0 (Entity has no FK to itself)

### From `core/models.py`:

| # | Model.field | Line | on_delete | Conceptual ownership |
|---|---|---|---|---|
| 1 | `EntityOfficer.entity` | 409 | **CASCADE** | Entity-owned |
| 2 | `FinancialYear.entity` | 665 | **CASCADE** | Entity-owned |
| 3 | `EntityChartOfAccount.entity` | 1051 | **CASCADE** | Entity-owned |
| 4 | `ClientAccountMapping.entity` | 1265 | **CASCADE** | Entity-owned |
| 5 | `BankAccountMapping.entity` | 1309 | **CASCADE** | Entity-owned |
| 6 | `ClientAssociate.entity` | 2358 | **CASCADE** | Entity-owned |
| 7 | `ClientAssociate.related_entity` | 2380 | **SET_NULL** | Cross-entity ref |
| 8 | `EntityRelationship.from_entity` | 2441 | **CASCADE** | Entity-owned |
| 9 | `EntityRelationship.to_entity` | 2446 | **CASCADE** | Entity-owned |
| 10 | `SoftwareConfig.entity` | 2514 | **CASCADE** | Entity-owned |
| 11 | `MeetingNote.entity` | 2593 | **CASCADE** | Entity-owned |
| 12 | `ActivityLog.entity` | 2774 | **CASCADE** (nullable) | Entity-owned |
| 13 | `BankAccount.entity` | 2831 | **CASCADE** | Entity-owned |
| 14 | `TrustElectionRecord.related_entity` | 4592 | **SET_NULL** | Cross-entity ref |
| 15 | `GoverningDocument.entity` | 4641 | **CASCADE** | Entity-owned |
| 16 | `GoverningDocumentChunk.entity` | 4717 | **CASCADE** | Entity-owned |
| 17 | `LegalDocument.entity` | 4829 | **CASCADE** | Entity-owned |
| 18 | `DividendEvent.entity` | 4951 | **CASCADE** | Entity-owned |
| 19 | `FrankingAccountEntry.entity` | 5061 | **CASCADE** | Entity-owned |
| 20 | `Div7AComplianceRecord.entity` | 5434 | **CASCADE** | Entity-owned |
| 21 | `Div7AComplianceRecord.borrower_entity` | 5442 | **SET_NULL** | Cross-entity ref |
| 22 | `EngagementLetterConfig.entity` (OneToOne) | 5105 | **CASCADE** | Entity-owned |
| 23 | `EngagementLetter.entity` | 5714 | **CASCADE** | Entity-owned |
| 24 | `FamilyTrustElectionDocument.entity` | 6109 | **CASCADE** | Entity-owned |

### From `core/models_office_admin.py`:

| # | Model.field | Line | on_delete | Conceptual ownership |
|---|---|---|---|---|
| 25 | `Correspondence.entity` | 47 | **CASCADE** (nullable) | Entity-owned |
| 26 | `ASICReturn.entity` | 108 | **CASCADE** | Entity-owned |
| 27 | `NoticeOfAssessment.entity` | 165 | **CASCADE** | Entity-owned |
| 28 | `DebtorRecord.entity` | 216 | **CASCADE** | Entity-owned |
| 29 | `PaymentPlan.entity` | 264 | **CASCADE** | Entity-owned |

### From `integrations/models.py`:

| # | Model.field | Line | on_delete | Conceptual ownership |
|---|---|---|---|---|
| 30 | `AccountingConnection.entity` | 33 | **CASCADE** | Entity-owned |
| 31 | `XeroTenant.entity` | 337 | **SET_NULL** | Independent (tenant exists in Xero regardless) |
| 32 | `QBTenant.entity` | 441 | **SET_NULL** | Independent |
| 33 | `MYOBCompanyFile.entity` | 560 | **SET_NULL** | Independent |

### From `review/models.py`:

| # | Model.field | Line | on_delete | Conceptual ownership |
|---|---|---|---|---|
| 34 | `ReviewJob.entity` | 39 | **SET_NULL** | Independent (FY drives cascade instead) |
| 35 | `TransactionPattern.entity` | 246 | **CASCADE** (nullable) | Entity-owned |
| 36 | `ClassificationRule.entity` | 316 | **CASCADE** | Entity-owned |
| 37 | `EntityGSTSettings.entity` | 387 | **CASCADE** | Entity-owned |

### Totals — FKs pointing at Entity

- **Total: 37**
- CASCADE: **30**
- SET_NULL: **7**
- **PROTECT: 0** ← *no Entity-pointing FK is currently PROTECT*

This means the bulk-delete error is **not** caused by an Entity-pointing FK directly. The chain runs:

> `Entity` → CASCADE → `EntityOfficer` → cascade-delete attempt blocked by `OfficerDistributionHistory.officer` (PROTECT)

---

## 1.2 — FKs pointing at `EntityOfficer`

| # | Model.field | Line | on_delete | Conceptual ownership |
|---|---|---|---|---|
| 1 | `OfficerDistributionHistory.officer` | 582 | **PROTECT** ⚠️ | Officer-owned |
| 2 | `EntityChartOfAccount.beneficiary_officer` | 1108 | **SET_NULL** | Loose ref |
| 3 | `ClientAccountMapping.beneficiary_officer` | 1277 | **SET_NULL** | Loose ref |
| 4 | `TrustDistributionAllocation.beneficiary` | 2990 | **CASCADE** | Officer-owned |
| 5 | `PartnershipPartnerShare.partner` | 3102 | **CASCADE** | Officer-owned |
| 6 | `PartnerCapitalAccount.partner` | 3154 | **CASCADE** | Officer-owned |
| 7 | `TaxPlanningBeneficiaryRow.beneficiary` | 3445 | **CASCADE** | Officer-owned |
| 8 | `TrustBeneficiaryProfile.beneficiary` | 4418 | **CASCADE** | Officer-owned |
| 9 | `Section100AAssessment.beneficiary` | 4501 | **CASCADE** | Officer-owned |
| 10 | `TrustElectionRecord.test_individual` | 4587 | **SET_NULL** | Loose ref |
| 11 | `DividendAllocation.shareholder` | 5014 | **CASCADE** | Officer-owned |

### Totals — FKs pointing at EntityOfficer

- **Total: 11**
- CASCADE: 7
- SET_NULL: 3
- **PROTECT: 1 — `OfficerDistributionHistory.officer`** ← *the actual blocker*

---

## 1.3 — Entity delete call sites

Searched for `Entity.objects...delete()`, `entity.delete()`, `entities.delete()` across the project (excluding tests and migrations).

**Only one production call site deletes Entity instances:**

- `core/views.py:10507` — `entities.delete()` inside `client_bulk_action` (line 10470). **No try/except for `ProtectedError`. This is the bug.**

**Test fixture only (not production):**
- `test_real_docgen.py:20` — local test cleanup; not on prod.

There is no individual `entity_delete` view; deletion is bulk-only via `client_bulk_action`.

### Other delete views worth knowing about (NOT Entity-level, but related)

- `entity_officer_delete` at `views.py:6519` — already pre-clears `OfficerDistributionHistory` and `EntityChartOfAccount` (auto-provisioned) inside `transaction.atomic()` before `officer.delete()`. **This is a hand-rolled workaround for the same PROTECT clause we need to fix.** Once we change the FK to CASCADE, the manual pre-clear becomes redundant (but it's harmless to leave — Phase 2 can decide).
- `entity_link_delete` at `views.py:7305` — deletes `EntityRelationship`. Already CASCADE-safe.
- `entity_coa_delete` at `views.py:12358` — deletes `EntityChartOfAccount`. Already CASCADE-safe.

---

## 1.4 — Delete-related views and URLs

| URL pattern | View | Scope | Error handling? |
|---|---|---|---|
| `entities/bulk-action/` | `entity_bulk_action` (= `client_bulk_action`) | Bulk delete + archive | **No** ProtectedError handling |
| `officers/<pk>/delete/` | `entity_officer_delete` | Single officer | Manual pre-clear, no try/except (works because of pre-clear) |
| `entity-account/<pk>/delete/` | `entity_coa_delete` | Single COA row | N/A (no PROTECT downstream) |
| `entity-links/<pk>/delete/` | `entity_link_delete` | Single relationship | N/A |
| `entities/<pk>/delete-unfinalised/` | `delete_unfinalised_fy` | Unfinalised FYs only | N/A |

Only `client_bulk_action` is exposed to the user-visible 500 because it's the only path that actually deletes Entity rows.

---

## 1.5 — LIVE DATA CENSUS (server script — Elio to run)

I cannot run this from local because `manage.py shell` requires `.env` with SECRET_KEY which is server-only. Run this on the droplet:

```bash
ssh root@<droplet>
source /opt/statementhub/venv/bin/activate
cd /opt/statementhub
python3 manage.py shell <<'PYEOF'
from django.apps import apps
from django.db.models import ForeignKey
from core.models import Entity, EntityOfficer

print("=" * 78)
print("ALL FKs POINTING AT Entity")
print("=" * 78)
entity_fks = []
for model in apps.get_models():
    for field in model._meta.get_fields():
        if isinstance(field, ForeignKey) and field.related_model is Entity:
            on_delete = field.remote_field.on_delete.__name__
            entity_fks.append((model._meta.label, field.name, on_delete))
            print(f"  {model._meta.label:50} .{field.name:30} on_delete={on_delete}")

print()
print(f"Total Entity FKs: {len(entity_fks)}")
from collections import Counter
print(Counter(x[2] for x in entity_fks))

print()
print("=" * 78)
print("ALL FKs POINTING AT EntityOfficer")
print("=" * 78)
officer_fks = []
for model in apps.get_models():
    for field in model._meta.get_fields():
        if isinstance(field, ForeignKey) and field.related_model is EntityOfficer:
            on_delete = field.remote_field.on_delete.__name__
            officer_fks.append((model._meta.label, field.name, on_delete))
            print(f"  {model._meta.label:50} .{field.name:30} on_delete={on_delete}")

print()
print(f"Total EntityOfficer FKs: {len(officer_fks)}")
print(Counter(x[2] for x in officer_fks))

print()
print("=" * 78)
print("CHIARAVALLE BLOCKAGE CENSUS")
print("=" * 78)
ent = Entity.objects.filter(entity_name__icontains='Chiaravalle').first()
if not ent:
    print("Chiaravalle entity not found")
else:
    print(f"Entity: {ent.entity_name} pk={ent.pk}")
    print(f"  type={ent.entity_type}")

    officers = EntityOfficer.objects.filter(entity=ent)
    print(f"\nOfficers: {officers.count()}")
    for officer in officers:
        print(f"  {officer.full_name} ({officer.role}) pk={officer.pk}")
        try:
            hist = officer.distribution_history.all()
            print(f"    OfficerDistributionHistory rows: {hist.count()}")
            for h in hist[:5]:
                print(f"      pct={h.distribution_pct} from={h.effective_from}")
        except Exception as e:
            print(f"    distribution_history error: {e}")

    # All reverse relations with counts
    print("\nAll reverse relations (count > 0 only):")
    for rel in ent._meta.get_fields():
        if rel.one_to_many or rel.one_to_one:
            accessor = rel.get_accessor_name() if hasattr(rel, 'get_accessor_name') else None
            if accessor:
                try:
                    mgr = getattr(ent, accessor, None)
                    if mgr is None:
                        continue
                    if rel.one_to_one:
                        count = 1 if mgr else 0
                    else:
                        count = mgr.count()
                    if count == 0:
                        continue
                    on_del = rel.on_delete.__name__ if hasattr(rel, 'on_delete') else '?'
                    print(f"  {rel.related_model.__name__:35} via {accessor:30} count={count:5} on_delete={on_del}")
                except Exception as e:
                    pass  # ignore m2m / non-iterable

PYEOF
```

**Expected output highlights:**
- Entity FK count should be ~37 with 0 PROTECT.
- EntityOfficer FK count should be ~11 with 1 PROTECT (`OfficerDistributionHistory.officer`).
- Chiaravalle: officer count > 0, distribution_history count > 0 — confirms the cascade chain blocks here.

---

## 1.6 — Proposed reclassification (awaiting Elio sign-off)

### CASCADE (entity-owned data — delete with the entity)

**Only one change is strictly required to fix the reported 500:**

| Field | Current | Proposed | Reason |
|---|---|---|---|
| `OfficerDistributionHistory.officer` | PROTECT | **CASCADE** | Distribution history is a per-officer audit trail. When the officer is removed (or their parent Entity is deleted) the history should go with them. The pre-existing `entity_officer_delete` view already manually clears these rows before deleting the officer — confirming the intent is "cascade with officer". |

### SET_NULL (no change needed)

The 7 SET_NULL relations on Entity (XeroTenant, QBTenant, MYOBCompanyFile, ClientAssociate.related_entity, TrustElectionRecord.related_entity, Div7AComplianceRecord.borrower_entity, ReviewJob.entity) are correct as-is — these represent cross-entity or external-system references that should survive the deletion of the linked entity.

### PROTECT (kept)

After this change, **no PROTECT FK exists in Entity's downstream subgraph**. This matches the design rule in §1.6 of `entity_delete_fix.md` ("Entity is the root of its own subgraph; deleting an Entity should be a deliberate cascade").

### Other CASCADE/SET_NULL fields

I do **not** recommend changing any other FK at this time. The 30 existing CASCADE relations and 7 existing SET_NULL relations on Entity are sensible. Changing more than necessary creates risk without benefit — the bug is one PROTECT clause, fix that clause.

---

## 1.7 — Findings summary

```
ENTITY DELETION AUDIT — FINDINGS

FKs POINTING AT Entity: 37 total
  CASCADE: 30
  PROTECT: 0
  SET_NULL: 7
  Other:   0

FKs POINTING AT EntityOfficer: 11 total
  CASCADE: 7
  PROTECT: 1   ← OfficerDistributionHistory.officer (core/models.py:582)
  SET_NULL: 3

DELETE CALL SITES (no ProtectedError handling): 1
  core/views.py:10507  client_bulk_action  entities.delete()

CHIARAVALLE BLOCKAGE: (pending live census — see 1.5 script)
  EntityOfficer rows: TBD on server
  OfficerDistributionHistory rows: TBD on server
  Other blocking relations: none expected (no other PROTECTs)

PROPOSED RECLASSIFICATION:
  CASCADE: OfficerDistributionHistory.officer  (was PROTECT)
  SET_NULL: (no changes)
  PROTECT (kept): none

ADDITIONAL HARDENING (independent of FK fix):
  Wrap entities.delete() at views.py:10507 in try/except ProtectedError
  + transaction.atomic() — defence-in-depth so future PROTECTs surface as
  user-friendly errors, not 500s.
```

---

## STOP — awaiting your sign-off before Phase 2

**Decision needed from you:**

1. **Confirm the proposed reclassification** (single FK: `OfficerDistributionHistory.officer` PROTECT → CASCADE).
2. Run the Phase 1.5 census script on the server and paste output here, OR tell me to skip it (the static analysis is sufficient to act).
3. Confirm scope of Phase 2:
   - 2.1 Migration (single AlterField).
   - 2.2 Wrap `client_bulk_action` delete in try/except + transaction.atomic.
   - 2.3 Add `Entity.get_delete_blockers()` helper (no UI yet).
   - 2.4 Audit log entry — `_log_action` already runs at views.py:10506 BEFORE delete, so this is partially covered. Phase 2.4 would extend to capture cascade counts. Worth doing or skip?

Once you confirm, I'll implement Phase 2 in a single commit per the prompt's spec.
