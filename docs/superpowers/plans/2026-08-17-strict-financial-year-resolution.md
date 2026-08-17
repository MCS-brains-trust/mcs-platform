# Strict Financial-Year Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A transaction posts to the financial year its date falls in, or it does not post at all — never to a fallback year its date has nothing to do with.

**Architecture:** One rule change in `core/txn_periods.py:resolve_fy_for_txn` (return `None` instead of falling back when a *parseable* date matches no postable year), plus a derived explanation function and three surfacing points. Six call sites share the resolution function, so `None` means a transaction is simultaneously never posted and never aggregated — which is what keeps the rebuild from zeroing lines it should not touch.

**Tech Stack:** Django 5.2, Postgres in production, sqlite for the Django test suite, Playwright for e2e.

**Spec:** `docs/superpowers/specs/2026-08-17-strict-financial-year-resolution-design.md`

## Global Constraints

- **Tests need a sqlite override.** `python3 manage.py test` cannot create a test database on the live managed Postgres. Always run:
  `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test <labels>`
- **Collect staticfiles first, or the baseline lies.** A checkout without `staticfiles/` fails 7 view tests on `Missing staticfiles manifest entry for 'css/style.css'`. Run `python3 manage.py collectstatic --noinput` once, then treat **47 failures + 11 errors** across `core review integrations` as the baseline. Compare failure *sets*, never counts.
- **`PendingTransaction.date` is a `CharField`** holding whatever the statement parser produced. Never assume it parses.
- **Money is `Decimal`.** Never introduce a float into a posting path.
- **`/opt/statementhub` is production on disk.** Work in a git worktree. Merging to `main` auto-deploys and restarts gunicorn.
- **View tests need three things or they fail misleadingly:** `role=User.Role.ADMIN` (`can_do_accounting` is a read-only property, not a field), `totp_confirmed=True` plus `session["2fa_verified"] = True` after `force_login`, and `secure=True` on every request (`SECURE_SSL_REDIRECT` 301s otherwise).
- **`POSTABLE_FY_STATUSES` contains a status that does not exist.** It is `("draft", "in_review", "finished")`, while `FinancialYear.Status` offers `draft`, `in_review`, `finalised`, `reopened`. `"finished"` matches nothing, so postable means draft + in_review, and **both `finalised` and `reopened` years are non-postable**. Do not "fix" this tuple in this plan — changing which years are postable is a separate behavioural change. Do write messages that name the year's actual status rather than hardcoding "finalised".

---

## Sequencing Gate

**Task 1 is a gate, not a formality.** Tasks 2 onward must not start until Task 1's probe has been run and its result recorded in this file. If the probe finds any transaction already posted into a year its date does not cover, **stop and escalate** — the rule change would make the rebuild exclude those transactions and zero the trial-balance lines they created, and that is a decision about historical client ledgers.

---

### Task 1: Pre-flight — has anything already posted into the wrong year?

**Files:**
- Create: `probe_wrong_year_postings.py` (repo root, alongside the existing `probe_*.py` diagnostics)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing later tasks import. Produces a *decision*: proceed or stop.

- [ ] **Step 1: Write the probe**

```python
# probe_wrong_year_postings.py
# READ-ONLY probe — has any posted transaction landed in a year its date does
# not cover?
#
#   cd <worktree>
#   python3 manage.py shell < probe_wrong_year_postings.py
#
# Pure read + print. No writes of any kind.
#
# WHY THIS GATES THE CHANGE. resolve_fy_for_txn currently falls back to the most
# recent POSTABLE year when no postable year covers a transaction's date, so such
# a transaction posted somewhere its date has nothing to do with. After the
# change it resolves to None, which means the aggregation excludes it — and the
# rebuild would then zero the trial-balance lines it created. That is data loss
# on historical client ledgers, so the count has to be zero before proceeding.
#
# Two ways in, reported separately because they need different decisions:
#   NON_POSTABLE  the date IS covered by one of the entity's years, but that year
#                 is finalised or reopened, so it is not a posting target
#   NO_YEAR       no year of the entity covers the date at all (a statement
#                 running past the last year that exists)

from core.models import Entity, FinancialYear
from core.txn_periods import entity_financial_years, parse_txn_date
from review.models import PendingTransaction

RULE = "=" * 78
non_postable, no_year, unparseable = [], [], []

for entity in Entity.objects.all().order_by("entity_name"):
    postable = entity_financial_years(entity)
    if not postable:
        continue
    all_years = list(FinancialYear.objects.filter(entity=entity))
    posted = PendingTransaction.objects.filter(
        job__entity=entity, is_confirmed=True, posted_to_tb=True,
    ).select_related("job")

    for txn in posted:
        txn_date = parse_txn_date(txn.date)
        if not txn_date:
            unparseable.append((entity, txn))
            continue
        if any(fy.start_date <= txn_date <= fy.end_date for fy in postable):
            continue  # resolves correctly today and after the change
        covering = [fy for fy in all_years if fy.start_date <= txn_date <= fy.end_date]
        landed = max(postable, key=lambda f: f.end_date)
        if covering:
            non_postable.append((entity, txn, covering[0], landed))
        else:
            no_year.append((entity, txn, landed))

print(RULE)
print("WRONG-YEAR POSTING PROBE — read-only")
print(RULE)

print(f"\nNON_POSTABLE — date falls in a year that cannot receive postings: {len(non_postable)}")
for entity, txn, covering, landed in non_postable[:40]:
    print(f"  {entity.entity_name} | {txn.date} {txn.amount} {txn.confirmed_code} | "
          f"date is in {covering.year_label} (status {covering.status!r}) | "
          f"posted to {landed.year_label}")
if len(non_postable) > 40:
    print(f"  … and {len(non_postable) - 40} more")

print(f"\nNO_YEAR — no financial year covers the date: {len(no_year)}")
for entity, txn, landed in no_year[:40]:
    print(f"  {entity.entity_name} | {txn.date} {txn.amount} {txn.confirmed_code} | "
          f"posted to {landed.year_label}")
if len(no_year) > 40:
    print(f"  … and {len(no_year) - 40} more")

print(f"\nUNPARSEABLE DATE (informational — behaviour unchanged by this work): {len(unparseable)}")

print("\n" + RULE)
total = len(non_postable) + len(no_year)
if total == 0:
    print("ZERO wrong-year postings. Safe to proceed with the strict rule.")
else:
    print(f"{total} wrong-year posting(s). STOP — do not change the rule.")
    print("The rebuild would zero the trial-balance lines these created.")
print("Nothing was written.")
print(RULE)
```

- [ ] **Step 2: Verify the probe writes nothing**

Run: `grep -nE "\.save\(|\.create\(|\.update\(|\.delete\(|bulk_" probe_wrong_year_postings.py`
Expected: no matches outside comments.

- [ ] **Step 3: Run it against production, read-only**

Run: `python3 manage.py shell < probe_wrong_year_postings.py`

Elio's stated position is that the count is zero. Note the classifier on this host may refuse a
`manage.py` invocation against production; if so, hand the command to Elio to run and paste back.

- [ ] **Step 4: Record the result in this plan, whatever it says**

Replace this line with the actual output's summary counts and the date, then commit. If either
`NON_POSTABLE` or `NO_YEAR` is non-zero, **stop here and escalate** rather than continuing.

> **Probe result:** ENTITIES examined: 3 (postable), skipped: 10 (no postable, 0 txns) | TRANSACTIONS classified: 0, unparseable: 0. VACUOUS — zero transactions to classify. Verdict triggers only on ledger ruling, not measurement. (2026-08-17)

- [ ] **Step 5: Commit**

```bash
git add probe_wrong_year_postings.py docs/superpowers/plans/2026-08-17-strict-financial-year-resolution.md
git commit -m "test: probe for transactions posted into a year their date does not cover"
```

---

### Task 2: Strict resolution

**Files:**
- Modify: `core/txn_periods.py:50-84` (`resolve_fy_for_txn`)
- Modify: `core/tests_txn_periods.py`

**Interfaces:**
- Consumes: `core.txn_periods.parse_txn_date`, `entity_financial_years`; fixtures from `core.tests_bank_tb_fixtures`.
- Produces: `resolve_fy_for_txn(txn, fys=None) -> FinancialYear | None`. The signature is unchanged; what changes is that it now returns `None` when a *parseable* date matches no postable year. Task 3 and Task 4 depend on this returning `None` rather than a wrong year.

- [ ] **Step 1: Write the failing tests**

Append to `core/tests_txn_periods.py`:

```python
@override_settings(STORAGES=STORAGES_OVERRIDE)
class StrictYearResolutionTests(TestCase):
    """A transaction posts to the year its date falls in, or not at all.

    resolve_fy_for_txn used to fall back to the most recent postable year when
    nothing covered the date, so a 15 July 2026 transaction posted into FY2026 —
    overstating the year and corrupting its BAS. The fallback survives for dates
    that cannot be parsed, deliberately: there is nothing to reason from.
    """

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)        # FY2026: 2025-07-01 .. 2026-06-30
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _txn(self, date_str):
        return make_txn(self.job, date_str=date_str, amount="-110.00", code="0400")

    def test_a_date_inside_an_open_year_resolves_to_it(self):
        self.assertEqual(resolve_fy_for_txn(self._txn("2025-08-14")), self.fy)

    def test_a_date_beyond_every_year_resolves_to_nothing(self):
        """The reported defect: 15 July 2026 with no FY2027 in existence."""
        self.assertIsNone(resolve_fy_for_txn(self._txn("2026-07-15")))

    def test_a_date_inside_a_finalised_year_resolves_to_nothing(self):
        """A finalised year is not a posting target, so this must not fall back."""
        old = make_fy(self.entity, label="FY2023",
                      start=date(2022, 7, 1), end=date(2023, 6, 30))
        old.status = "finalised"
        old.save(update_fields=["status"])
        self.assertIsNone(resolve_fy_for_txn(self._txn("2023-01-15")))

    def test_a_date_inside_a_reopened_year_resolves_to_nothing(self):
        """'reopened' is also outside POSTABLE_FY_STATUSES — same treatment."""
        old = make_fy(self.entity, label="FY2024",
                      start=date(2023, 7, 1), end=date(2024, 6, 30))
        old.status = "reopened"
        old.save(update_fields=["status"])
        self.assertIsNone(resolve_fy_for_txn(self._txn("2024-01-15")))

    def test_an_unparseable_date_still_falls_back(self):
        """Unchanged on purpose: with unreadable text there is nothing to use."""
        self.assertEqual(resolve_fy_for_txn(self._txn("n/a")), self.fy)

    def test_the_fallback_picks_the_most_recent_year_as_before(self):
        later = make_fy(self.entity, label="FY2027",
                        start=date(2026, 7, 1), end=date(2027, 6, 30))
        later.status = "draft"
        later.save(update_fields=["status"])
        self.assertEqual(resolve_fy_for_txn(self._txn("garbage")), later)

    def test_a_date_beyond_every_year_resolves_once_that_year_exists(self):
        """The workflow this exists to support: allocate now, post when the year opens."""
        txn = self._txn("2026-07-15")
        self.assertIsNone(resolve_fy_for_txn(txn))
        later = make_fy(self.entity, label="FY2027",
                        start=date(2026, 7, 1), end=date(2027, 6, 30))
        later.status = "draft"
        later.save(update_fields=["status"])
        self.assertEqual(resolve_fy_for_txn(txn), later)
```

Add to that file's imports if absent:

```python
from datetime import date
from django.test import TestCase, override_settings
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job, make_txn,
)
from core.txn_periods import resolve_fy_for_txn
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_txn_periods.StrictYearResolutionTests -v 2`
Expected: FAIL on `test_a_date_beyond_every_year_resolves_to_nothing`, `..._finalised_year...`, `..._reopened_year...` and `..._once_that_year_exists` — each returns FY2026 instead of `None`. The two fallback tests and the open-year test PASS already; they are the regression guards.

- [ ] **Step 3: Make the rule strict**

In `core/txn_periods.py`, replace the tail of `resolve_fy_for_txn` (currently the `txn_date`
block followed by the unconditional `return max(...)`) with:

```python
    txn_date = parse_txn_date(txn.date)
    if not txn_date:
        # Unparseable date: there is nothing to reason from, so keep the
        # historical fallback. Deliberately NOT changed — see the spec's
        # decision 3. Making these unpostable would strand transactions whose
        # date may not be editable, and unreadable-date rows are the ones most
        # likely to be already posted through this fallback.
        return max(fys, key=lambda f: f.end_date)

    for fy in fys:
        if fy.start_date <= txn_date <= fy.end_date:
            return fy

    # The date is known and no POSTABLE year covers it. Posting it anywhere
    # would put it in a year it has nothing to do with — which is how a
    # statement running to 31 July 2026 overstated FY2026. Returning None means
    # "do not post": _post_confirmed_txn_to_tb returns False, and every
    # aggregation caller excludes it, so the rebuild can never zero a line for a
    # transaction it also refuses to post.
    return None
```

Update the function's docstring: the paragraph promising a fallback for dates "outside every year"
is now wrong. State the three outcomes — the covering postable year, `None` for a known date no
postable year covers, and the most recent year only for an unparseable date.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_txn_periods -v 2`
Expected: PASS, all tests in the module.

- [ ] **Step 5: Verify the rebuild did not change behaviour**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bank_tb_rebuild core.tests_bank_tb_partition core.tests_bank_contra_fy core.tests_audit_bank_tb_desync -v 1`
Expected: PASS. These pin the rebuild, the partition, the contra and the audit. If any fails, the
strict rule has changed what a year aggregates for a fixture whose dates sit inside its year —
which would mean the fixtures rely on the fallback, and that needs understanding before going on.

- [ ] **Step 6: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the baseline (47 failures + 11 errors with staticfiles collected).

- [ ] **Step 7: Commit**

```bash
git add core/txn_periods.py core/tests_txn_periods.py
git commit -m "fix: a transaction posts to the year its date falls in, or not at all"
```

---

### Task 3: Explain why a transaction cannot post

**Files:**
- Modify: `core/txn_periods.py` (add `unpostable_reason`)
- Modify: `core/tests_txn_periods.py`

**Interfaces:**
- Consumes: Task 2's `resolve_fy_for_txn`.
- Produces: `unpostable_reason(txn) -> str | None` — `None` when the transaction resolves to a year, otherwise a human-readable sentence. Tasks 4 and 5 call it to populate a `post_warning` field.

- [ ] **Step 1: Write the failing tests**

Append to `core/tests_txn_periods.py`:

```python
@override_settings(STORAGES=STORAGES_OVERRIDE)
class UnpostableReasonTests(TestCase):
    """The reason is derived from the date and the entity's years, never stored.

    is_confirmed=True with posted_to_tb=False already represents "confirmed but
    not posted", so no model field is added. Only the explanation is new.
    """

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _txn(self, date_str):
        return make_txn(self.job, date_str=date_str, amount="-110.00", code="0400")

    def test_a_postable_transaction_has_no_reason(self):
        self.assertIsNone(unpostable_reason(self._txn("2025-08-14")))

    def test_no_year_covers_the_date(self):
        reason = unpostable_reason(self._txn("2026-07-15"))
        self.assertIn("No financial year", reason)
        self.assertIn("15 Jul 2026", reason)

    def test_the_covering_year_is_finalised(self):
        old = make_fy(self.entity, label="FY2023",
                      start=date(2022, 7, 1), end=date(2023, 6, 30))
        old.status = "finalised"
        old.save(update_fields=["status"])
        reason = unpostable_reason(self._txn("2023-01-15"))
        self.assertIn("FY2023", reason)
        self.assertIn("finalised", reason)

    def test_the_covering_year_is_reopened(self):
        """The message names the actual status, so 'reopened' is not mislabelled."""
        old = make_fy(self.entity, label="FY2024",
                      start=date(2023, 7, 1), end=date(2024, 6, 30))
        old.status = "reopened"
        old.save(update_fields=["status"])
        reason = unpostable_reason(self._txn("2024-01-15"))
        self.assertIn("FY2024", reason)
        self.assertIn("reopened", reason)

    def test_an_unparseable_date_has_no_reason_because_it_still_posts(self):
        self.assertIsNone(unpostable_reason(self._txn("n/a")))
```

Add `unpostable_reason` to that file's import from `core.txn_periods`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_txn_periods.UnpostableReasonTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'unpostable_reason'`.

- [ ] **Step 3: Add the function**

Append to `core/txn_periods.py`:

```python
def unpostable_reason(txn):
    """Why this transaction cannot post, or None if it can.

    Derived rather than stored: is_confirmed=True with posted_to_tb=False
    already records the state, and the explanation follows from the date plus
    the entity's years. Called only when posting was skipped, so the ordinary
    path pays for no extra query.

    Looks at ALL of the entity's years, not just the postable ones, because that
    is what distinguishes the two cases — a year that exists but cannot receive
    postings, versus no year at all. Only the second is fixed by creating a year,
    so the messages must not be interchangeable.
    """
    from core.models import FinancialYear

    if resolve_fy_for_txn(txn) is not None:
        return None

    txn_date = parse_txn_date(txn.date)
    if not txn_date:
        # An unparseable date still posts, via the fallback, so it is not
        # unpostable and resolve_fy_for_txn above would have returned a year.
        # Reaching here means the entity has no postable year at all.
        return "This entity has no financial year open for posting."

    entity = txn.job.entity if txn.job else None
    if entity is None:
        return "This transaction is not attached to an entity."

    shown = txn_date.strftime("%d %b %Y")
    covering = FinancialYear.objects.filter(
        entity=entity, start_date__lte=txn_date, end_date__gte=txn_date,
    ).first()
    if covering:
        return (
            f"{covering.year_label} covers {shown} but its status is "
            f"'{covering.status}', so it cannot receive postings."
        )
    return (
        f"No financial year covers {shown}. Create that year to post this "
        f"transaction — it will post itself once the year exists."
    )
```

Note the deliberate wording: the finalised/reopened message names the year's **actual status**
rather than hardcoding "finalised", because `POSTABLE_FY_STATUSES` excludes `reopened` too.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_txn_periods -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/txn_periods.py core/tests_txn_periods.py
git commit -m "feat: explain why a transaction cannot post"
```

---

### Task 4: Say so on the review screen

**Files:**
- Modify: `review/views.py` (`confirm_transaction`'s success `JsonResponse`, currently returning `status`/`confirmed_count`/`flagged_count`/`progress_percent`/`gst_amount`/`net_amount`/`live_matches`)
- Modify: `templates/review/review_detail.html:946` (`confirmTransaction`'s success handler) and the transaction row markup at `:545-556`
- Create: `review/tests_unpostable_confirm.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: `posted` (bool) and `post_warning` (str, `""` when posted) on the confirm response. Task 5 mirrors the same two field names.

- [ ] **Step 1: Write the failing test**

```python
# review/tests_unpostable_confirm.py
"""Confirming a transaction that cannot post says so, and posts nothing.

The workflow this supports: allocate a whole statement in one sitting, lodge the
BAS for the year that exists, and let the out-of-year rows post themselves when
their year is opened. Before the strict rule they posted into the most recent
open year instead, overstating it.
"""
import json
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class UnpostableConfirmTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)          # FY2026: 2025-07-01 .. 2026-06-30
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        # Dated 15 July 2026 — one FY past the only year that exists.
        self.txn = make_txn(self.job, date_str="2026-07-15", amount="-1100.00",
                            code="", tax_type="")
        self.txn.is_confirmed = False
        self.txn.save(update_fields=["is_confirmed"])

        User = get_user_model()
        self.user = User.objects.create_user(
            username="unpostable", password="pw", email="u@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-unpostable", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _confirm(self):
        return self.client.post(
            reverse("review:confirm_transaction", args=[self.txn.pk]),
            data=json.dumps({"confirmed_code": "0400", "confirmed_name": "Office",
                             "confirmed_tax_type": "GST on Expenses"}),
            content_type="application/json",
            secure=True,
        )

    def test_it_confirms_but_does_not_post(self):
        response = self._confirm()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["posted"])
        self.assertIn("No financial year", body["post_warning"])

        self.txn.refresh_from_db()
        self.assertTrue(self.txn.is_confirmed, "the allocation is still recorded")
        self.assertFalse(self.txn.posted_to_tb)

    def test_no_trial_balance_line_is_created_anywhere(self):
        self._confirm()
        self.assertEqual(
            TrialBalanceLine.objects.filter(financial_year=self.fy).count(), 0,
            "a July 2026 transaction must not touch FY2026's ledger",
        )

    def test_a_postable_transaction_reports_posted(self):
        inside = make_txn(self.job, date_str="2025-08-14", amount="-220.00",
                          code="", tax_type="")
        inside.is_confirmed = False
        inside.save(update_fields=["is_confirmed"])
        response = self.client.post(
            reverse("review:confirm_transaction", args=[inside.pk]),
            data=json.dumps({"confirmed_code": "0400", "confirmed_name": "Office",
                             "confirmed_tax_type": "GST Free Expenses"}),
            content_type="application/json",
            secure=True,
        )
        body = response.json()
        self.assertTrue(body["posted"])
        self.assertEqual(body["post_warning"], "")
        self.assertIsNotNone(bs_line(self.fy, "0400"))

    def test_it_posts_to_the_right_year_once_that_year_exists(self):
        self._confirm()
        fy27 = make_fy(self.entity, label="FY2027",
                       start=date(2026, 7, 1), end=date(2027, 6, 30))
        fy27.status = "draft"
        fy27.save(update_fields=["status"])

        response = self._confirm()          # same allocation, confirmed again
        self.assertTrue(response.json()["posted"])
        self.assertEqual(bs_line(fy27, "0400").debit, D("1000.00"))
        self.assertEqual(
            TrialBalanceLine.objects.filter(financial_year=self.fy).count(), 0,
            "FY2026 stays untouched",
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test review.tests_unpostable_confirm -v 2`
Expected: FAIL — `KeyError: 'posted'`, because the response does not carry the field yet. Note
`test_no_trial_balance_line_is_created_anywhere` may already pass thanks to Task 2; that is fine,
it is the regression guard.

- [ ] **Step 3: Report it in the confirm response**

In `review/views.py:confirm_transaction`, the posting block already distinguishes the first post
from a correction. Capture whether the transaction ended up posted, and why not. Immediately
before the success `JsonResponse`, add:

```python
    # A transaction whose date falls outside every postable year confirms
    # normally but posts nowhere — see core/txn_periods.unpostable_reason. Report
    # it so the row can say so, instead of the user believing the ledger moved.
    from core.txn_periods import unpostable_reason

    txn.refresh_from_db(fields=["posted_to_tb"])
    post_warning = "" if txn.posted_to_tb else (unpostable_reason(txn) or "")
```

and extend the response dict with:

```python
        "posted": txn.posted_to_tb,
        "post_warning": post_warning,
```

`refresh_from_db` on that one field is deliberate: `_post_confirmed_txn_to_tb` sets `posted_to_tb`
through its own path, so the in-memory copy may not reflect it.

- [ ] **Step 4: Run the test to verify it passes**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test review.tests_unpostable_confirm -v 2`
Expected: PASS, 4 tests.

- [ ] **Step 5: Show it on the row**

In `templates/review/review_detail.html`, inside `confirmTransaction`'s `if (data.status === 'ok')`
block (at `:954`), after the `status-icon` line, add:

```javascript
            // A confirmed row that did not post must say so on the spot. The
            // status icon alone would show a tick and imply the ledger moved.
            const warnCell = row.querySelector('.post-warning');
            if (warnCell) {
                if (data.post_warning) {
                    warnCell.innerHTML =
                        '<span class="badge bg-warning text-dark" title="'
                        + data.post_warning.replace(/"/g, '&quot;')
                        + '">Not posted</span>';
                } else {
                    warnCell.innerHTML = '';
                }
            }
```

In the row markup, add a cell to carry it. Put it beside the existing status icon cell so it is
visible without horizontal scrolling, and match the surrounding cell classes rather than inventing
new ones. Server-render the same badge for rows that are already in this state, so it survives a
reload:

```html
<td class="post-warning">
  {% if txn.is_confirmed and not txn.posted_to_tb %}
    <span class="badge bg-warning text-dark"
          title="{{ txn.post_warning|default:'This transaction is confirmed but has not posted to the trial balance.' }}">Not posted</span>
  {% endif %}
</td>
```

For the server-rendered case, `review_detail` must annotate each transaction with
`post_warning`. In `review/views.py:review_detail`, after the transactions queryset is built:

```python
    # Only for rows already in the confirmed-but-unposted state; unpostable_reason
    # issues a query per transaction, so it is not called for the common case.
    from core.txn_periods import unpostable_reason
    for t in transactions:
        t.post_warning = (
            unpostable_reason(t) if (t.is_confirmed and not t.posted_to_tb) else ""
        )
```

- [ ] **Step 6: Verify the template still compiles and renders**

Run:
```bash
DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
django.setup()
from django.template.loader import get_template
get_template('review/review_detail.html')
print('template compiled OK')
"
```
Expected: `template compiled OK`. A template syntax error here 500s the review page, and no Django
test in this plan renders it.

- [ ] **Step 7: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the baseline.

- [ ] **Step 8: Commit**

```bash
git add review/views.py review/tests_unpostable_confirm.py templates/review/review_detail.html
git commit -m "feat: say when a confirmed transaction did not post"
```

---

### Task 5: Say so on the BAS reallocation endpoints

**Files:**
- Modify: `core/views_bas.py` (`bas_reallocate_transaction`'s success `JsonResponse`, and `bas_bulk_reallocate`'s)
- Create: `core/tests_bas_reallocate_unpostable.py`

**Interfaces:**
- Consumes: Tasks 2 and 3. Uses the same `posted` / `post_warning` field names Task 4 introduced.
- Produces: nothing later tasks import.

- [ ] **Step 1: Write the failing test**

```python
# core/tests_bas_reallocate_unpostable.py
"""Reallocating a transaction that cannot post says so.

A reallocation changes a transaction's account and tax treatment but never its
date, so a transaction that could not post still cannot. The response must not
imply the ledger moved.
"""
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job, make_txn,
)

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasReallocateUnpostableTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        self.txn = make_txn(self.job, date_str="2026-07-15", amount="-1100.00",
                            code="0400", tax_type="GST on Expenses", gst="100.00")

        User = get_user_model()
        self.user = User.objects.create_user(
            username="realloc_unpostable", password="pw", email="ru@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-ru", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def test_single_reallocation_reports_it_did_not_post(self):
        response = self.client.post(
            reverse("core:bas_reallocate_transaction", args=[self.fy.pk]),
            data=json.dumps({"txn_id": str(self.txn.pk), "account_code": "0450",
                             "account_name": "Repairs",
                             "tax_type": "GST on Expenses"}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["posted"])
        self.assertIn("No financial year", body["post_warning"])
        self.assertEqual(
            TrialBalanceLine.objects.filter(financial_year=self.fy).count(), 0)

    def test_bulk_reallocation_reports_how_many_did_not_post(self):
        response = self.client.post(
            reverse("core:bas_bulk_reallocate", args=[self.fy.pk]),
            data=json.dumps({"txn_ids": [str(self.txn.pk)], "account_code": "0450",
                             "account_name": "Repairs",
                             "tax_type": "GST on Expenses"}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unposted_count"], 1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bas_reallocate_unpostable -v 2`
Expected: FAIL — `KeyError: 'posted'` and `KeyError: 'unposted_count'`.

- [ ] **Step 3: Report it from the single endpoint**

In `bas_reallocate_transaction`, the rebuild block already computes `target_fy = resolve_fy_for_txn(txn)`.
Extend the success response dict with:

```python
        "posted": bool(target_fy),
        "post_warning": "" if target_fy else (unpostable_reason(txn) or ""),
```

`unpostable_reason` is imported at module level alongside the existing
`from core.txn_periods import flag_period_amended, resolve_fy_for_txn`.

- [ ] **Step 4: Report it from the bulk endpoint**

In `bas_bulk_reallocate`, the loop already calls `resolve_fy_for_txn(txn)` when collecting
`touched_fys`. Count the ones that resolve to nothing and report the total, rather than a message
per row:

```python
    unposted_count = 0
```

before the loop; inside the loop, where `target_fy` is resolved:

```python
        if target_fy is None:
            unposted_count += 1
```

and extend the success response dict with:

```python
        "unposted_count": unposted_count,
```

A count rather than a list of reasons is deliberate: a bulk reallocation can touch hundreds of
rows, and the per-row reasons are already visible on the review screen from Task 4.

- [ ] **Step 5: Run the test to verify it passes**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bas_reallocate_unpostable core.tests_bas_reallocate_posting -v 2`
Expected: PASS. The second module is included so a change to the shared response shape cannot
break the Task 9 tests from the previous project.

- [ ] **Step 6: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the baseline.

- [ ] **Step 7: Commit**

```bash
git add core/views_bas.py core/tests_bas_reallocate_unpostable.py
git commit -m "feat: report unposted transactions from the BAS reallocation endpoints"
```

---

### Task 6: Pin the BAS date window

**Files:**
- Create: `core/tests_bas_date_window.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: nothing.

The spec establishes that the BAS needs no change: `_confirmed_transactions` selects candidates by
the **job's** financial year, which is a different attribution rule from posting's, but
`calculate_gst_for_period` then bounds them by `period_start or fy.start_date` through
`period_end or fy.end_date`. So an out-of-year transaction cannot reach the BAS through any period
selection. Two rules coexisting is exactly the kind of thing that drifts, so it gets a test rather
than a comment.

- [ ] **Step 1: Write the test**

```python
# core/tests_bas_date_window.py
"""An out-of-year transaction cannot reach the BAS, by any period selection.

The BAS selects candidate transactions by their JOB's financial year
(core/bas_utils.py:_confirmed_transactions) while posting selects by transaction
DATE. Those are different rules, and what stops them diverging is the date window
calculate_gst_for_period applies — period_start or fy.start_date through
period_end or fy.end_date, so even the full-year view is bounded by the year.

If this test ever fails, a July 2026 transaction sitting in an FY2026 job has
started appearing in FY2026's BAS while posting refuses to post it — a BAS that
disagrees with the ledger, which is the defect class the desync project closed.
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from core.bas_utils import calculate_gst_for_period
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job, make_txn,
)

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasDateWindowTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)          # 2025-07-01 .. 2026-06-30
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        # Inside the year: 1,100 gross of GST-bearing income.
        make_txn(self.job, date_str="2025-08-14", amount="1100.00",
                 code="0510", tax_type="GST on Income", gst="100.00")

    def _full_year_1a(self):
        return calculate_gst_for_period(self.fy)["bas_data"]["1A"]

    def test_the_full_year_view_excludes_a_transaction_dated_after_the_year(self):
        before = self._full_year_1a()
        # Same shape, dated one FY later, in the SAME FY2026 job.
        make_txn(self.job, date_str="2026-07-15", amount="2200.00",
                 code="0510", tax_type="GST on Income", gst="200.00")
        self.assertEqual(
            self._full_year_1a(), before,
            "a July 2026 transaction must not reach FY2026's BAS",
        )

    def test_the_full_year_view_excludes_a_transaction_dated_before_the_year(self):
        before = self._full_year_1a()
        make_txn(self.job, date_str="2024-09-10", amount="3300.00",
                 code="0510", tax_type="GST on Income", gst="300.00")
        self.assertEqual(self._full_year_1a(), before)
```

- [ ] **Step 2: Run the test**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bas_date_window -v 2`
Expected: PASS immediately — this pins existing behaviour rather than driving a change. If it
FAILS, the spec's claim that the BAS needs no change is wrong; **stop and report** rather than
adjusting the test to match.

- [ ] **Step 3: Commit**

```bash
git add core/tests_bas_date_window.py
git commit -m "test: an out-of-year transaction cannot reach the BAS"
```

---

### Task 7: Documents, verification and the PR

**Files:**
- Modify: `docs/superpowers/specs/2026-08-17-strict-financial-year-resolution-design.md` (status line)
- Modify: `core/txn_periods.py` (module docstring)
- Modify: `docs/superpowers/plans/2026-08-17-strict-financial-year-resolution.md` (this file — the probe result)

- [ ] **Step 1: Update the module docstring**

`core/txn_periods.py`'s module docstring describes four consumers needing one rule. Add that the
rule now has an outcome it did not have before — "no year, do not post" — and that the fallback
survives only for unparseable dates. A reader who sees `None` returned needs to know it is
intentional and what it means.

- [ ] **Step 2: Mark the spec implemented**

Change the spec's `**Status:**` line from `design approved, not implemented` to implemented, naming
the commits, in the same style as
`docs/superpowers/plans/2026-08-16-desync-repair-gate-record.md`.

- [ ] **Step 3: Run the full verification**

```bash
DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1
cd e2e && STATEMENTHUB_ROOT=<worktree> npx playwright test tier2/bank_to_bas_company.spec.ts --workers=1
cd e2e && STATEMENTHUB_ROOT=<worktree> npx playwright test --project=tier1 --reporter=line
```

Expected: no new failures against the baseline; Tier 2's 17 tests pass; Tier 1's 215 pass. Before
the first e2e run, confirm `staticfiles/` exists in the worktree and that
`sh_e2e_template` carries every migration on this branch — see "Running Tier 2 from a git
worktree" in `e2e/README.md`. This plan adds no migration, so the template should already be
current.

- [ ] **Step 4: Commit and open the PR**

```bash
git add -A
git commit -m "docs: strict financial-year resolution is implemented"
git push git@github.com:MCS-brains-trust/mcs-platform.git fix/strict-fy-resolution
```

`origin` is an HTTPS remote and there is no `gh` CLI, so push with the explicit SSH URL and open
the PR from the link GitHub prints. **Do not merge without Elio** — merging to `main` auto-deploys
and restarts gunicorn.

The PR body must state plainly: the fallback survives for unparseable dates; the pre-flight probe's
result; and that no repair of historical wrong-year postings is included.

---

## Self-Review

**Spec coverage.** The rule → Task 2. `unpostable_reason` → Task 3. Surfacing on the confirm path →
Task 4. Surfacing on the reallocation endpoints → Task 5. "The BAS needs no change", which the spec
says deserves a test rather than a comment → Task 6. The pre-flight probe → Task 1, as a gate.
Documents and verification → Task 7. Every "Out of scope" item stays out: no import-filter change,
no BAS attribution change, no auto-created years, no historical repair, and unparseable dates keep
the fallback (pinned by two tests in Task 2).

**Known soft spots, stated rather than hidden:**

1. **Task 4 Step 5 does not give exact row markup** for where the `post-warning` cell goes, because
   the row's column layout has not been read end to end and adding a cell in the wrong place
   breaks the header/body alignment. The implementer must read the row's `<td>` sequence and its
   `<thead>` first, and add a matching header cell.
2. **Task 4's `review_detail` annotation issues a query per confirmed-but-unposted row.** That is
   bounded by how many such rows exist, which should be near zero in normal use, but a job full of
   out-of-year transactions would feel it. If that shows up, the fix is to resolve the entity's
   years once and pass them in — `resolve_fy_for_txn` already accepts an `fys` argument for exactly
   this, though `unpostable_reason` does not yet thread it through.
3. **The e2e suite is untouched.** The spec permits this: the bank-to-BAS fixture's six
   transactions all land in October and its BAS figures are hand-computed, so adding an out-of-year
   transaction would change figures the suite pins deliberately. The Django integration tests in
   Task 4 cover the behaviour. Do not bless new e2e figures to accommodate a test.
4. **`POSTABLE_FY_STATUSES` contains `"finished"`, which is not a `FinancialYear.Status` value.**
   Left alone deliberately — it matches nothing, so it changes no behaviour, but it means the
   postable set is draft + in_review only. Worth its own look; not in this plan.
