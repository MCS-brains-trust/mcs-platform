# Cashbook GST Journals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an accountant journal a cash-basis period straight into the trial balance with GST accounted for inside the journal, so the `3380` control account ties to the BAS to the cent.

**Architecture:** A new `JournalType.CASHBOOK` gates the feature. Its lines carry a `tax_code` and a `gst_amount`; the accountant keys **gross** and a save-time split rewrites each line to net and appends exactly two generated `3380` control lines. Because the journal's own rows end up net-plus-control, `_post_journal_to_tb` is untouched. The BAS engine reads the stored per-line GST, which becomes authoritative for 1A/1B.

**Tech Stack:** Django 5, Postgres (sqlite for tests), Decimal arithmetic at `ROUND_HALF_UP`, Bootstrap 5 templates with vanilla JS.

**Spec:** `docs/superpowers/specs/2026-08-31-cashbook-gst-journals-design.md`

## Global Constraints

- **Branch:** `feat/cashbook-gst-journals`. Work in the worktree, never in `/opt/statementhub` — that checkout is what gunicorn serves.
- **Run tests with a sqlite override**, never against the default DB:
  `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core -v 2`
- **Run `python3 manage.py collectstatic --noinput` once in the worktree before treating any failure count as a baseline** — a fresh worktree has no `staticfiles/` and template-rendering tests fail on `Missing staticfiles manifest entry`.
- **The suite has a large pre-existing failure baseline.** Compare failure *sets*, not counts. Capture the baseline before changing anything.
- **View tests need two things** or they fail confusingly: `SECURE_SSL_REDIRECT` is on, so every request needs `secure=True`; and `Require2FAMiddleware` needs `session["2fa_verified"] = True` set by hand after `force_login`.
- **GST arithmetic is always `Decimal`, divided by `Decimal("11")`, quantized to `Decimal("0.01")` with `rounding=ROUND_HALF_UP`.** Never float.
- **Never widen a tax-code test to a bare `in TAXABLE_CODES`.** Always normalise through `bas_utils.normalise_tax_treatment` first — account `1946 Tools` carries `'inp'` lowercase in the live chart.
- **`core/views.py:8781 gst_activity_statement` and `:9042 gst_activity_statement_download` are dead code**, already marked `SUPERSEDED — NOT ROUTED` at `core/views.py:8768`. Do not modify them and do not "fix" them to match. Both URL names route to `views_bas`.
- **Do not touch `BulkJournalUpload`.** It creates no `AdjustingJournal` and is out of scope.

---

### Task 1: Model fields and migration

**Files:**
- Modify: `core/models.py:2065-2073` (`JournalType`), `core/models.py:2217-2245` (`JournalLine`)
- Create: `core/migrations/0152_cashbook_gst_journal_lines.py`
- Test: `core/tests_cashbook_gst_split.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AdjustingJournal.JournalType.CASHBOOK == "cashbook"`; `JournalLine.tax_code: str`, `JournalLine.gst_amount: Decimal`, `JournalLine.is_gst_control: bool`.

- [ ] **Step 1: Capture the pre-change failure baseline**

```bash
python3 manage.py collectstatic --noinput
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core 2>&1 | tail -40 > /tmp/baseline_core.txt
grep -E "^(FAIL|ERROR):" /tmp/baseline_core.txt | sort > /tmp/baseline_set.txt
wc -l /tmp/baseline_set.txt
```

Keep `/tmp/baseline_set.txt`. Every later "did I regress?" question is answered by diffing against it, never by comparing counts.

- [ ] **Step 2: Write the failing test**

Create `core/tests_cashbook_gst_split.py`:

```python
from decimal import Decimal

from django.test import TestCase

from core.models import AdjustingJournal, JournalLine


class CashbookModelFieldsTest(TestCase):
    def test_cashbook_journal_type_exists(self):
        self.assertEqual(AdjustingJournal.JournalType.CASHBOOK, "cashbook")
        self.assertIn(
            ("cashbook", "Cashbook (Cash Basis)"),
            AdjustingJournal.JournalType.choices,
        )

    def test_journal_line_gst_fields_default_to_today_behaviour(self):
        line = JournalLine()
        self.assertEqual(line.tax_code, "")
        self.assertEqual(line.gst_amount, Decimal("0"))
        self.assertIs(line.is_gst_control, False)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_cashbook_gst_split -v 2`
Expected: FAIL — `AttributeError: CASHBOOK` on the first test.

- [ ] **Step 4: Add the journal type**

In `core/models.py`, inside `class JournalType(models.TextChoices)` (starts `core/models.py:2065`), add as the second entry so it sits next to GENERAL:

```python
        GENERAL = "general", "General Journal"
        CASHBOOK = "cashbook", "Cashbook (Cash Basis)"
        ADJUSTING = "adjusting", "Adjusting Entry"
```

- [ ] **Step 5: Add the three JournalLine fields**

In `core/models.py`, in `class JournalLine`, immediately after the `credit` field declaration:

```python
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_code = models.CharField(
        max_length=10, blank=True, default="",
        help_text=(
            "MYOB-style GST treatment for this line (GST, INP, FRE, CAP, "
            "N-T, ...). Same vocabulary as ChartOfAccount.tax_code. Blank "
            "means fall back to the account's chart default."
        ),
    )
    gst_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text=(
            "GST on this line. Authoritative: the BAS reports 1A/1B from the "
            "sum of these, not from G8/11. The gross the accountant keyed is "
            "reconstructed as (debit or credit) + gst_amount, which is what "
            "makes the split idempotent."
        ),
    )
    is_gst_control = models.BooleanField(
        default=False,
        help_text=(
            "True for the two 3380 lines generated by the cashbook split. A "
            "structural flag rather than matching on account_code, so an "
            "accountant's own 3380 line (the quarterly ATO payment, say) is "
            "not wiped when the split regenerates."
        ),
    )
```

- [ ] **Step 6: Generate and inspect the migration**

```bash
python3 manage.py makemigrations core --name cashbook_gst_journal_lines
```

Confirm the generated file is `core/migrations/0152_cashbook_gst_journal_lines.py`, contains exactly one `AlterField` on `adjustingjournal.journal_type` and three `AddField`s on `journalline`, and **no other model's changes**. If it picked up anything else, another session has uncommitted model edits — stop and report rather than committing a mixed migration.

- [ ] **Step 7: Run the tests**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_cashbook_gst_split -v 2`
Expected: PASS, 2 tests.

- [ ] **Step 8: Commit**

```bash
git add core/models.py core/migrations/0152_cashbook_gst_journal_lines.py core/tests_cashbook_gst_split.py
git commit -m "feat(gst): add Cashbook journal type and per-line GST fields

A cash-basis journal needs to carry its own GST treatment: JournalLine
gains tax_code, gst_amount and is_gst_control, and JournalType gains
CASHBOOK to gate the behaviour. Defaults are chosen so every existing
line behaves exactly as it does today.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The split engine

**Files:**
- Create: `core/gst_journal.py`
- Test: `core/tests_cashbook_gst_split.py` (append)

**Interfaces:**
- Consumes: `JournalLine.tax_code/gst_amount/is_gst_control` and `JournalType.CASHBOOK` from Task 1; `bas_utils.normalise_tax_treatment`, `bas_utils.TAXABLE_CODES`.
- Produces:
  - `resolve_line_tax_code(entity, account_code) -> str`
  - `line_gst(gross: Decimal, tax_code: str, override: Decimal | None = None) -> Decimal`
  - `split_cashbook_journal(journal) -> None` (raises `ValueError` if the split does not balance)
  - `GST_CONTROL_CODE = "3380"`

- [ ] **Step 1: Write the failing tests**

Append to `core/tests_cashbook_gst_split.py`:

```python
from django.contrib.auth import get_user_model

from core.models import (
    Entity, EntityChartOfAccount, FinancialYear,
)


def _entity_with_chart():
    """A GST-registered sole trader with the accounts JE-001 actually uses.

    Tools deliberately carries lowercase 'inp' — that is what the live chart
    holds, and a bare `tax_code in TAXABLE_CODES` test silently treats it as
    GST-free.
    """
    entity = Entity.objects.create(
        entity_name="Test Sole Trader",
        entity_type=Entity.EntityType.SOLE_TRADER,
        is_gst_registered=True,
    )
    chart = [
        ("105", "Sales", "GST", "revenue"),
        ("1510", "Accountancy", "INP", "expenses"),
        ("1804", "M/V car - Fuel & oil", "INP", "expenses"),
        ("1946", "Tools", "inp", "expenses"),
        ("1990", "Bank charges", "FRE", "expenses"),
        ("3380", "GST payable control account", "", "liabilities"),
        ("4080", "Drawings", "", "capital_accounts"),
    ]
    for code, name, tax, section in chart:
        EntityChartOfAccount.objects.create(
            entity=entity, account_code=code, account_name=name,
            tax_code=tax, section=section,
        )
    return entity


def _cashbook_journal(entity, rows):
    """rows: list of (account_code, account_name, dr_gross, cr_gross, tax_code)."""
    fy = FinancialYear.objects.create(
        entity=entity, year_label="Q2 2026",
        start_date="2025-10-01", end_date="2025-12-31",
    )
    journal = AdjustingJournal.objects.create(
        financial_year=fy,
        journal_type=AdjustingJournal.JournalType.CASHBOOK,
        journal_date="2025-12-31",
        description="Oct-Dec 2025 Income & Expenses",
    )
    for i, (code, name, dr, cr, tax) in enumerate(rows, start=1):
        JournalLine.objects.create(
            journal=journal, line_number=i,
            account_code=code, account_name=name,
            debit=Decimal(dr), credit=Decimal(cr), tax_code=tax,
        )
    return journal


class ResolveLineTaxCodeTest(TestCase):
    def setUp(self):
        self.entity = _entity_with_chart()

    def test_resolves_from_entity_chart(self):
        from core.gst_journal import resolve_line_tax_code
        self.assertEqual(resolve_line_tax_code(self.entity, "105"), "GST")

    def test_normalises_lowercase_chart_value(self):
        """Account 1946 Tools carries 'inp' lowercase on the live file."""
        from core.gst_journal import resolve_line_tax_code
        self.assertEqual(resolve_line_tax_code(self.entity, "1946"), "INP")

    def test_unknown_account_returns_blank(self):
        from core.gst_journal import resolve_line_tax_code
        self.assertEqual(resolve_line_tax_code(self.entity, "9999"), "")


class LineGstTest(TestCase):
    def test_taxable_line_gets_one_eleventh_rounded_half_up(self):
        from core.gst_journal import line_gst
        self.assertEqual(line_gst(Decimal("1990.40"), "INP"), Decimal("180.95"))
        self.assertEqual(line_gst(Decimal("23187.00"), "GST"), Decimal("2107.91"))
        self.assertEqual(line_gst(Decimal("250.00"), "INP"), Decimal("22.73"))

    def test_lowercase_tax_code_is_still_taxable(self):
        from core.gst_journal import line_gst
        self.assertEqual(line_gst(Decimal("510.00"), "inp"), Decimal("46.36"))

    def test_non_taxable_codes_get_nil(self):
        from core.gst_journal import line_gst
        for code in ("FRE", "N-T", ""):
            self.assertEqual(line_gst(Decimal("500.00"), code), Decimal("0.00"))

    def test_override_wins_over_the_computed_figure(self):
        from core.gst_journal import line_gst
        self.assertEqual(
            line_gst(Decimal("1990.40"), "INP", override=Decimal("145.50")),
            Decimal("145.50"),
        )

    def test_override_on_a_non_taxable_line_is_forced_to_nil(self):
        """Changing a line from GST to FRE must not leave a stale override."""
        from core.gst_journal import line_gst
        self.assertEqual(
            line_gst(Decimal("1990.40"), "FRE", override=Decimal("180.95")),
            Decimal("0.00"),
        )


class SplitCashbookJournalTest(TestCase):
    def setUp(self):
        self.entity = _entity_with_chart()
        # JE-001's real shape, trimmed to four lines plus the drawings plug.
        self.journal = _cashbook_journal(self.entity, [
            ("105", "Sales", "0", "23187.00", "GST"),
            ("1510", "Accountancy", "250.00", "0", "INP"),
            ("1804", "M/V car - Fuel & oil", "1990.40", "0", "INP"),
            ("1946", "Tools", "510.00", "0", "inp"),
            ("4080", "Drawings", "20436.60", "0", "N-T"),
        ])

    def _lines(self):
        return list(self.journal.lines.order_by("line_number", "id"))

    def test_lines_become_net_and_two_control_lines_are_appended(self):
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        by_code = {l.account_code: l for l in self._lines() if not l.is_gst_control}
        self.assertEqual(by_code["105"].credit, Decimal("21079.09"))
        self.assertEqual(by_code["105"].gst_amount, Decimal("2107.91"))
        self.assertEqual(by_code["1510"].debit, Decimal("227.27"))
        self.assertEqual(by_code["1804"].debit, Decimal("1809.45"))
        self.assertEqual(by_code["1946"].debit, Decimal("463.64"))
        self.assertEqual(by_code["4080"].debit, Decimal("20436.60"))
        self.assertEqual(by_code["4080"].gst_amount, Decimal("0.00"))

        controls = [l for l in self._lines() if l.is_gst_control]
        self.assertEqual(len(controls), 2)
        collected = next(l for l in controls if l.credit)
        paid = next(l for l in controls if l.debit)
        self.assertEqual(collected.account_code, "3380")
        self.assertEqual(collected.credit, Decimal("2107.91"))
        self.assertEqual(paid.account_code, "3380")
        self.assertEqual(paid.debit, Decimal("250.04"))  # 22.73 + 180.95 + 46.36

    def test_split_journal_still_balances(self):
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        lines = self._lines()
        self.assertEqual(
            sum(l.debit for l in lines), sum(l.credit for l in lines),
        )

    def test_control_lines_sort_last(self):
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        lines = self._lines()
        self.assertFalse(lines[0].is_gst_control)
        self.assertTrue(lines[-1].is_gst_control)
        self.assertTrue(lines[-2].is_gst_control)

    def test_splitting_twice_equals_splitting_once(self):
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        first = [
            (l.account_code, l.debit, l.credit, l.gst_amount, l.is_gst_control)
            for l in self._lines()
        ]
        split_cashbook_journal(self.journal)
        second = [
            (l.account_code, l.debit, l.credit, l.gst_amount, l.is_gst_control)
            for l in self._lines()
        ]
        self.assertEqual(first, second)

    def test_hand_override_survives_a_resplit_and_net_absorbs_the_rest(self):
        from core.gst_journal import split_cashbook_journal
        fuel = self.journal.lines.get(account_code="1804")
        fuel.gst_amount = Decimal("145.50")  # 80% business use
        fuel.save(update_fields=["gst_amount"])
        split_cashbook_journal(self.journal)
        fuel.refresh_from_db()
        self.assertEqual(fuel.gst_amount, Decimal("145.50"))
        self.assertEqual(fuel.debit, Decimal("1844.90"))
        split_cashbook_journal(self.journal)
        fuel.refresh_from_db()
        self.assertEqual(fuel.gst_amount, Decimal("145.50"))
        self.assertEqual(fuel.debit, Decimal("1844.90"))

    def test_an_accountants_own_3380_line_survives_the_split(self):
        JournalLine.objects.create(
            journal=self.journal, line_number=99,
            account_code="3380", account_name="ATO payment",
            debit=Decimal("500.00"), credit=Decimal("0"),
            tax_code="N-T", is_gst_control=False,
        )
        # Rebalance so the journal is postable.
        drawings = self.journal.lines.get(account_code="4080")
        drawings.debit = Decimal("19936.60")
        drawings.save(update_fields=["debit"])

        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        manual = self.journal.lines.filter(
            account_code="3380", is_gst_control=False,
        )
        self.assertEqual(manual.count(), 1)
        self.assertEqual(manual.first().debit, Decimal("500.00"))

    def test_blank_tax_code_falls_back_to_the_chart(self):
        from core.gst_journal import split_cashbook_journal
        line = self.journal.lines.get(account_code="1804")
        line.tax_code = ""
        line.save(update_fields=["tax_code"])
        split_cashbook_journal(self.journal)
        line.refresh_from_db()
        self.assertEqual(line.tax_code, "INP")
        self.assertEqual(line.gst_amount, Decimal("180.95"))

    def test_non_cashbook_journal_is_left_completely_alone(self):
        """A Hazaway JE-002-shaped migration journal posts already-net figures."""
        from core.gst_journal import split_cashbook_journal
        self.journal.journal_type = AdjustingJournal.JournalType.GENERAL
        self.journal.save(update_fields=["journal_type"])
        before = [
            (l.account_code, l.debit, l.credit, l.gst_amount, l.is_gst_control)
            for l in self._lines()
        ]
        split_cashbook_journal(self.journal)
        after = [
            (l.account_code, l.debit, l.credit, l.gst_amount, l.is_gst_control)
            for l in self._lines()
        ]
        self.assertEqual(before, after)

    def test_unbalanced_split_raises_rather_than_saving(self):
        from core.gst_journal import split_cashbook_journal
        self.journal.lines.get(account_code="4080").delete()
        with self.assertRaises(ValueError):
            split_cashbook_journal(self.journal)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_cashbook_gst_split -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.gst_journal'`.

- [ ] **Step 3: Write the split engine**

Create `core/gst_journal.py`:

```python
"""GST splitting for cash-basis (Cashbook) journals.

An accountant working a cash-basis client journals the period's transactions
straight into the trial balance — the journal IS the transaction record, so
GST has to be accounted for inside it. The accountant keys the GROSS figure
off the invoice and a tax code per line; this module rewrites those lines to
net and appends exactly two 3380 control lines.

Only ``JournalType.CASHBOOK`` journals are touched. Every other type is left
byte-identical, because general journals legitimately carry already-net
figures — Hazaway JE-002 ("migrated previous accountant profit & loss") posts
another accountant's net P&L to GST-coded accounts, and splitting it would
strip 1/11th out of every line.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from .bas_utils import TAXABLE_CODES, normalise_tax_treatment

GST_CONTROL_CODE = "3380"
GST_CONTROL_NAME = "GST payable control account"
CENTS = Decimal("0.01")
ELEVEN = Decimal("11")


def resolve_line_tax_code(entity, account_code):
    """The chart's default tax treatment for an account, normalised.

    EntityChartOfAccount first, then the entity-type template, then blank.
    Always normalised: account 1946 Tools carries 'inp' in lowercase on the
    live file, and a bare ``tax_code in TAXABLE_CODES`` test would silently
    treat Tools as GST-free.
    """
    from .models import ChartOfAccount, EntityChartOfAccount, template_entity_type

    ecoa = EntityChartOfAccount.objects.filter(
        entity=entity, account_code=account_code,
    ).first()
    if ecoa:
        return normalise_tax_treatment(ecoa.tax_code)

    coa = ChartOfAccount.objects.filter(
        entity_type=template_entity_type(entity.entity_type),
        account_code=account_code,
    ).first()
    if coa:
        return normalise_tax_treatment(coa.tax_code)
    return ""


def line_gst(gross, tax_code, override=None):
    """GST on one line.

    An override is respected on a taxable line — it carries partial input tax
    credits (business-use apportionment) and the non-creditable remainder stays
    in the expense, because net is always ``gross - gst``.

    On a non-taxable line the override is forced to nil rather than trusted.
    Without that, changing a line from GST to FRE would leave the stale figure
    behind and quietly overstate the input credit.
    """
    code = normalise_tax_treatment(tax_code)
    if code not in TAXABLE_CODES:
        return Decimal("0.00")
    if override is not None:
        return Decimal(override).quantize(CENTS, rounding=ROUND_HALF_UP)
    return (Decimal(gross) / ELEVEN).quantize(CENTS, rounding=ROUND_HALF_UP)


def _gross_of(line):
    """The gross the accountant keyed, reconstructed rather than stored.

    ``gross = (debit or credit) + gst_amount`` is what makes the split
    idempotent: re-splitting reconstructs 1,990.40 from 1,809.45 + 180.95 and
    lands in the same place. A stored gross field would be a fourth value that
    can drift out of agreement with the other three.
    """
    return max(line.debit, line.credit) + (line.gst_amount or Decimal("0"))


@transaction.atomic
def split_cashbook_journal(journal):
    """Rewrite a Cashbook journal's gross lines to net plus two 3380 lines.

    Idempotent. Raises ``ValueError`` if the result does not balance, which
    rolls the whole rewrite back.
    """
    from .models import AdjustingJournal, JournalLine

    if journal.journal_type != AdjustingJournal.JournalType.CASHBOOK:
        return

    entity = journal.financial_year.entity

    # Regenerate, never patch: the previous pair is deleted outright. Matching
    # on is_gst_control rather than account_code is what lets an accountant
    # keep their own 3380 line (the quarterly ATO payment) through a re-split.
    journal.lines.filter(is_gst_control=True).delete()

    source_lines = list(journal.lines.order_by("line_number", "id"))

    gst_on_credits = Decimal("0.00")
    gst_on_debits = Decimal("0.00")
    next_line_number = 0

    for line in source_lines:
        next_line_number = max(next_line_number, line.line_number)
        gross = _gross_of(line)

        tax_code = normalise_tax_treatment(line.tax_code)
        if not tax_code:
            tax_code = resolve_line_tax_code(entity, line.account_code)

        override = line.gst_amount if line.gst_amount else None
        gst = line_gst(gross, tax_code, override=override)

        is_credit = line.credit > line.debit
        line.tax_code = tax_code
        line.gst_amount = gst
        if is_credit:
            line.credit = gross - gst
            line.debit = Decimal("0.00")
            gst_on_credits += gst
        else:
            line.debit = gross - gst
            line.credit = Decimal("0.00")
            gst_on_debits += gst
        line.save(update_fields=["debit", "credit", "tax_code", "gst_amount"])

    if gst_on_credits:
        next_line_number += 1
        JournalLine.objects.create(
            journal=journal, line_number=next_line_number,
            account_code=GST_CONTROL_CODE, account_name=GST_CONTROL_NAME,
            description="GST collected",
            debit=Decimal("0.00"), credit=gst_on_credits,
            tax_code="N-T", gst_amount=Decimal("0.00"), is_gst_control=True,
        )
    if gst_on_debits:
        next_line_number += 1
        JournalLine.objects.create(
            journal=journal, line_number=next_line_number,
            account_code=GST_CONTROL_CODE, account_name=GST_CONTROL_NAME,
            description="GST paid",
            debit=gst_on_debits, credit=Decimal("0.00"),
            tax_code="N-T", gst_amount=Decimal("0.00"), is_gst_control=True,
        )

    # Every line satisfies net + gst = gross, and the control lines carry the
    # GST sums on the matching side, so a gross journal that balanced must
    # still balance. If it does not, something above is wrong — refuse rather
    # than post a broken journal.
    final = list(journal.lines.all())
    total_dr = sum(l.debit for l in final)
    total_cr = sum(l.credit for l in final)
    if total_dr != total_cr:
        raise ValueError(
            f"Cashbook GST split did not balance: "
            f"Dr {total_dr} != Cr {total_cr} on {journal.reference_number}"
        )

    journal.recalculate_totals()
```

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_cashbook_gst_split -v 2`
Expected: PASS, all tests.

- [ ] **Step 5: Confirm no regression**

```bash
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core 2>&1 | tail -40 > /tmp/after_t2.txt
grep -E "^(FAIL|ERROR):" /tmp/after_t2.txt | sort > /tmp/after_t2_set.txt
diff /tmp/baseline_set.txt /tmp/after_t2_set.txt
```

Expected: no lines added. Removed lines are fine.

- [ ] **Step 6: Commit**

```bash
git add core/gst_journal.py core/tests_cashbook_gst_split.py
git commit -m "feat(gst): split cashbook journal lines into net plus a 3380 pair

The accountant keys gross plus a tax code; split_cashbook_journal rewrites
each line to net and appends one Cr 'GST collected' and one Dr 'GST paid'
line, so the journal itself remains the complete audit record.

Idempotent by reconstructing gross as (debit or credit) + gst_amount rather
than storing it. Non-cashbook journals are left byte-identical.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: BAS engine reads the stored per-line GST

**Files:**
- Modify: `core/bas_utils.py:779-828` (journal branch of `_calculate_gst`), `core/bas_utils.py:916-955` (`_build_bas_result`)
- Test: `core/tests_bas_cashbook_journal.py`

**Interfaces:**
- Consumes: `JournalLine.tax_code/gst_amount` from Task 1; `split_cashbook_journal` from Task 2 (tests use it to build fixtures).
- Produces: `_jl_gross_and_gst(jl, tax_code) -> (Decimal, Decimal)`; `_build_bas_result(..., actual_gst_collected=None, actual_gst_paid=None)`.

- [ ] **Step 1: Write the failing tests**

Create `core/tests_bas_cashbook_journal.py`:

```python
from decimal import Decimal

from django.test import TestCase

from core.bas_utils import calculate_gst_for_period, get_period_dates
from core.gst_journal import split_cashbook_journal
from core.models import (
    AdjustingJournal, Entity, EntityChartOfAccount, FinancialYear, JournalLine,
)


class CashbookBasTest(TestCase):
    """ELLIOTT JAQUES JE-001, the journal this feature was built for."""

    JE001 = [
        ("105", "Sales", "0", "23187.00", "GST"),
        ("1510", "Accountancy", "250.00", "0", "INP"),
        ("1804", "M/V car - Fuel & oil", "1990.40", "0", "INP"),
        ("1845", "Protective clothing", "200.00", "0", "INP"),
        ("1940", "Telephone & Internet", "85.00", "0", "INP"),
        ("1940", "Telephone & Internet", "50.00", "0", "INP"),
        ("1809", "M/V car - Other", "605.00", "0", "INP"),
        ("1946", "Tools", "510.00", "0", "inp"),
        ("1800", "Materials & supplies", "570.00", "0", "INP"),
        ("1808", "M/V car - Repairs", "345.00", "0", "INP"),
        ("4080", "Drawings", "18581.60", "0", "N-T"),
    ]

    CHART = [
        ("105", "Sales", "GST", "revenue"),
        ("1510", "Accountancy", "INP", "expenses"),
        ("1800", "Materials & supplies", "INP", "expenses"),
        ("1804", "M/V car - Fuel & oil", "INP", "expenses"),
        ("1808", "M/V car - Repairs", "INP", "expenses"),
        ("1809", "M/V car - Other", "INP", "expenses"),
        ("1845", "Protective clothing", "INP", "expenses"),
        ("1940", "Telephone & Internet", "INP", "expenses"),
        ("1946", "Tools", "inp", "expenses"),
        ("3380", "GST payable control account", "", "liabilities"),
        ("4080", "Drawings", "", "capital_accounts"),
    ]

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="ELLIOTT JAQUES TEST",
            entity_type=Entity.EntityType.SOLE_TRADER,
            is_gst_registered=True,
        )
        for code, name, tax, section in self.CHART:
            EntityChartOfAccount.objects.create(
                entity=self.entity, account_code=code, account_name=name,
                tax_code=tax, section=section,
            )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="Q2 2026",
            start_date="2025-10-01", end_date="2025-12-31",
        )

    def _journal(self, journal_type, rows):
        journal = AdjustingJournal.objects.create(
            financial_year=self.fy, journal_type=journal_type,
            journal_date="2025-12-31", status="posted",
            description="Oct-Dec 2025 Income & Expenses",
        )
        for i, (code, name, dr, cr, tax) in enumerate(rows, start=1):
            JournalLine.objects.create(
                journal=journal, line_number=i,
                account_code=code, account_name=name,
                debit=Decimal(dr), credit=Decimal(cr), tax_code=tax,
            )
        return journal

    def _q2(self):
        start, end = get_period_dates(self.fy, "quarterly", 2)
        return calculate_gst_for_period(self.fy, start, end)

    def test_cashbook_journal_reports_the_accounts_method_figures(self):
        journal = self._journal(
            AdjustingJournal.JournalType.CASHBOOK, self.JE001,
        )
        split_cashbook_journal(journal)
        bas = self._q2()["bas_data"]
        # G1/G11 stay GROSS — they are turnover and purchases.
        self.assertEqual(bas["G1"], Decimal("23187.00"))
        self.assertEqual(bas["G11"], Decimal("4605.40"))
        # 1A/1B are the GST actually recorded, not G8/11 and G19/11.
        self.assertEqual(bas["1A"], Decimal("2107.91"))
        self.assertEqual(bas["1B"], Decimal("418.68"))
        self.assertEqual(bas["gst_payable"], Decimal("1689.23"))
        # G9/G20 move with 1A/1B so the worksheet cannot contradict itself.
        self.assertEqual(bas["G9"], bas["1A"])
        self.assertEqual(bas["G20"], bas["1B"])

    def test_the_3380_control_lines_contribute_to_no_g_label(self):
        journal = self._journal(
            AdjustingJournal.JournalType.CASHBOOK, self.JE001,
        )
        split_cashbook_journal(journal)
        result = self._q2()
        codes = {
            line["code"]
            for line in result["sales_lines"] + result["purchase_lines"]
            + result["capital_lines"]
        }
        self.assertNotIn("3380", codes)

    def test_an_explicit_nt_tax_code_keeps_drawings_off_the_worksheet(self):
        journal = self._journal(
            AdjustingJournal.JournalType.CASHBOOK, self.JE001,
        )
        split_cashbook_journal(journal)
        result = self._q2()
        codes = {
            line["code"]
            for line in result["sales_lines"] + result["purchase_lines"]
            + result["capital_lines"]
        }
        self.assertNotIn("4080", codes)

    def test_3380_ties_to_the_bas_net(self):
        """The whole point of the feature, asserted directly."""
        journal = self._journal(
            AdjustingJournal.JournalType.CASHBOOK, self.JE001,
        )
        split_cashbook_journal(journal)
        controls = journal.lines.filter(is_gst_control=True)
        control_net = (
            sum(l.credit for l in controls) - sum(l.debit for l in controls)
        )
        self.assertEqual(control_net, self._q2()["bas_data"]["gst_payable"])

    def test_a_legacy_gross_journal_reports_exactly_as_before(self):
        """gst_amount=0 and tax_code='' must fall back to 1/11 of the gross."""
        rows = [(c, n, dr, cr, "") for c, n, dr, cr, _ in self.JE001]
        self._journal(AdjustingJournal.JournalType.GENERAL, rows)
        bas = self._q2()["bas_data"]
        self.assertEqual(bas["G1"], Decimal("23187.00"))
        self.assertEqual(bas["G11"], Decimal("4605.40"))
        self.assertEqual(bas["1A"], Decimal("2107.91"))
        self.assertEqual(bas["1B"], Decimal("418.68"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_bas_cashbook_journal -v 2`
Expected: FAIL — `1B` comes back as `418.67` (the `G19÷11` figure) and `G9`/`G20` do not equal `1A`/`1B`.

- [ ] **Step 3: Add `_jl_gross_and_gst`**

In `core/bas_utils.py`, immediately after `_txn_gross_and_gst` (which ends around line 630), add:

```python
def _jl_gross_and_gst(jl, tax_code):
    """Return (gross, gst) for one journal line under the resolved tax code.

    Mirrors _txn_gross_and_gst: a stored GST amount wins, because it carries
    the accountant's overrides and partial input tax credits; otherwise fall
    back to the ATO 1/11th method.

    Cashbook lines store NET with the GST alongside, so gross is reconstructed
    by adding it back. Every pre-existing journal line has gst_amount = 0, so
    gross is unchanged and the 1/11th fallback reproduces today's behaviour
    exactly.
    """
    stored = abs(jl.gst_amount or Decimal("0"))
    gross = max(jl.debit, jl.credit) + stored
    if tax_code not in TAXABLE_CODES:
        return gross, Decimal("0.00")
    if stored:
        return gross, stored
    return gross, (gross / Decimal("11")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
```

- [ ] **Step 4: Rewrite the journal branch to use it**

In `core/bas_utils.py`, the journal loop currently starting at line 786 (`for jl in journal.lines.all():`) reads:

```python
        for jl in journal.lines.all():
            section, tax_code, exclude_reason = _resolve_section_and_tax(
                jl.account_code, coa_lookup, entity_coa_lookup, ""
            )
            if exclude_reason:
                continue
            amount = max(jl.debit, jl.credit)
            if amount == 0:
                continue
```

Replace those lines with:

```python
        for jl in journal.lines.all():
            # The line's own tax code wins over the chart. That hardcoded ""
            # meant a journal line could never override its account default —
            # which is what let an explicit N-T on Drawings be ignored.
            # Pre-existing lines carry "", so they still fall through to the
            # chart exactly as before.
            section, tax_code, exclude_reason = _resolve_section_and_tax(
                jl.account_code, coa_lookup, entity_coa_lookup, jl.tax_code
            )
            if exclude_reason:
                continue
            amount, jl_gst = _jl_gross_and_gst(jl, tax_code)
            if amount == 0:
                continue
```

Then, further down the same loop, replace:

```python
            has_gst = tax_code in TAXABLE_CODES
            gst = (
                (amount / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if has_gst else Decimal("0")
            )
```

with:

```python
            has_gst = tax_code in TAXABLE_CODES
            gst = jl_gst
            actual_gst_collected, actual_gst_paid = _accumulate_actual_gst(
                section, gst, actual_gst_collected, actual_gst_paid,
            )
```

- [ ] **Step 5: Accumulate actual GST across both sources**

Still in `core/bas_utils.py`, add this helper next to `_jl_gross_and_gst`:

```python
def _accumulate_actual_gst(section, gst, collected, paid):
    """Add one line's GST to the collected/paid running totals.

    Revenue sections feed 1A; expense, cost-of-sales and asset sections feed
    1B. Sections that classify to nothing (N-T lines, accounts absent from the
    chart) contribute to neither, which matches their G-label treatment.
    """
    if section in ("revenue", "Revenue"):
        return collected + gst, paid
    if section in ("expenses", "Expenses", "cost_of_sales", "Cost of Sales",
                   "assets", "Assets"):
        return collected, paid + gst
    return collected, paid
```

In `_calculate_gst`, initialise the two accumulators alongside `g_totals` (near line 655, where `sales_lines = []` and friends are declared):

```python
    actual_gst_collected = Decimal("0.00")
    actual_gst_paid = Decimal("0.00")
```

In the **bank-statement** branch, inside `for txn in all_confirmed:` after `gross, gst = _txn_gross_and_gst(txn, tax_code)` and the `_is_contra` sign flip, add:

```python
            actual_gst_collected, actual_gst_paid = _accumulate_actual_gst(
                section, gst, actual_gst_collected, actual_gst_paid,
            )
```

In `_add_tb_line`, GST is not carried per line, so it contributes nothing to the accumulators — the aggregate `G÷11` fallback below covers those. Leave `_add_tb_line` unchanged.

- [ ] **Step 6: Report 1A/1B from the accumulators**

Change the `return _build_bas_result(...)` call at the end of `_calculate_gst` (line 850) to pass them through:

```python
    return _build_bas_result(
        g_totals, sales_lines, purchase_lines, capital_lines, excluded_lines,
        sales_transactions=sales_transactions,
        purchase_transactions=purchase_transactions,
        actual_gst_collected=actual_gst_collected,
        actual_gst_paid=actual_gst_paid,
    )
```

Then in `_build_bas_result`, change the signature and the G9/G20 computation. Current:

```python
def _build_bas_result(g_totals, sales_lines, purchase_lines, capital_lines, excluded_lines,
                      sales_transactions=None, purchase_transactions=None):
```

becomes:

```python
def _build_bas_result(g_totals, sales_lines, purchase_lines, capital_lines, excluded_lines,
                      sales_transactions=None, purchase_transactions=None,
                      actual_gst_collected=None, actual_gst_paid=None):
```

and the two quantize lines:

```python
    g["G9"] = (g["G8"] / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if g["G8"] else Decimal("0")
```
```python
    g["G20"] = (g["G19"] / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if g["G19"] else Decimal("0")
```

become:

```python
    # Accounts method: 1A and 1B are the GST actually recorded, not G8/11.
    # Summing per-line GST and dividing an aggregate by 11 disagree by cents —
    # 418.68 against 418.67 on the first live cashbook quarter — and only one
    # can be authoritative if the 3380 control account is to tie to the BAS.
    # The recorded figure wins, so a hand-overridden partial input tax credit
    # reaches the BAS instead of being averaged away.
    #
    # G9/G20 move with 1A/1B rather than keeping the worksheet division: left
    # as G8/11 they would print 418.67 directly above 1B's 418.68 on the same
    # screen. G1 and G11 stay gross — they are turnover and purchases.
    if actual_gst_collected is not None:
        g["G9"] = Decimal(actual_gst_collected).quantize(Decimal("0.01"))
    else:
        g["G9"] = (g["G8"] / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if g["G8"] else Decimal("0")
    if actual_gst_paid is not None:
        g["G20"] = Decimal(actual_gst_paid).quantize(Decimal("0.01"))
    else:
        g["G20"] = (g["G19"] / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if g["G19"] else Decimal("0")
```

Note the placement: `G9` is currently computed after `G8`, and `G20` after `G19`. Keep that order — `G8` and `G19` are still needed for display.

Leave `label_1a = g["G9"]` and `label_1b = g["G20"]` (line 933) exactly as they are; they now pick up the accounts-method figures automatically.

- [ ] **Step 7: Handle the undated-balance fallback**

`_add_tb_line` contributes no per-line GST, so a full-year view of an entity whose figures come only from imported balances would report 1A/1B as nil. Guard against that: in `_calculate_gst`, just before the `return _build_bas_result(...)`, add:

```python
    # Imported/rollover TB lines carry no per-line GST, so they cannot feed the
    # accounts method. When nothing at all contributed a recorded GST figure,
    # fall back to the worksheet division rather than reporting nil.
    if actual_gst_collected == 0 and actual_gst_paid == 0:
        actual_gst_collected = actual_gst_paid = None
```

- [ ] **Step 8: Run the new tests**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_bas_cashbook_journal -v 2`
Expected: PASS, 5 tests.

- [ ] **Step 9: Run the existing BAS suites specifically**

```bash
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test \
  core.tests_bas_gst core.tests_bas_date_window core.tests_bas_stub_year \
  core.tests_bas_reallocate_posting core.tests_bas_reallocate_unpostable \
  core.tests_bank_tb_rebuild core.tests_bank_contra_fy core.tests_bank_tb_partition -v 2
```

Any failure here is a real regression from the accounts-method switch, not baseline noise. Read it before proceeding — an existing test asserting `1B == G19/11` needs its expectation updated *and* a comment saying why, not deletion.

- [ ] **Step 10: Confirm the full-suite failure set is unchanged**

```bash
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core 2>&1 | tail -40 > /tmp/after_t3.txt
grep -E "^(FAIL|ERROR):" /tmp/after_t3.txt | sort > /tmp/after_t3_set.txt
diff /tmp/baseline_set.txt /tmp/after_t3_set.txt
```

- [ ] **Step 11: Verify the four measured live moves**

```bash
DATABASE_URL="$(grep -m1 DATABASE_URL .env 2>/dev/null | cut -d= -f2-)" python3 - <<'PY'
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
django.setup()
from core.models import Entity
from core.bas_utils import calculate_gst_for_period, get_period_dates
want = {
    ("D.P Vaughan & D Vriend", 4): ("9251.08", "5292.25"),
    ("ELLIOTT JAQUES", 2): ("2107.91", "418.68"),
    ("Hazaway Operations Pty Ltd", 4): ("65698.55", "35919.98"),
    ("Veronica Cerratti Pty Ltd", 4): ("0.00", "3970.00"),
}
for e in Entity.objects.all():
    for fy in e.financial_years.all():
        for q in (1, 2, 3, 4):
            key = (e.entity_name, q)
            if key not in want:
                continue
            s, en = get_period_dates(fy, "quarterly", q)
            b = calculate_gst_for_period(fy, s, en)["bas_data"]
            if b["1A"] or b["1B"]:
                print(key, "1A", b["1A"], "1B", b["1B"], "expected", want[key])
PY
```

Expected: each printed 1A/1B matches the `expected` pair. This runs read-only against the live DB — it calculates and prints, it writes nothing.

- [ ] **Step 12: Commit**

```bash
git add core/bas_utils.py core/tests_bas_cashbook_journal.py
git commit -m "feat(bas): report 1A/1B from the GST actually recorded

Three changes make the BAS read a cashbook journal correctly, and all three
are inert for existing journals (tax_code='' and gst_amount=0):

- the line's own tax code now wins over the chart default, so an explicit
  N-T on Drawings keeps it off the worksheet
- _jl_gross_and_gst reconstructs gross by adding the stored GST back, and
  respects that GST instead of re-deriving 1/11th
- 1A/1B (and G9/G20 with them) come from summed recorded GST, so the 3380
  control account ties to the BAS to the cent

G1/G11 stay gross. Moves four live periods by 1-4 cents, all measured.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Wire the split into the journal save paths

**Files:**
- Modify: `core/forms.py:244-262` (`JournalLineForm`)
- Modify: `core/views.py:5909-5930` (`adjustment_create`), `core/views.py:6708-6750` (`journal_edit`)
- Test: `core/tests_cashbook_journal_views.py`

**Interfaces:**
- Consumes: `split_cashbook_journal` from Task 2.
- Produces: `JournalLineForm` accepting `tax_code` and `gst_amount`; both save paths splitting before the balance check.

- [ ] **Step 1: Write the failing test**

Create `core/tests_cashbook_journal_views.py`:

```python
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    AdjustingJournal, Entity, EntityChartOfAccount, FinancialYear,
    TrialBalanceLine,
)


class CashbookCreateViewTest(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Cashbook Client",
            entity_type=Entity.EntityType.SOLE_TRADER,
            is_gst_registered=True,
        )
        for code, name, tax, section in [
            ("105", "Sales", "GST", "revenue"),
            ("1804", "M/V car - Fuel & oil", "INP", "expenses"),
            ("3380", "GST payable control account", "", "liabilities"),
            ("4080", "Drawings", "", "capital_accounts"),
        ]:
            EntityChartOfAccount.objects.create(
                entity=self.entity, account_code=code, account_name=name,
                tax_code=tax, section=section,
            )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="Q2 2026",
            start_date="2025-10-01", end_date="2025-12-31",
        )
        User = get_user_model()
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant",
        )
        self.client.force_login(self.user)
        # Require2FAMiddleware is on; without this every request 302s.
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _post_cashbook(self):
        rows = [
            ("105", "Sales", "0", "23187.00", "GST"),
            ("1804", "M/V car - Fuel & oil", "1990.40", "0", "INP"),
            ("4080", "Drawings", "21196.60", "0", "N-T"),
        ]
        data = {
            "journal_type": "cashbook",
            "journal_date": "2025-12-31",
            "description": "Oct-Dec 2025 Income & Expenses",
            "narration": "",
            "lines-TOTAL_FORMS": str(len(rows)),
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for i, (code, name, dr, cr, tax) in enumerate(rows):
            data[f"lines-{i}-account_code"] = code
            data[f"lines-{i}-account_name"] = name
            data[f"lines-{i}-description"] = ""
            data[f"lines-{i}-debit"] = dr
            data[f"lines-{i}-credit"] = cr
            data[f"lines-{i}-tax_code"] = tax
            data[f"lines-{i}-gst_amount"] = ""
        # SECURE_SSL_REDIRECT is on; without secure=True this is a bare 301.
        return self.client.post(
            reverse("core:adjustment_create", args=[self.fy.pk]),
            data, secure=True, follow=True,
        )

    def test_posting_a_cashbook_journal_splits_gst_and_posts_net_to_the_tb(self):
        self._post_cashbook()
        journal = AdjustingJournal.objects.get(financial_year=self.fy)
        self.assertEqual(journal.journal_type, "cashbook")

        sales = journal.lines.get(account_code="105", is_gst_control=False)
        self.assertEqual(sales.credit, Decimal("21079.09"))
        self.assertEqual(sales.gst_amount, Decimal("2107.91"))

        controls = journal.lines.filter(is_gst_control=True)
        self.assertEqual(controls.count(), 2)

        tb = TrialBalanceLine.objects.get(
            financial_year=self.fy, account_code="3380",
        )
        # closing_balance is debit - credit: 180.95 - 2107.91
        self.assertEqual(tb.closing_balance, Decimal("-1926.96"))

    def test_general_journal_is_not_split(self):
        rows = [
            ("105", "Sales", "0", "1000.00", ""),
            ("4080", "Drawings", "1000.00", "0", ""),
        ]
        data = {
            "journal_type": "general",
            "journal_date": "2025-12-31",
            "description": "Net figures from prior accountant",
            "narration": "",
            "lines-TOTAL_FORMS": "2",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for i, (code, name, dr, cr, tax) in enumerate(rows):
            data[f"lines-{i}-account_code"] = code
            data[f"lines-{i}-account_name"] = name
            data[f"lines-{i}-description"] = ""
            data[f"lines-{i}-debit"] = dr
            data[f"lines-{i}-credit"] = cr
            data[f"lines-{i}-tax_code"] = tax
            data[f"lines-{i}-gst_amount"] = ""
        self.client.post(
            reverse("core:adjustment_create", args=[self.fy.pk]),
            data, secure=True, follow=True,
        )
        journal = AdjustingJournal.objects.get(financial_year=self.fy)
        self.assertEqual(journal.lines.filter(is_gst_control=True).count(), 0)
        self.assertEqual(
            journal.lines.get(account_code="105").credit, Decimal("1000.00"),
        )
```

Before running, confirm the formset prefix. `JournalLineFormSet` is built by
`inlineformset_factory(AdjustingJournal, JournalLine, ...)`, so Django derives
the prefix from the FK's `related_name`, which is `lines`. If the test's
`lines-*` keys produce "ManagementForm data is missing", print
`JournalLineFormSet().prefix` and use that value instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_cashbook_journal_views -v 2`
Expected: FAIL — sales credit is still `23187.00` and no control lines exist.

- [ ] **Step 3: Add the fields to `JournalLineForm`**

In `core/forms.py`, change `JournalLineForm.Meta.fields`:

```python
        fields = ("account_code", "account_name", "description", "debit", "credit")
```

to:

```python
        fields = (
            "account_code", "account_name", "description", "debit", "credit",
            "tax_code", "gst_amount",
        )
```

Then in `JournalLineForm.__init__`, after the existing `description` setup, make both new fields optional and render `gst_amount` like the other money inputs:

```python
        self.fields["tax_code"].required = False
        self.fields["gst_amount"].required = False
        self.fields["gst_amount"].widget = forms.TextInput(attrs={
            "class": "form-control form-control-sm gst-field",
            "inputmode": "decimal",
            "placeholder": "auto",
        })
```

Add a `clean_gst_amount` beside the existing `clean_debit`/`clean_credit`. A
blank field must mean "compute it", which is `None`, not zero — zero would be
indistinguishable from a deliberate nil override:

```python
    def clean_gst_amount(self):
        """Blank means 'compute 1/11th', not 'nil'.

        The split engine treats a falsy gst_amount as absent and derives the
        figure; a value the accountant typed is respected as a partial input
        tax credit. Returning 0 for a blank field would be indistinguishable
        from a deliberate nil override.
        """
        raw = (self.data.get(self.add_prefix('gst_amount'), '') or '').strip()
        if not raw:
            return Decimal('0')
        from decimal import InvalidOperation
        try:
            return self._eval_expr(raw)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError(
                'Enter a valid GST amount, or leave blank to calculate it.')
```

Check the top of `core/forms.py` for an existing `from decimal import Decimal`. If there isn't one, add it rather than importing inside the method.

- [ ] **Step 4: Also update the `_is_truly_empty` guard**

`JournalLineForm._is_truly_empty` decides whether a blank row is ignored. A row
carrying only a tax code is still empty, so leave the logic as-is — but confirm
by reading it that adding fields did not change its behaviour. No edit expected;
note in your commit message that it was checked.

- [ ] **Step 5: Call the split in `adjustment_create`**

In `core/views.py`, inside `adjustment_create`, the block currently reads:

```python
            # Set line_number to preserve the order lines were entered
            for i, line in enumerate(lines, start=1):
                line.line_number = i
                line.save(update_fields=["line_number"])

            # Validate debits = credits
            total_dr = sum(l.debit for l in lines)
            total_cr = sum(l.credit for l in lines)
```

Replace with:

```python
            # Set line_number to preserve the order lines were entered
            for i, line in enumerate(lines, start=1):
                line.line_number = i
                line.save(update_fields=["line_number"])

            # Cashbook journals are keyed GROSS. Split before the balance
            # check so it validates what will actually be posted: net lines
            # plus the two 3380 control lines. A no-op for every other type.
            from core.gst_journal import split_cashbook_journal
            try:
                split_cashbook_journal(journal)
            except ValueError as exc:
                journal.delete()
                messages.error(request, str(exc))
                return render(request, "core/adjustment_form.html", {
                    "form": form, "formset": formset, "fy": fy,
                    "accounts": accounts,
                })

            # Validate debits = credits — re-read, because the split rewrote
            # the rows and appended new ones.
            all_lines = list(journal.lines.all())
            total_dr = sum(l.debit for l in all_lines)
            total_cr = sum(l.credit for l in all_lines)
```

- [ ] **Step 6: Call the split in `journal_edit`**

In `core/views.py`, inside `journal_edit`'s atomic block, after the renumber loop and before `# Validate balance`:

```python
                # Renumber lines
                for i, line in enumerate(journal.lines.order_by("line_number", "id"), start=1):
                    line.line_number = i
                    line.save(update_fields=["line_number"])

                # Cashbook journals are keyed GROSS — re-split before
                # validating, so the balance check sees the net lines plus the
                # regenerated 3380 pair. A no-op for every other type.
                from core.gst_journal import split_cashbook_journal
                try:
                    split_cashbook_journal(journal)
                except ValueError as exc:
                    db_transaction.set_rollback(True)
                    messages.error(request, str(exc))
                    return render(request, "core/journal_edit.html", {
                        "form": form, "formset": formset, "journal": journal,
                        "fy": fy, "entity": entity, "accounts": accounts,
                    })

                # Validate balance
```

- [ ] **Step 7: Keep the generated lines out of the edit formset**

Still in `journal_edit`, the formset must not offer the derived `3380` rows for
editing — the split deletes and regenerates them on every save, so any edit
would be silently discarded. Change both formset constructions:

```python
        formset = EditJournalLineFormSet(request.POST, instance=journal)
```
becomes
```python
        formset = EditJournalLineFormSet(
            request.POST, instance=journal,
            queryset=journal.lines.filter(is_gst_control=False),
        )
```

and the GET branch (around `core/views.py:6816`):

```python
        formset = EditJournalLineFormSet(instance=journal)
```
becomes
```python
        formset = EditJournalLineFormSet(
            instance=journal,
            queryset=journal.lines.filter(is_gst_control=False),
        )
```

- [ ] **Step 8: Show gross, not net, when re-opening a cashbook journal**

The stored `debit`/`credit` are net, but the accountant keyed gross and must
see gross. In `journal_edit`, after the GET-branch formset is constructed, add:

```python
        # The grid is a GROSS grid. Stored rows are net with the GST beside
        # them, so add it back for display; the split reconstructs the same
        # figure on save, which is what makes the round trip idempotent.
        if journal.journal_type == AdjustingJournal.JournalType.CASHBOOK:
            for line_form in formset.forms:
                inst = line_form.instance
                if not inst.pk or not inst.gst_amount:
                    continue
                if inst.debit:
                    line_form.initial["debit"] = inst.debit + inst.gst_amount
                elif inst.credit:
                    line_form.initial["credit"] = inst.credit + inst.gst_amount
```

- [ ] **Step 9: Run the view tests**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_cashbook_journal_views -v 2`
Expected: PASS, 2 tests.

- [ ] **Step 10: Confirm the full-suite failure set is unchanged**

```bash
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core 2>&1 | tail -40 > /tmp/after_t4.txt
grep -E "^(FAIL|ERROR):" /tmp/after_t4.txt | sort > /tmp/after_t4_set.txt
diff /tmp/baseline_set.txt /tmp/after_t4_set.txt
```

- [ ] **Step 11: Commit**

```bash
git add core/forms.py core/views.py core/tests_cashbook_journal_views.py
git commit -m "feat(gst): split cashbook journals on save in both journal paths

adjustment_create and journal_edit now split before validating the balance,
so the check sees what will be posted. The generated 3380 rows are kept out
of the edit formset (the split regenerates them, so edits there would be
discarded), and the grid adds GST back for display because the accountant
keys gross while the rows store net.

Blank GST means 'calculate it', not 'nil' — the two are different answers.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Journal grid UI

**Files:**
- Modify: `templates/core/adjustment_form.html:60-100` (table head/body), plus its JS block
- Modify: `templates/core/journal_edit.html` (same treatment)
- Modify: `templates/core/journal_detail.html` (display only)

**Interfaces:**
- Consumes: `JournalLineForm.tax_code` / `.gst_amount` from Task 4.
- Produces: no Python interface.

- [ ] **Step 1: Add the columns to `adjustment_form.html`**

In the `<thead>` (around line 62), the Debit/Credit headers become Gross, and
two columns are inserted before them:

```html
                                    <th style="width: 200px;">Account</th>
                                    <th style="width: 200px;">Account Name</th>
                                    <th>Description</th>
                                    <th class="cashbook-col" style="width: 110px;">Tax</th>
                                    <th class="cashbook-col" style="width: 110px;">GST</th>
                                    <th style="width: 130px;">Debit</th>
                                    <th style="width: 130px;">Credit</th>
                                    <th style="width: 50px;">Del</th>
```

In the `<tbody>` row loop, insert the two cells before the debit cell:

```html
                                    <td class="cashbook-col">{{ line_form.tax_code }}</td>
                                    <td class="cashbook-col"><div class="dr-cr-wrapper">{{ line_form.gst_amount }}</div></td>
                                    <td><div class="dr-cr-wrapper">{{ line_form.debit }}</div></td>
```

The footer totals row has a `colspan` that must grow by 2 to keep the totals
under the Debit/Credit columns. Find the `<td colspan="N">` immediately before
`id="total-debit"` (around line 94) and increase `N` by 2.

- [ ] **Step 2: Show the columns only for Cashbook journals**

Add to the same template's style/script area:

```html
<style>
  .cashbook-col { display: none; }
  table.cashbook-mode .cashbook-col { display: table-cell; }
</style>
<script>
(function () {
  var typeSel = document.querySelector('[name="journal_type"]');
  var table = document.querySelector('#journal-lines-table')
           || document.querySelector('table');
  if (!typeSel || !table) return;

  function sync() {
    table.classList.toggle('cashbook-mode', typeSel.value === 'cashbook');
  }
  typeSel.addEventListener('change', sync);
  sync();
})();
</script>
```

If the lines table has no `id`, add `id="journal-lines-table"` to its opening
`<table>` tag rather than relying on `querySelector('table')` picking the right
one.

- [ ] **Step 3: Compute GST client-side as the accountant types**

Append to the same script block, inside the same IIFE:

```javascript
  var ONE_ELEVENTH = ['GST', 'INP', 'GNR', 'CAP', 'ADS'];

  function rowOf(el) { return el.closest('tr'); }

  function recompute(row) {
    var taxEl = row.querySelector('[name$="-tax_code"]');
    var gstEl = row.querySelector('[name$="-gst_amount"]');
    if (!taxEl || !gstEl) return;
    // An accountant who typed their own figure owns it — a partial input tax
    // credit must not be recomputed away under them.
    if (gstEl.dataset.touched === '1') return;

    var code = (taxEl.value || '').trim().toUpperCase();
    if (ONE_ELEVENTH.indexOf(code) === -1) { gstEl.value = ''; return; }

    var drEl = row.querySelector('[name$="-debit"]');
    var crEl = row.querySelector('[name$="-credit"]');
    var raw = (drEl && parseRaw(drEl.value)) || (crEl && parseRaw(crEl.value)) || 0;
    gstEl.value = raw ? (Math.round((raw / 11) * 100) / 100).toFixed(2) : '';
  }

  table.addEventListener('input', function (e) {
    var n = e.target.name || '';
    if (n.endsWith('-gst_amount')) { e.target.dataset.touched = '1'; return; }
    if (n.endsWith('-debit') || n.endsWith('-credit') || n.endsWith('-tax_code')) {
      recompute(rowOf(e.target));
    }
  });
  table.addEventListener('change', function (e) {
    if ((e.target.name || '').endsWith('-tax_code')) {
      // A tax-code change clears a stale override: the figure was for the old
      // treatment. GST -> FRE must not leave 180.95 sitting there.
      var g = rowOf(e.target).querySelector('[name$="-gst_amount"]');
      if (g) { g.dataset.touched = ''; }
      recompute(rowOf(e.target));
    }
  });
```

`parseRaw` already exists in this template (used by the totals code around line
545). Confirm it is in scope inside your IIFE; if it is declared inside another
closure, move this block to the same scope rather than duplicating the function.

- [ ] **Step 4: Default the tax code from the chart on account selection**

The account picker already writes the account code and name into the row. Find
the function that does so (the one referencing `[name$="-account_code"]` around
line 390) and, after it sets the name, add:

```javascript
    var taxEl = row.querySelector('[name$="-tax_code"]');
    if (taxEl && !taxEl.value && acc && acc.tax_code) {
      taxEl.value = acc.tax_code;
      var g = row.querySelector('[name$="-gst_amount"]');
      if (g) { g.dataset.touched = ''; }
      recompute(row);
    }
```

For `acc.tax_code` to exist, the account list the template renders must carry
it. In `core/views.py`, both `adjustment_create` and `journal_edit` build
`accounts` from `.values("account_code", "account_name")`. Add `"tax_code"` to
both `.values(...)` calls and to the `merged[...]` dict literals, keyed
`client_account_tax_code`, then surface it in the template's `ACCOUNT_LIST`
mapping (around line 256) as `tax_code: acc.client_account_tax_code || ''`.

- [ ] **Step 5: Repeat steps 1–4 for `journal_edit.html`**

The two templates carry near-identical grids and JS. Apply the same header
cells, body cells, `colspan` bump, style block, and script block. Do not try to
factor them into a shared include in this task — that is a refactor with its own
review, and mixing it in here makes both changes harder to judge.

- [ ] **Step 6: Show tax code and GST on the journal detail page**

In `templates/core/journal_detail.html`, add a `Tax` and a `GST` column to the
lines table, and render the two `is_gst_control` rows with a muted style and a
"generated" badge so it is obvious they are derived rather than keyed.

- [ ] **Step 7: Verify in the browser**

```bash
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py migrate
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py runserver 8099
```

Create a Cashbook journal by hand with the three JE-001-shaped rows from Task 4's
test. Confirm, in order: the Tax and GST columns appear only when Cashbook is
selected; picking account 105 defaults the tax code to GST; GST fills in as
2,107.91 when 23,187.00 is keyed; typing over the GST figure sticks; changing
the tax code to FRE clears it; after saving, the grid shows gross again and the
detail page shows the two generated 3380 rows.

Take a screenshot of the saved detail page for the PR.

- [ ] **Step 8: Confirm the full-suite failure set is unchanged**

```bash
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core 2>&1 | tail -40 > /tmp/after_t5.txt
grep -E "^(FAIL|ERROR):" /tmp/after_t5.txt | sort > /tmp/after_t5_set.txt
diff /tmp/baseline_set.txt /tmp/after_t5_set.txt
```

- [ ] **Step 9: Commit**

```bash
git add templates/core/adjustment_form.html templates/core/journal_edit.html \
        templates/core/journal_detail.html core/views.py
git commit -m "feat(gst): Tax and GST columns on the cashbook journal grid

Columns appear only for Cashbook journals, default the tax code from the
chart on account selection, and compute GST at 1/11 as the accountant types.
A typed figure is left alone (it is a partial input tax credit); changing the
tax code clears it, because the figure belonged to the old treatment.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Balance-sheet mapping and the Eva reconciliation

**Files:**
- Modify: `core/gst_journal.py` (seed the mapping)
- Modify: `core/eva_engine.py:982-1020` (`gst_reconciliation` context)
- Test: `core/tests_gst_reconciliation.py`

**Interfaces:**
- Consumes: `GST_CONTROL_CODE` from Task 2.
- Produces: `ensure_gst_control_mapping(entity) -> None` in `core/gst_journal.py`.

- [ ] **Step 1: Write the failing tests**

Create `core/tests_gst_reconciliation.py`:

```python
from decimal import Decimal

from django.test import TestCase

from core.models import (
    ClientAccountMapping, Entity, EntityChartOfAccount, FinancialStatementLineItem,
)


class GstControlMappingTest(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Mapping Client",
            entity_type=Entity.EntityType.SOLE_TRADER,
            is_gst_registered=True,
        )
        EntityChartOfAccount.objects.create(
            entity=self.entity, account_code="3380",
            account_name="GST payable control account",
            tax_code="", section="liabilities",
        )

    def test_seeds_the_bs_cl_006_mapping(self):
        from core.gst_journal import ensure_gst_control_mapping
        ensure_gst_control_mapping(self.entity)
        cam = ClientAccountMapping.objects.get(
            entity=self.entity, client_account_code="3380",
        )
        self.assertIsNotNone(cam.mapped_line_item)
        self.assertEqual(cam.mapped_line_item.standard_code, "BS-CL-006")

    def test_is_idempotent_and_never_overwrites_an_existing_mapping(self):
        from core.gst_journal import ensure_gst_control_mapping
        ensure_gst_control_mapping(self.entity)
        ensure_gst_control_mapping(self.entity)
        self.assertEqual(
            ClientAccountMapping.objects.filter(
                entity=self.entity, client_account_code="3380",
            ).count(),
            1,
        )
```

Before writing the implementation, confirm the real model and field names:

```bash
grep -n "class ClientAccountMapping" -A 30 core/models.py
grep -n "class FinancialStatementLineItem" -A 25 core/models.py
grep -rn "BS-CL-006" --include=*.py . | grep -v venv | head
```

Adjust the test's field names (`standard_code`, `mapped_line_item`,
`client_account_code`) to whatever the models actually use. Do **not** guess —
the test is worthless if it asserts against invented names.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_gst_reconciliation -v 2`
Expected: FAIL — `ImportError: cannot import name 'ensure_gst_control_mapping'`.

- [ ] **Step 3: Implement the mapping seed**

Add to `core/gst_journal.py`:

```python
GST_CONTROL_STANDARD_CODE = "BS-CL-006"


def ensure_gst_control_mapping(entity):
    """Point 3380 at the standard 'GST payable' balance-sheet line.

    Without a ClientAccountMapping, docgen falls back to keyword/code-range
    classification for the account: the liability still appears under current
    liabilities, but not badged as the standard line. Six of the nine live
    entities already have this mapping from the bank-posting path; the two that
    do not have a 3380 balance sitting unmapped today.

    Never overwrites an existing mapping — the accountant may have pointed the
    account somewhere deliberately.
    """
    from .models import ClientAccountMapping, FinancialStatementLineItem

    if ClientAccountMapping.objects.filter(
        entity=entity, client_account_code=GST_CONTROL_CODE,
    ).exists():
        return

    item = FinancialStatementLineItem.objects.filter(
        standard_code=GST_CONTROL_STANDARD_CODE,
    ).first()
    if not item:
        return

    ClientAccountMapping.objects.create(
        entity=entity,
        client_account_code=GST_CONTROL_CODE,
        client_account_name=GST_CONTROL_NAME,
        mapped_line_item=item,
    )
```

Then call it from `split_cashbook_journal`, immediately before the control lines
are created:

```python
    if gst_on_credits or gst_on_debits:
        ensure_gst_control_mapping(entity)
```

If `ClientAccountMapping` has required fields beyond those three, add them —
read the model before writing, and match how `_post_journal_to_tb`'s CAM lookup
expects rows to look.

- [ ] **Step 4: Write the failing test for the Eva bucketing bug**

Append to `core/tests_gst_reconciliation.py`:

```python
class EvaGstBucketingTest(TestCase):
    """The control account matched the outer 'gst' filter but neither bucket,
    so the check reported $0.00 both sides while a real balance sat there."""

    def test_control_account_is_bucketed_by_its_columns_not_its_name(self):
        from core.eva_engine import _bucket_gst_account
        collected, paid = _bucket_gst_account(
            account_name="GST payable control account",
            effective_dr=Decimal("418.68"),
            effective_cr=Decimal("2107.91"),
        )
        self.assertEqual(collected, Decimal("2107.91"))
        self.assertEqual(paid, Decimal("418.68"))

    def test_named_accounts_still_bucket_by_name(self):
        from core.eva_engine import _bucket_gst_account
        collected, paid = _bucket_gst_account(
            account_name="GST Collected on Sales",
            effective_dr=Decimal("0"),
            effective_cr=Decimal("500.00"),
        )
        self.assertEqual(collected, Decimal("500.00"))
        self.assertEqual(paid, Decimal("0"))
```

- [ ] **Step 5: Run test to verify it fails**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_gst_reconciliation.EvaGstBucketingTest -v 2`
Expected: FAIL — `ImportError: cannot import name '_bucket_gst_account'`.

- [ ] **Step 6: Extract and fix the bucketing**

In `core/eva_engine.py`, add above the check-context builder:

```python
def _bucket_gst_account(account_name, effective_dr, effective_cr):
    """Split one GST account's balance into (collected, input credits).

    Named accounts bucket by name, as before. A combined control account —
    "GST payable control account", which is what the journal and bank paths
    both post to — matches neither name pattern, so it used to contribute to
    neither total and the check reported $0.00 on both sides while a real
    balance sat in the account. Bucket that one off its columns instead:
    credits are GST collected, debits are input tax credits.
    """
    zero = Decimal("0")
    name_lower = (account_name or "").lower()
    collected_names = ("gst collected", "gst on sales", "output tax")
    paid_names = ("gst paid", "gst on purchases", "input tax")

    if any(kw in name_lower for kw in collected_names):
        return abs(effective_cr - effective_dr), zero
    if any(kw in name_lower for kw in paid_names):
        return zero, abs(effective_dr - effective_cr)
    return abs(effective_cr or zero), abs(effective_dr or zero)
```

Then in the `elif check_id == "gst_reconciliation":` branch, replace the
name-matching classification:

```python
                # Classify for totals
                if any(kw in name_lower for kw in ["gst collected", "gst on sales", "output tax"]):
                    total_gst_collected += abs(cy_net)
                elif any(kw in name_lower for kw in ["gst paid", "gst on purchases", "input tax"]):
                    total_input_credits += abs(cy_net)
```

with:

```python
                collected, paid = _bucket_gst_account(
                    line.account_name, line.effective_dr, line.effective_cr,
                )
                total_gst_collected += collected
                total_input_credits += paid
```

Confirm `Decimal` is imported at the top of `core/eva_engine.py`; the branch
already uses a `ZERO` constant, so reuse that if it is module-level.

- [ ] **Step 7: Run the tests**

Run: `DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core.tests_gst_reconciliation -v 2`
Expected: PASS, 4 tests.

- [ ] **Step 8: Run the Eva suites**

```bash
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test \
  core.tests_eva_finding_stability core.tests_eva_reflection_phase2a \
  core.tests_risk_module_respects_addressed -v 2
```

`core/tests.py:1239` and `:1417` also exercise `gst_reconciliation` — run
`core.tests` too and read any failure rather than assuming it is baseline.

- [ ] **Step 9: Confirm the full-suite failure set is unchanged**

```bash
DATABASE_URL="sqlite:///$(pwd)/test_db.sqlite3" python3 manage.py test core 2>&1 | tail -40 > /tmp/after_t6.txt
grep -E "^(FAIL|ERROR):" /tmp/after_t6.txt | sort > /tmp/after_t6_set.txt
diff /tmp/baseline_set.txt /tmp/after_t6_set.txt
```

- [ ] **Step 10: Commit**

```bash
git add core/gst_journal.py core/eva_engine.py core/tests_gst_reconciliation.py
git commit -m "fix(gst): map 3380 to the standard line and bucket it in Eva

Two gaps the cashbook work exposed:

- a freshly created 3380 had no ClientAccountMapping, so docgen fell back to
  keyword classification and the liability was not badged as GST payable.
  ensure_gst_control_mapping seeds BS-CL-006 without overwriting a mapping
  the accountant set deliberately.
- Eva's gst_reconciliation bucketed GST accounts by name, and 'GST payable
  control account' matched the outer filter but neither bucket, so the check
  reported \$0.00 collected and \$0.00 credits while a real balance sat in the
  account. Pre-existing, and already misreporting for the six live entities
  holding a 3380 balance. Now bucketed off its columns.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Restate ELLIOTT JAQUES JE-001

**Files:**
- Create: `data_fixes/restate_elliott_jaques_je001.py`
- Creates at runtime: `data_fixes/elliott_jaques_je001_pre_gst_split_<ts>.json`

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: no code interface — a one-off data change.

**Preconditions to verify before running.** The FY was `status=draft`, unlocked, with zero `BASPeriod` rows when this was designed. Re-check, because another session may have moved it:

```bash
python3 - <<'PY'
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
django.setup()
from core.models import Entity, BASPeriod
e = Entity.objects.get(entity_name="ELLIOTT JAQUES")
fy = e.financial_years.first()
print("fy", fy.start_date, fy.end_date, "locked", fy.is_locked, "status", fy.status)
print("bas periods", BASPeriod.objects.filter(financial_year=fy).count())
j = fy.adjusting_journals.get(reference_number="JE-001")
print("journal", j.journal_type, j.status, "lines", j.lines.count(),
      "dr", j.total_debit, "cr", j.total_credit)
PY
```

If the year is locked, finalised, or has a lodged BAS period, **stop and report** rather than restating.

- [ ] **Step 1: Write the script**

Create `data_fixes/restate_elliott_jaques_je001.py`:

```python
"""Restate ELLIOTT JAQUES JE-001 as a Cashbook journal with GST split out.

JE-001 was keyed GST-inclusive with no 3380 line, which left the P&L
GST-inclusive and the ATO liability off the balance sheet. It is the only
journal on the platform in this shape, its financial year is draft and
unlocked, and nothing has been lodged — so it is restated in place rather than
converted through general tooling.

Backs up the journal and its TB lines first. Reposts through the normal path
(reverse via source_journal, then _post_journal_to_tb) rather than patching TB
rows, so the trial balance is rebuilt exactly as a new journal would build it.

Usage:
    python3 data_fixes/restate_elliott_jaques_je001.py --dry-run
    python3 data_fixes/restate_elliott_jaques_je001.py --commit
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction  # noqa: E402

from core.bas_utils import calculate_gst_for_period, get_period_dates  # noqa: E402
from core.gst_journal import split_cashbook_journal  # noqa: E402
from core.models import (  # noqa: E402
    AdjustingJournal, BASPeriod, Entity, TrialBalanceLine,
)
from core.views import _post_journal_to_tb, _reverse_journal_tb_lines  # noqa: E402

EXPECTED_GROSS_TOTAL = Decimal("23187.00")
EXPECTED_CONTROL_NET = Decimal("-1689.23")   # closing_balance on 3380
EXPECTED_1A = Decimal("2107.91")
EXPECTED_1B = Decimal("418.68")

TAX_CODES = {
    "105": "GST",
    "1510": "INP", "1800": "INP", "1804": "INP", "1808": "INP",
    "1809": "INP", "1845": "INP", "1940": "INP", "1946": "INP",
    "4080": "N-T",
}


def backup(journal, fy):
    stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"elliott_jaques_je001_pre_gst_split_{stamp}.json",
    )
    payload = {
        "journal": {
            "id": str(journal.id),
            "reference_number": journal.reference_number,
            "journal_type": journal.journal_type,
            "status": journal.status,
            "journal_date": str(journal.journal_date),
            "description": journal.description,
            "total_debit": str(journal.total_debit),
            "total_credit": str(journal.total_credit),
        },
        "lines": [
            {
                "id": str(l.id), "line_number": l.line_number,
                "account_code": l.account_code, "account_name": l.account_name,
                "description": l.description,
                "debit": str(l.debit), "credit": str(l.credit),
            }
            for l in journal.lines.order_by("line_number", "id")
        ],
        "tb_lines": [
            {
                "id": str(t.id), "account_code": t.account_code,
                "account_name": t.account_name,
                "debit": str(t.debit), "credit": str(t.credit),
                "closing_balance": str(t.closing_balance),
                "source": t.source,
                "source_journal": str(t.source_journal_id) if t.source_journal_id else None,
            }
            for t in TrialBalanceLine.objects.filter(financial_year=fy).order_by("account_code")
        ],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (args.commit or args.dry_run):
        ap.error("pass --dry-run or --commit")

    entity = Entity.objects.get(entity_name="ELLIOTT JAQUES")
    fy = entity.financial_years.first()
    journal = fy.adjusting_journals.get(reference_number="JE-001")

    if fy.is_locked:
        sys.exit("REFUSING: financial year is locked.")
    if BASPeriod.objects.filter(financial_year=fy, status="lodged").exists():
        sys.exit("REFUSING: a BAS period for this year is already lodged.")
    if journal.journal_type == AdjustingJournal.JournalType.CASHBOOK:
        sys.exit("Already restated — journal is a Cashbook journal.")

    gross_total = sum(max(l.debit, l.credit) for l in journal.lines.all()) / 2
    if journal.total_debit != EXPECTED_GROSS_TOTAL:
        sys.exit(
            f"REFUSING: JE-001 totals {journal.total_debit}, expected "
            f"{EXPECTED_GROSS_TOTAL}. The journal has changed since this "
            f"script was written — re-read it before restating."
        )

    path = backup(journal, fy)
    print(f"backup written: {path}")

    if args.dry_run:
        for line in journal.lines.order_by("line_number", "id"):
            code = line.account_code
            print(f"  {code:<6} {line.account_name[:28]:<29} "
                  f"gross {max(line.debit, line.credit):>10} "
                  f"tax {TAX_CODES.get(code, '(chart)')}")
        print("dry run — nothing written")
        return

    with transaction.atomic():
        _reverse_journal_tb_lines(journal)

        journal.journal_type = AdjustingJournal.JournalType.CASHBOOK
        journal.save(update_fields=["journal_type"])

        for line in journal.lines.all():
            line.tax_code = TAX_CODES.get(line.account_code, "")
            line.gst_amount = Decimal("0")
            line.save(update_fields=["tax_code", "gst_amount"])

        split_cashbook_journal(journal)
        _post_journal_to_tb(journal, fy)
        journal.recalculate_totals()

    journal.refresh_from_db()
    control = TrialBalanceLine.objects.get(financial_year=fy, account_code="3380")
    start, end = get_period_dates(fy, "quarterly", 2)
    bas = calculate_gst_for_period(fy, start, end)["bas_data"]

    print(f"journal totals  Dr {journal.total_debit}  Cr {journal.total_credit}")
    print(f"3380 closing    {control.closing_balance}")
    print(f"BAS Q2          1A {bas['1A']}  1B {bas['1B']}  net {bas['gst_payable']}")

    failures = []
    if journal.total_debit != EXPECTED_GROSS_TOTAL:
        failures.append(f"journal total {journal.total_debit} != {EXPECTED_GROSS_TOTAL}")
    if control.closing_balance != EXPECTED_CONTROL_NET:
        failures.append(f"3380 closing {control.closing_balance} != {EXPECTED_CONTROL_NET}")
    if bas["1A"] != EXPECTED_1A:
        failures.append(f"1A {bas['1A']} != {EXPECTED_1A}")
    if bas["1B"] != EXPECTED_1B:
        failures.append(f"1B {bas['1B']} != {EXPECTED_1B}")
    if -control.closing_balance != bas["gst_payable"]:
        failures.append(
            f"3380 {-control.closing_balance} does not tie to BAS net {bas['gst_payable']}"
        )
    if failures:
        sys.exit("POST-CHECK FAILED:\n  " + "\n  ".join(failures))
    print("all post-checks passed; 3380 ties to the BAS")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry run**

Run: `python3 data_fixes/restate_elliott_jaques_je001.py --dry-run`
Expected: a backup file is written, all 11 lines print with their tax codes (`105` GST, nine expenses INP, `4080` N-T), and nothing else changes.

- [ ] **Step 3: Confirm the backup is complete before committing the change**

```bash
python3 -c "
import json, glob
p = sorted(glob.glob('data_fixes/elliott_jaques_je001_pre_gst_split_*.json'))[-1]
d = json.load(open(p))
print(p)
print('journal lines backed up:', len(d['lines']))
print('tb lines backed up:', len(d['tb_lines']))
assert len(d['lines']) == 11, d['lines']
assert len(d['tb_lines']) == 10, d['tb_lines']
print('backup verified')
"
```

Do not proceed unless this prints `backup verified`.

- [ ] **Step 4: Run it for real**

Run: `python3 data_fixes/restate_elliott_jaques_je001.py --commit`
Expected output:

```
journal totals  Dr 23187.00  Cr 23187.00
3380 closing    -1689.23
BAS Q2          1A 2107.91  1B 418.68  net 1689.23
all post-checks passed; 3380 ties to the BAS
```

If any post-check fails, the atomic block has already committed — restore from
the backup JSON before doing anything else, and report what diverged.

- [ ] **Step 5: Eyeball the restated journal in the UI**

Open the journal detail page for ELLIOTT JAQUES JE-001. Confirm: 11 keyed lines
now net, two generated `3380` rows badged as generated, and the balance sheet
showing GST payable of 1,689.23 under current liabilities.

- [ ] **Step 6: Commit**

```bash
git add data_fixes/restate_elliott_jaques_je001.py \
        data_fixes/elliott_jaques_je001_pre_gst_split_*.json
git commit -m "fix(data): restate ELLIOTT JAQUES JE-001 with GST split out

JE-001 was keyed GST-inclusive with no 3380 line, leaving the P&L
GST-inclusive and the ATO liability off the balance sheet. Restated in place
as a Cashbook journal: sales to 21,079.09 net, expenses to 4,186.72 net,
drawings unchanged, and a generated 3380 pair netting 1,689.23 Cr which ties
to the Q2 BAS exactly.

Reposted through _reverse_journal_tb_lines + _post_journal_to_tb rather than
patching TB rows. Backup committed alongside.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** Every section maps to a task: decisions 1–2 → Task 2; decision 3 → Task 1 (type) + Task 5 (gating); decision 4 → Task 2 (`line_gst` override, no apportionment engine); decisions 5–6 → Task 3; decision 7 → Task 7. Components: data model → 1, split engine → 2, journal UI → 4/5, BAS engine → 3, TB/FS/reconciliation → 6, migration → 7, testing → spread through all seven. Regression surface → Task 3 Step 11.

**Deliberate omissions**, all recorded in the spec's Out of Scope: apportionment wiring, a general convert-to-cashbook action, cashbook journals with a bank line, general-journal GST derivation, `BulkJournalUpload`, and the dead `core/views.py` BAS copies.

**Two places the implementer must read before writing**, called out inline rather than guessed: `ClientAccountMapping` / `FinancialStatementLineItem` field names in Task 6 Step 1, and the formset prefix in Task 4 Step 1.
