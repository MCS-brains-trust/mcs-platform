# Follow-ups deferred from Task 6

**Raised:** 2026-08-17, on completing Task 6 of `docs/superpowers/plans/2026-08-16-bas-tb-desync-fixes.md`
(commit `5af720d`, branch `fix/bas-tb-desync-impl`).
**Status:** neither is scoped or built. Elio's call on 2026-08-17: both need spec'ing and
building at some point, not inside Task 6.

These are two independent work items. Either can be specced on its own.

---

## Follow-up 1 — the other unlocked full-row saves in `review/views_enhanced.py`

Task 6 fixed `set_gst_treatment` and `bulk_set_gst_treatment`: it gave them
`transaction.atomic()` + `select_for_update()` and narrowed their writes to
`save(update_fields=[...])`. **Four more sites in the same file still carry the shape Task 6
removed** — read the row unlocked, mutate a few fields, then `txn.save()`, which writes
every column back from the in-memory copy including `is_confirmed` and `posted_to_tb`.

The consequence is the one Task 6's finding documented: if the copy predates a concurrent
`/confirm/` commit, the save reverts both flags. The transaction silently becomes
unconfirmed and its `TrialBalanceLine` rows are orphaned — a posted entry lost from the
ledger, not merely a report disagreeing with another.

| Site | Definition | The `save()` | Fields it means to write | Exposure |
|---|---|---|---|---|
| `undo_bulk_gst` | `:693` | `:722` | `gst_treatment`, `creditable_percentage`, `is_gst_manual`, + `gst_amount`/`net_amount` via `_recalculate_gst` | **Highest.** Fires inside a 5-second undo window right after a bulk change — precisely when a confirm is most likely still in flight |
| `set_creditable_percentage` | `:739` | `:763` | `creditable_percentage`, + `gst_amount`/`net_amount` | User-driven, one row at a time |
| `set_gst_override` | `:790` | `:826` | `gst_amount_override`, `gst_override_reason`, `gst_amount`, `net_amount`, `creditable_percentage` | User-driven, one row at a time |
| `apply_classification_rules` | `:511` | `:546` | `ai_suggested_code`, `ai_suggested_name`, `gst_treatment`, `creditable_percentage`, `matched_rule`, `from_learning`, + `gst_amount`/`net_amount` | Not an endpoint — runs on **every GET of the review page**, from `review/views.py:518-520` |

`apply_classification_rules` deserves a note in both directions. It is the most frequently
executed of the four by a wide margin: a plain page load triggers it, and the review screen
is reloaded constantly. But it also filters `is_confirmed=False` at `:529`, so a row that is
already confirmed when the queryset runs is never picked up. The window is therefore narrow —
the row must be unconfirmed when the queryset evaluates and confirmed before that iteration's
save lands — where the three endpoints have no such filter at all. Narrow is not zero, and
the exposure is multiplied by how often it runs.

### Shape of a fix

Mechanically identical to Task 6, per site:

1. Wrap in `with db_transaction.atomic():` (the import is already at the top of the file
   after `5af720d`).
2. Re-fetch under `select_for_update()`.
3. Replace `txn.save()` with `save(update_fields=[...])` naming every field the site writes
   and nothing else. Read `_recalculate_gst` (`:1007`) when composing each list — it writes
   `gst_amount` and `net_amount`, and nothing else, and never either confirmation flag.

`apply_classification_rules` is the one that needs a design decision rather than a
transcription. It loops over a queryset and takes a lock per row; holding one transaction
open across the whole loop on a page render is not obviously right. Options worth weighing:
lock per row in its own atomic block, keep the narrowed save and skip the lock entirely
(the `is_confirmed=False` filter plus `update_fields` may be sufficient), or move the whole
thing off the render path.

### Testing

Reuse the harness Task 6 built, `review/tests_gst_treatment_race.py`. The trick that makes
the lost update reproducible single-threaded is documented in its module docstring: wrap
`_recalculate_gst`, which every one of these four sites calls between its read and its save,
so the concurrent confirm commits at exactly that instant via a queryset `.update()`. The
`CaptureQueriesContext` test in the same module asserts the UPDATE statement names neither
flag, which is the interleaving-independent proof.

Note what such tests cannot show: `select_for_update` is a no-op on sqlite, so they prove the
narrowed save only. The lock itself gets evidence from a Postgres run.

### Decisions needed before building

1. Do all four sites get fixed together, or does `undo_bulk_gst` go first on its own?
2. `apply_classification_rules` — which of the three options above.
3. Whether this lands on `fix/bas-tb-desync-impl` (it is the same defect class as Task 6, and
   would ride the same review) or as its own branch off `main`.

---

## Follow-up 2 — the test baseline the plan compares against is stale

Seven steps of the desync plan say "failure set identical to the Task 1 baseline", and the
plan records that baseline at line 17 as **229 tests, 47 failures + 16 errors** at commit
`d4156b9`. That number is from 2026-07-30 and is now wrong by a wide margin, because the
desync work's own test modules nearly doubled the suite.

Measured on `fix/bas-tb-desync-impl`, `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3"
python3 manage.py test core review integrations`:

| Commit | Tests | Failures | Errors |
|---|---|---|---|
| `d4156b9` (2026-07-30, as recorded in the plan) | 229 | 47 | 16 |
| `2f48a06` (Tasks 1–5) | 441 | 47 | 18 |
| `5af720d` (Task 6) | 445 | 47 | 18 |

The count is not the real problem — the plan already says to compare failure *sets*, not
counts, and Task 6 did exactly that by running the suite twice with the change stashed and
diffing the two sorted `FAIL:`/`ERROR:` lists. They came back identical, which is the only
statement about regression worth making.

The problem is that the comparison is remembered rather than recorded. Nothing in the repo
holds the expected failure set, so every task pays for a second full suite run to
re-establish it, and any session that skips that step is comparing against a number in a doc
that was already stale two tasks ago.

### Shape of a fix

Make "no new failures" mechanical:

1. Commit the current expected failure set — the sorted `FAIL:`/`ERROR:` lines — as a
   checked-in fixture.
2. Give it a thin runner that runs the suite, extracts the set, diffs it against the fixture,
   and exits non-zero only on lines that are *new*. Disappearing failures are progress and
   should be reported, not failed on.
3. Have it print the drift so the fixture can be regenerated deliberately, with a commit
   message saying which failures went away and why.
4. Correct line 17 of the plan, and the seven steps that cite it, to point at the fixture
   instead of a number.

### Decisions needed before building

1. Where the fixture lives, and whether it is per-app or one file.
2. Whether the runner is a management command, a shell script, or a pytest/CI step — noting
   there is no CI runner for the Django suite today.
3. Whether the 47 + 18 are worth triaging at the same time. Some are order-dependent
   (`Missing staticfiles manifest entry for 'css/style.css'`), which means a fixture keyed on
   test names is stable but one keyed on counts is not.
