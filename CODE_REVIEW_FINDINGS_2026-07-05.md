# StatementHub — Full Codebase Bug Review (2026-07-05)

Reviewed by 8 parallel passes over `core/`, `accounts/`, `config/`, `integrations/`,
and `review/`. Findings that were **independently re-verified against source** during
compilation are marked ✅. Everything else is high-confidence from the review pass but
should be confirmed before fixing. Ordered by severity within each area.

Legend: 🔴 critical · 🟠 high · 🟡 medium · ⚪ low

---

## A. Financial-data correctness (wrong numbers on client deliverables)

These are the most dangerous because they silently produce incorrect financial
statements, tax figures, or trial balances rather than crashing.

### 🔴 A1. Beneficiary loan accounts netted under the wrong codes ✅
`core/fs_template_service.py:305-322` (`_net_beneficiary_accounts`)
The canonical COA (`beneficiary_account_service.py:31-32`) and the posting logic
(`views_trust.py:1125`) both define **4004 = "Funds loaned to trust"** and
**4003 = "Interest received on loan"**. But `_net_beneficiary_accounts` treats
`4003.xx` as the loan account and **silently strips `4004.xx`** as "rounding/misc".
Every trust on the standard pipeline discards its real beneficiary loan liability
(4004) from the balance sheet and nets an unrelated interest account (4003) in its
place. Trusts skip the post-injection balance check, so it fails silently.
**Fix:** net `4004.xx` (pair `4053.xx` physical distributions against it), reclassify 4003.

### 🔴 A2. Bank-derived management accounts post GST-inclusive amounts
`core/mgmt_accounts.py:244-255` (`build_bank_derived_tb`)
Aggregates `txn.amount` (GST-inclusive) into P&L accounts, never `txn.net_amount`.
The real TB path (`views.py:_post_txn_to_tb`) nets GST to the 3380 control account.
For any GST-registered entity on `BANK_DERIVED` source, every management-accounts P&L
overstates revenue and expenses by ~9.09%.
**Fix:** use `txn.net_amount` (fall back to `calculate_gst`) when posting to P&L accounts.

### 🔴 A3. GST partial-credit calc leaves the trial balance out of balance ✅
`review/views_enhanced.py:1013-1016` (also `management/commands/recalc_gst.py:49-54`)
`net_amount = abs_amount - full_gst`, but `gst_amount = full_gst * cred_pct/100`.
For any creditable % < 100 (RITC 75%, entertainment 50%, vehicle business-use),
`net + gst < gross`, so posting expense + GST + gross bank contra leaves the TB
permanently out of balance on every such transaction.
**Fix:** `net_amount = abs_amount - txn.gst_amount`.

### 🔴 A4. Re-import silently duplicates every TB line and depreciation asset ✅
`core/access_ledger_import.py:1195-1200` + unconditional `bulk_create` at 1372/1415
Re-import without `replace_existing` appends a warning but never `return`s. The delete
(1334-1336) is gated on `replace_existing`; the `bulk_create` is not. With no unique
constraint on `(financial_year, account_code)`, re-importing a ZIP without ticking
"replace existing" doubles every overlapping year's balances.
**Fix:** `return result` after the warning (or skip years that already have TB lines).

### 🔴 A5. LITO offset uses a single linear taper instead of the ATO two-step taper
`core/tax_engine.py:121-142`
Reduces LITO linearly (~2.4c/$) across $37,500–$66,667. Real taper is 5c/$ from
$37,500–$45,000 then 1.5c/$ to $66,667. At $45,000 taxable income this returns ~$520
LITO vs the correct $325 — understating tax across a very common income band.
**Fix:** implement the two-segment taper.

### 🔴 A6. Medicare Levy shade-in logic inverted
`core/tax_engine.py:97-118`
Treats the low-income threshold as the *upper* bound of the shade zone and derives a
`*0.885` floor (should be `/0.8`); above the threshold it jumps straight to full 2%
with no shading. Both over- and under-charges the levy near the threshold.
**Fix:** zero below true lower threshold, 10%-of-excess shading between lower/upper
(upper = lower/0.8), 2% flat above.

### 🟠 A7. Bendigo / ING parsers store every amount as positive ✅ (Bendigo)
`review/pdf_parsers.py:1655-1666` (Bendigo), `1396-1408` (ING)
Sign logic never implemented ("Positive = credit for now"). Every withdrawal/fee is
recorded as a deposit. **Fix:** track `prev_balance`, negate when balance decreases
(as Macquarie/Westpac paths do).

### 🟠 A8. Multi-period CBA statements truncated to the first month ✅
`review/statement_geometry.py:77-89` (`_rows()` returns at first `CLOSINGBALANCE`)
Multi-month CBA PDFs parse only month 1; later months' transactions are dropped
silently (month 1 reconciles internally so `_reconcile()` passes). No fallback parser
exists (`pdf_parsers.py:1760`), so it can't be caught.
**Fix:** scan to last `CLOSINGBALANCE`, accumulate across sub-periods.

### 🟠 A9. Prior-year comparatives silently zeroed
`core/document_context_builder.py:2408-2410`
`prior_closing_balance` is a real field (`default=0`), so the `hasattr` fallback is dead
and the roll-forward path only populates `prior_debit`/`prior_credit`. All `_py` figures
and variances compute to $0 in non-FS documents (eva client/year-end summaries).
Same class of bug in `eva_yearend_commentary.py:140`.
**Fix:** use `prior_debit - prior_credit` (or the prior FY's own `closing_balance`).

### 🟠 A10. Total Equity omits current-year net profit (management balance sheet)
`core/docgen.py:1938-1971` (`_add_detailed_balance_sheet`)
Builds Total Equity from raw equity balances and never adds the passed-in `net_profit`.
Under the app's unclosed-TB convention every management-accounts balance sheet fails to
balance by the period's net profit. **Fix:** inject the current-year-profit line like
`fs_template_service` does. (Related: summary retained-profits roll-forward drops profit,
`docgen.py:2041-2060`; sole-trader equity uses `=` not `+=`, `docgen.py:1527-1534`.)

### 🟠 A11. `int(code)` crashes balance-sheet render on dotted sub-accounts
`core/docgen.py:1591` — `int(code)` where every sibling uses `int(code.split('.')[0])`.
Dotted codes like `"2090.01"` raise `ValueError`, crashing the balance sheet (live via
`mgmt_accounts.py`). **Fix:** `int(code.split('.')[0])` guarded by try/except.

### 🟠 A12. Div 7A exposure & MYR computed off the wrong balance
`core/eva_div7a.py:565-570, 840-848, 772-781`
(a) Exposure/escalation uses current-year *movement*, not closing balance — a $520k loan
with a $20k draw reports "$20k, no agreement" and skips the $200k escalation.
(b) MYR recomputed off the original `loan_amount` every year (never declines), inflating
the demanded repayment ~$17k/yr and manufacturing false shortfalls.
(c) New-loan drawdown year uses closing as opening → false first-year interest shortfalls.
**Fix:** use the year's actual opening/closing TB balance; $0 opening for genuinely new loans.

### 🟠 A13. Stock / General Pool journal lines print as $0 on exported TB
`core/views.py:9688,9699,9708,14563`
`TrialBalanceLine.objects.create(...)` omits `closing_balance` (defaults 0); the export
aggregator `_aggregate_tb_lines` reads only `closing_balance`. Stock and pool depreciation
show correctly on-screen but $0.00 on the printed/Word/Excel TB accountants sign off on.
**Fix:** set `closing_balance=debit-credit` on those create calls.

### 🟠 A14. "Recalc Contra" collapses multi-bank entities into one account
`core/views.py:10207-10297` (`_recalc_bank_contra`)
Resolves the bank mapping from one sample transaction and applies it to all confirmed
transactions. An entity with two bank accounts gets both consolidated into whichever code
the sample resolved to — corrupting balances instead of fixing them.
**Fix:** group by each transaction's own resolved `tb_account_code`.

### 🟠 A15. Bulk approve/push posts wrong financial year's transactions
`core/views.py:9739,9981,10824`
Bulk endpoints select by `job__entity=fy.entity` only (no date/FY match that the
single-transaction path applies). An entity with two open FYs posts the prior year's
transactions into the current year's TB. **Fix:** filter by `job__financial_year=fy`.

### 🟠 A16. Div 7A benchmark rate & SG rate hardcoded/stale
`core/document_context_builder.py:1820` (`8.27%`), `:1055` (SG `11.5%`, legislated 12%
from 1 Jul 2025), `core/tax_engine.py:42-59` (fallback brackets are pre-Stage-3 rates
mislabeled "FY2025"). Stale rates drive Div 7A minimum repayments and SG shortfall calcs.
**Fix:** source all three from `RiskReferenceData`/`TaxReferenceData` by income year.

### 🟡 A17. Risk engine shows wrong dollar totals for aggregated accounts
`core/risk_engine.py:986,780,881,1002` — displays raw `.debit/.credit` from one underlying
row instead of the aggregated `effective_dr/effective_cr`. A loan account with both an
import line and a journal line triggers correctly but shows a materially understated total.
**Fix:** use `effective_dr - effective_cr` everywhere a dollar total is shown.

### 🟡 A18. Concurrent double-post races (no `select_for_update`)
`review/views.py:574` (confirm_transaction), `core/views_trust.py:1080` (distribution),
`core/views.py:9235` (depreciation post), `core/models.py:1816,3676` (journal ref numbers)
In-memory `posted_to_tb` guards and pre-atomic idempotency reads let double-clicks
double-post to the TB / create duplicate journal references. **Fix:** re-check the flag on a
`select_for_update()` row inside the atomic block; add DB unique constraints.

### 🟡 A19. Other correctness bugs (verified by review, fix as a batch)
- Prior-year label from digit-parsing breaks on `"Q1 2025"` → header `"12024"`
  (`views.py:1710,4832,7107,11820`); prefer `fy.prior_year.year_label`.
- BAS gross-up omits capital-purchase codes `CAP`/`FCA` → G10 understated (`views.py:~7986`).
- GST BAS quantize uses banker's rounding, not ATO round-half-up (`bas_utils.py:975,981,627,947`).
- Div 7A first-repayment date identical in both if/else branches
  (`document_context_builder.py:1853`); leap-day final date crashes (`:1854`).
- Director "appointed during year" compares formatted date *strings* lexicographically
  (`document_context_builder.py:1609`).
- Director-loan Div 7A nets asset-side against liability-side → real exposure hidden
  (`document_context_builder.py:1008`).
- HandiLedger "finalised" checks 1 of 3 flag columns (`access_ledger_import.py:1285`).
- ANZ mid-statement year rollover applied to all rows (`pdf_parsers.py:542-679`).
- Xero comparative duplicated across merged lines (`integrations/views.py:1120`).
- `_safe_decimal`/BOM decoding silently zero unparseable amounts
  (`access_ledger_import.py:92,168`).
- "Gross margin" is actually net margin, shown to clients (`eva_yearend_commentary.py:178`).

---

## B. Security / access control

### 🔴 B1. Fake authorization shim disables IDOR protection ✅
`core/views_family_trust_election.py:27-29`
`_get_entity_for_user` claims to check access but just returns
`get_object_or_404(Entity, pk=entity_pk)`. Any logged-in user can view/edit/delete any
client's Family Trust Election. **Fix:** call the real `config.authorization.get_entity_for_user`.

### 🔴 B2. Systemic IDOR across many view modules ✅ (spot-checked)
The canonical boundary is `config/authorization.get_entity_for_user` /
`get_financial_year_for_user`. These modules fetch by raw pk with only `@login_required`,
so any authenticated user can reach any client's data by guessing a UUID:
- `views_trust.py` (full trust workflow incl. posting/un-posting distributions)
- `views_legal_docs.py` (incl. sending another firm's doc to FuseSign for e-signature)
- `views_compliance_docs.py`, `views_eva.py` (incl. `eva_finalise` locking another
  client's TB), `views_package_assembly.py`, `views_client_summary.py`,
  `views_engagement_letters.py`, `views_governing_docs.py`, `views_bulk_operations.py`,
  `views_partnership_docs.py`
- `review/views.py` + `review/views_enhanced.py` (many transaction/rule endpoints)
- Isolated gaps in otherwise-scoped files: `views_audit.py:874,949,1015`,
  `views_upgrades.py:368`, `core/views.py:8116` (`gst_activity_statement_download` ✅),
  `core/views.py:12307` (`delete_document` — deletes any doc by pk ✅)
**Fix:** route every FinancialYear/Entity fetch through the authorization helpers.

### 🔴 B3. Unauthenticated Textract webhook + SSRF
`core/views_webhooks.py:120-150` — no signature check (unlike `fusesign_webhook`), and on
`SubscriptionConfirmation` it `requests.get(SubscribeURL)` unvalidated. An anonymous caller
can force an outbound GET to `169.254.169.254` (cloud metadata) or forge a job result.
**Fix:** verify the SNS signature/shared secret; validate `SubscribeURL` host is AWS SNS.

### 🔴 B4. Admin login bypasses mandatory 2FA and rate-limiting
`config/urls.py:9`, `config/middleware.py:36-72`, `accounts/views.py:59-78`
The custom login is TOTP-gated and rate-limited; Django's `/admin/login/` is neither, and
`Require2FAMiddleware` only checks whether 2FA is *configured*, not *performed this session*.
A staff/superuser password (brute-forceable, no rate limit) yields a full session with no
TOTP. **Fix:** gate admin auth through the TOTP flow; track a per-session "2fa verified"
flag; rate-limit `/admin/login/`.

### 🟠 B5. Guaranteed-crash endpoints (wrong field names)
- `core/views_bulk_operations.py:110` — `Entity.objects.filter(is_active=True).order_by("name")`;
  `Entity` has `is_archived`/`entity_name`, not `is_active`/`name`. `FieldError` every call. ✅
- `core/views_office_admin.py:608` — `.order_by("-year_end")`; field is `end_date`. ✅
**Fix:** `filter(is_archived=False).order_by("entity_name")`; `.order_by("-end_date")`.

### 🟠 B6. Concurrent OAuth token refresh races single-use tokens
`integrations/views.py:279-314,2138-2172`; `integrations/xpm_sync.py:62-94`
No lock around read-refresh-save on the practice-wide `XeroGlobalConnection`. Two
simultaneous imports race the rotating refresh token; the loser gets `invalid_grant` and
the except branch marks the whole connection `expired`, locking out the practice.
**Fix:** `select_for_update()` + re-check `needs_refresh` after the lock.

### 🟠 B7. Missing lock guard / permission checks on TB-mutating views
- `core/views.py` — no `fy.is_locked` guard on `review_push_to_tb`, `review_approve_all`,
  `review_approve_selected`, `review_bulk_edit_transactions`, `review_delete_*`,
  `recalculate_bank_contra_entries`, `stock_push_to_tb` (~25 sibling views do check).
  Allows posting/deleting TB entries in a finalised year.
- `core/views.py:5480` (`calculate_tax_journal`) — no `@require_POST`, no permission check;
  GET-reachable so triggerable via `<img>` (CSRF-bypassing) to post a real tax journal.
- Missing `can_do_accounting`/`can_edit` on `auto_tax_provision:5763`,
  `entity_coa_add/edit/delete:12723+`, `bulk_journal_reallocate:13916`,
  `tb_line_reallocate:5111`, `map_client_accounts:5217`, `access_ledger_import:6972`.
**Fix:** add the standard `is_locked` guard, `@require_POST`, and permission checks.

### 🟡 B8. Other security items
- Uploaded files served inline (no forced `attachment`) + CSP allows `unsafe-inline`
  → stored XSS via `.html`/`.svg` upload (`config/media_serving.py:39`, `settings.py:227`);
  SVG logo upload unsanitized (`views_firm_settings.py:67`).
- `FIELD_ENCRYPTION_KEY` never set → TOTP secrets protected by a key derived from
  `SECRET_KEY` (single point of failure) (`config/encryption.py:22`).
- Raw exception text returned to API callers (`views_coworker_api.py`); PII log scrubber
  misses non-string args & tracebacks (`config/log_filters.py:23`).
- Open redirect in post-2FA flow — unvalidated `next` (`accounts/views.py:74,100`).
- Excel formula injection in TB export (`views.py:12169`).
- Dividend create not wrapped in a transaction; `shareholder_id` unscoped
  (`views_compliance_docs.py:93`); cross-entity officer leak
  (`views_partnership_docs.py:192`).

---

## C. Crashes / robustness (raise on realistic input)

### 🔴 C1. `GeneralPool.calculate()` and pool properties crash — `Decimal` not imported ✅
`core/models.py:6799` (and `business_cost`/`business_termination_value` at 6891,6921)
`Decimal` is only imported locally inside a few *other* methods, never at module level.
`GeneralPool().calculate()` raises `NameError: name 'Decimal' is not defined`. Viewing the
Small Business General Pool tab, adding an asset/disposal, or recalculating all crash — the
entire Div 328 simplified-depreciation feature is non-functional.
**Fix:** add `from decimal import Decimal` at the top of `core/models.py`.

### 🟠 C2. Eva agent tool calls broken
- `_tool_check_prior_suppressions` queries nonexistent fields on `EvaFindingSuppression`
  → `FieldError` swallowed → agent believes no suppressions exist, re-raises dismissed
  findings (`eva_agent.py:263-290`).
- Tool-call extraction regex `\{[^{}]*"tool"[^{}]*\}` can't cross the nested `args:{}`
  → never matches a real tool call; raw JSON shipped to the accountant as "Eva's answer"
  (`eva_agent.py:410`).
**Fix:** query by `fingerprint`; use `json.JSONDecoder().raw_decode` / balanced-brace scan.

### 🟠 C3. Eva review-completion gate ignores `reopened` findings ✅
`core/eva_engine.py:2349,2492` count only `status="open"`; recurring findings are created
`status="reopened"` (`:1708`) and treated as pending everywhere else. A still-outstanding
(possibly critical) finding lets the review flip to `cleared` and unlocks package assembly.
**Fix:** `status__in=["open","reopened"]` in both places.

### 🟡 C4. Silent-failure robustness bugs
- Embedding failure marks docs `synced` but stores no vector → permanently invisible to RAG
  (`eva_knowledge.py:492`); `UnboundLocalError` in the sync error handler aborts the run
  (`eva_knowledge.py:201`).
- Year-end commentary bypasses the shared retry/timeout wrapper — one blip fails it
  (`eva_yearend_commentary.py:226`).
- One malformed LLM score aborts the rest of the batch (`ai_service.py:663`).
- `order_by("-severity")` sorts CRITICAL *last* (string sort) in risk reports
  (`ai_service.py:693,827`).
- ATO due-dates widget hardcoded to FY2025-26 → empty as of today, re-fetches every request
  (`review/ato_due_dates.py:153`).
- Race spawns duplicate concurrent Eva reviews (`eva_engine.py:1969`).
- Section parsers split on substrings in body prose, corrupting stored summaries
  (`eva_client_summary.py:231`, `eva_yearend_commentary.py:314`).
- Several unguarded `int(...)`/`.get()` on query params / JSON → 500s
  (`views_audit.py:763`, `views.py:6688,12658`, `views_bulk_operations.py:40`).

---

## Suggested fix order

1. **Stop the bleeding on client numbers** — A1, A2, A3, A4, A5, A6 (wrong financials/tax/TB).
2. **Close the access holes** — B1, B2, B3, B4 (IDOR / unauth webhook / admin 2FA).
3. **Un-break the crashing features** — C1, B5, C3 (Decimal, field-name crashes, Eva gate).
4. **Concurrency & locking** — A18, B6, B7 (`select_for_update`, `is_locked`, unique constraints).
5. **Rates, parsers, robustness** — A7–A17, A19, C2, C4 as a rolling cleanup with tests.

Every A-series item that touches money should get a regression test capturing the correct
figure before the fix lands, since these are silent (no crash) and easy to reintroduce.

---

## IMPLEMENTATION STATUS (applied 2026-07-06, branch `fix/codebase-review-2026-07-05`)

Essentially all findings above were implemented as **code-only** changes (no DB
migrations). 60 source files changed. `manage.py check` is clean, `makemigrations
--check` reports no changes, all edited modules import, and the test suite shows
**no new regressions** vs the pre-existing baseline.

### Div 7A exposure — IMPLEMENTED as closing balance (team decision 2026-07-06)
- **A12:** Div 7A exposure is now the outstanding **closing debit balance**, not the
  current-year movement. Per the team: any debit loan closing balance owed by a
  shareholder / director / associate to a company IS a Div 7A loan issue, whether or
  not the balance moved this year. `_rule_t2_d7a_01` no longer gates on movement
  (Guard 1 — a net-debit closing balance — remains the false-positive guard); the
  finding text and the `Div7AFalsePositiveTestCase` were updated, and a new test
  (`test_div7a_fires_for_static_debit_balance_no_movement`) covers the carried-forward
  case. (The other Div 7A fixes — MYR opening balance, new-loan drawdown-year, rule
  05/06 decoupling, UPE regime, fy-number default — were also kept.)

### Deferred / needs a human decision
- **B4 (admin 2FA), behavioural change:** the middleware now requires TOTP to have
  been completed *this session*. On deploy, every currently-logged-in user is logged
  out once and must re-authenticate through the TOTP flow. Two test classes were
  updated to mark their test-client session `2fa_verified` (mimicking a real session).

### Known follow-ups intentionally left (noted by the implementers)
- `EntityOfficer.save()` display_order race (cosmetic) — not fixed.
- `views_bulk_operations.py` `bulk_generate_packages`/`bulk_readiness_check` still
  accept arbitrary entity IDs under only `@login_required` (per-entity authorization
  not added — only their guaranteed-crash bugs were fixed).
- A few sub-resource lookups inside already-authorized views left as referenced data
  (e.g. `section_100a_api` beneficiary, change-of-trustee `new_trustee`).
- HandiLedger CR/DR sign handling in `_safe_decimal` — implemented but flag for
  confirmation against a real export.
- CBA geometry→legacy parser fallback — restored, but it is a genuine behaviour change
  if geometry rejects a statement for a real reconciliation failure.
- `review_approve_selected` did not get the cross-FY `job__financial_year=fy` filter
  (only the other three bulk endpoints did).
- `ato_due_dates.py` offline fallback text is still static 2026 content (only the
  re-fetch/empty-list bug was fixed).

### NOT introduced by this work — pre-existing test failures
The suite had **47 failures + 12 errors before any of these changes** (baseline on
`main`). Those are pre-existing and were not touched here; worth a separate cleanup.
