# BAS Period Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A BAS period whose GST came from a posted cashbook journal reads as complete, auto-selects on the dashboard, and lodges without an override reason — while every period that has bank activity keeps today's month-by-month rule exactly.

**Architecture:** `get_bank_coverage` stays as the honest bank primitive. A new `get_period_coverage` wraps it and answers the different question "is this period accounted for?", adding `source` and `journal_refs` to the returned dict. Four call sites switch to it. `compute_period_status` needs no logic change — it already maps `complete → ready`, so the dashboard's auto-selection fixes itself as a consequence.

**Tech Stack:** Django 5, Postgres (sqlite for tests), Decimal arithmetic, Bootstrap 5 templates.

**Spec:** `docs/superpowers/specs/2026-09-01-bas-period-coverage-design.md`

## Global Constraints

- **Branch:** `feat/bas-period-coverage`. Work in the worktree, never in `/opt/statementhub` — that checkout is what gunicorn serves.
- **Run tests with a sqlite override and a throwaway secret**, never against the default DB:
  `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core -v 2`
- **Run `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py collectstatic --noinput` once** before treating any failure count as a baseline — a fresh worktree has no `staticfiles/` and template-rendering tests fail on `Missing staticfiles manifest entry`.
- **The suite has a pre-existing failure baseline.** Compare failure *sets*, not counts. Capture the baseline before changing anything.
- **A journalled period reports `months: []` and `missing: []`.** Month-level coverage is not derivable from a journal — `JournalLine` has no date, only `AdjustingJournal.journal_date`. Never synthesise a months list for a journalled period.
- **Only `AdjustingJournal.JournalType.CASHBOOK` with `status="posted"` and `journal_date` inside the period counts.** Not general journals, not drafts, not journals dated after period end.
- **A cashbook journal need not carry GST.** An all-`N-T` quarter is a legitimate nil BAS.
- **Do not rename `get_bank_coverage`** and do not change what it returns.

---

## File Structure

- `core/bas_utils.py` — add `get_period_coverage` next to `get_bank_coverage`; change one line inside `compute_period_status`.
- `core/views_bas.py` — swap the import and three call sites.
- `templates/core/gst_activity_statement.html` — two branches so a journalled period does not claim bank coverage.
- `core/tests_bas_period_coverage.py` — new, the whole behaviour of this change.

---

### Task 1: `get_period_coverage`

**Files:**
- Modify: `core/bas_utils.py` (add after `get_bank_coverage`, which ends just before `def compute_period_status` at line 244)
- Test: `core/tests_bas_period_coverage.py` (create)

**Interfaces:**
- Consumes: `get_bank_coverage(fy, period_start, period_end)` — returns `{"status": "complete"|"partial"|"none", "months": [{"month": str, "covered": bool}], "missing": [str]}`
- Produces: `get_period_coverage(fy, period_start, period_end) -> dict` with the same three keys plus `"source": "bank"|"journal"|"none"` and `"journal_refs": list[str]`

- [ ] **Step 1: Write the failing tests**

Create `core/tests_bas_period_coverage.py`:

```python
"""Is this BAS period accounted for?

get_bank_coverage answers a bank question. Once a cashbook journal carries its
own GST, "does this period have complete source data" stops being the same
question, and get_period_coverage is the one the BAS page actually needs.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.bas_utils import (
    get_bank_coverage, get_period_coverage, compute_period_status,
)
from core.models import (
    AdjustingJournal, Entity, EntityChartOfAccount, FinancialYear,
)
from review.models import PendingTransaction, ReviewJob

Q2_START = date(2025, 10, 1)
Q2_END = date(2025, 12, 31)


class PeriodCoverageTestBase(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Cashbook Client",
            entity_type=Entity.EntityType.SOLE_TRADER,
            is_gst_registered=True,
        )
        for code, name, tax, section in [
            ("105", "Sales", "GST", "revenue"),
            ("3380", "GST payable control account", "", "liabilities"),
            ("4080", "Drawings", "", "capital_accounts"),
        ]:
            EntityChartOfAccount.objects.create(
                entity=self.entity, account_code=code, account_name=name,
                tax_code=tax, section=section,
            )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )

    def make_journal(self, *, journal_date=date(2025, 12, 31), status="posted",
                     journal_type=None, reference="JE-001"):
        """A balanced two-line cashbook journal. Not split -- these tests are
        about period evidence, not about GST arithmetic."""
        if journal_type is None:
            journal_type = AdjustingJournal.JournalType.CASHBOOK
        j = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number=reference,
            journal_type=journal_type, journal_date=journal_date,
            description="Oct-Dec cash book", status=status,
        )
        j.lines.create(line_number=1, account_code="105", account_name="Sales",
                       debit=Decimal("0"), credit=Decimal("23187.00"),
                       tax_code="GST")
        j.lines.create(line_number=2, account_code="4080",
                       account_name="Drawings", debit=Decimal("23187.00"),
                       credit=Decimal("0"), tax_code="N-T")
        return j

    def make_bank_months(self, *iso_dates):
        """One confirmed bank transaction per given date."""
        job = ReviewJob.objects.create(
            entity=self.entity, financial_year=self.fy,
            client_name="Cashbook Client", is_gst_registered=True,
        )
        for i, d in enumerate(iso_dates):
            PendingTransaction.objects.create(
                job=job, date=d, description="Bank txn %d" % i,
                amount=Decimal("110.00"), gst_amount=Decimal("10.00"),
                net_amount=Decimal("100.00"),
                confirmed_code="105", confirmed_name="Sales",
                confirmed_tax_type="GST on Income",
                confirmed_gst_amount=Decimal("10.00"), is_confirmed=True,
            )
        return job


class JournalledPeriodTest(PeriodCoverageTestBase):
    def test_a_posted_cashbook_journal_makes_the_period_complete(self):
        self.make_journal()
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "complete")
        self.assertEqual(cov["source"], "journal")
        self.assertEqual(cov["journal_refs"], ["JE-001"])

    def test_a_journalled_period_names_no_months(self):
        """Month coverage is not derivable from a journal -- JournalLine has no
        date. Reporting months here would be inventing data."""
        self.make_journal()
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["months"], [])
        self.assertEqual(cov["missing"], [])

    def test_a_journalled_period_computes_as_ready(self):
        self.make_journal()
        self.assertEqual(
            compute_period_status(self.fy, Q2_START, Q2_END), "ready",
        )

    def test_a_cashbook_journal_with_no_gst_still_counts(self):
        """An all-N-T quarter is a legitimate nil BAS."""
        j = self.make_journal()
        j.lines.update(tax_code="N-T")
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "complete")
        self.assertEqual(cov["source"], "journal")


class JournalsThatDoNotCountTest(PeriodCoverageTestBase):
    def test_a_draft_cashbook_journal_does_not_count(self):
        self.make_journal(status="draft")
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "none")
        self.assertEqual(cov["source"], "none")

    def test_a_general_journal_carrying_gst_does_not_count(self):
        """A general journal makes no claim to be the period's cash book."""
        self.make_journal(journal_type=AdjustingJournal.JournalType.GENERAL)
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "none")
        self.assertEqual(cov["source"], "none")

    def test_a_journal_dated_after_period_end_does_not_count(self):
        self.make_journal(journal_date=date(2026, 1, 5))
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "none")
        self.assertEqual(cov["source"], "none")


class BankPeriodsUnchangedTest(PeriodCoverageTestBase):
    def test_a_fully_banked_period_is_complete_and_sourced_bank(self):
        self.make_bank_months("2025-10-15", "2025-11-15", "2025-12-15")
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "complete")
        self.assertEqual(cov["source"], "bank")
        self.assertEqual(cov["journal_refs"], [])
        self.assertEqual(cov["missing"], [])

    def test_a_partly_banked_period_with_a_journal_stays_partial(self):
        """The mixed-period rule, and the most important guard in this file:
        one bank month present and the month-by-month rule governs, so a
        forgotten December import is still flagged."""
        self.make_bank_months("2025-10-15", "2025-11-15")
        self.make_journal()
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "partial")
        self.assertEqual(cov["source"], "bank")
        self.assertEqual(cov["missing"], ["Dec 2025"])

    def test_an_empty_period_is_none(self):
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "none")
        self.assertEqual(cov["source"], "none")
        self.assertEqual(cov["journal_refs"], [])

    def test_get_bank_coverage_still_answers_only_the_bank_question(self):
        """The primitive must stay honest whatever journals exist."""
        self.make_journal()
        bank = get_bank_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(bank["status"], "none")
        self.assertNotIn("source", bank)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core.tests_bas_period_coverage -v 2`

Expected: FAIL — `ImportError: cannot import name 'get_period_coverage' from 'core.bas_utils'`.

- [ ] **Step 3: Write `get_period_coverage`**

In `core/bas_utils.py`, insert immediately before `def compute_period_status`:

```python
def get_period_coverage(fy, period_start, period_end):
    """Is this BAS period accounted for?

    ``get_bank_coverage`` answers a narrower question -- is the *bank* data
    complete -- and stays the primitive. This one adds the other way a period
    can be accounted for: a posted cashbook journal, which is the accountant
    asserting the quarter is written up, the same assertion that importing and
    confirming every bank transaction makes.

    The journal only counts when the period has no bank activity at all. The
    moment one bank month is present the month-by-month rule governs
    unchanged, so a forgotten import is still flagged on a mixed period.

    Adds two keys to the bank-coverage dict:
        source        -- "bank" | "journal" | "none"
        journal_refs  -- reference numbers, empty unless source == "journal"

    A journalled period reports no months and nothing missing: JournalLine has
    no date (only AdjustingJournal.journal_date), so month-level coverage is
    not derivable and naming months would be inventing data.
    """
    from .models import AdjustingJournal

    coverage = get_bank_coverage(fy, period_start, period_end)

    if any(m["covered"] for m in coverage["months"]):
        coverage["source"] = "bank"
        coverage["journal_refs"] = []
        return coverage

    journal_refs = list(
        AdjustingJournal.objects.filter(
            financial_year=fy,
            journal_type=AdjustingJournal.JournalType.CASHBOOK,
            status="posted",
            journal_date__gte=period_start,
            journal_date__lte=period_end,
        )
        .order_by("reference_number")
        .values_list("reference_number", flat=True)
    )
    if journal_refs:
        return {
            "status": "complete",
            "months": [],
            "missing": [],
            "source": "journal",
            "journal_refs": journal_refs,
        }

    coverage["source"] = "none"
    coverage["journal_refs"] = []
    return coverage
```

- [ ] **Step 4: Point `compute_period_status` at it**

In `core/bas_utils.py`, inside `compute_period_status` (line 254), change the one call:

```python
    coverage = get_period_coverage(fy, period_start, period_end)
```

Also update that function's docstring first line, which currently claims bank coverage:

```python
    """
    Compute the dynamic status of a BAS period from its coverage -- bank
    transactions, or a posted cashbook journal. If the period is explicitly
    lodged, that status is preserved.

    Returns one of: 'lodged', 'ready', 'partial', 'empty'
    """
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core.tests_bas_period_coverage -v 2`

Expected: PASS, 11 tests.

- [ ] **Step 6: Verify nothing else regressed**

Run: `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core.tests_bas_gst core.tests_bas_cashbook_journal -v 2`

Expected: PASS. These are the suites that exercise coverage and period status today.

- [ ] **Step 7: Commit**

```bash
git add core/bas_utils.py core/tests_bas_period_coverage.py
git commit -m "feat(bas): a posted cashbook journal accounts for its period

get_bank_coverage answers whether the bank data is complete. Once a cashbook
journal carries its own GST that stopped being the same question as whether
the period is accounted for, so get_period_coverage answers the second one and
leaves the primitive honest.

The journal only counts when the period has no bank activity at all -- one
bank month present and the month-by-month rule governs unchanged, so a
forgotten import is still flagged on a mixed period.

A journalled period reports no months and nothing missing: JournalLine has no
date, so month-level coverage is not derivable and naming months would be
inventing data.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The dashboard, the lodge gate, and the endpoint

**Files:**
- Modify: `core/views_bas.py:32` (import), `:98`, `:260`, `:366`
- Test: `core/tests_bas_period_coverage.py` (append)

**Interfaces:**
- Consumes: `get_period_coverage(fy, period_start, period_end)` from Task 1.
- Produces: no new Python interface. `bas_coverage_check` gains `source` and `journal_refs` in its JSON body.

- [ ] **Step 1: Write the failing tests**

Append to `core/tests_bas_period_coverage.py`:

```python
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import BASPeriod
from core.test_support import Require2FAMixin


class JournalledPeriodViewTest(Require2FAMixin, PeriodCoverageTestBase):
    """The three view-level consequences: which quarter opens, whether the
    lodge gate demands an override, and what the endpoint reports."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        # Require2FAMiddleware checks has_2fa before it looks at the session
        # flag, so the user needs the secret as well as the verified session.
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant", totp_secret="TESTSECRET", totp_confirmed=True,
        )
        # An accountant reaches only entities assigned to them.
        self.entity.assigned_accountant = self.user
        self.entity.save(update_fields=["assigned_accountant"])
        self.login_as(self.user)
        self.make_journal()

    def test_the_dashboard_opens_on_the_journalled_quarter(self):
        """Auto-selection looks for a 'ready' period. Q2 is journalled, so it
        is ready, and the page must not fall back to the empty Q1."""
        # SECURE_SSL_REDIRECT is on; without secure=True this is a bare 301.
        r = self.client.get(
            reverse("core:gst_activity_statement", args=[self.fy.pk]),
            secure=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["selected_period"]["period"].period_number, 2)
        self.assertEqual(r.context["selected_period"]["status"], "ready")

    def test_a_journalled_period_lodges_without_an_override_reason(self):
        r = self.client.post(
            reverse("core:bas_lodge_period",
                    args=[self.fy.pk, 2]),
            {}, secure=True, follow=True,
        )
        self.assertEqual(r.status_code, 200)
        bp = BASPeriod.objects.get(
            financial_year=self.fy, period_type="quarterly", period_number=2,
        )
        self.assertEqual(bp.status, "lodged")

    def test_an_empty_period_still_cannot_lodge_without_an_override(self):
        r = self.client.post(
            reverse("core:bas_lodge_period", args=[self.fy.pk, 1]),
            {}, secure=True, follow=True,
        )
        bp = BASPeriod.objects.get(
            financial_year=self.fy, period_type="quarterly", period_number=1,
        )
        self.assertNotEqual(bp.status, "lodged")

    def test_the_coverage_endpoint_reports_the_journal_source(self):
        r = self.client.get(
            reverse("core:bas_coverage_check", args=[self.fy.pk, 2]),
            secure=True,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "complete")
        self.assertEqual(body["source"], "journal")
        self.assertEqual(body["journal_refs"], ["JE-001"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core.tests_bas_period_coverage.JournalledPeriodViewTest -v 2`

Expected: FAIL. `test_the_dashboard_opens_on_the_journalled_quarter` gets period 1, the lodge test finds the period not lodged, and the endpoint test raises `KeyError: 'source'`.

- [ ] **Step 3: Switch the import**

In `core/views_bas.py`, in the `from .bas_utils import (...)` block at line 32:

```python
from .bas_utils import (
    ensure_bas_periods,
    get_period_coverage,
    calculate_gst_for_period,
    compute_period_status,
)
```

`get_bank_coverage` is no longer imported here — `get_period_coverage` calls it internally.

- [ ] **Step 4: Switch the three call sites**

All three are the same edit, `get_bank_coverage(` → `get_period_coverage(`:

- line 98, in `bas_dashboard`'s per-period loop:
  ```python
          coverage = get_period_coverage(fy, bp.period_start, bp.period_end)
  ```
- line 260, in `bas_lodge_period`:
  ```python
      coverage = get_period_coverage(fy, bp.period_start, bp.period_end)
  ```
- line 366, in `bas_coverage_check`:
  ```python
      coverage = get_period_coverage(fy, bp.period_start, bp.period_end)
  ```

- [ ] **Step 5: Add the new fields to the endpoint response**

In `core/views_bas.py`, `bas_coverage_check`'s `JsonResponse`:

```python
    return JsonResponse({
        "period": bp.period_number,
        "label": bp.label,
        "status": coverage["status"],
        "months": coverage["months"],
        "missing": coverage["missing"],
        "source": coverage["source"],
        "journal_refs": coverage["journal_refs"],
    })
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core.tests_bas_period_coverage -v 2`

Expected: PASS, 15 tests.

- [ ] **Step 7: Commit**

```bash
git add core/views_bas.py core/tests_bas_period_coverage.py
git commit -m "feat(bas): the dashboard and lodge gate read period coverage

The dashboard's auto-selection looks for a 'ready' period, so a journalled
quarter now opens instead of the page falling back to an empty Q1 -- that
falls out of compute_period_status rather than needing a special case.

The lodgement gate stops demanding an override reason for a quarter that is
actually complete. That mattered more than it looks: overriding on every
cashbook BAS trains accountants to treat the override as routine, which erodes
the control for the bank-fed entities where it does real work.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Say "journalled", not "all months covered"

**Files:**
- Modify: `templates/core/gst_activity_statement.html:132-147` (period strip), `:212` (status card)
- Test: `core/tests_bas_period_coverage.py` (append)

**Interfaces:**
- Consumes: `coverage.source` and `coverage.journal_refs` on both `pd.coverage` (period strip) and `selected_period.coverage` (status card).
- Produces: nothing.

**Correction to the spec.** The spec also asked to suppress the "Bank statement coverage is incomplete" warning at line 1026. That is unnecessary: the warning lives inside `#lodgePartialModal`, which is only opened from the `{% elif selected_period.status == 'partial' %}` branch at line 213. A journalled period is `ready`, so it never reaches that branch. The same applies to the "No bank transactions" text at line 224, which is in the `empty` branch. Leave both alone.

- [ ] **Step 1: Write the failing tests**

Append to `core/tests_bas_period_coverage.py`, inside `JournalledPeriodViewTest`:

```python
    def test_the_status_card_does_not_claim_all_months_are_covered(self):
        """The 'ready' branch says 'All months covered'. For a journalled
        period no months were covered -- there is no bank feed at all."""
        html = self.client.get(
            reverse("core:gst_activity_statement", args=[self.fy.pk]),
            secure=True,
        ).content.decode()
        self.assertNotIn("All months covered", html)
        self.assertIn("Journalled", html)
        self.assertIn("JE-001", html)

    def test_a_banked_period_still_says_all_months_covered(self):
        self.make_bank_months("2026-01-15", "2026-02-15", "2026-03-15")
        html = self.client.get(
            reverse("core:gst_activity_statement", args=[self.fy.pk])
            + "?period=3",
            secure=True,
        ).content.decode()
        self.assertIn("All months covered", html)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core.tests_bas_period_coverage.JournalledPeriodViewTest -v 2`

Expected: FAIL — `'All months covered' unexpectedly found`, and `'Journalled' not found`.

- [ ] **Step 3: Branch the status card**

In `templates/core/gst_activity_statement.html`, replace line 212:

```html
                    <div class="small text-muted mt-1">All months covered</div>
```

with:

```html
                    {% if selected_period.coverage.source == 'journal' %}
                    <div class="small text-muted mt-1">
                        Journalled &mdash; {{ selected_period.coverage.journal_refs|join:", " }}
                    </div>
                    {% else %}
                    <div class="small text-muted mt-1">All months covered</div>
                    {% endif %}
```

- [ ] **Step 4: Branch the period strip**

In the same file, the block at lines 132-147 currently tests `pd.coverage.missing` then `pd.coverage.status == 'complete'`. A journalled period has both `missing` and `months` empty, so it falls into the second branch and renders an empty row of pills. Add a journal branch **first**, so `source` is read before `months` or `missing`:

```html
                {% if pd.coverage.source == 'journal' %}
                <div class="mt-1" style="font-size: 0.7rem;">
                    <span class="text-primary" title="Journalled: {{ pd.coverage.journal_refs|join:", " }}">
                        <i class="bi bi-journal-check"></i> Journalled
                    </span>
                </div>
                {% elif pd.coverage.missing %}
```

The rest of the chain (the `{% for m in pd.coverage.months %}` loop, the
`{% elif pd.coverage.status == 'complete' %}` branch and the closing
`{% endif %}`) is unchanged.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core.tests_bas_period_coverage -v 2`

Expected: PASS, 17 tests.

- [ ] **Step 6: Commit**

```bash
git add templates/core/gst_activity_statement.html core/tests_bas_period_coverage.py
git commit -m "feat(bas): a journalled period says so instead of claiming bank coverage

Returning status 'complete' makes the gate and the auto-selection correct, but
on its own it would have the ready card say 'All months covered' for a period
with no bank feed at all, and render an empty row of month pills. Both now
branch on coverage.source and name the journal instead.

The period strip tests source BEFORE months and missing, because a journalled
period has both empty and would otherwise fall through to a loop that renders
nothing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Whole-suite guard

**Files:** none modified — this task is verification only.

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: nothing.

- [ ] **Step 1: Capture the baseline from the branch point**

```bash
git stash --include-untracked
SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core > /tmp/bas_baseline.txt 2>&1 || true
grep -E "^(FAIL|ERROR):" /tmp/bas_baseline.txt | sort > /tmp/bas_baseline_set.txt
git stash pop
```

If the working tree is already clean (everything committed in Tasks 1-3), get the baseline from `origin/main` in a scratch checkout instead — the point is a failure set from *before* this branch's changes.

- [ ] **Step 2: Run the full suite on the finished branch**

```bash
SECRET_KEY=x DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" ./venv/bin/python manage.py test core > /tmp/bas_after.txt 2>&1 || true
grep -E "^(FAIL|ERROR):" /tmp/bas_after.txt | sort > /tmp/bas_after_set.txt
diff /tmp/bas_baseline_set.txt /tmp/bas_after_set.txt
```

Expected: no diff. Test *count* rises by 17; the failure *set* must be byte-identical. If anything new appears, read it — do not assume it is baseline.

- [ ] **Step 3: Confirm the driving client end to end**

Elliott Jaques (`bcb8a828-2791-4788-8b17-1964dd0d1a93`) is the reason this exists. Against a seeded copy of its shape — **not production** — confirm in order: the dashboard opens on Q2 rather than Q1; Q2 reads `ready`; the card says "Journalled — JE-001" and not "All months covered"; and the lodge button posts without an override reason.

- [ ] **Step 4: Push and open a PR**

```bash
git push -u origin feat/bas-period-coverage
```

PR body should state: the lodge gate's behaviour changed only for periods with no bank activity at all, and the mixed-period guard (`test_a_partly_banked_period_with_a_journal_stays_partial`) is the test that proves the existing control is intact.

---

## Self-Review

**Spec coverage.** `get_period_coverage` and its three-row table — Task 1. `compute_period_status` unchanged in logic — Task 1 Step 4. The four call sites — Task 1 Step 4 plus Task 2 Step 4. `source`/`journal_refs` on the endpoint — Task 2 Step 5. Template honesty — Task 3. The `months: []` decision and its "branch on source first" consequence — Global Constraints, Task 1 Step 3, Task 3 Step 4. All three judgment calls (CASHBOOK only, GST not required, posted only) have a test in Task 1. The out-of-scope list is honoured: no rename, no per-month journal coverage, nothing touching `calculate_gst_for_period` or `bas_download`, and journals dated after period end are tested as *not* counting.

**One spec correction, recorded in Task 3:** suppressing the incomplete-coverage warning at line 1026 is unnecessary, because that warning is inside a modal only reachable from the `partial` branch, which a journalled period never enters. The spec overstated the template work.

**Type consistency.** `get_period_coverage(fy, period_start, period_end)` is called with exactly those three positional arguments at all four sites. `source` values are `"bank"`/`"journal"`/`"none"` everywhere, `journal_refs` is a list of `reference_number` strings in every branch including the two empty ones, and the template reads both under `coverage.` on `pd` and on `selected_period`.
