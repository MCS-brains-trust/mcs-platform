# Eva Knowledge Base — Phase 2, Wave 1: Foundation Fixes

**Owner:** Elio Scarton
**Audience:** Claude Code (running locally at `C:\Users\Elio\mcs-platform`)
**Predecessor:** `audit_reports/eva_knowledge_audit_20260507.md`
**Goal:** Resolve the embedding dual-write defect, backfill the 309 unembedded chunks, diagnose the "12 documents loop", and verify whether the SharePoint folder mapping still matches the live SharePoint structure.
**Out of scope (deferred to Wave 2+):** entity-aware retrieval, retrieval logging, M2M provenance, external feed scheduling, dashboard.

---

## 0. Post-merge reconciliation (added 2026-05-07)

This spec was originally written against local `master @ ed26ff8`. Investigation during Phase 1 (see `audit_reports/eva_kb_wave1_phase1_origin_diff_20260507.md`) established that:

- The production server has been running `origin/master` since at least **2026-05-04 13:29 UTC** — confirmed by comparing the origin tip date against the predecessor audit's `MAX(synced_at) = 2026-05-07 03:47:13 UTC`.
- The predecessor audit's server-side measurements (Block B–H) **remain valid** because they were captured against the same code production runs.
- The predecessor audit's code-level quotes are **line-shifted** by the Sprint 2 merge but still point at the correct logical defects. Line numbers in this spec have been updated to match the merged tree.
- The **Sprint 2 Eva Intelligence Upgrade** (hybrid retrieval, learned lessons, knowledge graph, agentic orchestrator, nightly reflection, weekly style update, daily proactive scan) is in scope of `origin/master` but **explicitly out of scope of Wave 1** — Wave 2 planning will address it.

The two structural defects this spec targets — the embedding dual-write and the broken timestamp skip check — are byte-identical between `ed26ff8` and `origin/master`. Wave 1's plan does not need redesigning; only line-number citations were updated.

---

## 1. Constraints (mandatory)

1. **Four-phase structure.** Phase 1 (audit, read-only) → Phase 2 (implement) → Phase 3 (verify) → Phase 4 (commit & deploy). Do not skip phases.
2. **No new packages.** All work uses libraries already in `requirements.txt`.
3. **No model schema changes** beyond what is explicitly authorised in section 4 below.
4. **All Django shell commands run on the server** at `/opt/statementhub` via SSH, prefixed with:
   ```
   source /opt/statementhub/venv/bin/activate && cd /opt/statementhub
   ```
   Never run `manage.py shell` locally.
5. **PowerShell compatibility** for any local shell commands: use `;` for chaining, not `&&`.
6. **Every change traces to a Phase 1 finding.** No speculative refactors. No fixes outside the scope below.
7. **Git discipline.** Phase 4 is mandatory: explicit `git add`, `git commit`, `git push`. Migration files MUST be added.

---

## 2. Phase 1 — Audit (read-only)

Save findings inline as markdown comments at the top of each file you'll modify, OR as a brief block printed to terminal — but produce a written record. Investigate, do not fix.

### 2.1 Confirm the embedding dual-write defect

- Open `core/eva_service.py` and quote lines **1297–1312** verbatim. Confirm that `KnowledgeChunk.objects.create(...)` writes only `embedding` (JSON) and never `embedding_vector`.
- Open `core/eva_knowledge.py` and quote `embed_and_store_chunks` (lines **485–526**, with the dual-write block at **510–525**). Confirm it writes both fields.
- Confirm that `eva_service.sync_knowledge_brain` does not call `embed_and_store_chunks`, and that `eva_knowledge.sync_sharepoint_library` (the simpler walker) is not invoked by `core/tasks.py:24` or by `core/management/commands/sync_knowledge_brain.py`.

### 2.2 Identify the 12 looping documents

Run on the server (read-only):

```python
python3 manage.py shell <<'PY'
from core.models import KnowledgeDocument, KnowledgeChunk
from django.db.models import Count, Q
docs_with_null = (KnowledgeDocument.objects
    .filter(chunks__embedding_vector__isnull=True)
    .annotate(
        null_chunks=Count("chunks", filter=Q(chunks__embedding_vector__isnull=True)),
        total_chunks=Count("chunks"),
    )
    .order_by("-null_chunks"))
print(f"Documents with at least one NULL embedding_vector chunk: {docs_with_null.count()}")
for d in docs_with_null:
    print(f"  id={d.pk} | category={d.category} | title={d.title[:80]!r}")
    print(f"    null_chunks={d.null_chunks}/{d.total_chunks} | sharepoint_path={d.sharepoint_path!r}")
    print(f"    created_at={d.created_at} | synced_at={d.synced_at} | sharepoint_modified_at={d.sharepoint_modified_at}")
PY
```

Confirm the count is 12 and that the sum of `null_chunks` across them equals 309. Record the document titles and categories — they tell us what's stuck (it may indicate file-format problems if e.g. all 12 are PDFs).

### 2.3 Confirm or refute the loop hypothesis

Locate the diff logic in `core/eva_service.py:sync_knowledge_brain` that decides whether a SharePoint item triggers an update vs is skipped. Quote the exact lines (the skip check is at **line 1232** on `origin/master`). Identify what condition causes a document to be re-processed. Specifically determine whether the condition is based on:

(a) `sharepoint_modified_at` comparison only, OR
(b) presence/absence of chunks, OR
(c) embedding completeness.

Report which it is. If the answer is (b) or (c), the loop is explained — fixing the dual-write will close the loop. If (a), the loop has a separate cause and Wave 1 needs an additional fix.

**Phase 1 result (2026-05-07):** the answer is (a) — and the comparison is structurally broken (`str(<datetime>) == "<ISO-string>"` against a `DateTimeField`). The fix is specified in section **3.3a** below.

### 2.4 Live SharePoint folder audit

Walk every folder declared in `SHAREPOINT_FOLDER_MAP` (canonical home: `core/eva_knowledge.py:42–93`; re-exported from `core/eva_service.py:1091`) via Graph API and report:

- Folder path as declared in the map.
- Whether the folder exists in the live SharePoint drive.
- Number of files in the folder (recursive count, excluding `archive` subfolders).
- Most recent `lastModifiedDateTime` of any file in the folder.

Do this read-only via a one-off management command in `core/management/commands/audit_sharepoint_folders.py` that prints to stdout and exits. Do NOT have it write to the database. The file should be deleted in Phase 4 after we capture its output (or moved to `_archive/` — your call).

The output of this audit answers the strategic question: *has SharePoint received new files we're missing, or has nothing new been added?*

### 2.5 Phase 1 deliverable

A short markdown file at `audit_reports/eva_kb_wave1_phase1_<YYYYMMDD>.md` containing:

- Section 2.1 finding (confirmed / refuted, with quoted code).
- Section 2.2 finding (12 doc IDs, titles, categories, null/total chunk counts, file paths).
- Section 2.3 finding (loop cause, with quoted code and your one-paragraph explanation).
- Section 2.4 finding (folder-by-folder table).
- A green/red verdict on whether Phase 2 should proceed as specified, or whether the findings change the plan.

Stop after this file is saved and confirm with the user before proceeding to Phase 2.

---

## 3. Phase 2 — Implement

### 3.1 Fix the embedding dual-write

In `core/eva_service.py`, modify the chunk creation block at **lines 1306–1312** so it writes `embedding_vector` alongside `embedding`. The existing `embed_and_store_chunks` function in `core/eva_knowledge.py` (lines 485–526) already does this correctly — refactor `eva_service.sync_knowledge_brain` to call `embed_and_store_chunks` for the embedding+persist step, OR inline the dual-write directly. Pick whichever produces less code duplication. State your choice in the commit message.

Acceptance: every `KnowledgeChunk.objects.create(...)` (or `bulk_create`) in the production sync path produces a row where both `embedding` and `embedding_vector` are non-null.

### 3.2 Backfill management command

Create `core/management/commands/backfill_kb_embeddings.py`. It must:

- Take an optional `--dry-run` flag.
- Take an optional `--limit N` flag for testing.
- Query `KnowledgeChunk.objects.filter(embedding_vector__isnull=True)`.
- For each chunk, generate the pgvector embedding from `chunk.text` using the existing `_get_embeddings` helper from `core/eva_knowledge.py` (do not reinvent — reuse the same OpenAI client and the same `text-embedding-3-small` model).
- Process in batches of 50 to limit memory and respect API rate limits.
- Print a one-line progress summary every 50 chunks.
- Write the resulting vector to `chunk.embedding_vector` and save.
- On completion, print: `Backfilled N chunks. Remaining NULL: M.`

Acceptance: running this command on the server reduces `chunks_null_pgvector` from 309 to 0. Re-running it should be a no-op (0 chunks backfilled).

### 3.3 Loop fix routing (Phase 1.3 result)

Phase 1.3 confirmed the loop cause is **(a) — broken `sharepoint_modified_at` comparison**, not chunk-presence or embedding-completeness. The dual-write fix in section 3.1 will therefore **not** close the loop on its own; it will only ensure that re-created chunks are correctly dual-written. The actual loop fix is specified in section 3.3a below.

### 3.3a Fix the broken timestamp skip check (added 2026-05-07)

**Trace:** Phase 1.3 finding in `audit_reports/eva_kb_wave1_phase1_20260507.md` and the origin-diff confirmation in `audit_reports/eva_kb_wave1_phase1_origin_diff_20260507.md`.

**Defect:** `core/eva_service.py:1232` reads:

```python
if existing and str(existing.sharepoint_modified_at) == modified_at:
    stats["skipped"] += 1
    continue
```

`existing.sharepoint_modified_at` is a Python `datetime` (the field is `DateTimeField` per `core/models.py:4147`). `modified_at` is an ISO 8601 string from Microsoft Graph (`core/eva_service.py:1221`). `str(<datetime>)` formats as `"2026-02-27 09:24:05+00:00"` while Graph returns `"2026-02-27T09:24:05Z"`. The strings can never be equal, so the skip branch is unreachable for any document whose timestamp was successfully ingested.

**Fix:** parse the Graph string into a datetime and compare datetimes, mirroring the working pattern at `core/eva_knowledge.py:200–207`:

```python
from datetime import datetime
remote_dt = (
    datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
    if modified_at else None
)
if (
    existing
    and remote_dt
    and existing.sharepoint_modified_at
    and remote_dt <= existing.sharepoint_modified_at
):
    stats["skipped"] += 1
    continue
```

Constraints on this fix:

- Mirror the existing correct pattern; do **not** invent a new approach.
- Keep the change tightly scoped to the skip check at line 1232. Do not refactor surrounding code.
- The `from datetime import datetime` import goes at the top of the function (or use the module-level import if one already exists in `eva_service.py`).
- Do not change the field type of `sharepoint_modified_at`. No model/migration changes.

**Acceptance:** see Phase 3.2 below — after deploy, the next 2-hourly sync run reports `Documents updated: 0` (or only counts of genuinely modified files since the last sync).

### 3.4 What NOT to change in Wave 1

- Do not modify the Sprint 2 retrieval surface area: `core/eva_engine.py` (hybrid-retrieval call site), `core/eva_retrieval.py`, `core/eva_agent.py`, `core/eva_graph.py`, `core/eva_reflection.py`, `core/eva_style.py`, `core/eva_proactive_v2.py`. All Wave 2 territory.
- Do not add new models or migrations beyond what is created by `_get_embeddings` writes (i.e. nothing).
- Do not modify the Eva system prompt.
- Do not touch the chat retrieval path.
- Do not schedule the ATO scraper.
- Do not modify `SHAREPOINT_FOLDER_MAP` even if Phase 1.4 reveals broken folder paths — that finding becomes its own ticket.

---

## 4. Phase 3 — Verify

All verification runs on the production server via SSH, after deployment.

### 4.1 Backfill verification

Run on server:

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub
python3 manage.py backfill_kb_embeddings --dry-run
```

Expected output: `Would backfill 309 chunks.` (or whatever the current count is.)

Then run the real backfill:

```
python3 manage.py backfill_kb_embeddings
```

Expected output: `Backfilled 309 chunks. Remaining NULL: 0.`

### 4.2 Sync loop verification

Wait for the next scheduled `sync-knowledge-brain` run (every 2 hours), then query AuditLog:

```python
python3 manage.py shell <<'PY'
from core.models import AuditLog
for a in AuditLog.objects.filter(action="eva_sync").order_by("-timestamp")[:3]:
    print(a.timestamp, "|", (a.description or "")[:160])
PY
```

**Acceptance (revised 2026-05-07):** after the backfill (3.2) **and** the timestamp fix (3.3a) are deployed, the next sync run reports `Documents updated: 0` (or only counts of genuinely modified files since the last sync). `Documents added` may be non-zero if and only if SharePoint genuinely contains new files since the previous run.

If documents are still being "updated" after both fixes are deployed and no SharePoint-side change accounts for it, the loop has a third cause and Wave 1.5 is needed.

### 4.3 Embedding coverage verification

```
python3 manage.py dbshell <<'SQL'
SELECT
  COUNT(*) FILTER (WHERE embedding_vector IS NOT NULL) AS with_pgvector,
  COUNT(*) FILTER (WHERE embedding_vector IS NULL)     AS null_pgvector
FROM core_knowledgechunk;
SQL
```

Expected: `with_pgvector = 10235`, `null_pgvector = 0`.

### 4.4 Retrieval smoke test

```python
python3 manage.py shell <<'PY'
from core.eva_knowledge import retrieve_relevant_chunks
chunks = retrieve_relevant_chunks("trust distribution Section 100A", top_k=4)
print(f"Retrieved {len(chunks)} chunks.")
for c in chunks:
    print(f"  - {c['document_title']} ({c['category']}) — score {c['score']:.3f}")
PY
```

Expected: at least 1 chunk, ideally 4. Document titles should look trust-relevant. (This is a smoke test only — Wave 2 makes retrieval actually entity-aware.)

---

## 5. Phase 4 — Commit & deploy

1. `git status` — confirm changed files include:
   - `core/eva_service.py`
   - `core/management/commands/backfill_kb_embeddings.py`
   - (and any helper changes in `core/eva_knowledge.py` if you refactored)

2. `git add` each file explicitly. Migrations, if any, must be added.

3. Commit with a descriptive message in this format:
   ```
   eva-kb wave 1: fix embedding dual-write + backfill 309 chunks

   - eva_service.sync_knowledge_brain now writes both embedding (JSON) and
     embedding_vector (pgvector) per chunk, closing the silent loop where 12
     documents were re-processed every 2 hours but never gained pgvector embeddings.
   - Adds backfill_kb_embeddings management command (--dry-run, --limit supported).
   - No model schema changes, no new packages.

   Refs: audit_reports/eva_knowledge_audit_20260507.md (Tasks 2, 3)
   ```

4. `git push origin master`.

5. Deploy on server:
   ```
   source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && \
   git pull && python3 manage.py migrate && \
   sudo systemctl restart gunicorn celery celerybeat
   ```

6. Run the backfill (Phase 3.1).

7. Confirm verification (Phase 3.2, 3.3, 3.4).

8. Print all four verification outputs back to the user.

---

## 6. Completion

Wave 1 is complete when:

- All four Phase 3 verifications pass on the server.
- The commit is on `origin/master`.
- The backfill run output is captured.

Stop after step 8. Do not propose Wave 2. Wait for review.
