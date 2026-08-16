# BAS-to-Trial-Balance Desync Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a correction to a confirmed bank transaction re-post the trial balance, so an entity's BAS and its financial statements can no longer disagree.

**Architecture:** One rule for which financial year a transaction belongs to, in one module. Bank postings own their own trial-balance rows, making `source` a partition. A rebuild-from-source primitive recomputes those rows from the posted transactions, and every correction path calls it. A management command audits existing books without writing to them.

**Tech Stack:** Django 5, PostgreSQL in production, sqlite for the test suite, Playwright for end-to-end.

**Spec:** `docs/superpowers/specs/2026-08-16-bas-tb-desync-fixes-design.md` — read it before Task 1. The plan argues from it and does not repeat its reasoning.

## Global Constraints

- **Tests need a sqlite override.** `python3 manage.py test` cannot create a test database on the live managed Postgres. Always run:
  `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test <labels>`
- **There is a large pre-existing failure baseline.** `core review integrations` was 229 tests, 47 failures + 16 errors at commit `d4156b9`. Compare failure *sets*, never counts — adding tests reorders them and some errors are order-dependent.
- **This working tree is production on disk.** `/opt/statementhub` is what gunicorn and celery serve. Editing a file changes production code immediately; workers keep the old module in memory until restarted. Never leave a file in a broken intermediate state across a commit boundary.
- **Merging to main auto-deploys.** Nothing merges until every gate in the spec's Verification section holds.
- **`select_for_update` is a no-op on sqlite.** No Django test can prove the lock. Only the Postgres end-to-end soak in Task 11 can.
- **`PendingTransaction.date` is a `CharField`**, holding whatever the statement parser produced. Never assume it parses.
- **Money is `Decimal`.** Never introduce a float into a posting path.

## Sequencing Gate

Tasks 1–7 and 10 may land in any order that respects their stated dependencies. **Tasks 8 and 9 wire the rebuild into live request paths and must not merge until the manual repair gate between Task 7 and Task 8 has been signed off by Elio.** That gate is not a code step; it is described in full before Task 8.

---

### Task 1: One rule for which financial year a transaction belongs to

**Files:**
- Create: `core/txn_periods.py`
- Create: `core/tests_bank_tb_fixtures.py`
- Create: `core/tests_txn_periods.py`
- Modify: `review/views.py:110-146` (`_post_confirmed_txn_to_tb`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `core.txn_periods.parse_txn_date(raw: str | None) -> datetime.date | None`
  - `core.txn_periods.entity_financial_years(entity: Entity) -> list[FinancialYear]`
  - `core.txn_periods.resolve_fy_for_txn(txn: PendingTransaction, fys: list[FinancialYear] | None = None) -> FinancialYear | None`
  - `core.tests_bank_tb_fixtures` — `STORAGES_OVERRIDE`, `make_entity`, `make_fy`, `make_bank_mapping`, `make_job`, `make_txn`, used by Tasks 2, 3, 4, 5, 7, 8, 9.

- [ ] **Step 1: Write the shared test fixture module**

This is not a test file — it holds no `TestCase` and asserts nothing. It exists because six later test modules need the same book built the same way.

```python
# core/tests_bank_tb_fixtures.py
"""Shared fixtures for the bank-statement trial-balance tests.

Six test modules in this project need the same four things: a GST-registered
entity, one or more financial years, a bank mapping, and posted transactions.
Building them inline six times is how the tests drift apart from each other.
"""
from datetime import date
from decimal import Decimal

from core.models import (
    BankAccountMapping, Client, Entity, FinancialYear, TrialBalanceLine,
)
from review.models import PendingTransaction, ReviewJob

D = Decimal

# The suite runs without collected staticfiles or a configured object store.
STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def make_entity(name="Desync Test Pty Ltd", gst=True):
    client = Client.objects.create(name=f"{name} Client")
    return Entity.objects.create(
        entity_name=name,
        entity_type="company",
        client=client,
        is_gst_registered=gst,
        bas_frequency="quarterly",
    )


def make_fy(entity, label="FY2026", start=date(2025, 7, 1), end=date(2026, 6, 30)):
    return FinancialYear.objects.create(
        entity=entity, year_label=label, start_date=start, end_date=end,
    )


def make_bank_mapping(entity, code="1100", name="Business Cheque Account"):
    return BankAccountMapping.objects.create(
        entity=entity, bsb="", account_number="", is_default=True,
        tb_account_code=code, tb_account_name=name,
    )


def make_job(entity, fy):
    return ReviewJob.objects.create(
        entity=entity, financial_year=fy, client_name=entity.entity_name,
        is_gst_registered=entity.is_gst_registered,
    )


def make_txn(job, *, date_str, amount, code, name="", tax_type="", gst="0"):
    """A confirmed but NOT yet posted transaction.

    Post it with core.views._post_txn_to_tb(txn, fy, has_gst) so the test
    exercises the real posting path rather than hand-writing TB rows.
    """
    amount = D(str(amount))
    gst = D(str(gst))
    txn = PendingTransaction.objects.create(
        job=job,
        date=date_str,
        description=f"{code} {date_str}",
        amount=amount,
        confirmed_code=code,
        confirmed_name=name or f"Account {code}",
        confirmed_tax_type=tax_type,
        confirmed_gst_amount=gst,
        gst_amount=gst,
        net_amount=abs(amount) - gst,
        is_confirmed=True,
    )
    return txn


def bs_line(fy, code):
    """The single source='bank_statement' non-adjustment row, or None."""
    return TrialBalanceLine.objects.filter(
        financial_year=fy, account_code=code,
        source="bank_statement", is_adjustment=False,
    ).first()
```

- [ ] **Step 2: Write the failing tests for the resolution module**

```python
# core/tests_txn_periods.py
"""The one rule for which financial year a transaction belongs to.

Three functions used to answer this differently — see the spec's "Three
functions disagree about which year a transaction belongs to". These tests pin
the rule, including its fallback, because the rebuild reproduces posting only if
it reproduces the fallback too.
"""
from datetime import date

from django.test import TestCase, override_settings

from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_entity, make_fy, make_job, make_txn,
)
from core.txn_periods import (
    entity_financial_years, parse_txn_date, resolve_fy_for_txn,
)


class ParseTxnDateTests(TestCase):
    def test_parses_every_format_the_parsers_emit(self):
        self.assertEqual(parse_txn_date("2025-08-14"), date(2025, 8, 14))
        self.assertEqual(parse_txn_date("14/08/2025"), date(2025, 8, 14))
        self.assertEqual(parse_txn_date("14-08-2025"), date(2025, 8, 14))
        self.assertEqual(parse_txn_date("14 Aug 2025"), date(2025, 8, 14))

    def test_tolerates_surrounding_whitespace(self):
        self.assertEqual(parse_txn_date("  2025-08-14 "), date(2025, 8, 14))

    def test_returns_none_rather_than_raising(self):
        for raw in ("", None, "   ", "not a date", "31/02/2025"):
            self.assertIsNone(parse_txn_date(raw), raw)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class ResolveFyForTxnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.entity = make_entity()
        cls.fy25 = make_fy(cls.entity, "FY2025", date(2024, 7, 1), date(2025, 6, 30))
        cls.fy26 = make_fy(cls.entity, "FY2026", date(2025, 7, 1), date(2026, 6, 30))
        # The job is attached to FY2025 deliberately: a statement spanning the
        # year end is exactly the case job__financial_year got wrong.
        cls.job = make_job(cls.entity, cls.fy25)

    def test_resolves_to_the_year_covering_the_date(self):
        txn = make_txn(self.job, date_str="2025-05-20", amount="-110.00", code="0400")
        self.assertEqual(resolve_fy_for_txn(txn), self.fy25)

    def test_ignores_the_jobs_own_year(self):
        # Job is FY2025; the transaction is July, so it belongs to FY2026.
        txn = make_txn(self.job, date_str="2025-07-03", amount="-110.00", code="0400")
        self.assertEqual(resolve_fy_for_txn(txn), self.fy26)

    def test_unparseable_date_falls_back_to_the_most_recent_year(self):
        txn = make_txn(self.job, date_str="n/a", amount="-110.00", code="0400")
        self.assertEqual(resolve_fy_for_txn(txn), self.fy26)

    def test_date_outside_every_year_falls_back_to_the_most_recent_year(self):
        txn = make_txn(self.job, date_str="1999-01-01", amount="-110.00", code="0400")
        self.assertEqual(resolve_fy_for_txn(txn), self.fy26)

    def test_returns_none_when_the_entity_has_no_years(self):
        other = make_entity("Yearless Pty Ltd")
        fy = make_fy(other, "FY2026")
        job = make_job(other, fy)
        txn = make_txn(job, date_str="2025-08-01", amount="-110.00", code="0400")
        fy.delete()
        txn.refresh_from_db()
        self.assertIsNone(resolve_fy_for_txn(txn))

    def test_prefetched_years_give_the_same_answer(self):
        txn = make_txn(self.job, date_str="2025-07-03", amount="-110.00", code="0400")
        fys = entity_financial_years(self.entity)
        with self.assertNumQueries(0):
            self.assertEqual(resolve_fy_for_txn(txn, fys=fys), self.fy26)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_txn_periods -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.txn_periods'`

- [ ] **Step 4: Write the resolution module**

```python
# core/txn_periods.py
"""Resolve a PendingTransaction's free-text date onto the periods it belongs to.

PendingTransaction.date is a CharField holding whatever the statement parser
produced, so every consumer needing a real date has to parse it. Four consumers
need it — the posting path, the trial-balance rebuild, the bank-contra
recalculation and the amended-period flag — and three of them used to answer
"which financial year is this?" differently. A statement spanning a year end
therefore posted to one year and had its bank contra counted into another.

One rule, one implementation. Everything asks here.
"""
from datetime import datetime

# The formats the statement parsers are known to emit. Taken verbatim from the
# posting path so the rebuild parses exactly what posting parsed.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y")

# A financial year is only a posting target while it is open for work. Matches
# the filter the posting path has always used.
POSTABLE_FY_STATUSES = ("draft", "in_review", "finished")


def parse_txn_date(raw):
    """Parse a PendingTransaction.date string to a date, or None if unparseable."""
    if not raw:
        return None
    try:
        raw = raw.strip()
    except AttributeError:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def entity_financial_years(entity):
    """Every financial year a transaction for this entity could post to."""
    from core.models import FinancialYear

    return list(
        FinancialYear.objects.filter(
            entity=entity, status__in=POSTABLE_FY_STATUSES
        )
    )


def resolve_fy_for_txn(txn, fys=None):
    """Return the FinancialYear this transaction posts to, or None.

    The year whose date range covers the transaction's date. When the date is
    unparseable, or falls outside every year, fall back to the most recent year
    — the fallback the posting path has always used.

    The rebuild must reproduce this exactly, fallback included. Filtering on the
    date range instead would drop every unparseable-date transaction out of the
    year posting put it in, and the rebuild would then zero lines it had
    legitimately created — turning the rebuild into the data loss it exists to
    prevent.

    Pass `fys` from entity_financial_years() when resolving many transactions
    for one entity, to keep the batch to a single query.
    """
    entity = txn.job.entity if txn.job else None
    if not entity:
        return None
    if fys is None:
        fys = entity_financial_years(entity)
    if not fys:
        return None

    txn_date = parse_txn_date(txn.date)
    if txn_date:
        for fy in fys:
            if fy.start_date <= txn_date <= fy.end_date:
                return fy

    return max(fys, key=lambda f: f.end_date)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_txn_periods -v 2`
Expected: PASS, 10 tests

- [ ] **Step 6: Make the posting path use it**

Replace `review/views.py:110-146` — everything from the `# Avoid circular import` comment to the `return` — with:

```python
    # Avoid circular import at module level
    from core.txn_periods import resolve_fy_for_txn
    from core.views import _post_txn_to_tb

    if not txn.job or not txn.job.entity:
        return False

    # One rule for which year this belongs to, shared with the rebuild and the
    # bank-contra recalculation. See core/txn_periods.py.
    target_fy = resolve_fy_for_txn(txn)
    if not target_fy:
        return False

    has_gst = bool(txn.confirmed_gst_amount and txn.confirmed_gst_amount > 0)
    return _post_txn_to_tb(txn, target_fy, has_gst)
```

Then delete the now-unused `from core.models import FinancialYear` and `from datetime import datetime as dt` imports from inside that function.

- [ ] **Step 7: Verify the refactor changed no behaviour**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test review core.tests_txn_periods -v 1`
Expected: the `review` failure set is identical to the baseline; `core.tests_txn_periods` passes.

Record the review failure set in the commit message — later tasks compare against it.

- [ ] **Step 8: Commit**

```bash
git add core/txn_periods.py core/tests_txn_periods.py core/tests_bank_tb_fixtures.py review/views.py
git commit -m "refactor: one shared rule for a transaction's financial year"
```

---

### Task 2: Bank postings own their own trial-balance rows

**Files:**
- Modify: `core/views.py:836-872` (`_get_or_create_tb_line`)
- Modify: `core/views.py:1072-1141` (`_post_txn_to_tb`, three call sites)
- Modify: `core/views.py:972-983` (`_post_bank_contra_entry`)
- Create: `core/tests_bank_tb_partition.py`

**Interfaces:**
- Consumes: `core.tests_bank_tb_fixtures` from Task 1.
- Produces: `_get_or_create_tb_line(..., bank_statement_only: bool = False)`. When true it returns only a `source='bank_statement', is_adjustment=False` row, creating one if absent. Task 4's rebuild depends on this invariant holding.

- [ ] **Step 1: Write the failing test**

```python
# core/tests_bank_tb_partition.py
"""Bank postings must land in a row of their own.

_get_or_create_tb_line fell through to qs.first() when an account had no
non-adjustment row, so an account carrying only journal adjustments received its
bank postings inside one of them. Two live entities reached that state (Veronica
Cerratti 3565, Daniel Habteslassie 4080). The rebuild reads only bank_statement
rows and refuses to touch manual_journal, so it cannot see that money — and
would create a second row holding it all over again.
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.views import _post_txn_to_tb

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BankPostingPartitionTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def test_posting_beside_a_journal_adjustment_creates_its_own_row(self):
        """The Cerratti shape, reduced to a fixture."""
        journal = TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="manual_journal",
            is_adjustment=True, debit=D("62500.00"), credit=D("0.00"),
            closing_balance=D("62500.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-14", amount="-1100.00",
                       code="3565", gst="100.00")

        _post_txn_to_tb(txn, self.fy, has_gst=True)

        journal.refresh_from_db()
        self.assertEqual(journal.debit, D("62500.00"),
                         "the journal adjustment must not absorb a bank posting")
        line = bs_line(self.fy, "3565")
        self.assertIsNotNone(line, "a bank_statement row should have been created")
        self.assertEqual(line.debit, D("1000.00"))

    def test_posting_still_accumulates_onto_an_existing_bank_statement_row(self):
        for i in range(2):
            txn = make_txn(self.job, date_str=f"2025-08-1{i}", amount="-110.00",
                           code="0400", gst="10.00")
            _post_txn_to_tb(txn, self.fy, has_gst=True)

        rows = TrialBalanceLine.objects.filter(
            financial_year=self.fy, account_code="0400")
        self.assertEqual(rows.count(), 1, "must not create a row per posting")
        self.assertEqual(rows.first().debit, D("200.00"))

    def test_bank_contra_also_gets_its_own_row(self):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="1100",
            account_name="Business Cheque Account", source="tb_import",
            is_adjustment=False, debit=D("5000.00"), credit=D("0.00"),
            closing_balance=D("5000.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-14", amount="-110.00",
                       code="0400", gst="10.00")

        _post_txn_to_tb(txn, self.fy, has_gst=True)

        imported = TrialBalanceLine.objects.get(
            financial_year=self.fy, account_code="1100", source="tb_import")
        self.assertEqual(imported.debit, D("5000.00"),
                         "an imported balance must not absorb bank movement")
        self.assertEqual(bs_line(self.fy, "1100").credit, D("110.00"))

    def test_other_callers_of_the_helper_are_unchanged(self):
        """bank_statement_only defaults off; the old lookup still applies."""
        from core.views import _get_or_create_tb_line

        existing = TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="9999",
            account_name="Imported", source="tb_import", is_adjustment=False,
            debit=D("1.00"),
        )
        line, created = _get_or_create_tb_line(
            financial_year=self.fy, account_code="9999", defaults={})
        self.assertFalse(created)
        self.assertEqual(line.pk, existing.pk)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bank_tb_partition -v 2`
Expected: FAIL — `test_posting_beside_a_journal_adjustment_creates_its_own_row` asserts `journal.debit == 62500.00` but finds `63500.00`; `test_bank_contra_also_gets_its_own_row` finds the imported row credited.

- [ ] **Step 3: Add the narrowed lookup to the helper**

In `core/views.py`, change the signature and the lookup only. Creation is shared and stays as it is.

```python
def _get_or_create_tb_line(financial_year=None, account_code=None, defaults=None,
                           fy=None, bank_statement_only=False):
    """
    Safely get or create a TrialBalanceLine.

    Unlike Django's get_or_create, this handles the case where multiple
    rows already exist for the same (financial_year, account_code) — which
    is normal because journal adjustments create separate rows.  We pick
    the *first non-adjustment* row, or the first row overall, to accumulate
    into.

    bank_statement_only=True narrows that to a source='bank_statement',
    is_adjustment=False row, creating one when there is none. Bank postings
    MUST use it. Without it the fall-through to qs.first() puts bank postings
    inside a journal adjustment row on any account that has only adjustments,
    and the rebuild — which reads bank_statement rows and never touches
    manual_journal — cannot see that money.

    When creating a new line, automatically applies any existing
    ClientAccountMapping so the line is pre-mapped.
    """
    fy_resolved = financial_year or fy
    qs = TrialBalanceLine.objects.filter(
        financial_year=fy_resolved, account_code=account_code,
    )
    if bank_statement_only:
        tb_line = qs.filter(is_adjustment=False, source='bank_statement').first()
    else:
        # Prefer the non-adjustment (original / bank_statement) row
        tb_line = qs.filter(is_adjustment=False).first() or qs.first()
    if tb_line:
        return tb_line, False
    # No row exists — create one.
    # Apply existing ClientAccountMapping if available.
    defaults = dict(defaults or {})
    if bank_statement_only:
        defaults['source'] = 'bank_statement'
        defaults['is_adjustment'] = False
    if 'mapped_line_item' not in defaults or defaults.get('mapped_line_item') is None:
        cam = ClientAccountMapping.objects.filter(
            entity=fy_resolved.entity,
            client_account_code=account_code,
            mapped_line_item__isnull=False,
        ).select_related('mapped_line_item').first()
        if cam:
            defaults['mapped_line_item'] = cam.mapped_line_item
    tb_line = TrialBalanceLine.objects.create(
        financial_year=fy_resolved,
        account_code=account_code,
        **(defaults),
    )
    return tb_line, True
```

- [ ] **Step 4: Pass the flag at all four bank-posting call sites**

Add `bank_statement_only=True` to the `_get_or_create_tb_line(...)` calls at:
- `core/views.py:1072` — the expense/income line in `_post_txn_to_tb`
- `core/views.py:1104` — the income GST line (3380)
- `core/views.py:1122` — the expense GST line (3380)
- `core/views.py:972` — the bank contra line in `_post_bank_contra_entry`

Change nothing else in those calls. Their `defaults` already carry `"source": "bank_statement"`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bank_tb_partition -v 2`
Expected: PASS, 4 tests

- [ ] **Step 6: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the Task 1 baseline.

- [ ] **Step 7: Commit**

```bash
git add core/views.py core/tests_bank_tb_partition.py
git commit -m "fix: keep bank postings out of journal adjustment rows"
```

---

### Task 3: The bank contra follows the same year rule, and sheds what leaves

**Files:**
- Modify: `core/views.py:10557-10663` (`_recalc_bank_contra`)
- Create: `core/tests_bank_contra_fy.py`

**Interfaces:**
- Consumes: `core.txn_periods.resolve_fy_for_txn`, `entity_financial_years` (Task 1).
- Produces: `_recalc_bank_contra(fy)` unchanged in signature and return shape. Its transaction set is now date-derived, and it zeroes a bank row whose transactions have all left the year. Task 4's rebuild delegates to it.

- [ ] **Step 1: Write the failing test**

```python
# core/tests_bank_contra_fy.py
"""The bank contra is grouped by the same year rule as the posting it mirrors.

_recalc_bank_contra scoped on job__financial_year while posting resolved the
year from the transaction's own date, so a statement spanning a year end posted
to one year and had its contra counted into the other.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.views import _post_txn_to_tb, _recalc_bank_contra

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BankContraYearScopeTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy25 = make_fy(self.entity, "FY2025", date(2024, 7, 1), date(2025, 6, 30))
        self.fy26 = make_fy(self.entity, "FY2026", date(2025, 7, 1), date(2026, 6, 30))
        make_bank_mapping(self.entity)
        # One job, attached to FY2025, holding a statement that crosses 30 June.
        self.job = make_job(self.entity, self.fy25)

    def _post(self, date_str, amount):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code="0400")
        from core.txn_periods import resolve_fy_for_txn
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=False)
        return txn

    def test_a_year_spanning_statement_splits_its_contra(self):
        self._post("2025-06-20", "-100.00")   # FY2025
        self._post("2025-07-03", "-250.00")   # FY2026

        _recalc_bank_contra(self.fy25)
        _recalc_bank_contra(self.fy26)

        self.assertEqual(bs_line(self.fy25, "1100").credit, D("100.00"))
        self.assertEqual(bs_line(self.fy26, "1100").credit, D("250.00"),
                         "the July transaction belongs to FY2026, not the job's year")

    def test_a_vacated_bank_row_is_zeroed_not_left_standing(self):
        txn = self._post("2025-06-20", "-100.00")
        _recalc_bank_contra(self.fy25)
        self.assertEqual(bs_line(self.fy25, "1100").credit, D("100.00"))

        # The transaction is re-dated into the next year — the FY2025 contra
        # must shed it rather than keep it forever.
        txn.date = "2025-07-03"
        txn.save(update_fields=["date"])

        _recalc_bank_contra(self.fy25)

        self.assertEqual(bs_line(self.fy25, "1100").credit, D("0.00"))
        self.assertEqual(bs_line(self.fy25, "1100").closing_balance, D("0.00"))

    def test_receipts_debit_and_payments_credit(self):
        self._post("2025-08-01", "440.00")
        self._post("2025-08-02", "-110.00")

        _recalc_bank_contra(self.fy26)

        line = bs_line(self.fy26, "1100")
        self.assertEqual(line.debit, D("440.00"))
        self.assertEqual(line.credit, D("110.00"))

    def test_calling_it_twice_changes_nothing(self):
        self._post("2025-08-01", "-330.00")
        _recalc_bank_contra(self.fy26)
        first = bs_line(self.fy26, "1100").credit
        _recalc_bank_contra(self.fy26)
        self.assertEqual(bs_line(self.fy26, "1100").credit, first)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bank_contra_fy -v 2`
Expected: FAIL — `test_a_year_spanning_statement_splits_its_contra` finds FY2025 credited 350.00 and FY2026 with no row; `test_a_vacated_bank_row_is_zeroed_not_left_standing` finds 100.00 still standing.

- [ ] **Step 3: Change the transaction set to date-derived**

In `core/views.py`, replace the queryset and the early return at `:10571-10579` with:

```python
    from core.txn_periods import entity_financial_years, resolve_fy_for_txn

    # Date-derived, matching the posting path. Scoping on job__financial_year
    # put a July transaction from an FY2025 job into FY2025's contra while
    # posting had sent it to FY2026. See core/txn_periods.py.
    fys = entity_financial_years(fy.entity)
    confirmed_txns = [
        t for t in PendingTransaction.objects.filter(
            job__entity=fy.entity, is_confirmed=True, posted_to_tb=True,
        ).select_related('job', 'job__entity')
        if resolve_fy_for_txn(t, fys) == fy
    ]
```

Then delete the `if not confirmed_txns.exists(): return {...}` early return entirely — an empty set must still zero a vacated row, which is what Step 4 adds — and change the grouping loop's iterable from `confirmed_txns.select_related('job')` to `confirmed_txns`.

- [ ] **Step 4: Zero the rows the transactions have left**

Immediately before the `return` at the end of `_recalc_bank_contra`, add:

```python
    # A bank row whose transactions have all moved to another year (or been
    # deleted) must go to zero. Without this the old figure stands forever and
    # the year-end realignment above could never take effect.
    _zero_vacated_bank_rows(fy, set(groups))
```

And define the helper directly above `_recalc_bank_contra`:

```python
def _zero_vacated_bank_rows(fy, live_bank_codes):
    """Zero any source='bank_statement' row for a mapped bank account of this
    entity that no longer has transactions in this year."""
    mapped_codes = set(
        BankAccountMapping.objects.filter(entity=fy.entity)
        .values_list('tb_account_code', flat=True)
    )
    stale = TrialBalanceLine.objects.filter(
        financial_year=fy,
        account_code__in=mapped_codes - live_bank_codes,
        source='bank_statement',
        is_adjustment=False,
    )
    for line in stale:
        line.debit = Decimal('0')
        line.credit = Decimal('0')
        line.closing_balance = (line.opening_balance or Decimal('0'))
        line.save(update_fields=["debit", "credit", "closing_balance"])
```

The `groups` dict is built before the `if not groups:` guard; move the `_zero_vacated_bank_rows(fy, set(groups))` call above that guard so an entity whose transactions have all left still gets zeroed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bank_contra_fy -v 2`
Expected: PASS, 4 tests

- [ ] **Step 6: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the Task 1 baseline.

- [ ] **Step 7: Commit**

```bash
git add core/views.py core/tests_bank_contra_fy.py
git commit -m "fix: group the bank contra by the same year rule as posting"
```

---

### Task 4: Finish the rebuild primitive

**Files:**
- Modify: `core/views.py:1150-1264` (`_recalculate_bank_tb_lines`)
- Create: `core/tests_bank_tb_rebuild.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 3.
- Produces:
  - `_recalculate_bank_tb_lines(fy) -> dict` — now returns `{"status": "ok"}` or `{"status": "entangled", "codes": [...]}` instead of `None`. Tasks 8 and 9 check the status.
  - `_bank_tb_totals(fy, fys=None) -> dict` — the aggregation rule, shared with Task 5's audit command. Shape:
    `{"accounts": {code: {"debit": Decimal, "credit": Decimal, "name": str}}, "gst": {"debit": Decimal, "credit": Decimal}, "entangled": {code: [source, ...]}}`

- [ ] **Step 1: Write the failing tests**

```python
# core/tests_bank_tb_rebuild.py
"""The rebuild primitive: recompute bank-statement TB rows from the transactions.

Everything in this project depends on this function being right, because once
wired it runs on every edit of every book.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.txn_periods import resolve_fy_for_txn
from core.views import _post_txn_to_tb, _recalculate_bank_tb_lines

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RebuildPrimitiveTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _post(self, date_str, amount, code, gst="0"):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code=code,
                       gst=gst, tax_type="GST on Expenses" if gst != "0" else "")
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=(gst != "0"))
        return txn

    def test_equivalence_with_incremental_posting_on_a_clean_book(self):
        self._post("2025-08-01", "-1100.00", "0400", gst="100.00")
        self._post("2025-08-02", "-550.00", "0400", gst="50.00")
        self._post("2025-08-03", "2200.00", "0510", gst="200.00")
        before = {
            l.account_code: (l.debit, l.credit)
            for l in TrialBalanceLine.objects.filter(
                financial_year=self.fy, source="bank_statement")
        }

        _recalculate_bank_tb_lines(self.fy)

        after = {
            l.account_code: (l.debit, l.credit)
            for l in TrialBalanceLine.objects.filter(
                financial_year=self.fy, source="bank_statement")
        }
        self.assertEqual(before, after)

    def test_creates_a_line_for_an_account_that_has_none(self):
        txn = self._post("2025-08-01", "-110.00", "0400", gst="10.00")
        # Reallocate to an account with no TB row at all.
        txn.confirmed_code = "0450"
        txn.confirmed_name = "Repairs"
        txn.save(update_fields=["confirmed_code", "confirmed_name"])

        _recalculate_bank_tb_lines(self.fy)

        line = bs_line(self.fy, "0450")
        self.assertIsNotNone(line, "the rebuild must create the vacated-to line")
        self.assertEqual(line.debit, D("100.00"))
        self.assertEqual(line.account_name, "Repairs")

    def test_zeroes_the_line_the_transactions_left(self):
        txn = self._post("2025-08-01", "-110.00", "0400", gst="10.00")
        txn.confirmed_code = "0450"
        txn.save(update_fields=["confirmed_code"])

        _recalculate_bank_tb_lines(self.fy)

        self.assertEqual(bs_line(self.fy, "0400").debit, D("0.00"))
        self.assertEqual(bs_line(self.fy, "0400").closing_balance, D("0.00"))

    def test_is_idempotent(self):
        self._post("2025-08-01", "-1100.00", "0400", gst="100.00")
        _recalculate_bank_tb_lines(self.fy)
        once = bs_line(self.fy, "0400").debit
        _recalculate_bank_tb_lines(self.fy)
        _recalculate_bank_tb_lines(self.fy)
        self.assertEqual(bs_line(self.fy, "0400").debit, once)

    def test_manual_journal_lines_are_untouched(self):
        journal = TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="0400",
            account_name="Office costs", source="manual_journal",
            is_adjustment=True, debit=D("777.00"), closing_balance=D("777.00"),
        )
        self._post("2025-08-01", "-110.00", "0400", gst="10.00")

        _recalculate_bank_tb_lines(self.fy)

        journal.refresh_from_db()
        self.assertEqual(journal.debit, D("777.00"))

    def test_opening_balance_is_preserved(self):
        self._post("2025-08-01", "-110.00", "0400", gst="10.00")
        line = bs_line(self.fy, "0400")
        line.opening_balance = D("500.00")
        line.save(update_fields=["opening_balance"])

        _recalculate_bank_tb_lines(self.fy)

        line.refresh_from_db()
        self.assertEqual(line.opening_balance, D("500.00"))
        self.assertEqual(line.closing_balance, D("600.00"))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RebuildYearIsolationTests(TestCase):
    """The rebuild had no year filter at all — it summed every year onto one."""

    def setUp(self):
        self.entity = make_entity()
        self.fy25 = make_fy(self.entity, "FY2025", date(2024, 7, 1), date(2025, 6, 30))
        self.fy26 = make_fy(self.entity, "FY2026", date(2025, 7, 1), date(2026, 6, 30))
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy25)

    def _post(self, date_str, amount, code="0400"):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code=code)
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=False)
        return txn

    def test_rebuilding_one_year_does_not_absorb_the_other(self):
        self._post("2025-06-20", "-100.00")
        self._post("2025-07-03", "-250.00")

        _recalculate_bank_tb_lines(self.fy25)
        _recalculate_bank_tb_lines(self.fy26)

        self.assertEqual(bs_line(self.fy25, "0400").debit, D("100.00"))
        self.assertEqual(bs_line(self.fy26, "0400").debit, D("250.00"))

    def test_rebuilding_one_year_leaves_the_other_untouched(self):
        self._post("2025-06-20", "-100.00")
        self._post("2025-07-03", "-250.00")
        _recalculate_bank_tb_lines(self.fy25)
        _recalculate_bank_tb_lines(self.fy26)
        before = bs_line(self.fy26, "0400").debit

        _recalculate_bank_tb_lines(self.fy25)

        self.assertEqual(bs_line(self.fy26, "0400").debit, before)

    def test_an_unparseable_date_stays_in_the_year_posting_put_it_in(self):
        txn = make_txn(self.job, date_str="n/a", amount="-90.00", code="0400")
        posted_to = resolve_fy_for_txn(txn)
        self.assertEqual(posted_to, self.fy26, "fallback is the most recent year")
        _post_txn_to_tb(txn, posted_to, has_gst=False)

        _recalculate_bank_tb_lines(self.fy26)

        self.assertEqual(bs_line(self.fy26, "0400").debit, D("90.00"),
                         "filtering on the date range would have zeroed this")


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RebuildEntanglementGuardTests(TestCase):
    """A book whose bank postings sit inside journal rows must not be rebuilt."""

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def test_declines_and_writes_nothing_when_entangled(self):
        # The Cerratti shape: a journal row holding bank money, no bank row.
        journal = TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="manual_journal",
            is_adjustment=True, debit=D("62500.00"), closing_balance=D("62500.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-01", amount="-500.00",
                       code="3565")
        txn.posted_to_tb = True
        txn.save(update_fields=["posted_to_tb"])

        result = _recalculate_bank_tb_lines(self.fy)

        self.assertEqual(result["status"], "entangled")
        self.assertIn("3565", result["codes"])
        journal.refresh_from_db()
        self.assertEqual(journal.debit, D("62500.00"))
        self.assertIsNone(bs_line(self.fy, "3565"),
                          "declining means writing nothing, not writing a duplicate")

    def test_runs_normally_once_the_book_is_repaired(self):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="manual_journal",
            is_adjustment=True, debit=D("62000.00"), closing_balance=D("62000.00"),
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="bank_statement",
            is_adjustment=False, debit=D("500.00"), closing_balance=D("500.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-01", amount="-500.00",
                       code="3565")
        txn.posted_to_tb = True
        txn.save(update_fields=["posted_to_tb"])

        result = _recalculate_bank_tb_lines(self.fy)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(bs_line(self.fy, "3565").debit, D("500.00"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bank_tb_rebuild -v 2`
Expected: FAIL — creation, zeroing, year isolation and both guard tests fail; `TypeError: 'NoneType' object is not subscriptable` on the guard tests because the function returns nothing yet.

- [ ] **Step 3: Extract the aggregation rule**

Add above `_recalculate_bank_tb_lines` in `core/views.py`:

```python
def _bank_tb_totals(fy, fys=None):
    """Aggregate this year's posted bank transactions into the figures its
    source='bank_statement' TB rows should hold.

    The single aggregation rule. The rebuild writes these figures; the
    audit_bank_tb_desync command compares them against what is stored. That
    sharing is deliberate and it bounds what the audit proves: it detects a
    trial balance that disagrees with its transactions, not an aggregation rule
    that is itself wrong. The tests in core/tests_bank_tb_rebuild.py are the
    only thing standing behind the rule.
    """
    from review.models import PendingTransaction
    from core.txn_periods import entity_financial_years, resolve_fy_for_txn

    if fys is None:
        fys = entity_financial_years(fy.entity)

    accounts = {}
    gst = {'debit': Decimal("0"), 'credit': Decimal("0")}
    entangled = {}

    posted = PendingTransaction.objects.filter(
        job__entity=fy.entity, is_confirmed=True, posted_to_tb=True,
    ).select_related('job', 'job__entity')

    for txn in posted:
        if resolve_fy_for_txn(txn, fys) != fy:
            continue
        code = txn.confirmed_code
        if not code:
            continue

        has_gst = bool(txn.confirmed_gst_amount and txn.confirmed_gst_amount > 0)
        net_for_tb = txn.net_amount if has_gst else abs(txn.amount)

        bucket = accounts.setdefault(
            code, {'debit': Decimal("0"), 'credit': Decimal("0"),
                   'name': txn.confirmed_name or code},
        )
        if txn.amount < 0:
            bucket['debit'] += net_for_tb
        else:
            bucket['credit'] += net_for_tb

        if has_gst:
            if txn.amount > 0:
                gst['credit'] += txn.confirmed_gst_amount
            else:
                gst['debit'] += txn.confirmed_gst_amount

    # Entanglement: an account that received bank postings but has no
    # bank_statement row to hold them, while carrying a row of another source.
    # Rebuilding such an account would create a duplicate holding the same
    # money. See the spec's "The rebuild assumes a partition that posting does
    # not honour".
    for code in list(accounts) + (['3380'] if (gst['debit'] or gst['credit']) else []):
        rows = TrialBalanceLine.objects.filter(financial_year=fy, account_code=code)
        sources = set(rows.values_list('source', flat=True))
        if sources and 'bank_statement' not in sources:
            entangled[code] = sorted(sources)

    return {'accounts': accounts, 'gst': gst, 'entangled': entangled}
```

- [ ] **Step 4: Rewrite the rebuild on top of it**

Replace the whole body of `_recalculate_bank_tb_lines` (`core/views.py:1150-1264`) with:

```python
def _recalculate_bank_tb_lines(fy):
    """Recompute this year's source='bank_statement' TB rows from the posted
    transactions.

    Rebuild-from-source rather than reverse-and-repost: _post_txn_to_tb
    accumulates with +=, so re-posting a corrected transaction would double-count
    it. The transaction set is the authority; these rows are derived from it.

    Journal-created adjustment lines (source='manual_journal') are never touched
    — they are managed by _apply_journal_line_to_tb / _reverse_journal_line_from_tb.

    Returns {"status": "ok"} or {"status": "entangled", "codes": [...]}, having
    written nothing in the entangled case.
    """
    import logging
    logger = logging.getLogger('core.views')

    totals = _bank_tb_totals(fy)

    if totals['entangled']:
        logger.error(
            "_recalculate_bank_tb_lines: refusing to rebuild entity %s FY %s — "
            "bank postings are entangled with non-bank_statement rows on %s. "
            "Repair by hand (see audit_bank_tb_desync) before rebuilding.",
            fy.entity.pk, fy.pk, totals['entangled'],
        )
        return {"status": "entangled", "codes": sorted(totals['entangled'])}

    # Every bank_statement row this year holds, so rows the transactions have
    # left can be zeroed rather than standing at their old figure forever.
    existing = {
        line.account_code: line
        for line in TrialBalanceLine.objects.filter(
            financial_year=fy, is_adjustment=False, source='bank_statement',
        )
    }

    wanted = {
        code: (t['debit'], t['credit'], t['name'])
        for code, t in totals['accounts'].items()
    }
    if totals['gst']['debit'] or totals['gst']['credit']:
        wanted['3380'] = (
            totals['gst']['debit'], totals['gst']['credit'],
            'GST payable control account',
        )

    for code, (debit, credit, name) in wanted.items():
        tb_line = existing.get(code)
        if tb_line is None:
            tb_line, _created = _get_or_create_tb_line(
                financial_year=fy,
                account_code=code,
                defaults={
                    "account_name": name,
                    "debit": Decimal("0"),
                    "credit": Decimal("0"),
                    "closing_balance": Decimal("0"),
                    "source": "bank_statement",
                },
                bank_statement_only=True,
            )
        ob = tb_line.opening_balance or Decimal("0")
        tb_line.debit = debit
        tb_line.credit = credit
        tb_line.closing_balance = ob + debit - credit
        tb_line.save(update_fields=["debit", "credit", "closing_balance"])

    # Bank contra rows are owned by _recalc_bank_contra, which creates a missing
    # row and consolidates duplicates. One writer, so the two cannot disagree
    # about the same row.
    bank_codes = set(
        BankAccountMapping.objects.filter(entity=fy.entity)
        .values_list('tb_account_code', flat=True)
    )
    _recalc_bank_contra(fy)

    # Zero the rows the transactions have left. Bank contra codes are excluded:
    # _recalc_bank_contra has just set them and does its own zeroing.
    for code, tb_line in existing.items():
        if code in wanted or code in bank_codes:
            continue
        ob = tb_line.opening_balance or Decimal("0")
        tb_line.debit = Decimal("0")
        tb_line.credit = Decimal("0")
        tb_line.closing_balance = ob
        tb_line.save(update_fields=["debit", "credit", "closing_balance"])

    # Clean up orphaned reversal adjustment lines from the old pattern
    TrialBalanceLine.objects.filter(
        financial_year=fy,
        is_adjustment=True,
        source='bank_statement',
        description__startswith='Reversal of ',
    ).delete()

    return {"status": "ok"}
```

`_recalc_bank_contra` is defined at `core/views.py:10557`, below this function. Python resolves it at call time, so no forward declaration is needed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bank_tb_rebuild -v 2`
Expected: PASS, 11 tests

- [ ] **Step 6: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the Task 1 baseline.

- [ ] **Step 7: Commit**

```bash
git add core/views.py core/tests_bank_tb_rebuild.py
git commit -m "feat: finish the bank trial-balance rebuild primitive"
```

---

### Task 5: The desync audit command

**Files:**
- Create: `core/management/commands/audit_bank_tb_desync.py`
- Create: `core/tests_audit_bank_tb_desync.py`

**Interfaces:**
- Consumes: `_bank_tb_totals` (Task 4).
- Produces: `manage.py audit_bank_tb_desync [--entity <pk>]`. Exit code 0 when clean, 1 when it finds variance or entanglement. Writes nothing.

This task must land before Tasks 8 and 9 — it is what feeds the manual repair gate.

- [ ] **Step 1: Write the failing test**

```python
# core/tests_audit_bank_tb_desync.py
"""The audit command reports; it never writes."""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.txn_periods import resolve_fy_for_txn
from core.views import _post_txn_to_tb

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class AuditBankTbDesyncTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _post(self, date_str, amount, code="0400"):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code=code)
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=False)
        return txn

    def _run(self):
        out, err = StringIO(), StringIO()
        try:
            call_command("audit_bank_tb_desync", "--entity", str(self.entity.pk),
                         stdout=out, stderr=err)
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue() + err.getvalue()

    def test_clean_book_exits_zero(self):
        self._post("2025-08-01", "-110.00")
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("no variance", output.lower())

    def test_variance_is_reported_and_exits_non_zero(self):
        self._post("2025-08-01", "-110.00")
        line = bs_line(self.fy, "0400")
        line.debit = D("999.00")          # simulate the desync defect
        line.save(update_fields=["debit"])

        code, output = self._run()

        self.assertEqual(code, 1)
        self.assertIn("0400", output)
        self.assertIn("999.00", output)
        self.assertIn("110.00", output)

    def test_the_command_writes_nothing(self):
        self._post("2025-08-01", "-110.00")
        line = bs_line(self.fy, "0400")
        line.debit = D("999.00")
        line.save(update_fields=["debit"])

        self._run()

        line.refresh_from_db()
        self.assertEqual(line.debit, D("999.00"), "the audit must not repair")

    def test_entanglement_is_reported_as_its_own_category(self):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="manual_journal",
            is_adjustment=True, debit=D("62500.00"), closing_balance=D("62500.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-01", amount="-500.00", code="3565")
        txn.posted_to_tb = True
        txn.save(update_fields=["posted_to_tb"])

        code, output = self._run()

        self.assertEqual(code, 1)
        self.assertIn("ENTANGLED", output)
        self.assertIn("3565", output)
        self.assertIn("manual_journal", output)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_audit_bank_tb_desync -v 2`
Expected: FAIL — `CommandError: Unknown command: 'audit_bank_tb_desync'`

- [ ] **Step 3: Write the command**

```python
# core/management/commands/audit_bank_tb_desync.py
"""Report where a book's bank-statement TB rows disagree with its transactions.

Writes nothing. Some entities may have been compensated by hand by an
accountant, and no automated sweep can tell a defect-induced variance from a
deliberate correction — which entities to repair is an accounting decision.
"""
import sys
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import Entity, FinancialYear, TrialBalanceLine
from core.views import _bank_tb_totals

D = Decimal


class Command(BaseCommand):
    help = "Audit bank-statement trial-balance rows against their transactions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--entity", dest="entity", default=None,
            help="Limit the audit to one entity primary key.",
        )

    def handle(self, *args, **options):
        entities = Entity.objects.all()
        if options["entity"]:
            entities = entities.filter(pk=options["entity"])

        variances = []
        entanglements = []

        for entity in entities.order_by("entity_name"):
            for fy in FinancialYear.objects.filter(entity=entity).order_by("start_date"):
                totals = _bank_tb_totals(fy)

                for code, sources in sorted(totals["entangled"].items()):
                    entanglements.append((entity, fy, code, sources))

                wanted = {
                    code: (t["debit"], t["credit"])
                    for code, t in totals["accounts"].items()
                }
                if totals["gst"]["debit"] or totals["gst"]["credit"]:
                    wanted["3380"] = (totals["gst"]["debit"], totals["gst"]["credit"])

                stored = {
                    line.account_code: (line.debit, line.credit)
                    for line in TrialBalanceLine.objects.filter(
                        financial_year=fy, source="bank_statement",
                        is_adjustment=False,
                    )
                }

                for code in sorted(set(wanted) | set(stored)):
                    if code in totals["entangled"]:
                        continue  # already reported, and not comparable
                    want = wanted.get(code, (D("0"), D("0")))
                    have = stored.get(code, (D("0"), D("0")))
                    if want != have:
                        variances.append((entity, fy, code, want, have))

        if entanglements:
            self.stdout.write(self.style.ERROR("\nENTANGLED — repair by hand before rebuilding:"))
            for entity, fy, code, sources in entanglements:
                self.stdout.write(
                    f"  {entity.entity_name} [{entity.pk}] {fy.year_label} "
                    f"account {code}: bank postings have no bank_statement row; "
                    f"rows present are {', '.join(sources)}"
                )

        if variances:
            self.stdout.write(self.style.WARNING("\nVARIANCE — trial balance disagrees with transactions:"))
            for entity, fy, code, want, have in variances:
                self.stdout.write(
                    f"  {entity.entity_name} [{entity.pk}] {fy.year_label} "
                    f"account {code}: transactions say Dr {want[0]} / Cr {want[1]}, "
                    f"trial balance holds Dr {have[0]} / Cr {have[1]}"
                )

        if not entanglements and not variances:
            self.stdout.write(self.style.SUCCESS("no variance and no entanglement found"))
            return

        self.stdout.write(
            f"\n{len(entanglements)} entangled account(s), {len(variances)} variance(s). "
            f"Nothing was written."
        )
        sys.exit(1)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_audit_bank_tb_desync -v 2`
Expected: PASS, 4 tests

- [ ] **Step 5: Run it against production, read-only**

Run: `python3 manage.py audit_bank_tb_desync`
Expected: it reports Veronica Cerratti 3565 and Daniel Habteslassie 4080 as ENTANGLED, plus whatever variance exists. Save the output — it is the input to the repair gate.

```bash
python3 manage.py audit_bank_tb_desync > docs/superpowers/plans/2026-08-16-desync-audit-baseline.txt 2>&1 || true
```

- [ ] **Step 6: Commit**

```bash
git add core/management/commands/audit_bank_tb_desync.py core/tests_audit_bank_tb_desync.py docs/superpowers/plans/2026-08-16-desync-audit-baseline.txt
git commit -m "feat: audit bank trial-balance rows against their transactions"
```

---

### Task 6: Fix the unsynchronised write in the GST treatment endpoints

**Files:**
- Modify: `review/views_enhanced.py:555-607` (`set_gst_treatment`)
- Modify: `review/views_enhanced.py:611-664` (`bulk_set_gst_treatment`)
- Create: `review/tests_gst_treatment_race.py`

**Interfaces:**
- Consumes: `core.tests_bank_tb_fixtures` (Task 1).
- Produces: nothing later tasks import. Task 8 adds the rebuild call to the same function.

- [ ] **Step 1: Write the failing test**

`select_for_update` is a no-op on sqlite, so this proves the narrowed save and nothing about the lock. Task 11's Postgres soak is the only evidence for the lock itself. Say so in the module docstring so nobody later mistakes a green run for proof.

```python
# review/tests_gst_treatment_race.py
"""set_gst_treatment must not clobber the confirmation flags.

It read the row unlocked and saved every column from its in-memory copy, so when
the review screen fired it concurrently with /confirm/ its later save reverted
is_confirmed and posted_to_tb to their pre-confirm values — silently
unconfirming the transaction and orphaning its trial-balance entries.

WHAT THESE TESTS CANNOT PROVE: select_for_update is a no-op on sqlite. These
tests prove the narrowed update_fields save. Only the Postgres end-to-end soak
proves the lock.
"""
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job,
    make_txn,
)

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class SetGstTreatmentDoesNotClobberTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        self.txn = make_txn(self.job, date_str="2025-08-01", amount="-110.00",
                            code="0400", tax_type="GST on Expenses", gst="10.00")
        self.txn.posted_to_tb = True
        self.txn.save(update_fields=["posted_to_tb"])

        User = get_user_model()
        self.user = User.objects.create_user(
            username="gsttester", password="pw", email="gst@example.com")
        self.user.can_do_accounting = True
        self.user.save()
        self.client.force_login(self.user)

    def _post(self, treatment="gst_free"):
        return self.client.post(
            reverse("review:set_gst_treatment", args=[self.txn.pk]),
            data=json.dumps({"gst_treatment": treatment, "is_manual": True}),
            content_type="application/json",
        )

    def test_a_stale_in_memory_copy_cannot_unconfirm_the_row(self):
        """The exact lost-update shape: the view's copy predates the confirm."""
        from review.models import PendingTransaction

        # Simulate the view having loaded the row BEFORE it was confirmed, by
        # unconfirming in memory only and confirming in the database.
        PendingTransaction.objects.filter(pk=self.txn.pk).update(
            is_confirmed=True, posted_to_tb=True)

        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.is_confirmed)
        self.assertTrue(self.txn.posted_to_tb)

    def test_it_still_writes_the_fields_it_owns(self):
        self._post("gst_free")
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.gst_treatment, "gst_free")
        self.assertEqual(self.txn.confirmed_tax_type, "GST Free Expenses")
        self.assertEqual(self.txn.gst_amount, D("0.00"))

    def test_bulk_endpoint_does_not_clobber_either(self):
        response = self.client.post(
            reverse("review:bulk_set_gst_treatment", args=[self.job.pk]),
            data=json.dumps({"transaction_ids": [str(self.txn.pk)],
                             "gst_treatment": "gst_free"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.is_confirmed)
        self.assertTrue(self.txn.posted_to_tb)
        self.assertEqual(self.txn.gst_treatment, "gst_free")
```

Check the two URL names against `review/urls.py` before running; use whatever the project actually registered.

- [ ] **Step 2: Run the test to verify it fails**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test review.tests_gst_treatment_race -v 2`
Expected: FAIL — `is_confirmed` and `posted_to_tb` come back False.

- [ ] **Step 3: Lock the row and narrow the save in `set_gst_treatment`**

Add at the top of `review/views_enhanced.py` if not already imported: `from django.db import transaction as db_transaction`.

Replace the body from `txn = get_object_or_404(PendingTransaction, pk=pk)` down to `txn.save()` with:

```python
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    treatment = data.get("gst_treatment", "")
    valid_treatments = {"taxable", "gst_free", "input_taxed", "out_of_scope", "not_registered", ""}
    if treatment not in valid_treatments:
        return JsonResponse({"status": "error", "message": f"Invalid GST treatment: {treatment}"}, status=400)

    # The review screen fires this endpoint concurrently with /confirm/, which
    # takes select_for_update inside an atomic block specifically to make
    # confirmation race-safe. An unlocked full-row save here defeated that lock
    # and reverted is_confirmed / posted_to_tb. Take the same lock, and write
    # only the fields this endpoint owns so ordering cannot matter.
    with db_transaction.atomic():
        txn = get_object_or_404(
            PendingTransaction.objects.select_for_update(), pk=pk
        )
        get_review_job_for_user(request, txn.job_id)  # enforce access (B2 IDOR)

        txn.gst_treatment = treatment
        txn.is_gst_manual = data.get("is_manual", True)

        # Map to legacy tax type for backward compatibility
        TREATMENT_TO_TAX_TYPE = {
            "taxable": "GST on Expenses" if txn.amount < 0 else "GST on Income",
            "gst_free": "GST Free Expenses" if txn.amount < 0 else "GST Free Income",
            "input_taxed": "Input Taxed",
            "out_of_scope": "BAS Excluded",
            "not_registered": "N-T",
        }
        if treatment:
            legacy_tax = TREATMENT_TO_TAX_TYPE.get(treatment, "")
            txn.confirmed_tax_type = legacy_tax
            if not txn.ai_suggested_tax_type:
                txn.ai_suggested_tax_type = legacy_tax

        # Reset creditable percentage for non-taxable treatments (AC-APT-01)
        if treatment in ("gst_free", "input_taxed", "out_of_scope", "not_registered"):
            txn.creditable_percentage = Decimal("0")

        _recalculate_gst(txn, txn.job.is_gst_registered)
        txn.save(update_fields=[
            "gst_treatment", "is_gst_manual", "confirmed_tax_type",
            "ai_suggested_tax_type", "creditable_percentage",
            "gst_amount", "net_amount", "confirmed_gst_amount",
        ])
```

Read `_recalculate_gst` first and confirm which fields it mutates; the `update_fields` list must name every one of them and nothing else. If it writes a field not listed here, add it. If it writes `is_confirmed` or `posted_to_tb`, stop and raise it — that would be a separate defect.

- [ ] **Step 4: Do the same for `bulk_set_gst_treatment`**

Wrap the `for txn in txns:` loop in `with db_transaction.atomic():`, re-fetch the rows locked, and narrow the save:

```python
    updated_ids = []
    with db_transaction.atomic():
        txns = job.transactions.select_for_update().filter(pk__in=txn_ids)
        for txn in txns:
            txn.gst_treatment = treatment
            txn.is_gst_manual = True
            txn.confirmed_tax_type = TREATMENT_TO_TAX_TYPE[treatment](txn)

            if treatment in ("gst_free", "input_taxed", "out_of_scope", "not_registered"):
                txn.creditable_percentage = Decimal("0")

            _recalculate_gst(txn, job.is_gst_registered)
            txn.save(update_fields=[
                "gst_treatment", "is_gst_manual", "confirmed_tax_type",
                "creditable_percentage", "gst_amount", "net_amount",
                "confirmed_gst_amount",
            ])
            updated_ids.append(str(txn.pk))
```

Move the `TREATMENT_TO_TAX_TYPE` dict above the `with` block so it is defined before use.

- [ ] **Step 5: Run the test to verify it passes**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test review.tests_gst_treatment_race -v 2`
Expected: PASS, 3 tests

- [ ] **Step 6: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the Task 1 baseline.

- [ ] **Step 7: Commit**

```bash
git add review/views_enhanced.py review/tests_gst_treatment_race.py
git commit -m "fix: lock and narrow the GST treatment writes"
```

---

### Task 7: The amended-since-lodgement flag

**Files:**
- Modify: `core/models.py:3762` (add three fields to `BASPeriod`)
- Create: `core/migrations/0142_basperiod_amended_since_lodgement.py` (generated)
- Modify: `core/txn_periods.py` (add `resolve_bas_period_for_txn`)
- Modify: `core/views_bas.py:274` (clear the flag on lodge)
- Modify: `core/views_bas.py:166-172` (badge alongside the status colours)
- Modify: `templates/core/gst_activity_statement.html` (render the badge)
- Create: `core/tests_bas_amended_flag.py`

**Interfaces:**
- Consumes: `core.txn_periods` (Task 1).
- Produces: `core.txn_periods.flag_period_amended(txn, user) -> BASPeriod | None` — sets the flag when the transaction's date falls inside a lodged period, and returns that period; returns None otherwise. Tasks 8 and 9 call it at every correction site.

- [ ] **Step 1: Add the model fields**

In `core/models.py`, directly after `unlodged_at` on `BASPeriod`:

```python
    # Amendment audit — a correction landing inside a lodged period is allowed,
    # but the lodged snapshot stays frozen, so the two diverge. Flag it rather
    # than let the divergence be silent.
    amended_since_lodgement = models.BooleanField(
        default=False,
        help_text="A transaction in this period changed after it was lodged",
    )
    amended_at = models.DateTimeField(null=True, blank=True)
    amended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="amended_bas_periods",
    )
```

- [ ] **Step 2: Generate and inspect the migration**

Run: `python3 manage.py makemigrations core --name basperiod_amended_since_lodgement`
Expected: `core/migrations/0142_basperiod_amended_since_lodgement.py` adding exactly three fields. Read it. If it contains anything else, another model has uncommitted changes — stop and resolve that first.

- [ ] **Step 3: Write the failing test**

```python
# core/tests_bas_amended_flag.py
"""A correction inside a lodged period is allowed, and flagged."""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from core.bas_utils import ensure_bas_periods
from core.models import BASPeriod
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job,
    make_txn,
)
from core.txn_periods import flag_period_amended, resolve_bas_period_for_txn

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class AmendedFlagTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        ensure_bas_periods(self.fy, "quarterly")
        self.q1 = BASPeriod.objects.get(
            financial_year=self.fy, period_type="quarterly", period_number=1)
        User = get_user_model()
        self.user = User.objects.create_user(
            username="amender", password="pw", email="a@example.com")

    def _txn(self, date_str):
        return make_txn(self.job, date_str=date_str, amount="-110.00", code="0400")

    def test_resolves_a_transaction_to_its_period(self):
        txn = self._txn("2025-08-14")   # Q1 is Jul-Sep
        self.assertEqual(resolve_bas_period_for_txn(txn), self.q1)

    def test_flag_sets_inside_a_lodged_period(self):
        self.q1.status = "lodged"
        self.q1.lodged_at = timezone.now()
        self.q1.save()

        period = flag_period_amended(self._txn("2025-08-14"), self.user)

        self.assertEqual(period, self.q1)
        self.q1.refresh_from_db()
        self.assertTrue(self.q1.amended_since_lodgement)
        self.assertEqual(self.q1.amended_by, self.user)
        self.assertIsNotNone(self.q1.amended_at)

    def test_flag_stays_clear_outside_a_lodged_period(self):
        self.assertIsNone(flag_period_amended(self._txn("2025-08-14"), self.user))
        self.q1.refresh_from_db()
        self.assertFalse(self.q1.amended_since_lodgement)

    def test_the_lodged_snapshot_is_never_written(self):
        self.q1.status = "lodged"
        self.q1.snapshot_1a = D("1234.00")
        self.q1.save()

        flag_period_amended(self._txn("2025-08-14"), self.user)

        self.q1.refresh_from_db()
        self.assertEqual(self.q1.snapshot_1a, D("1234.00"))

    def test_an_unparseable_date_flags_nothing(self):
        self.q1.status = "lodged"
        self.q1.save()
        self.assertIsNone(flag_period_amended(self._txn("n/a"), self.user))

    def test_no_period_row_means_nothing_to_flag(self):
        BASPeriod.objects.all().delete()
        self.assertIsNone(flag_period_amended(self._txn("2025-08-14"), self.user))
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bas_amended_flag -v 2`
Expected: FAIL — `ImportError: cannot import name 'flag_period_amended'`

- [ ] **Step 5: Add the two functions to `core/txn_periods.py`**

```python
def resolve_bas_period_for_txn(txn):
    """Return the BASPeriod covering this transaction's date, or None.

    Periods are created lazily, so a transaction may fall in a range with no
    row. That is not a case to handle: no row means no lodgement, so there is
    nothing to flag.
    """
    from core.models import BASPeriod

    txn_date = parse_txn_date(txn.date)
    if not txn_date:
        return None
    fy = resolve_fy_for_txn(txn)
    if not fy:
        return None
    return BASPeriod.objects.filter(
        financial_year=fy, period_start__lte=txn_date, period_end__gte=txn_date,
    ).first()


def flag_period_amended(txn, user=None):
    """Mark the transaction's BAS period as amended, if it is lodged.

    A correction inside a lodged period is allowed — the BAS detail tabs offer
    that workflow today. The trial balance rebuilds, the lodged snapshot stays
    frozen, and this flag makes the resulting divergence visible instead of
    silent. Returns the period it flagged, or None.
    """
    from django.utils import timezone

    period = resolve_bas_period_for_txn(txn)
    if period is None or period.status != "lodged":
        return None
    period.amended_since_lodgement = True
    period.amended_at = timezone.now()
    period.amended_by = user if (user and user.is_authenticated) else None
    period.save(update_fields=[
        "amended_since_lodgement", "amended_at", "amended_by",
    ])
    return period
```

- [ ] **Step 6: Clear the flag on lodge**

In `core/views_bas.py`, in `bas_lodge_period`, alongside the other `bp.` assignments before `bp.save()`:

```python
    # A fresh lodgement takes a fresh snapshot that incorporates the amendment,
    # so the flag has served its purpose. Unlodge deliberately preserves it —
    # that is when the audit trail matters most.
    bp.amended_since_lodgement = False
    bp.amended_at = None
    bp.amended_by = None
```

Add nothing to `bas_unlodge_period`.

- [ ] **Step 7: Surface the badge**

In `core/views_bas.py` after the `status_colours` dict, add `"amended": "#B45309"` to it, and pass the period rows' `amended_since_lodgement` through to the template context as they already carry `status`. In `templates/core/gst_activity_statement.html`, beside the existing status pill for each period, render:

```html
{% if period.period.amended_since_lodgement %}
  <span class="badge" style="background:#B45309;color:#fff"
        title="A transaction in this period was corrected after lodgement. The lodged snapshot is unchanged.">Amended</span>
{% endif %}
```

Match the surrounding markup — read the existing status pill and mirror its classes rather than inventing new ones.

- [ ] **Step 8: Run the test to verify it passes**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bas_amended_flag -v 2`
Expected: PASS, 6 tests

- [ ] **Step 9: Apply the migration and verify the whole suite**

```bash
DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1
python3 manage.py migrate core   # production; the field is nullable/defaulted so it is safe
```
Expected: failure set identical to the Task 1 baseline; the migration applies without a table rewrite prompt.

- [ ] **Step 10: Commit**

```bash
git add core/models.py core/migrations/0142_*.py core/txn_periods.py core/views_bas.py core/tests_bas_amended_flag.py templates/core/gst_activity_statement.html
git commit -m "feat: flag a BAS period amended after lodgement"
```

---

## Repair Gate — not a code task

**Tasks 8 and 9 must not merge until this is signed off.**

Tasks 1–7 change no correction path: the rebuild exists, is proven and is called from nowhere. Tasks 8 and 9 wire it into live requests, and from that moment it runs on every edit of every book.

Two entities cannot be rebuilt safely yet:

| Entity | Account | State | Posted transactions |
|---|---|---|---|
| Veronica Cerratti Pty Ltd | 3565 | 3 × `manual_journal` adjustment rows, no `bank_statement` row | 72, $419,356.03 |
| Daniel Habteslassie | 4080 | 1 × `manual_journal` adjustment row, no `bank_statement` row | 25, $237,464.00 |

Task 4's guard means the rebuild declines on these rather than corrupting them, so wiring is not *unsafe* — but corrections on those entities keep today's desync until someone unpicks the journal rows and decides what each account should read. That is an accounting decision, not an engineering one.

**To close the gate:**

1. Run `python3 manage.py audit_bank_tb_desync` and hand Elio the ENTANGLED section.
2. Elio decides, per account, how much of each journal row is bank posting and how much is genuine journal.
3. Apply that decision by hand, with a backup of the affected rows written to `data_fixes/` first, following the existing pattern in that directory.
4. Re-run the audit. The ENTANGLED section must be empty.
5. Elio confirms in writing that the two entities read correctly.

Only then proceed.

---

### Task 8: Wire the rebuild into the review paths

**Files:**
- Modify: `review/views.py:692-700` (`confirm_transaction`)
- Modify: `review/views_enhanced.py` (`set_gst_treatment`, end of the atomic block)
- Create: `review/tests_correction_reposts.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: nothing later tasks import.

- [ ] **Step 1: Write the failing test**

```python
# review/tests_correction_reposts.py
"""Correcting an already-posted transaction must move the trial balance.

confirm_transaction guards posting on posted_to_tb — correct for stopping a
double-click double-post, and wrong for a correction, because the guard cannot
tell "post this twice" from "this changed, post it again".
"""
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class CorrectionRepostsTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        self.txn = make_txn(self.job, date_str="2025-08-14", amount="-1100.00",
                            code="", tax_type="")
        self.txn.is_confirmed = False
        self.txn.save(update_fields=["is_confirmed"])

        User = get_user_model()
        self.user = User.objects.create_user(
            username="corrector", password="pw", email="c@example.com")
        self.user.can_do_accounting = True
        self.user.save()
        self.client.force_login(self.user)

    def _confirm(self, code, tax_type, name="Account"):
        return self.client.post(
            reverse("review:confirm_transaction", args=[self.txn.pk]),
            data=json.dumps({"confirmed_code": code, "confirmed_name": name,
                             "confirmed_tax_type": tax_type}),
            content_type="application/json",
        )

    def test_a_second_confirm_moves_the_trial_balance(self):
        self._confirm("0400", "GST on Expenses")
        self.assertEqual(bs_line(self.fy, "0400").debit, D("1000.00"))

        self._confirm("0450", "GST on Expenses", name="Repairs")

        self.assertEqual(bs_line(self.fy, "0400").debit, D("0.00"),
                         "the vacated account must go to zero")
        self.assertEqual(bs_line(self.fy, "0450").debit, D("1000.00"))

    def test_changing_only_the_tax_type_moves_the_gst_control_account(self):
        self._confirm("0400", "GST on Expenses")
        self.assertEqual(bs_line(self.fy, "3380").debit, D("100.00"))

        self._confirm("0400", "GST Free Expenses")

        self.assertEqual(bs_line(self.fy, "3380").debit, D("0.00"))
        self.assertEqual(bs_line(self.fy, "0400").debit, D("1100.00"),
                         "with no GST the full gross hits the expense account")

    def test_the_double_post_guard_still_holds(self):
        """Confirming twice with identical values must not double the figure."""
        self._confirm("0400", "GST on Expenses")
        self._confirm("0400", "GST on Expenses")
        self.assertEqual(bs_line(self.fy, "0400").debit, D("1000.00"))

    def test_the_bank_contra_follows_the_correction(self):
        self._confirm("0400", "GST on Expenses")
        self._confirm("0450", "GST on Expenses", name="Repairs")
        self.assertEqual(bs_line(self.fy, "1100").credit, D("1100.00"),
                         "the gross that moved through the bank is unchanged")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test review.tests_correction_reposts -v 2`
Expected: FAIL — after the second confirm, `0400` still holds 1000.00 and `0450` has no row.

- [ ] **Step 3: Rebuild after a corrected confirm**

In `review/views.py`, replace the posting block inside the atomic (`:692-700`) with:

```python
        # Post to trial balance (expense/income + GST + bank contra).
        # Re-check the freshly-locked row's posted flag before posting (A18).
        # The guard is correct for its own purpose — it stops a double-click
        # double-posting — but it cannot tell a correction from a repeat, so a
        # row that was already posted goes down the rebuild path instead.
        if not txn.posted_to_tb:
            _post_confirmed_txn_to_tb(txn)
            if txn.job and txn.job.financial_year:
                from core.views import _recalc_bank_contra
                _recalc_bank_contra(txn.job.financial_year)
        else:
            from core.txn_periods import flag_period_amended, resolve_fy_for_txn
            from core.views import _recalculate_bank_tb_lines

            target_fy = resolve_fy_for_txn(txn)
            if target_fy:
                result = _recalculate_bank_tb_lines(target_fy)
                if result.get("status") == "entangled":
                    logger.error(
                        "confirm_transaction: trial balance not rebuilt for txn %s "
                        "— entity is entangled on %s", txn.pk, result["codes"],
                    )
            flag_period_amended(txn, request.user)
```

`_recalc_bank_contra` is called inside `_recalculate_bank_tb_lines`, so the rebuild branch must not call it again — it would be harmless but misleading.

Confirm a module-level `logger` exists in `review/views.py`; if not, add `logger = logging.getLogger(__name__)` near the imports.

- [ ] **Step 4: Rebuild after a GST treatment change on a posted row**

At the end of the atomic block in `set_gst_treatment`, after `txn.save(update_fields=[...])`:

```python
        # A GST treatment change on an already-posted row changes what should be
        # in the ledger. Rebuild rather than re-post: _post_txn_to_tb accumulates.
        if txn.posted_to_tb:
            from core.txn_periods import flag_period_amended, resolve_fy_for_txn
            from core.views import _recalculate_bank_tb_lines

            target_fy = resolve_fy_for_txn(txn)
            if target_fy:
                _recalculate_bank_tb_lines(target_fy)
            flag_period_amended(txn, request.user)
```

Do the same at the end of `bulk_set_gst_treatment`'s atomic block, **once after the loop**, using the last transaction's year — or better, collect `{resolve_fy_for_txn(t) for t in txns}` during the loop and rebuild each distinct year once.

- [ ] **Step 5: Run the test to verify it passes**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test review.tests_correction_reposts review.tests_gst_treatment_race -v 2`
Expected: PASS

- [ ] **Step 6: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the Task 1 baseline. In particular the existing Tier 2 double-post test must stay green.

- [ ] **Step 7: Commit**

```bash
git add review/views.py review/views_enhanced.py review/tests_correction_reposts.py
git commit -m "fix: re-post the trial balance when a confirmed transaction changes"
```

---

### Task 9: Wire the rebuild into the BAS reallocation paths

**Files:**
- Modify: `core/views_bas.py:913-930` (`bas_reallocate_transaction`)
- Modify: `core/views_bas.py:985-1000` (`bas_bulk_reallocate`, after the loop)
- Create: `core/tests_bas_reallocate_posting.py`

**Interfaces:**
- Consumes: Tasks 1–8.
- Produces: nothing later tasks import.

- [ ] **Step 1: Write the failing test**

```python
# core/tests_bas_reallocate_posting.py
"""Reallocating from the BAS screen must move the trial balance.

These two endpoints had no posting logic at all: they updated the transaction's
confirmed fields and returned. The BAS reads those fields and saw the change;
the financial statements read the trial balance and did not.
"""
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.txn_periods import resolve_fy_for_txn
from core.views import _post_txn_to_tb

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasReallocatePostingTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        self.txn = make_txn(self.job, date_str="2025-08-14", amount="-1100.00",
                            code="0400", tax_type="GST on Expenses", gst="100.00")
        _post_txn_to_tb(self.txn, resolve_fy_for_txn(self.txn), has_gst=True)

        User = get_user_model()
        self.user = User.objects.create_user(
            username="reallocator", password="pw", email="r@example.com")
        self.user.can_do_accounting = True
        self.user.save()
        self.client.force_login(self.user)

    def test_single_reallocation_moves_the_trial_balance(self):
        self.assertEqual(bs_line(self.fy, "0400").debit, D("1000.00"))

        response = self.client.post(
            reverse("core:bas_reallocate_transaction", args=[self.fy.pk]),
            data=json.dumps({"txn_id": str(self.txn.pk), "account_code": "0450",
                             "account_name": "Repairs",
                             "tax_type": "GST on Expenses"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(bs_line(self.fy, "0400").debit, D("0.00"))
        self.assertEqual(bs_line(self.fy, "0450").debit, D("1000.00"))

    def test_bulk_reallocation_moves_the_trial_balance(self):
        second = make_txn(self.job, date_str="2025-08-15", amount="-2200.00",
                          code="0400", tax_type="GST on Expenses", gst="200.00")
        _post_txn_to_tb(second, resolve_fy_for_txn(second), has_gst=True)

        response = self.client.post(
            reverse("core:bas_bulk_reallocate", args=[self.fy.pk]),
            data=json.dumps({"txn_ids": [str(self.txn.pk), str(second.pk)],
                             "account_code": "0450", "account_name": "Repairs",
                             "tax_type": "GST on Expenses"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(bs_line(self.fy, "0400").debit, D("0.00"))
        self.assertEqual(bs_line(self.fy, "0450").debit, D("3000.00"))

    def test_bulk_rebuilds_once_not_once_per_transaction(self):
        """The rebuild is O(all posted transactions); n of them is n rebuilds.

        Patched at core.views_bas, which is why Step 3 imports the function at
        module level there rather than inside the view.
        """
        from unittest.mock import patch

        import core.views_bas as views_bas

        second = make_txn(self.job, date_str="2025-08-15", amount="-2200.00",
                          code="0400", tax_type="GST on Expenses", gst="200.00")
        _post_txn_to_tb(second, resolve_fy_for_txn(second), has_gst=True)

        with patch.object(views_bas, "_recalculate_bank_tb_lines",
                          wraps=views_bas._recalculate_bank_tb_lines) as spy:
            self.client.post(
                reverse("core:bas_bulk_reallocate", args=[self.fy.pk]),
                data=json.dumps({"txn_ids": [str(self.txn.pk), str(second.pk)],
                                 "account_code": "0450", "account_name": "Repairs",
                                 "tax_type": "GST on Expenses"}),
                content_type="application/json",
            )

        self.assertEqual(spy.call_count, 1)

    def test_reallocating_to_a_gst_free_type_clears_the_gst_control(self):
        self.assertEqual(bs_line(self.fy, "3380").debit, D("100.00"))

        self.client.post(
            reverse("core:bas_reallocate_transaction", args=[self.fy.pk]),
            data=json.dumps({"txn_id": str(self.txn.pk), "account_code": "0400",
                             "account_name": "Office costs",
                             "tax_type": "GST Free Expenses"}),
            content_type="application/json",
        )

        self.assertEqual(bs_line(self.fy, "3380").debit, D("0.00"))
        self.assertEqual(bs_line(self.fy, "0400").debit, D("1100.00"))
```

Check the two URL names against `core/urls.py` before running; use whatever the project actually registered.

- [ ] **Step 2: Run the test to verify it fails**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bas_reallocate_posting -v 2`
Expected: FAIL — `0400` still holds 1000.00 and `0450` has no row.

- [ ] **Step 3: Rebuild after a single reallocation**

Add to the module-level imports of `core/views_bas.py`:

```python
from core.txn_periods import flag_period_amended, resolve_fy_for_txn
from core.views import _recalculate_bank_tb_lines
```

If that import is circular at module load, put it inside a small module-level helper in `core/views_bas.py` instead and patch that helper in the test.

In `bas_reallocate_transaction`, immediately after `txn.save()`:

```python
    # The trial balance is derived from the transactions, so a reallocation has
    # to rebuild it. This endpoint used to change the transaction and stop,
    # which is why the BAS and the financial statements could disagree.
    target_fy = resolve_fy_for_txn(txn)
    if target_fy:
        result = _recalculate_bank_tb_lines(target_fy)
        if result.get("status") == "entangled":
            return JsonResponse({
                "ok": False,
                "error": (
                    "This entity's ledger cannot be rebuilt automatically "
                    f"(accounts {', '.join(result['codes'])} need repair). "
                    "The reallocation was saved but the trial balance was not "
                    "updated — contact the accounting team."
                ),
            }, status=409)
    flag_period_amended(txn, request.user)
```

- [ ] **Step 4: Rebuild once after a bulk reallocation**

In `bas_bulk_reallocate`, collect years inside the loop and rebuild after it:

```python
    updated = []
    touched_fys = set()
    for txn in txns:
        ...                       # existing body, unchanged
        touched_fys.add(resolve_fy_for_txn(txn))
        flag_period_amended(txn, request.user)

    # Once per year, after the loop — not once per transaction. The rebuild is
    # O(all posted transactions), so calling it inside the loop turns a bulk
    # reallocation of n transactions into n full rebuilds.
    for target_fy in touched_fys:
        if target_fy:
            _recalculate_bank_tb_lines(target_fy)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core.tests_bas_reallocate_posting -v 2`
Expected: PASS

- [ ] **Step 6: Verify nothing else regressed**

Run: `DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1`
Expected: failure set identical to the Task 1 baseline.

- [ ] **Step 7: Commit**

```bash
git add core/views_bas.py core/tests_bas_reallocate_posting.py
git commit -m "fix: rebuild the trial balance when the BAS screen reallocates"
```

---

### Task 10: End-to-end coverage, and retiring the test workaround

**Files:**
- Modify: `e2e/tier2/bank_to_bas_flow.ts` (the `ALLOCATIONS` table and its comment)
- Modify: `e2e/tier2/bank_to_bas_company.spec.ts` (four new tests)

**Interfaces:**
- Consumes: Tasks 1–9, all merged.
- Produces: nothing.

- [ ] **Step 1: Read the current suite and its workaround**

Read `e2e/tier2/bank_to_bas_flow.ts` in full, and the `ALLOCATIONS` comment in particular. It records that every account code in the table must carry no mapped `tax_code`, because an account whose tax code resolves fires `/gst-treatment/` concurrently with `/confirm/` and triggers the race. That is the workaround Task 6 retires.

- [ ] **Step 2: Put a mapped-tax-code account back in the table**

Change one `ALLOCATIONS` entry to `0510` (Sales), which carries a mapped `tax_code`, and rewrite the comment to say what it now means: this entry exercises the account picker's auto-apply path, which was unreachable while the race existed, and it is a regression test for that race.

- [ ] **Step 3: Add the four end-to-end tests**

To `e2e/tier2/bank_to_bas_company.spec.ts`, following the file's existing structure and helpers:

1. Correct an already-confirmed transaction on the review screen; assert the trial balance moved and the BAS reconciles to it.
2. Reallocate through the BAS detail tab; assert the trial balance moved.
3. Allocate to `0510`; assert the transaction posts exactly once (the confirmed count and the account balance both).
4. Lodge a period, then correct a transaction inside it; assert the Amended badge appears and the lodged snapshot figures are unchanged.

- [ ] **Step 4: Run the spec**

Run: `cd e2e && npx playwright test tier2/bank_to_bas_company.spec.ts --workers=1`
Expected: PASS, including the four new tests. `known_failures.json` stays empty.

- [ ] **Step 5: Commit**

```bash
git add e2e/tier2/bank_to_bas_flow.ts e2e/tier2/bank_to_bas_company.spec.ts
git commit -m "test: cover correction and reallocation end to end"
```

---

### Task 11: The soak, the documents, and the close

**Files:**
- Modify: `e2e/README.md` (limits 3, 4, 5, and the `ALLOCATIONS` note)
- Modify: `docs/superpowers/specs/2026-08-14-bank-to-bas-tier2-design.md` ("what this does not cover")
- Modify: `FINDING_bas_ledger_desync_on_reallocation.md` (status)

- [ ] **Step 1: Run the soak**

The original race failed intermittently — fail / pass / fail across three consecutive runs — so one green run is not evidence. Run the spec ten consecutive times, at `--workers=1`, off-peak, because production shares this host.

```bash
cd e2e
for i in $(seq 1 10); do
  echo "=== run $i ==="
  npx playwright test tier2/bank_to_bas_company.spec.ts --workers=1 || echo "RUN $i FAILED"
done
```
Expected: ten passes. A single failure means the lock is not fixed — stop and reopen Task 6.

This soak is a one-off acceptance gate, not an addition to the standing suite.

- [ ] **Step 2: Rewrite the documents that describe these defects as accepted**

Each of these currently tells a reader the desync is known and tolerated. Leaving them is worse than having no note:

- `e2e/README.md` — limits 3, 4 and 5, and the load-bearing `ALLOCATIONS` note
- `docs/superpowers/specs/2026-08-14-bank-to-bas-tier2-design.md` — "what this does not cover"
- `FINDING_bas_ledger_desync_on_reallocation.md` — change **Status** to fixed, name the commits, and keep the finding body as the historical record

- [ ] **Step 3: Run the full verification**

Every gate from the spec's Verification section:

```bash
DATABASE_URL="sqlite:////tmp/statementhub_test.sqlite3" python3 manage.py test core review integrations -v 1
cd e2e && npx playwright test tier2/bank_to_bas_company.spec.ts --workers=1
cd e2e && npx playwright test tier1 --workers=1
cd /opt/statementhub && python3 manage.py audit_bank_tb_desync
```

Expected: no new failures against the baseline; Tier 1's 215 tests pass; the audit reports zero entanglement.

- [ ] **Step 4: Commit and open the PR**

```bash
git add -A
git commit -m "docs: the BAS-to-ledger desync is closed"
git push origin fix/bas-tb-desync
```

Open the PR by hand — there is no `gh` CLI on this host, and merging to main auto-deploys, so do not merge until Elio has reviewed the soak output and the audit report.

---

## Self-Review

**Spec coverage.** Workstream 0 → Task 2. Workstream 1 → Tasks 1, 3, 4. Workstream 2 → Tasks 6, 8, 9. Workstream 3 → Task 7. Workstream 4 → Task 5. Testing groups 1–8 → Tasks 1–9. Tier 2 tests 7–10 → Task 10. Documentation and soak → Task 11. The repair gate → the Repair Gate section. No spec section is unimplemented.

**Known soft spots, called out rather than hidden:**

1. Task 7 Step 7 says "match the surrounding markup" for the badge rather than giving exact HTML, because the template's pill markup has not been read. The implementer must read it first.
2. Task 6 Step 3 says to read `_recalculate_gst` and confirm the `update_fields` list names every field it writes. That list is a guess from the field names in the endpoint; getting it wrong silently drops a write.
3. Task 9 Step 3 flags a possible circular import between `core/views_bas.py` and `core/views.py`. If it bites, the fallback is stated.
4. `bulk_set_gst_treatment` (Task 8 Step 4) rebuilds per distinct year, which for a single-year job is one rebuild. The plan does not test that count; Task 9 does test it for the BAS bulk path, which shares the shape.
