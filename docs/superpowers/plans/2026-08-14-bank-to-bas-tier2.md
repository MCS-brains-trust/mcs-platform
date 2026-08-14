# Bank Statement to BAS Tier 2 Spec — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A permanent Tier 2 spec covering bank statement upload, allocation, posting to the trial balance, and BAS/GST figures, driven by a synthesised CBA statement whose geometry reproduces a real one.

**Architecture:** A generator script builds a committed CBA-format PDF whose word coordinates satisfy `review/statement_geometry.py`. A parameterised flow module drives the real UI on its own Django instance and database branch, mirroring `e2e/tier2/roll_forward_flow.ts`. Every BAS label is hand-computed, not baselined.

**Tech Stack:** Playwright (TypeScript), Django, pdfplumber, reportlab 4.4.10, PostgreSQL template-database branching.

**Spec:** `docs/superpowers/specs/2026-08-14-bank-to-bas-tier2-design.md`

## Global Constraints

- **Port 8206.** 8202–8205 are taken by the four roll-forward specs; a collision wedges both files.
- **`--workers=1` while developing.** Each spec file branches a ~471 MB database and **production shares this host**.
- **No real client statement is ever committed**, and none is left in the working tree.
- **reportlab must run in invariant mode** — `rl_config.invariant = 1` plus `Canvas(buf, invariant=1)`. Without it a `/CreationDate` is embedded and no two runs produce the same bytes. Verified 2026-08-14.
- **The geometry engine is CBA-only.** `review/pdf_parsers.py` routes on `if bank == "cba"`; every other bank still uses the flat-text parser. Do not add another bank without a real exemplar statement for it.
- **If a hand-computed figure disagrees with the application, STOP and report it.** Do not adjust the expectation to match the output. The whole point of hand-computing is that a wrong figure fails; editing the expectation converts this suite into the golden baseline it was designed not to be.
- **Playwright serial mode** skips every remaining test in a file after the first failure, so any test expected to be red must be last. `tier2/known_failures.json` must stay empty.

## Reference: verified facts

All checked against the tree at `4403f5b` on 2026-08-14.

**What `parse_cba_geometry` requires** (`review/statement_geometry.py`):

| Requirement | Detail |
|---|---|
| Rows | words grouped by `round(w['top'])`, so each statement line must sit on one y |
| Money columns | two distinct clusters of bare-amount `x1` right-edges, **>12.0pt apart**, each with ≥`max(2, n//20)` members — so **at least 2 debits and 2 credits** |
| Date tokens | `^(\d{1,2})(Jan\|Feb\|...)` — **glued**, e.g. `31Oct` |
| Opening anchor | a row concatenating to contain `OPENINGBALANCE`, carrying `10,000.00 CR` and a `20\d{2}` year token |
| Closing anchor | a row concatenating to contain `CLOSINGBALANCE` with its balance |
| Reconciliation | `opening + sum(signed amounts) == closing` within 0.01, else `StatementParseError` |
| Furniture skipped | `Page\d+of\d+`, `DateTransactionDebitCreditBalance`, `AccountNumber`, `^\d{5}\.\d{5}` |
| Sign convention | credit > 0, debit < 0 |

**How the fixture actually gets routed — corrected 2026-08-14, after Task 2's review checked it against the source.**

This section previously claimed `detect_bank` matches `Date\s+Transaction\s+Debit\s+Credit\s+Balance` against `extract_text()`, so the fixture needed real whitespace in its header. **That is false.** `detect_bank` (`review/pdf_parsers.py:1741`) selects CBA on:

```python
is_cba = "commonwealth bank" in text_lower or "commbank" in text_lower
```

A bare substring match on the preamble bank name. The only whitespace regex in that function (`:1743`) discriminates the transaction-listing variant, `date\s+transaction\s+details\s+amount\s+balance`, which this fixture never matches because its header says Debit/Credit rather than details/amount.

So the properties that actually matter are:

| Property | What needs it |
|---|---|
| Preamble contains "Commonwealth Bank" | `detect_bank` → `"cba"` |
| Header does NOT say "details"/"amount" | avoids `"cba_txn_listing"` |
| Dates glued as `02Oct` | `statement_geometry.DATE_RE` |
| Debit/credit columns >12pt apart | `_money_columns` |
| Opening/closing anchors reconcile | the geometry engine's gate |

The header's column spacing matters only to the legacy `parse_cba_statement`, which this fixture never reaches because the geometry engine succeeds first. Task 2's tests are honest about this: only one of its two guards defends the header spacing, and it defends it as an input-shape property rather than as the routing mechanism.

**A fidelity limit to record, not fix.** The dates are stored as glued literals and drawn with one call, so the fixture assumes the kerning collapse's outcome rather than reproducing it. Correct input shape for the parser; unable to catch a regression in real-world kerning handling. Task 9 documents this.

**Selector hooks that exist.** There are **no `data-testid` attributes anywhere in the project** — Tier 2's convention is CSS ids plus `expect(page.locator('body')).toContainText(...)`.

| Page | Hook | Notes |
|---|---|---|
| FY detail (`templates/core/financial_year_detail.html`) | `#fyFileInput` | `input[name="files"]`, carries `.d-none`; `setInputFiles` works on hidden inputs |
| FY detail | `#periodStartInput`, `#periodEndInput` | read by the upload JS at `:2856` |
| FY detail | `#fyUploadSubmitBtn` | starts the parse loop |
| Upload preview (`templates/review/upload_preview.html`) | `#importCount`, `#confirmImportBtn` | the button is `disabled` until the preview is satisfied |
| Review (`templates/review/review_detail.html`) | `[data-txn-id]` | per row, carrying `data-code`, `data-amount`, `data-confirmed`, `data-gst-treatment` |
| Review | `.account-picker-input` → `.account-option[data-code="…"]` | filter-as-you-type picker, **not** a `<select>`; clicking an option calls `selectAccount(txnId, code, name)` |
| Review | `.tax-select` | a real `<select>` |
| Review | `#btn-submit`, `#confirmed-count`, `#btn-accept-all` | `#btn-submit` is disabled until every row is confirmed; `#btn-accept-all` accepts AI suggestions and is never used here |

**Routes.** The upload is three hops, not one:

| Purpose | Name | Path |
|---|---|---|
| Parse only (no DB write) | `review:parse_statement` | `/parse-statement/` — multipart `file`, `entity_id`, `period_start`, `period_end`, `clear_session` |
| Preview page | `review:upload_preview` | `/upload-preview/?fy=<uuid>&entity=<uuid>` |
| Confirm import | `review:confirm_import` | `/confirm-import/`, JSON body |
| ~~Legacy direct upload~~ | `review:upload_statement` | `/upload-statement/` — **marked legacy in `review/urls.py:24`, writes straight to the database. Do not use.** |
| Review UI | `review:review_detail` | `/review/<uuid>/` |
| Confirm one txn | `review:confirm_transaction` | JSON body |
| BAS dashboard | `core:gst_activity_statement` | `/years/<uuid>/gst/` |
| Lodge | `core:bas_lodge_period` | `/years/<uuid>/gst/lodge/<int>/` |
| Unlodge | `core:bas_unlodge_period` | `/years/<uuid>/gst/unlodge/<int>/` |
| Coverage | `core:bas_coverage_check` | `/years/<uuid>/gst/coverage/<int>/` |

**The fixture's transaction table.** Six transactions, three credits and three debits, satisfying the two-cluster minimum:

| Date | Description | Debit | Credit | Tax code |
|---|---|---:|---:|---|
| 02Oct | EFTPOS SALES INV 1001 | | 1,100.00 | GST |
| 05Oct | OFFICE SUPPLIES PTY LTD | 550.00 | | GST |
| 12Oct | BANK FEES AND CHARGES | 22.00 | | GST |
| 18Oct | CONSULTING FEE INV 1002 | | 2,200.00 | GST |
| 24Oct | FRESH FOOD SUPPLIES | 300.00 | | FRE |
| 28Oct | EXPORT SALE INV 1003 | | 800.00 | FRE |

Opening 10,000.00 CR. Credits 4,100.00, debits 872.00, closing **13,228.00 CR**.

**Hand-computed BAS labels** from that table, GST at 1/11 of the GST-inclusive amount:

```
G1  Total sales (incl GST)        1,100 + 2,200 + 800 = 4,100.00
G2  Export sales                                        0.00
G3  Other GST-free sales                              800.00
G10 Capital purchases                                   0.00
G11 Non-capital purchases (incl GST)  550 + 22 + 300 = 872.00
1A  GST on sales                  (1,100 + 2,200)/11 =  300.00
1B  GST on purchases                     (550 + 22)/11 = 52.00
Net GST payable                        300.00 - 52.00 = 248.00
```

---

### Task 1: The fixture generator

Pure Python and fast — no Playwright, no database. Doing this first means the hardest part (satisfying the geometry engine) is proven before any browser work.

**Files:**
- Create: `e2e/fixtures/statements/make_cba.py`
- Create: `e2e/fixtures/statements/cba_sample.pdf` (generated)
- Test: `review/tests_statement_fixture.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `make_cba.build_pdf() -> bytes` and `make_cba.TRANSACTIONS`, a list of `(date_str, description, debit, credit)` tuples. Task 2 and the flow module's expected values both read `TRANSACTIONS`.

- [ ] **Step 1: Write the failing test**

```python
# review/tests_statement_fixture.py
"""The synthesised CBA fixture must satisfy the real geometry parser.

If this fails, no Playwright test built on the fixture can mean anything --
the statement would never get past parsing.
"""
from django.test import SimpleTestCase

from e2e.fixtures.statements import make_cba
from review.statement_geometry import parse_cba_geometry


class FixtureParsesTests(SimpleTestCase):
    def setUp(self):
        self.pdf = make_cba.build_pdf()
        self.result = parse_cba_geometry(self.pdf)

    def test_all_six_transactions_are_extracted(self):
        self.assertEqual(len(self.result["transactions"]), 6)

    def test_the_balances_anchor_the_statement(self):
        self.assertEqual(self.result["opening_balance"], 10000.00)
        self.assertEqual(self.result["closing_balance"], 13228.00)

    def test_debits_are_negative_and_credits_positive(self):
        by_desc = {t["description"]: t["amount"] for t in self.result["transactions"]}
        self.assertAlmostEqual(by_desc["EFTPOS SALES INV 1001"], 1100.00, places=2)
        self.assertAlmostEqual(by_desc["OFFICE SUPPLIES PTY LTD"], -550.00, places=2)
        self.assertAlmostEqual(by_desc["EXPORT SALE INV 1003"], 800.00, places=2)

    def test_the_dates_survive_the_kerning_collapse(self):
        """The defect this fixture reproduces: extract_text() drops the space in
        '31 Oct', so a date regex expecting a space matched zero lines and the
        parser returned nothing. The geometry engine reads the glued form."""
        dates = sorted(t["date"] for t in self.result["transactions"])
        self.assertEqual(dates[0], "2025-10-02")
        self.assertEqual(dates[-1], "2025-10-28")

    def test_the_statement_reconciles(self):
        total = sum(t["amount"] for t in self.result["transactions"])
        self.assertAlmostEqual(
            self.result["opening_balance"] + total,
            self.result["closing_balance"],
            places=2,
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/fixt.sqlite3" \
  venv/bin/python manage.py test review.tests_statement_fixture -v2
```

Expected: FAIL with `ModuleNotFoundError: No module named 'e2e.fixtures.statements'`.

- [ ] **Step 3: Write the generator**

> **The code below does NOT parse as written — corrected in `7bcb260`.** Proven during
> execution on 2026-08-14, twice over. Take the committed
> `e2e/fixtures/statements/make_cba.py` as the authority, not this snippet. Two changes
> were required:
>
> 1. **Draw no per-transaction running balance.** `_money_columns` picks the two *most
>    populous* clusters of bare-amount right edges. A balance on every row gives 8 tokens
>    against 3 each for debit and credit, so the balance out-populates them, is mistaken
>    for a money column, and reconciliation fails by **+67,334.00**. Gluing the balance to
>    `CR` excludes it from `MOVE_RE` but it then survives `_text_only` and contaminates
>    every description instead. Only the OPENING and CLOSING anchor rows carry a balance,
>    and that costs nothing: `parse_cba_geometry`'s transaction dicts have no balance
>    field, so nothing downstream can observe its absence.
> 2. **Give the preamble row a `Page 1 of 1` marker**, so `_is_furniture` skips it.
>    Otherwise nothing resets the description accumulator before the first transaction and
>    the bank's name prefixes it.
>
> The snippet is kept rather than rewritten because the two failures it produces are
> instructive, and any future bank's fixture will hit the same two traps.

Both properties matter: the header keeps real spaces (true of a real CBA statement), the date column is drawn tight so the date glues. `drawString` at explicit x positions is what puts the debit and credit columns >12pt apart.

> **Correction (post-merge cleanup, 2026-08-14):** the clause above once read "so
> `detect_bank` fires" -- that is false, per the correction already noted at line
> 42 of this file. `detect_bank` routes this fixture to "cba" on a bare substring
> match of the bank name in the preamble ("commonwealth bank" in the lowercased
> text), never on this header's whitespace. Left uncorrected in the quoted
> snippet below, which this document already treats as a superseded historical
> artefact (see the disclaimer immediately above it).

```python
# e2e/fixtures/statements/make_cba.py
"""Generate the committed CBA statement fixture.

The PDF is a build artifact of this script, not a hand-made binary, so a reviewer
reads a transaction table instead of an opaque blob. Regenerate with:

    venv/bin/python e2e/fixtures/statements/make_cba.py

Two properties must hold at once, and both are true of real CBA statements:

  * the column header keeps real spaces, so detect_bank's
    r"Date\\s+Transaction\\s+Debit\\s+Credit\\s+Balance" matches extract_text()
  * the date column is drawn tight enough that "02 Oct" extracts as "02Oct",
    which is the glued form review/statement_geometry.py's DATE_RE expects

Invariant mode is required: reportlab embeds a /CreationDate by default and no
two runs would produce the same bytes.
"""
import io
import os

import reportlab.rl_config as rl_config

rl_config.invariant = 1

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT_PATH = os.path.join(os.path.dirname(__file__), "cba_sample.pdf")

OPENING = 10000.00
YEAR = "2025"

# (date, description, debit, credit)
TRANSACTIONS = [
    ("02Oct", "EFTPOS SALES INV 1001", None, 1100.00),
    ("05Oct", "OFFICE SUPPLIES PTY LTD", 550.00, None),
    ("12Oct", "BANK FEES AND CHARGES", 22.00, None),
    ("18Oct", "CONSULTING FEE INV 1002", None, 2200.00),
    ("24Oct", "FRESH FOOD SUPPLIES", 300.00, None),
    ("28Oct", "EXPORT SALE INV 1003", None, 800.00),
]

X_DATE, X_DESC, X_DEBIT, X_CREDIT, X_BALANCE = 40, 95, 300, 380, 470
LINE_HEIGHT = 18


def _money(value):
    return f"{value:,.2f}"


def build_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 9)
    y = 780

    c.drawString(X_DATE, y, "Commonwealth Bank of Australia")
    y -= LINE_HEIGHT
    c.drawString(X_DATE, y, "Account Number 06 2000 12345678")
    y -= LINE_HEIGHT * 2

    # Header with real spaces: detect_bank needs whitespace here.
    c.drawString(X_DATE, y, "Date    Transaction    Debit    Credit    Balance")
    y -= LINE_HEIGHT

    balance = OPENING
    c.drawString(X_DATE, y, "01Oct")
    c.drawString(X_DESC, y, f"{YEAR} OPENING BALANCE")
    c.drawString(X_BALANCE, y, f"{_money(balance)} CR")
    y -= LINE_HEIGHT

    for date, desc, debit, credit in TRANSACTIONS:
        c.drawString(X_DATE, y, date)
        c.drawString(X_DESC, y, desc)
        if debit is not None:
            c.drawString(X_DEBIT, y, _money(debit))
            balance -= debit
        if credit is not None:
            c.drawString(X_CREDIT, y, _money(credit))
            balance += credit
        c.drawString(X_BALANCE, y, f"{_money(balance)} CR")
        y -= LINE_HEIGHT

    c.drawString(X_DATE, y, "31Oct")
    c.drawString(X_DESC, y, "CLOSING BALANCE")
    c.drawString(X_BALANCE, y, f"{_money(balance)} CR")

    c.save()
    return buf.getvalue()


if __name__ == "__main__":
    with open(OUT_PATH, "wb") as fh:
        fh.write(build_pdf())
    print(f"wrote {OUT_PATH}")
```

Create `e2e/fixtures/statements/__init__.py` (empty) so the test can import the module.

- [ ] **Step 4: Run the test**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/fixt.sqlite3" \
  venv/bin/python manage.py test review.tests_statement_fixture -v2
```

Expected: PASS. If the date does **not** glue, tighten the date column by drawing `"02"` and `"Oct"` as separate `drawString` calls 1pt apart rather than one string — pdfplumber's word splitter joins tokens whose gap is below its threshold. If column detection fails, widen `X_CREDIT - X_DEBIT`; the clusters must be more than 12.0pt apart.

- [ ] **Step 5: Add the determinism test**

```python
    def test_the_pdf_regenerates_byte_for_byte(self):
        """The committed binary must be exactly what the script produces, or a
        reviewer cannot tell what they are approving. reportlab embeds a
        /CreationDate unless invariant mode is on."""
        self.assertEqual(make_cba.build_pdf(), make_cba.build_pdf())
```

- [ ] **Step 6: Run it, then generate the committed PDF**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/fixt.sqlite3" \
  venv/bin/python manage.py test review.tests_statement_fixture
venv/bin/python e2e/fixtures/statements/make_cba.py
```

- [ ] **Step 7: Commit**

```bash
git add e2e/fixtures/statements/ review/tests_statement_fixture.py
git commit -m "test: a CBA statement fixture the geometry parser accepts"
```

---

### Task 2: The fixture routes to the CBA geometry path

Task 1 proved the parser *can* read the fixture. This proves the application actually *sends* it there — the header-spacing property — and that the legacy fallback is not silently doing the work.

**Files:**
- Modify: `review/tests_statement_fixture.py`

**Interfaces:**
- Consumes: `make_cba.build_pdf()` from Task 1.
- Produces: nothing consumed later.

- [ ] **Step 1: Write the failing test**

```python
class FixtureRoutingTests(SimpleTestCase):
    """The dual property: a header with real whitespace so detect_bank fires,
    and glued dates so the geometry engine is the thing that parses it."""

    def setUp(self):
        self.pdf = make_cba.build_pdf()

    def test_detect_bank_identifies_it_as_cba(self):
        from review.pdf_parsers import detect_bank

        self.assertEqual(detect_bank(self.pdf), "cba")

    def test_the_header_keeps_its_spaces_but_the_dates_do_not(self):
        import io
        import pdfplumber

        with pdfplumber.open(io.BytesIO(self.pdf)) as pdf:
            text = pdf.pages[0].extract_text()
        self.assertRegex(text, r"Date\s+Transaction\s+Debit\s+Credit\s+Balance")
        self.assertIn("02Oct", text)
        self.assertNotIn("02 Oct", text)
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub && DATABASE_URL="sqlite:////tmp/fixt.sqlite3" \
  venv/bin/python manage.py test review.tests_statement_fixture.FixtureRoutingTests -v2
```

Expected: initially FAIL on one of the two spacing assertions. Adjust the header's inter-word spacing (widen) or the date rendering (tighten) in `make_cba.py` until both hold, then regenerate the PDF.

- [ ] **Step 3: Commit**

```bash
git add e2e/fixtures/statements/ review/tests_statement_fixture.py
git commit -m "test: the fixture reaches the geometry parser, not the legacy one"
```

---

### Task 3: A GST-registered fixture entity

**Files:**
- Modify: `core/e2e_fixture_data.py`
- Modify: `core/management/commands/e2e_seed_fixture_entity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a manifest at `.e2e/fixture_entity_bank_bas.json` containing `{"entity": <uuid>, "current_fy": <uuid>, "bank_account_code": "2000"}`, read by the flow module in Task 4.

- [ ] **Step 1: Add the profile**

`FixtureProfile` is a frozen dataclass at `core/e2e_fixture_data.py:65` with fields `key`, `ids`, `entity_kwargs`, `chart` (a list of `(code, name, section)`), `prior_year_tb` (a list of `(code, name, debit, credit)`), `retained_profits_code`, `client_name` and optional `depreciation_asset`. The company profile at `:86` is the model to follow.

Codes here are HandiLedger numerics, matching the trust, partnership and sole-trader profiles and every real entity in the book. The company profile's MYOB-style `1-2000` codes are deliberately unlike production and exist to expose a specific defect; there is no reason to inherit that here.

> **The snippet below does NOT seed as written — corrected in `67a838d`.** Proven during
> execution on 2026-08-14. Take the committed `core/e2e_fixture_data.py` as the authority.
> Three changes were required:
>
> 1. **`BANK_BAS_IDS` needs a `prior_fy` key.** `seed_fixture_entity` always creates a
>    prior `FinancialYear` row — it is the row that carries `prior_year_tb`, even an empty
>    one — so omitting the key is a `KeyError` on seed, not an inert no-op. The flow never
>    reads that year; it exists only so the seeder's shared code path has somewhere to put
>    the empty `prior_year_tb`.
> 2. **Sales' section is `"revenue"`, not `"income"`.**
>    `EntityChartOfAccount.StatementSection` has no `income` choice. Note the failure mode:
>    sqlite carries no CHECK constraint, so a bad value saves silently there and only the
>    hardened Postgres E2E copy rejects it — which is the second reason the verification in
>    Step 3 below had to change.
> 3. **`FixtureProfile` gained a `bank_account_code` field**, read by
>    `e2e_seed_fixture_entity` to write the one extra manifest key this profile's flow
>    needs. The Interfaces block above requires that key in the manifest but the snippet
>    gave no way to produce it. No other profile populates the field.
>
> The snippet is annotated rather than rewritten because 1 and 2 are traps any future
> fixture profile will hit.

```python
# core/e2e_fixture_data.py — append after the existing profiles

BANK_BAS_IDS = {
    "client": "b1a5c0de-0000-4000-8000-000000000001",
    "entity": "b1a5c0de-0000-4000-8000-000000000002",
    "current_fy": "b1a5c0de-0000-4000-8000-000000000003",
}

BANK_BAS_CHART = [
    ("2000", "Cash at bank", "current_assets"),
    ("0510", "Sales", "income"),
    ("1520", "Office supplies", "expenses"),
    ("1530", "Bank fees and charges", "expenses"),
    ("1540", "Food supplies", "expenses"),
    ("4199", "Retained profits", "equity"),
]

BANK_BAS = FixtureProfile(
    key="bank_bas",
    ids=BANK_BAS_IDS,
    client_name="E2E Bank BAS Client",
    entity_kwargs={
        "entity_name": "E2E Bank BAS Pty Ltd",
        "entity_type": "company",
        # Valid check digits — core/validators.py rejects malformed identifiers,
        # and a fixture that could not be saved through the UI is a trap.
        "abn": "51824753556",
        "acn": "004085616",
        "financial_year_end": "06-30",
        "reporting_framework": "SPFR",
        "company_size": "small_proprietary",
        # The whole point of this profile: without it there is no BAS to compute.
        "is_gst_registered": True,
        "include_comparative_figures": False,
    },
    chart=BANK_BAS_CHART,
    # No prior year. This flow never rolls forward, and an unnecessary prior year
    # would only add figures that later assertions could trip over.
    prior_year_tb=[],
    retained_profits_code="4199",
)
```

Register it wherever the existing profiles are looked up by `--profile` key, following the same pattern.

- [ ] **Step 2: Confirm the BAS frequency field**

`core/views_bas.py:234` reads `getattr(entity, "bas_frequency", "quarterly")`, so quarterly is the fallback whether or not the field exists.

```bash
cd /opt/statementhub && venv/bin/python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from core.models import Entity
print('bas_frequency is a real field:', any(f.name=='bas_frequency' for f in Entity._meta.get_fields()))
"
```

If it prints `True`, add `"bas_frequency": "quarterly"` to `entity_kwargs`. If `False`, leave it out — the `getattr` default already gives quarterly, and inventing a field would break the seed.

The financial year the seeder creates must cover **1 July 2025 to 30 June 2026**, so the October statement falls inside Q2.

- [ ] **Step 3: Verify the manifest is written**

The sqlite command this step originally carried **cannot work for any profile**, not just
this one: `e2e_seed_fixture_entity` calls `assert_e2e_database()`
(`core/e2e_support.py:110`), which runs `SELECT to_regclass('public.e2e_marker')` and
aborts unless the connected database is a hardened Postgres E2E copy. That guard is
deliberate — these commands mutate financial data and must refuse to run anywhere else.
Pre-existing, not introduced by this task. Seed against a scratch branch of the E2E
template instead, which is what `e2e/scripts/start_server.sh:32-37` does per instance:

```bash
cd /opt/statementhub          # or the worktree under test — this runs its checkout's code
set -a; source /opt/statementhub/.e2e/db.env; set +a
export PGPASSWORD="$E2E_DB_PASSWORD"
PSQL="psql -h $E2E_DB_HOST -p $E2E_DB_PORT -U $E2E_DB_USER -v ON_ERROR_STOP=1"

$PSQL -d postgres -qc "DROP DATABASE IF EXISTS e2e_scratch_bank_bas WITH (FORCE);"
$PSQL -d postgres -qc "CREATE DATABASE e2e_scratch_bank_bas TEMPLATE ${E2E_TEMPLATE_DB};"

DJANGO_SETTINGS_MODULE=config.settings_e2e E2E_DB_NAME=e2e_scratch_bank_bas \
  /opt/statementhub/venv/bin/python manage.py e2e_seed_fixture_entity --profile bank_bas
cat /opt/statementhub/.e2e/fixture_entity_bank_bas.json

$PSQL -d postgres -qc "DROP DATABASE e2e_scratch_bank_bas WITH (FORCE);"
```

Expected: JSON containing `entity`, `current_fy` and `bank_account_code`. The manifest is
written under `STATEMENTHUB_RUNTIME_ROOT/.e2e`, not the checkout under test.

Real use is unaffected either way: Tier 2 always seeds against a Postgres branch.

**No further wiring is needed to reach Task 4.** `start_server.sh:57` runs
`e2e_seed_fixture_entity` with no `--profile` on every instance boot, and the command
defaults to `options["profile"] or sorted(PROFILES)` — every registered key. Registering
`bank_bas` in `PROFILES` is therefore sufficient, and it is the cheapest of the five to
seed against the ~4s database branch each boot already pays.

- [ ] **Step 4: Commit**

```bash
git add core/e2e_fixture_data.py core/management/commands/e2e_seed_fixture_entity.py
git commit -m "test: a GST-registered fixture entity for the bank-to-BAS flow"
```

---

### Task 4: The flow module boots and logs in

The smallest possible Playwright deliverable: prove the instance, the branch and the login work on port 8206 before adding any assertions worth arguing about.

**Files:**
- Create: `e2e/tier2/bank_to_bas_flow.ts`
- Create: `e2e/tier2/bank_to_bas_company.spec.ts`

**Interfaces:**
- Consumes: `startInstance(slug, port)` from `../fixtures/instance`; `loadUsers()`, `loginAs(page, spec, password)` from `../fixtures/login`; `E2E_STATE_DIR` from `../fixtures/paths`.
- Produces: `describeBankToBas(opts: BankToBasOptions): void`, where `BankToBasOptions` is `{ profile: string; port: number; manifest: string; instanceSlug: string; checkpointPrefix: string; }`.

- [ ] **Step 1: Write the flow module and its thin caller**

```typescript
// e2e/tier2/bank_to_bas_flow.ts
/**
 * The bank-statement-to-BAS flow, parameterised by entity type.
 *
 * Every BAS label asserted here is hand-computed from the fixture's transaction
 * table, not baselined -- these figures go to the ATO, and a baseline blesses
 * whatever the code produced the first time it ran. See
 * docs/superpowers/specs/2026-08-14-bank-to-bas-tier2-design.md.
 */
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';
import { E2E_STATE_DIR } from '../fixtures/paths';

export interface BankToBasOptions {
  profile: string;
  port: number;
  manifest: string;
  instanceSlug: string;
  checkpointPrefix: string;
}

export function describeBankToBas(opts: BankToBasOptions): void {
  const IDS = JSON.parse(fs.readFileSync(`${E2E_STATE_DIR}/${opts.manifest}`, 'utf-8'));
  const FY = IDS.current_fy;

  let instance: Instance;

  test.describe.configure({ mode: 'serial' });

  test.beforeAll(async () => {
    // The hook budget must exceed instance.ts's 180s boot budget, or a hook
    // timeout fires first and reports a generic timeout instead of the real
    // boot failure.
    test.setTimeout(240_000);
    instance = await startInstance(opts.instanceSlug, opts.port);
  });

  test.afterAll(async () => {
    await instance?.stop();
  });

  async function seniorPage(browser: any) {
    const users = loadUsers();
    const context = await browser.newContext({ baseURL: instance.baseURL });
    const page = await context.newPage();
    await loginAs(page, users.roles.senior, users.password);
    return page;
  }

  test('the fixture entity has a GST dashboard', async ({ browser }) => {
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);
    await expect(page.locator('body')).toContainText('Activity Statement');
  });
}
```

```typescript
// e2e/tier2/bank_to_bas_company.spec.ts
/**
 * Bank statement to BAS, for a company.
 *
 * One entity type for now. entity_type reaches the GST path only to select a
 * chart of accounts (core/bas_utils.py:341-345); the arithmetic is driven by tax
 * codes on transactions, so a second type would mostly re-prove the same sums.
 * Adding one is a new profile plus a file like this one.
 */
import { describeBankToBas } from './bank_to_bas_flow';

describeBankToBas({
  profile: 'bank_bas',
  port: 8206,
  manifest: 'fixture_entity_bank_bas.json',
  instanceSlug: 'bank_bas_company',
  checkpointPrefix: 'bank_bas:',
});
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/bank_to_bas_company.spec.ts --workers=1
```

Expected: PASS. A failure here is infrastructure — port collision, missing manifest, seed not run — not logic.

- [ ] **Step 3: Commit**

```bash
git add e2e/tier2/bank_to_bas_flow.ts e2e/tier2/bank_to_bas_company.spec.ts
git commit -m "test: boot a Tier 2 instance for the bank-to-BAS flow"
```

---

### Task 5: Upload and import the statement

**Files:**
- Modify: `e2e/tier2/bank_to_bas_flow.ts`

**Interfaces:**
- Consumes: `describeBankToBas` from Task 4; the PDF at `e2e/fixtures/statements/cba_sample.pdf` from Task 1.
- Produces: a module-scoped `reviewJobUrl` captured after import, used by Task 6.

- [ ] **Step 1: Write the failing test**

The real upload path is three hops, not one. `/upload-statement/` is marked **legacy** in `review/urls.py:24` and writes straight to the database — do not use it. The UI on the financial-year detail page posts each file to `/parse-statement/` (parse only, no write), redirects to `/upload-preview/`, and that page posts to `/confirm-import/`.

Declare the captured URL at flow scope, beside `let instance: Instance;`:

```typescript
  let instance: Instance;
  // Captured by the upload test and read by every test after it. Serial mode
  // guarantees the ordering; a parallel file would need this per-test.
  let reviewJobUrl = '';
```

Then add, after the dashboard test:

```typescript
  const STATEMENT_PDF = `${REPO_DIR}/e2e/fixtures/statements/cba_sample.pdf`;

  test('the statement parses and imports all six transactions', async ({ browser }) => {
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/?tab=review`);

    await page.locator('#periodStartInput').fill('2025-10-01');
    await page.locator('#periodEndInput').fill('2025-10-31');
    // #fyFileInput carries .d-none; setInputFiles works on hidden inputs.
    await page.setInputFiles('#fyFileInput', STATEMENT_PDF);
    await page.locator('#fyUploadSubmitBtn').click();

    // The page JS posts to /parse-statement/ per file, then redirects.
    await page.waitForURL(/\/upload-preview\//, { timeout: 60_000 });

    // Six rows offered for import. A zero here is the exact defect b073cca
    // fixed: bank detected, header matched, no transactions extracted.
    await expect(page.locator('#importCount')).toHaveText('6');

    await page.locator('#confirmImportBtn').click();
    await page.waitForURL(/(\?tab=review|\/review\/)/, { timeout: 60_000 });
    reviewJobUrl = page.url();
  });
```

Add `REPO_DIR` to the existing `../fixtures/paths` import.

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/bank_to_bas_company.spec.ts --workers=1
```

Expected: PASS. If `#confirmImportBtn` is still disabled, the preview requires each statement to be marked verified first — read `templates/review/upload_preview.html` around its `confirm-import` fetch at line 590 for the control that enables it, and click that first.

- [ ] **Step 3: Assert the signs survived the round trip**

```typescript
  test('the debit and credit signs survive into the review queue', async ({ browser }) => {
    // The sign is encoded only in the statement's column geometry -- nothing in
    // the text says debit or credit -- so this is what proves the columns were
    // read rather than guessed.
    const page = await seniorPage(browser);
    await page.goto(reviewJobUrl);
    const rows = page.locator('[data-txn-id]');
    await expect(rows.filter({ hasText: 'EFTPOS SALES INV 1001' })).toHaveAttribute(
      'data-amount', '1100.00',
    );
    await expect(rows.filter({ hasText: 'OFFICE SUPPLIES PTY LTD' })).toHaveAttribute(
      'data-amount', '-550.00',
    );
  });
```

- [ ] **Step 4: Run, then commit**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/bank_to_bas_company.spec.ts --workers=1
cd /opt/statementhub && git add e2e/tier2/bank_to_bas_flow.ts
git commit -m "test: the statement uploads and its signs survive into review"
```

---

### Task 6: Allocate and post, and prove the double-post guard

**Files:**
- Modify: `e2e/tier2/bank_to_bas_flow.ts`

**Interfaces:**
- Consumes: `reviewJobUrl` from Task 5.
- Produces: nothing; Task 7 reads the posted trial balance through the GST dashboard.

- [ ] **Step 1: Write the allocation test**

The test picks each code itself. `#btn-accept-all` is deliberately **not** used — that accepts AI suggestions, which are metered and vary per run.

```typescript
  const ALLOCATIONS: Array<[string, string, string]> = [
    ['EFTPOS SALES INV 1001', '0510', 'GST'],
    ['OFFICE SUPPLIES PTY LTD', '1520', 'GST'],
    ['BANK FEES AND CHARGES', '1530', 'GST'],
    ['CONSULTING FEE INV 1002', '0510', 'GST'],
    ['FRESH FOOD SUPPLIES', '1540', 'FRE'],
    ['EXPORT SALE INV 1003', '0510', 'FRE'],
  ];

  test('every transaction allocates and posts to the trial balance', async ({ browser }) => {
    const page = await seniorPage(browser);
    await page.goto(reviewJobUrl);

    for (const [description, code, taxType] of ALLOCATIONS) {
      const row = page.locator('[data-txn-id]').filter({ hasText: description });
      // The account control is a filter-as-you-type picker, not a <select>:
      // .account-picker-input narrows a .account-dropdown of .account-option
      // divs, and clicking one calls selectAccount(txnId, code, name).
      await row.locator('.account-picker-input').fill(code);
      await page.locator(`.account-option[data-code="${code}"]`).first().click();
      await row.locator('.tax-select').selectOption(taxType);
      await expect(row).toHaveAttribute('data-confirmed', 'true');
    }

    await expect(page.locator('#confirmed-count')).toHaveText('6');
    // #btn-submit is disabled until every row is confirmed, so this click both
    // submits and proves the count above is what the page believes too.
    await page.locator('#btn-submit').click();
  });
```

Note `#btn-accept-all` is deliberately never clicked — it accepts AI suggestions, which are metered and vary per run.

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/bank_to_bas_company.spec.ts --workers=1
```

Expected: PASS. If `data-confirmed` does not flip, the attribute is rendered server-side at `templates/review/review_detail.html:549` and may only update after reload — in that case assert `.account-picker-input.confirmed` (the class the picker adds client-side, `:118`) instead, and reload before asserting `#confirmed-count`.

- [ ] **Step 3: Write the double-post test**

```typescript
  test('re-confirming a transaction does not post it twice', async ({ browser }) => {
    /**
     * review/views.py:651 takes select_for_update on the transaction row so
     * concurrent confirms cannot both pass the posted_to_tb guard (A18). This
     * asserts the guard's observable effect rather than its mechanism: after
     * re-confirming an already-posted transaction, the trial balance must be
     * unchanged.
     */
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/`);
    const before = await page.locator('body').innerText();

    await page.goto(reviewJobUrl);
    const row = page.locator('[data-txn-id]').filter({ hasText: 'BANK FEES AND CHARGES' });
    await row.locator('.account-picker-input').fill('1530');
    await page.locator('.account-option[data-code="1530"]').first().click();
    await page.locator('#btn-submit').click();

    await page.goto(`${instance.baseURL}/years/${FY}/`);
    await expect(page.locator('body')).toHaveText(before);
  });
```

- [ ] **Step 4: Assert the trial balance still balances**

The spec asks that posted lines tie to the statement's net movement. Asserted here as the absolute form — total debits equal total credits — because that holds whatever per-account posting convention `_post_confirmed_txn_to_tb` uses (`review/views.py:94`), and needs no guess about whether GST is stripped on the way in. The per-account amounts are pinned instead by G1 and G11 in Task 7, so nothing is left uncovered.

```typescript
  test('the trial balance still balances after posting', async ({ browser }) => {
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/`);
    const text = await page.locator('body').innerText();
    const totals = text.match(/Total\s+Debits?[^0-9]*([\d,]+\.\d{2})[\s\S]*?Total\s+Credits?[^0-9]*([\d,]+\.\d{2})/i);
    if (!totals) throw new Error('could not find trial balance totals on the year page');
    const num = (s: string) => parseFloat(s.replace(/,/g, ''));
    expect(num(totals[1])).toBeCloseTo(num(totals[2]), 2);
  });
```

If the year page does not render those totals, read the trial balance template for the labels it does render and match those — the assertion is debits equal credits, whatever they are called.

- [ ] **Step 5: Run, then commit**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/bank_to_bas_company.spec.ts --workers=1
cd /opt/statementhub && git add e2e/tier2/bank_to_bas_flow.ts
git commit -m "test: allocation posts once per transaction, never twice"
```

---

### Task 7: The BAS labels, hand-computed

The task the whole spec exists for.

**Files:**
- Modify: `e2e/tier2/bank_to_bas_flow.ts`

**Interfaces:**
- Consumes: the posted trial balance from Task 6.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

```typescript
  test('the BAS labels equal their hand-computed values', async ({ browser }) => {
    /**
     * From the fixture's transaction table, GST at 1/11 of the GST-inclusive
     * amount:
     *
     *   G1  total sales incl GST      1,100 + 2,200 + 800 = 4,100.00
     *   G2  export sales                                     0.00
     *   G3  other GST-free sales                           800.00
     *   G10 capital purchases                                0.00
     *   G11 non-capital purchases     550 + 22 + 300     =  872.00
     *   1A  GST on sales              (1,100 + 2,200)/11 =  300.00
     *   1B  GST on purchases          (550 + 22)/11      =   52.00
     *   net                           300.00 - 52.00     =  248.00
     *
     * If the application disagrees, STOP and report it. Do not edit these
     * numbers to match the output -- that turns this suite into the golden
     * baseline it was designed not to be.
     */
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);

    const body = page.locator('body');
    await expect(body).toContainText('4,100.00');   // G1
    await expect(body).toContainText('800.00');     // G3
    await expect(body).toContainText('872.00');     // G11
    await expect(body).toContainText('300.00');     // 1A
    await expect(body).toContainText('52.00');      // 1B
    await expect(body).toContainText('248.00');     // net
  });

  test('the GST identity holds', async ({ browser }) => {
    // 1A - 1B = net, whatever the individual figures turn out to be. An
    // absolute relationship needs no baseline: if it fails the figures are
    // wrong regardless of what was blessed.
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);
    const text = await page.locator('body').innerText();
    const num = (label: string) => {
      const m = text.match(new RegExp(`${label}[^0-9-]*(-?[\\d,]+\\.\\d{2})`));
      if (!m) throw new Error(`could not find ${label} on the BAS dashboard`);
      return parseFloat(m[1].replace(/,/g, ''));
    };
    expect(num('1A') - num('1B')).toBeCloseTo(num('Net'), 2);
  });
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/bank_to_bas_company.spec.ts --workers=1
```

Expected: PASS if the allocations and GST engine agree with the hand computation. **If a figure differs, stop and report which label and by how much** — that is a finding, not a test to adjust. Confirm the label definitions against `core/bas_utils.py` `calculate_gst_for_period` before concluding either way; G2 versus G3 for the export line is the most likely honest disagreement.

- [ ] **Step 3: Commit**

```bash
git add e2e/tier2/bank_to_bas_flow.ts
git commit -m "test: BAS labels asserted against hand-computed figures"
```

---

### Task 8: The coverage gate, lodgement and unlodge

**Files:**
- Modify: `e2e/tier2/bank_to_bas_flow.ts`

**Interfaces:**
- Consumes: the computed BAS from Task 7.
- Produces: nothing.

- [ ] **Step 1: Write the coverage-gate test**

```typescript
  test('lodgement is blocked while bank coverage is incomplete', async ({ browser }) => {
    /**
     * core/views_bas.py:228 refuses to lodge unless get_bank_coverage reports
     * complete, or an override reason is supplied. The fixture holds one month
     * of a three-month quarter, so the gate must fire.
     */
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);
    await page.locator('form[action*="/gst/lodge/"] button[type="submit"]').first().click();
    await expect(page.locator('body')).toContainText('incomplete bank coverage');
  });

  test('an override reason lets it lodge and freezes the figures', async ({ browser }) => {
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);
    await page.locator('input[name="override_reason"]').first()
      .fill('e2e fixture: single month of quarter by design');
    await page.locator('form[action*="/gst/lodge/"] button[type="submit"]').first().click();
    await expect(page.locator('body')).toContainText('lodged');
    // The snapshot captured at lodge time carries the same net figure.
    await expect(page.locator('body')).toContainText('248.00');
  });
```

- [ ] **Step 2: Run it**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/bank_to_bas_company.spec.ts --workers=1
```

Expected: FAIL on the lodge form selector; read the BAS dashboard template for the real form and override-reason field, then correct.

- [ ] **Step 3: Write the unlodge permission test — LAST in the file**

Serial mode skips everything after a failure, so this goes last.

```typescript
  test('only a senior can unlodge', async ({ browser }) => {
    const users = loadUsers();
    const context = await browser.newContext({ baseURL: instance.baseURL });
    const page = await context.newPage();
    await loginAs(page, users.roles.accountant, users.password);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);
    await page.locator('form[action*="/gst/unlodge/"] button[type="submit"]').first().click();
    await expect(page.locator('body')).toContainText(
      'Only senior accountants and administrators can unlodge',
    );
  });
```

- [ ] **Step 4: Run the whole file, then commit**

```bash
cd /opt/statementhub/e2e && npm run test:tier2 -- tier2/bank_to_bas_company.spec.ts --workers=1
cd /opt/statementhub && git add e2e/tier2/bank_to_bas_flow.ts
git commit -m "test: the coverage gate, lodgement snapshot and unlodge permission"
```

---

### Task 9: Register the spec and record what it does not cover

**Files:**
- Modify: `e2e/README.md`
- Modify: `e2e/tier2/known_failures.json` (confirm it stays empty)

- [ ] **Step 1: Update the coverage section**

Add the new spec to the Tier 2 description: seven spec files now, and the bank-to-BAS flow covering CBA only, company only, with the AI suggestion path excluded. State the port (8206) in the same table the others use, and record explicitly that the other eight bank parsers are uncovered and why — a reader must not infer that "bank statements are tested" means all banks.

Two further limits must be stated, both discovered during execution and neither fixable with a committed fixture:

1. **The fixture assumes the kerning collapse rather than reproducing it.** Dates are stored as glued literals (`02Oct`) and drawn with a single call. That is the input shape the geometry parser expects, so it is correct — but it means a regression in how real-world kerning is handled would not be caught here. Only a real PDF could catch that, and a real client statement cannot be committed.
2. **The fixture carries no per-transaction running balance,** because a balance on every row out-populates the real debit and credit clusters in `_money_columns` and gets mistaken for a money column. Real statements have that column. The parser reads no balance from transaction rows, so nothing under test observes the difference — but the fixture is that much less like the real thing, and a reader comparing the two should know why.

- [ ] **Step 2: Confirm nothing is red**

```bash
cd /opt/statementhub/e2e && cat tier2/known_failures.json
npm run test:tier2 -- --workers=2
npm run test:tier1
```

Expected: `known_failures.json` is `{}` or `[]`; all Tier 2 specs pass; Tier 1's 215 tests pass.

- [ ] **Step 3: Commit**

```bash
git add e2e/README.md
git commit -m "docs: record the bank-to-BAS spec and what it does not cover"
```

---

## Verification

Complete when all of these hold:

1. `review.tests_statement_fixture` passes — the fixture parses, routes to the geometry engine, and regenerates byte-for-byte.
2. `bank_to_bas_company.spec.ts` passes at `--workers=1`.
3. The other six Tier 2 spec files still pass, and `known_failures.json` is still empty.
4. Tier 1's 215 tests still pass.
5. `git status` shows no real client statement anywhere in the tree.
6. No production code was modified. This plan adds tests and fixtures only. **If a scenario fails on a genuine defect, stop and report it rather than changing the expectation to match the output.**
