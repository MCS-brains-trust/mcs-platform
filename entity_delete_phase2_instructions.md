# Entity Delete Fix — Phase 2 Instructions

Confirmed. Proceed to Phase 2 with the minimal fix.

## Decisions

### 1. Single FK reclassification — YES, CASCADE

The intent is clearly "officer's distribution history dies with the officer" — every other officer-related FK cascades, and the existing `entity_officer_delete` view at `views.py:6519` manually clears distribution history before deleting an officer, which is the *manual workaround* for the missing CASCADE. Fixing the FK lets that workaround be removed.

### 2. Live census script — SKIP

Phase 1's static audit was thorough. Running the live census just confirms what we already know (Chiaravalle has 2 officers with 2 distribution history records). Save the time, ship the fix.

### 3. Phase 2.4 audit log extension — SKIP for this sprint, add to backlog

The existing `_log_action` at `views.py:10506` already captures the deletion event itself. Extending it to enumerate cascade counts is nice-to-have, but it's a 30-minute add that doesn't address the immediate bug. There's also risk: computing cascade counts before deletion adds queries and another failure surface. Better to ship the minimal fix tonight, then add cascade-count logging in a deliberate sprint when not also fixing a 500.

## Phase 2 — execute these four changes

1. **Single AlterField migration:** `OfficerDistributionHistory.officer` PROTECT → CASCADE.

2. **Wrap `client_bulk_action` delete** at `views.py:10507` in `try/except ProtectedError` + `transaction.atomic()`. Friendly error message naming the blockers, redirect back, log the full `protected_objects` list at WARNING level.

3. **Add `Entity.get_delete_blockers()` helper** as specified in the original prompt. Don't wire to UI — just available for future use.

4. **Remove the manual clearing of `OfficerDistributionHistory`** in `entity_officer_delete` at `views.py:6519`. The CASCADE makes it redundant. Add a comment in the commit message noting this dead code was removed.

## Skip

- Phase 1.5 live census (audit was sufficient)
- Phase 2.4 audit log extension (backlog)

## Phase 3 — verify

- **3.1** Migration apply
- **3.2** Throwaway entity test (create entity → add officer → add distribution history → delete entity → confirm no ProtectedError, no orphan rows)
- **3.3** Chiaravalle dry-run delete (transaction rollback) confirming cascade works
- **3.4** Service restart
- **Skip 3.5** manual UI test for now — Elio will run it himself after deploy

## Phase 4 — commit

Single commit, message references the 4 changes plus the dead-code removal at `views.py:6519`.

```
Fix entity delete HTTP 500 — single FK reclassification + view hardening

Pre-existing bug surfaced when deleting Chiaravalle entity. Phase 1 audit
of all 48 FKs in Entity's downstream tree found exactly one misclassified:
OfficerDistributionHistory.officer used PROTECT, blocking the cascade
chain when EntityOfficer rows were being deleted as part of an entity
delete. Traceback labeled it "EntityOfficer.entity" because Django reports
the outer relationship, not the inner block point.

Changes:
1. Migration 01XX: OfficerDistributionHistory.officer PROTECT → CASCADE.
2. core/views.py:10507 client_bulk_action wraps delete() in
   try/except ProtectedError + transaction.atomic(). Friendly message,
   no more 500.
3. core/models.py: Entity.get_delete_blockers() helper added for future
   pre-delete confirmation UI.
4. core/views.py:6519 entity_officer_delete: removed manual
   distribution_history clearing — now redundant with CASCADE.

Refs: Phase 1 audit findings entity_delete_phase1_findings.md
```

Ship it.
