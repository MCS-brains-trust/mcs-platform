# Entity Deletion — ProtectedError Audit and Fix

**TASK:** Fix HTTP 500 on entity delete. Root cause: `EntityOfficer.entity` has `on_delete=PROTECT`, raising `django.db.models.deletion.ProtectedError` when an entity with officer distribution history is deleted. The view at `core/views.py:10507` doesn't catch the error. Audit ALL on_delete clauses on Entity-pointing FKs, fix per type, wrap delete views.

## Context

- **Symptom:** Trying to delete Chiaravalle entity (which has OfficerDistributionHistory attached) returns HTTP 500.
- **Traceback:**
  ```
  ProtectedError: Cannot delete some instances of model 'Entity' because they are
  referenced through protected foreign keys: 'EntityOfficer.entity'.
  ```
- **Stack:** Django/PostgreSQL on `/opt/statementhub`
- **Server prefix:** `source /opt/statementhub/venv/bin/activate && cd /opt/statementhub`
- **Pre-existing bug.** Not caused by today's deploys (trust COA / industry / textract work did not touch EntityOfficer or OfficerDistributionHistory).

## Constraints

- **Phase 1 is read-only.** No code changes, no migrations until audit complete and direction confirmed.
- All fixes traceable to a Phase 1 finding with file:line.
- All deletion-related view changes must use `transaction.atomic()`.
- Migration must be reversible.
- No new packages.
- Server-side commands run via SSH; local dev work is on Windows at `C:\Users\Elio\mcs-platform`.

---

## PHASE 1 — AUDIT (READ ONLY)

### 1.1 Find every FK that points at `Entity`

```bash
grep -rn "ForeignKey(Entity\|ForeignKey('core.Entity'\|ForeignKey(\"core.Entity\"\|ForeignKey(.*Entity," /opt/statementhub/core/ --include="*.py" | grep -v migrations | head -100
```

Also check via Django introspection on the server:

```python
from django.apps import apps
from django.db.models import ForeignKey
from core.models import Entity

print("ALL FKs POINTING AT Entity")
print("=" * 70)
for model in apps.get_models():
    for field in model._meta.get_fields():
        if isinstance(field, ForeignKey) and field.related_model is Entity:
            on_delete = field.remote_field.on_delete.__name__
            print(f"  {model._meta.label}.{field.name} | on_delete={on_delete}")
```

Report the full list. For each FK, capture:
- Model name and field name
- `on_delete` clause (CASCADE / PROTECT / SET_NULL / DO_NOTHING / RESTRICT / SET_DEFAULT / SET)
- Whether the related model is conceptually owned by Entity (delete cascades) or independent (delete protects/nulls)

### 1.2 Find every FK that points at `EntityOfficer`

The traceback specifically blocks because `OfficerDistributionHistory` references `EntityOfficer`. Get the full picture:

```python
from core.models import EntityOfficer

print("\nALL FKs POINTING AT EntityOfficer")
print("=" * 70)
for model in apps.get_models():
    for field in model._meta.get_fields():
        if isinstance(field, ForeignKey) and field.related_model is EntityOfficer:
            on_delete = field.remote_field.on_delete.__name__
            print(f"  {model._meta.label}.{field.name} | on_delete={on_delete}")
```

### 1.3 Find every place Entity gets deleted

```bash
grep -rn "\.delete()\|delete_view\|entity\.delete" /opt/statementhub/core/ --include="*.py" | grep -v migrations | grep -iE "entity|client" | head -50
```

Report each call site with file:line and what queryset is being deleted. Flag any that aren't wrapped in try/except for ProtectedError.

### 1.4 Find delete-related views and URLs

```bash
grep -rn "client_bulk_action\|entity_delete\|delete_entity" /opt/statementhub/core/ --include="*.py" | grep -v migrations | head -20
grep -rn "client_bulk_action\|entity_delete\|delete_entity" /opt/statementhub/core/urls.py
```

Report:
- View function names and locations
- URL patterns
- Whether they handle individual deletion vs bulk
- Whether any error handling exists

### 1.5 Live data census — what's actually blocking the Chiaravalle delete?

```python
from core.models import Entity, EntityOfficer

# Find the troublesome entity (E & J Chiaravalle Family Trust per traceback context)
ent = Entity.objects.filter(entity_name__icontains='Chiaravalle').first()
if not ent:
    print("Chiaravalle entity not found")
else:
    print(f"Entity: {ent.entity_name} pk={ent.pk}")
    print(f"  type={ent.entity_type} created_at={ent.created_at}")
    
    # All reverse relationships
    print("\nReverse relations:")
    for rel in ent._meta.get_fields():
        if rel.one_to_many or rel.one_to_one:
            accessor = rel.get_accessor_name() if hasattr(rel, 'get_accessor_name') else None
            if accessor:
                try:
                    mgr = getattr(ent, accessor, None)
                    if mgr:
                        count = mgr.count() if hasattr(mgr, 'count') else 1
                        on_del = rel.on_delete.__name__ if hasattr(rel, 'on_delete') else '?'
                        print(f"  {rel.related_model.__name__:35} via {accessor:30} count={count:5} on_delete={on_del}")
                except Exception as e:
                    print(f"  {rel.related_model.__name__}: error {e}")
    
    # Specifically the officers and their distribution history
    print("\nOfficers and their distribution history:")
    for officer in EntityOfficer.objects.filter(entity=ent):
        print(f"  Officer: {officer} pk={officer.pk}")
        for hist_field in ['distributionhistory_set', 'distribution_history', 'officerdistributionhistory_set']:
            if hasattr(officer, hist_field):
                hist = getattr(officer, hist_field)
                count = hist.count()
                print(f"    {hist_field}: {count}")
                for h in hist.all()[:5]:
                    print(f"      {h}")
                break
```

Report the full output.

### 1.6 Cross-reference Phase 1 findings to design rules

For each FK pointing at Entity, classify into one of:

- **CASCADE candidates** (entity-owned data — delete with the entity):
  - Officers, officer distribution history
  - EntityChartOfAccount
  - FinancialYear, TrialBalance, JournalEntry, AdjustingJournal
  - Documents (GoverningDocument), notes, attachments
  - Risk findings, Eva findings
  - Mappings (ClientAccountMapping, BankAccountMapping)
  - Audit log entries scoped to that entity

- **SET_NULL candidates** (independent data — keep but unlink):
  - Cross-entity relationships (related party records)
  - User-created content not strictly owned

- **PROTECT candidates** (truly forbidden — must clean up explicitly):
  - Records that represent legal commitments
  - Records that other entities depend on
  - **Probably nothing in Entity's downstream tree should be PROTECT** — Entity is the root of its own subgraph; deleting an Entity should be a deliberate cascade

Report your proposed classification for every FK found in 1.1 and 1.2. **Wait for Elio to confirm before changing any of them.**

### 1.7 Findings summary

```
ENTITY DELETION AUDIT — FINDINGS

FKs POINTING AT Entity: <n total>
  CASCADE: <n>
  PROTECT: <n>     ← these are the bombs
  SET_NULL: <n>
  Other: <n>

FKs POINTING AT EntityOfficer: <n total>
  <list with on_delete clauses>

DELETE CALL SITES (no ProtectedError handling): <n>
  <file:line list>

CHIARAVALLE BLOCKAGE:
  EntityOfficer rows: <n>
  OfficerDistributionHistory rows: <n>
  Other blocking relations: <list>

PROPOSED RECLASSIFICATION:
  CASCADE: <list of model.field>
  SET_NULL: <list>
  PROTECT (kept): <list with reason>
```

**Stop at end of Phase 1. Report findings and wait for Elio to confirm the proposed reclassification before any code changes.**

---

## PHASE 2 — FIX (after Phase 1 sign-off)

### 2.1 Migration: change on_delete clauses

Generate a single Django migration that updates `on_delete` on every FK identified in Phase 1.6 as needing reclassification.

The migration should:
- Be reversible (set old values in `reverse_code`)
- Use `migrations.AlterField` per field
- Not require a `--fake` step
- Include a docstring explaining the change and referencing this audit

Example shape (specific fields to be filled in from Phase 1):

```python
# core/migrations/01XX_fix_entity_delete_protected_fks.py
from django.db import migrations, models

class Migration(migrations.Migration):
    """Fix HTTP 500 on entity delete by reclassifying on_delete clauses on
    FKs pointing at Entity and EntityOfficer.
    
    Pre-existing bug: EntityOfficer.entity used PROTECT, blocking entity
    deletion when officers existed. Other entity-owned tables had similar
    issues. After this migration:
      - Entity-owned data CASCADES with the parent Entity
      - Independent references SET_NULL
      - True legal-protection FKs remain PROTECT (none currently identified)
    
    Refs: Phase 1 audit findings dated 2026-04-29.
    """
    
    dependencies = [
        ('core', '01XX_previous'),
    ]
    
    operations = [
        migrations.AlterField(
            model_name='entityofficer',
            name='entity',
            field=models.ForeignKey(
                'core.Entity',
                on_delete=models.CASCADE,  # was PROTECT
                related_name='officers',
            ),
        ),
        # ... one AlterField per field reclassified
    ]
```

### 2.2 View hardening: catch ProtectedError everywhere

For every delete call site identified in Phase 1.3 / 1.4, wrap in try/except. Pattern:

```python
from django.db.models import ProtectedError
from django.contrib import messages

def client_bulk_action(request):
    # ... existing code ...
    
    if action == 'delete':
        try:
            with transaction.atomic():
                count = entities.count()
                entities.delete()
                messages.success(request, f"Deleted {count} entities.")
        except ProtectedError as e:
            # Build a user-friendly message
            blockers = ", ".join(
                f"{obj._meta.verbose_name} ({obj})" for obj in list(e.protected_objects)[:5]
            )
            extra = "" if len(e.protected_objects) <= 5 else f" and {len(e.protected_objects) - 5} more"
            messages.error(
                request,
                f"Cannot delete: protected references exist. "
                f"Remove these first: {blockers}{extra}. "
                f"If you believe this is a bug, copy this message and send to support."
            )
            # Log the full set for diagnostics
            import logging
            logging.getLogger(__name__).warning(
                f"ProtectedError on entity delete: user={request.user.id} "
                f"entities={list(entities.values_list('pk', flat=True))} "
                f"protected_objects={[(o._meta.label, o.pk) for o in e.protected_objects]}"
            )
            return redirect(request.META.get('HTTP_REFERER', 'entity_list'))
    # ... rest of view ...
```

Apply this pattern to every entity-deletion view. Report each one wrapped.

### 2.3 Pre-delete safety helper

Add a helper on the Entity model that reports what would block a delete BEFORE the user attempts:

```python
# core/models.py — Entity class
def get_delete_blockers(self):
    """Return a dict of related-model -> count for any relation that would
    block deletion under current on_delete rules. Used by the UI to warn
    the user before they attempt deletion."""
    blockers = {}
    for rel in self._meta.get_fields():
        if not (rel.one_to_many or rel.one_to_one):
            continue
        on_delete = getattr(rel, 'on_delete', None)
        if on_delete is None:
            continue
        if on_delete.__name__ in ('PROTECT', 'RESTRICT'):
            accessor = rel.get_accessor_name()
            try:
                count = getattr(self, accessor).count()
                if count:
                    blockers[rel.related_model._meta.label] = count
            except Exception:
                pass
    return blockers
```

Don't expose this in a UI yet — just have it available so the next sprint can build a "Are you sure? This will also delete X officers, Y financial years, Z documents" confirmation dialog.

### 2.4 Audit trail on entity deletion

Before the entity is deleted, write an `AuditLog` record (or whatever audit model exists) capturing:
- Who deleted it (user)
- When
- Entity pk, name, type, abn
- Counts of related records that cascaded (officers deleted, financial years deleted, etc.)

This is a forensic record. If a deletion turns out to have been wrong, the audit log shows what was lost.

Locate the existing AuditLog or equivalent model first; if none exists, skip this and add a TODO.

---

## PHASE 3 — VERIFY

### 3.1 Migration apply

```bash
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub
git pull origin master
python3 manage.py migrate
python3 manage.py check
```

Confirm migration applied cleanly.

### 3.2 Test deletion on a throwaway entity

Create, populate with officers + distribution history, then delete via the actual view (not raw ORM):

```python
import requests, os
# Or via Django test client
from django.test import Client
from core.models import Entity, EntityOfficer
import uuid

# Create a test entity with officers
test = Entity.objects.create(
    entity_name=f"DELETE ME — protectederror test {uuid.uuid4().hex[:8]}",
    entity_type='trust',
    abn='00000000000',
)

# Add an officer (use real model fields)
officer = EntityOfficer.objects.create(
    entity=test,
    # ... required fields
)

# Add distribution history (use real model fields)
# ...

# Now attempt delete via the view layer or via cascade-safe ORM delete
print(f"Test entity pk: {test.pk}")
print(f"Officer count: {test.officers.count()}")

# This should now succeed without ProtectedError
test.delete()

print("Deleted cleanly.")
print(f"Officers remaining: {EntityOfficer.objects.filter(entity_id=test.pk).count()}")
# Expected: 0
```

Report success/failure.

### 3.3 Test the actual broken case — Chiaravalle

The original symptom. Confirm the entity can now be deleted via the view (without manually clearing officer history first).

Do this in a transaction that ROLLS BACK at the end so we don't actually delete the test data — just confirm the cascade works:

```python
from django.db import transaction
from django.db.models import ProtectedError
from core.models import Entity

ent = Entity.objects.filter(entity_name__icontains='Chiaravalle').first()
print(f"Testing delete cascade on: {ent.entity_name}")

# Snapshot what would cascade
officers_before = ent.officers.count()
# (count other reverse relations that should cascade)

try:
    with transaction.atomic():
        ent.delete()
        # Force rollback so we don't actually destroy test data
        raise transaction.TransactionManagementError("ROLLBACK_INTENTIONAL")
except transaction.TransactionManagementError as e:
    if "ROLLBACK_INTENTIONAL" in str(e):
        print(f"Delete would have succeeded (cascade ok). Rolled back.")
    else:
        raise
except ProtectedError as e:
    print(f"STILL PROTECTED: {e}")
    print(f"Protected objects: {list(e.protected_objects)}")
```

Expected: "Delete would have succeeded (cascade ok). Rolled back."

If still ProtectedError, Phase 1 missed an FK. Re-audit and add to migration.

### 3.4 Restart services

```bash
sudo systemctl restart gunicorn celery celerybeat
sudo systemctl status gunicorn --no-pager | head -10
```

### 3.5 Manual UI test

Elio: try the original delete from the UI (`/entities/bulk-action/`). Expected:
- ✅ Successful deletion (no 500)
- ✅ Success message ("Deleted N entities")
- ✅ Cascade fired correctly — officers, distribution history, etc. gone
- ✅ Audit log entry written (if 2.4 implemented)

If a future entity has a true PROTECT relationship (e.g. financial commitment), expected:
- ✅ Friendly error message naming the blocking records
- ✅ Redirect back to entity list, not 500

---

## PHASE 4 — COMMIT

Single commit:

```bash
git add -A
git commit -m "Fix entity delete HTTP 500 — reclassify on_delete + handle ProtectedError

Pre-existing bug surfaced during Chiaravalle delete attempt:
EntityOfficer.entity used on_delete=PROTECT, raising ProtectedError
when officers had attached OfficerDistributionHistory. View at
core/views.py:10507 did not catch it, returning 500.

Fixes:
1. Migration 01XX reclassifies on_delete on N FKs pointing at Entity
   and M FKs pointing at EntityOfficer. Entity-owned tables now
   CASCADE; independent references SET_NULL; no PROTECT remaining
   in Entity's downstream tree.
2. client_bulk_action and <other> entity-delete views now wrap delete()
   in try/except ProtectedError, returning a friendly message instead
   of 500.
3. New Entity.get_delete_blockers() helper for future pre-delete
   confirmation UI.
4. Audit log entry written before entity deletion (cascade snapshot).

Refs: Phase 1 audit findings 2026-04-29, traceback at views.py:10507"

git push origin master
```

Report commit hash. Confirm migration applied and services restarted on production.

---

## PHASE 5 — FINAL REPORT

```
ENTITY DELETE FIX — REPORT

PHASE 1 FKs FOUND:
  Entity → <n> incoming FKs
  EntityOfficer → <n> incoming FKs
  PROTECT clauses identified: <list>

PHASE 2 RECLASSIFICATIONS APPLIED:
  CASCADE: <n fields>
  SET_NULL: <n fields>
  PROTECT (kept): <n fields, with reasons>

PHASE 2 VIEWS HARDENED:
  <list of view function names>

PHASE 3 VERIFICATION:
  Migration applied: YES/NO
  Test entity delete: SUCCESS/FAILURE
  Chiaravalle dry-run delete: SUCCESS (rolled back)/STILL PROTECTED

COMMIT: <hash>
PRODUCTION: deployed/awaiting deploy

OUTSTANDING:
  <anything that needs Elio's attention>
```
