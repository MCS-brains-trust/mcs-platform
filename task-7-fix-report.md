# Task 7 — remove `ctx["_sections"]` exposure from DocumentContextBuilder

## What changed

**`core/document_context_builder.py`**
- Removed `"_sections": sections,` from the dict returned by
  `_financial_data_context()`. This was the only place this builder's
  `_sections` was exposed; `_write_audit_trail()` serialises the entire
  returned context (minus `practice_logo`) into every `LegalDocument.parameters`
  via `json.dumps(..., default=str)`, so this stopped every distribution
  minute / compilation report / directors' declaration from having the full
  classified trial balance (income, expenses, assets, liabilities, equity)
  persisted into its audit trail.
- Extracted the inline current-year-profit-injection block (the `ac7b099`
  fix logic, unchanged) into a new private method
  `_inject_profit_into_equity(self, sections, net_profit, net_profit_py,
  total_assets, total_liabilities)`, called from `_financial_data_context()`
  in the same place the inline block used to run. Behaviour is identical —
  this is a pure extraction, done so the test could observe the real
  equity-row mutation without needing `ctx["_sections"]`.
- Confirmed no other file reads `document_context_builder.py`'s `_sections`
  key (grepped for `ctx["_sections"]` / `context["_sections"]` across the
  repo — the only consumers are `core/fs_template_service.py`'s own
  unrelated `_sections` convention and its tests).

**`core/tests_trust_document_equity.py`**
- Both tests no longer read `ctx["_sections"]`.
- Added a helper `_build_ctx_and_capture_equity_rows()` that spies on
  `DocumentContextBuilder._inject_profit_into_equity` via
  `unittest.mock.patch.object(..., wraps-through-to-the-real-implementation)`,
  capturing the actual `sections["equity"]` list mutated inside the real
  `_financial_data_context()` call. The spy calls straight through to the
  original method (no behaviour change) and asserts it was actually
  invoked, so a build path that stops calling the injection method fails
  loudly (`assertIn` on the captured dict) rather than silently.
- `test_equity_section_contains_current_year_profit_row`: asserts the real
  equity rows contain a "Current year profit / (loss)" row.
- `test_total_equity_equals_sum_of_equity_rows_not_an_override`: asserts
  `ctx["total_equity"]` equals `-sum(cy for real equity rows)` (not just
  `net_assets`), and separately that `total_equity == net_assets`.
- Documented in the module docstring why a naive `total_equity ==
  net_assets` check is insufficient: for a balanced TB,
  `net_assets == -sum(equity rows without profit) + net_profit` is an
  accounting identity that holds regardless of whether total_equity came
  from genuine injected rows or from the old unconditional trust override —
  so only the structural row-presence check (via the spy) can tell them
  apart.

## Test commands and output

Target labels, green (current/fixed code):

```
cd /opt/sh-wt/trust-release
DATABASE_URL="sqlite:////opt/sh-wt/trust-release/testfix.sqlite3" \
  /opt/statementhub/venv/bin/python manage.py test \
  core.tests_trust_document_equity core.tests_partnership_docs \
  core.tests_directors_report core.tests_docgen_bs_aggregation
```

```
ERROR: test_non_partnership_entity_shows_error_not_crash (core.tests_partnership_docs.PartnershipDocsViewTests...)
ERROR: test_partner_statements_excludes_ceased_partners (core.tests_partnership_docs.PartnershipDocsViewTests...)
ERROR: test_partner_statements_page_renders (core.tests_partnership_docs.PartnershipDocsViewTests...)
ERROR: test_wizard_prefills_saved_sections (core.tests_directors_report.DirectorsReportWizardTests...)
ERROR: test_wizard_renders_section_textareas (core.tests_directors_report.DirectorsReportWizardTests...)
Ran 14 tests in 2.245s
FAILED (errors=5)
```

The 5 errors are all `ValueError: Missing staticfiles manifest entry for
'css/style.css'` — a pre-existing environment issue in this worktree (no
`staticfiles/` dir, ManifestStaticFilesStorage has nothing to serve from),
unrelated to this change. Confirmed identical (same 5 tests, same error)
before and after my edits. **Both `core.tests_trust_document_equity` tests
pass**, as do both `core.tests_docgen_bs_aggregation` tests.

## Step 3 — red-run evidence

Temporarily reproduced pre-fix behavior in `core/document_context_builder.py`
(commented out the call to `self._inject_profit_into_equity(...)` and
reinstated the old unconditional trust override
`if self.entity.entity_type == "trust": total_equity = net_assets;
total_equity_py = net_assets_py`, positioned exactly where `ac7b099` had
removed it from), then ran:

```
DATABASE_URL="sqlite:////opt/sh-wt/trust-release/testfix.sqlite3" \
  /opt/statementhub/venv/bin/python manage.py test core.tests_trust_document_equity -v 2
```

Result — both tests failed:

```
FAIL: test_equity_section_contains_current_year_profit_row (...)
AssertionError: 'equity_rows' not found in {} : DocumentContextBuilder._inject_profit_into_equity was never called by _financial_data_context()

FAIL: test_total_equity_equals_sum_of_equity_rows_not_an_override (...)
AssertionError: 'equity_rows' not found in {} : DocumentContextBuilder._inject_profit_into_equity was never called by _financial_data_context()

Ran 2 tests in 0.302s
FAILED (failures=2)
```

Restored `core/document_context_builder.py` to the fixed version (verified
via `git diff --stat` that the file returned to exactly my intended edit),
re-ran the same command — both tests passed (green), and the full target
label set was re-verified green (same 5 pre-existing static-file errors
only) as shown above.

## Concerns

- **Shared worktree contamination observed during this task.** The worktree
  was clean (`git status` → nothing to commit) at the start of this
  session. Partway through, `core/fs_template_service.py` was modified and
  a new untracked file `core/tests_trust_4199_carried_forward.py` appeared
  — neither authored by me, both about an unrelated 4199/"Undistributed
  income" equity-stripping fix. This means another process/agent is also
  writing directly into `/opt/sh-wt/trust-release` concurrently with this
  session, despite the task description implying this worktree was
  dedicated. I verified via `git diff --stat` that my commit includes only
  `core/document_context_builder.py` and `core/tests_trust_document_equity.py`,
  and did not touch, stage, or discard the other agent's in-progress
  changes. Worth flagging to whoever owns worktree allocation — this could
  cause a lost-work incident similar to the one noted in memory for the
  live `/opt/statementhub` checkout.
