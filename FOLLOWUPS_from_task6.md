# Follow-ups deferred from Tasks 6 and 7

> Filename says Task 6 for history's sake; follow-up 3 came out of Task 7 and is kept here rather
> than in a second file.

**Raised:** 2026-08-17, on completing Task 6 of `docs/superpowers/plans/2026-08-16-bas-tb-desync-fixes.md`
(commit `5af720d`, branch `fix/bas-tb-desync-impl`).
**Status:** none is scoped or built. Elio's call on 2026-08-17: follow-ups 1 and 2 need spec'ing
and building at some point, not inside Task 6. Follow-up 3 was found later the same day, from
production data, and is a defect in Task 7's own new code.

These are three independent work items. Any can be specced on its own.

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

The problem is that the recorded set is undiscoverable. Task 1 *did* record it, at
`.superpowers/sdd/2026-08-16-bas-tb-desync-fixes/baseline-failures.txt` — 65 lines, verified
2026-08-17 to be byte-identical to the sorted failure set of a fresh pre-Task-6 run. But that
path is git-ignored, nothing in the plan or the repo points at it, and the plan's line 17 cites
a stale count instead. Task 6 therefore paid for a second full suite run to re-establish by
hand a set that was already on disk.

The file holds only test names, so nothing stands in the way of it living in the repo. (The
audit output, which does hold client figures, was committed too — Elio's call on 2026-08-17.)

### Shape of a fix

Make "no new failures" mechanical:

1. Promote the existing `baseline-failures.txt` out of the ignored workspace into the repo as
   a checked-in fixture. It is already the right content; it is only in the wrong place. Note
   it will need `git add -f`, or a non-`.txt` name — `.gitignore:38` carries a repo-wide
   `*.txt` rule that makes a plain `git add` skip it without saying so.
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

---

## Follow-up 3 — `resolve_bas_period_for_txn` ignores `period_type`, and that is now a live case

Raised 2026-08-17, from Task 7.

`core/txn_periods.py:resolve_bas_period_for_txn` selects the `BASPeriod` covering a transaction's
date **without filtering on `period_type`**, then takes `.first()`. Its docstring records this as a
tolerable risk on the grounds that periods are created from the entity's `bas_frequency`, so a year
would normally hold only one type, and flagging one of two overlapping rows beats flagging neither.

**That assumption is false in production.** `probe_lodged_bas.py` found Veronica Cerratti's FY2026
holding **16 `BASPeriod` rows** — quarterly *and* monthly — while her `bas_frequency` is
`quarterly`. Overlapping rows cover the same dates, and `Meta.ordering = ["period_number"]` means
`.first()` picks the lower `period_number`. So a July transaction can resolve to either `Q1` or
`Jul`, decided by nothing meaningful.

Consequences today:

- `flag_period_amended` can set `amended_since_lodgement` on a monthly row that the BAS screen
  never displays, because that screen renders only `getattr(entity, "bas_frequency")` periods. The
  badge would then never appear despite the flag being set — a silent failure of the exact feature
  Task 7 added.
- The reverse also holds: it may flag a quarterly row when the monthly one is the lodged one.

**Fix.** Filter on the entity's `bas_frequency`, matching what `bas_dashboard` and
`bas_lodge_period` both already do (`getattr(entity, "bas_frequency", "quarterly") or "quarterly"`).
Where a year genuinely holds both types, the entity's own frequency is the only defensible choice —
it is the one the UI shows and the one lodgement writes to.

Needs a test with both period types present for one year, which
`core/tests_bas_amended_flag.py` does not currently construct: it calls
`ensure_bas_periods(self.fy, "quarterly")` only. Add a monthly set alongside and assert the
quarterly row is the one flagged.

**Also worth deciding separately:** why does a quarterly-frequency entity have 12 monthly
`BASPeriod` rows at all? `ensure_bas_periods` is called with the entity's frequency, so something
created them under a different frequency — either the setting changed after the rows existed, or
some other caller passes a literal. Worth finding before it produces a second class of bug.
