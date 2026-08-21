"""Every real statement we hold, run through the real pipeline.

These are the documents the parsers are actually judged on. The table below is
the measured state as at 2026-08-20, gaps included: a fixture that is known to
fail is recorded as failing, with the reason, rather than omitted. Each rollout
phase turns one of those entries green, and the diff shows exactly what changed.

The statements are client documents and are gitignored, so the whole module
skips with a message when they are absent -- a machine without the fixtures
reports "skipped", never "passed".

Two kinds of assertion run here:

* per statement -- detected bank, transaction count, both anchors,
  reconciliation to the cent, and whether the import gate accepts it.
* across statements -- consecutive statements must chain, one's closing
  balance being the next one's opening. That is much stronger evidence than
  reconciliation alone: a parse can be internally consistent and still wrong,
  but two independent documents cannot agree by accident.
"""
import hashlib
import os
import unittest
from functools import lru_cache

from django.conf import settings
from django.test import SimpleTestCase

from .pdf_parsers import detect_bank, extract_transactions_from_pdf_direct
from .statement_geometry import (
    StatementNotImportable,
    assert_importable,
    parse_cba_geometry,
)

# Set STATEMENT_FIXTURE_DIR to run against fixtures kept outside the checkout.
FIXTURE_DIR = os.environ.get(
    "STATEMENT_FIXTURE_DIR",
    os.path.join(str(settings.BASE_DIR), "test_fixtures"),
)

# Status meanings:
#   healthy  -- parses through its intended parser and passes the gate
#   legacy   -- parses, but via the legacy text parser because the geometry
#               engine rejected it (Phase 1 turns these into 'healthy')
#   gap      -- does not parse or does not pass the gate; `gap` names why
#   vision   -- no text layer at all, so it is read by Claude Vision OCR
#               rather than by any parser; `gap` names why
EXEMPLARS = [
    dict(name="cba_stmt9.pdf", bank="cba", rows=174,
         opening=27440.30, closing=8826.22, status="healthy", geometry=True),
    dict(name="cba_stmt10.pdf", bank="cba", rows=188,
         opening=8826.22, closing=26420.53, status="healthy", geometry=True),
    dict(name="July.pdf", bank="cba", rows=240,
         opening=26420.53, closing=14001.89, status="healthy", geometry=True),
    # Older CBA layout: unglued words, $-prefixed figures, date as two
    # separate tokens. Both rode the legacy text parser until Phase 1; the
    # geometry engine now produces the same row counts the legacy parser did,
    # which is independent corroboration that 108 and 120 are right.
    dict(name="CBA1.pdf", bank="cba", rows=108,
         opening=16759.60, closing=6218.20, status="healthy", geometry=True),
    dict(name="CBA2.pdf", bank="cba", rows=120,
         opening=6218.20, closing=10950.76, status="healthy", geometry=True),
    # NetBank's "Transaction History" export -- not a statement. It carries no
    # bank name anywhere, which is why it detected as "unknown" and could not
    # be read at all, and its rows run NEWEST FIRST.
    # Its footer says "No. of transactions 278" while the file holds 35 rows.
    # Do not chase that number: the same footer prints total debits 8,177.61
    # and total credits 15,361.00, whose difference is exactly this file's
    # movement and exactly its closing less its opening, so the 35 rows are
    # complete and the 278 counts something else.
    dict(name="CBA_1.pdf", bank="cba_txn_history", rows=35,
         opening=805.27, closing=7988.66, status="healthy", geometry=False),
    dict(name="WBC1.pdf", bank="westpac", rows=2,
         opening=14649.20, closing=21338.83, status="healthy", geometry=False),
    dict(name="WBC2.pdf", bank="westpac", rows=3,
         opening=11259.57, closing=14649.20, status="healthy", geometry=False),
    # Two consecutive busy statements. The text parser read 16 rows from
    # WBC_1's 218 dated lines and 14 from WBC_2's 246, and got the sign of
    # WBC_2's net movement wrong; the small pair above was far too quiet to
    # show any of that. Each statement's own summary gives the true net
    # movement independently: +2,584.82 and -3,842.27.
    dict(name="WBC_1.pdf", bank="westpac", rows=216,
         opening=5237.73, closing=7822.55, status="healthy", geometry=False),
    dict(name="WBC_2.pdf", bank="westpac", rows=244,
         opening=9080.00, closing=5237.73, status="healthy", geometry=False),
    dict(name="NAB1.pdf", bank="nab", rows=413,
         opening=19024.84, closing=13349.18, status="healthy", geometry=False),
    dict(name="NAB2.pdf", bank="nab", rows=370,
         opening=12624.69, closing=19024.84, status="healthy", geometry=False),
    # Two complete ANZ statements, supplied 2026-08-20. They are not
    # consecutive -- ANZ1 closes at 545.75 in March and ANZ2 opens at 3,045.71
    # in April -- so there is no boundary between them to check. Both
    # reconcile against their own anchors, which is what unparked ANZ: the
    # earlier exemplar was missing pages and could never reconcile.
    dict(name="ANZ1.pdf", bank="anz", rows=6,
         opening=11940.79, closing=545.75, status="healthy", geometry=False),
    dict(name="ANZ2.pdf", bank="anz", rows=9,
         opening=3045.71, closing=5454.62, status="healthy", geometry=False),
    # Fixed in Phase 2. ING prints money out as a negative figure, which no
    # amount pattern allowed: the amount/balance match failed on every debit
    # row, the fallback then read the BALANCE as the amount, and the anchors
    # failed the same way on "$-6,050.74" -- so there was nothing to reconcile
    # against and the two defects hid each other.
    # Three busier ANZ statements. ANZ1/ANZ2 above have 6 and 9 transactions,
    # which is the same blind spot that hid Westpac's 93% loss -- too few rows
    # for a description long enough to wrap. ANZ11 duly fails: its shortfall of
    # 5,706.00 is exactly page 3's printed credit total, so it is dropping a
    # credit. ANZ prints "TOTALS AT END OF PERIOD" with independent debit and
    # credit totals (14,400.93 and 19,086.00), which gives any fix ground truth
    # from outside the parser.
    dict(name="ANZ11.pdf", bank="anz", rows=25,
         opening=1575.49, closing=6260.56, status="healthy", geometry=False),
    dict(name="ANZ12.pdf", bank="anz", rows=39,
         opening=6260.56, closing=4015.19, status="healthy", geometry=False),
    dict(name="ANZ13.pdf", bank="anz", rows=33,
         opening=4015.19, closing=4007.98, status="healthy", geometry=False),
    # Bendigo, previously parked with no exemplars at all. All four reconcile
    # as of 2026-08-21; Ben4 took two fixes of its own. The text arrives glued
    # ("Openingbalanceon1Mar2025 $2,772.95") and the table is
    # Date / Transaction / Withdrawals / Deposits / Balance -- the same shape
    # Westpac had before Phase 3a.
    dict(name="Ben1.pdf", bank="bendigo", rows=10,
         opening=3878.82, closing=6731.16, status="healthy", geometry=False),
    dict(name="Ben3.pdf", bank="bendigo", rows=12,
         opening=6731.16, closing=2772.95, status="healthy", geometry=False),
    dict(name="Ben2.pdf", bank="bendigo", rows=10,
         opening=2772.95, closing=5107.28, status="healthy", geometry=False),
    # 31 pages holding THIRTEEN consecutive monthly statements for one
    # account, 198 transactions from Jun 2023 to Jun 2024. It was refused by
    # 20.00 until two faults were found in it, both of them things no
    # single-period statement contains: a signed figure in the withdrawals
    # column, and a fee summary printed inside the following month's table.
    dict(name="Ben4.pdf", bank="bendigo", rows=198,
         opening=98572.63, closing=114902.08, status="healthy", geometry=False),
    dict(name="ING.pdf", bank="ing", rows=56,
         opening=2156.82, closing=3514.82, status="healthy", geometry=False),
    # Two consecutive Orange Everyday statements from 2016, against the 2025
    # Everyday Family above: two account types and a nine-year format gap.
    # ING19 runs net negative and ING20 net positive, so the sign handling is
    # exercised in both directions.
    dict(name="ING19.pdf", bank="ing", rows=126,
         opening=6010.45, closing=5388.24, status="healthy", geometry=False),
    dict(name="ING20.pdf", bank="ing", rows=143,
         opening=5388.24, closing=23896.75, status="healthy", geometry=False),
    # Bank of Melbourne. Its text parser read 0 transactions from both of
    # these: the text arrives glued ("20MAYOPENINGBALANCE1,365.48"), the
    # dates carry no year at all, and the column header reads "Balance$".
    # BOM3 is three consecutive six-month periods in one 12-page file, and
    # each period prints its own summary -- opening, total credits, total
    # debits, closing -- which gives ground truth from outside the parser.
    dict(name="BOM3.pdf", bank="bankofmelb", rows=59,
         opening=1365.48, closing=7585.83, status="healthy", geometry=False),
    # A dormant account: three periods, 0.01 throughout, not one transaction.
    # The correct read of it is zero rows, which is also what a failed parse
    # looks like from outside -- so it is pinned deliberately.
    dict(name="BOM4.pdf", bank="bankofmelb", rows=0,
         opening=0.01, closing=0.01, status="healthy", geometry=False),
    # These two are SCANNED IMAGES: one image per page and no text layer at
    # all, so no text parser can touch them. They are read by Claude Vision
    # instead, and the figures below were measured through it on 2026-08-21
    # and corroborated against every period summary printed in the scans,
    # read page by page in separate calls: BOM1's three periods print credits
    # of 445,070.25 and debits of 34,659.40, BOM2's 44,255.41 and 42,384.80,
    # which are exactly what the extractions produce. Status "vision" so the
    # direct-parse tests skip them; the live test below re-measures on demand.
    dict(name="BOM1.pdf", bank="bankofmelb", rows=55,
         opening=100513.01, closing=510923.86, status="vision", geometry=False,
         gap="scanned image: the pages carry no text layer (0 words), so it "
             "cannot be read by any text parser -- read by Vision OCR"),
    dict(name="BOM2.pdf", bank="bankofmelb", rows=98,
         opening=394.38, closing=2264.99, status="vision", geometry=False,
         gap="scanned image: the pages carry no text layer (0 words), so it "
             "cannot be read by any text parser -- read by Vision OCR"),
    # Macquarie, never before pinned. It parses correctly and always did --
    # what was missing was evidence. M1 and M2 are two copies of the same
    # statement no. 25, from different downloads; M3 is the period after it.
    # Its rows carry no balance, so the gate's row-to-row chain check cannot
    # see them; the test below chains them against the figures printed on the
    # page instead, which is what proves no row is missing or mis-signed.
    dict(name="M1.pdf", bank="macquarie", rows=20,
         opening=12102.76, closing=11666.92, status="healthy", geometry=False),
    dict(name="M2.pdf", bank="macquarie", rows=20,
         opening=12102.76, closing=11666.92, status="healthy", geometry=False),
    dict(name="M3.pdf", bank="macquarie", rows=19,
         opening=11666.92, closing=4132.77, status="healthy", geometry=False),
]

# One statement's closing balance is the next one's opening balance.
CHAINS = [
    ("cba_stmt9.pdf", "cba_stmt10.pdf"),
    ("cba_stmt10.pdf", "July.pdf"),
    ("CBA1.pdf", "CBA2.pdf"),
    ("WBC2.pdf", "WBC1.pdf"),
    ("NAB2.pdf", "NAB1.pdf"),
    ("ING19.pdf", "ING20.pdf"),
    ("WBC_2.pdf", "WBC_1.pdf"),
    ("ANZ11.pdf", "ANZ12.pdf"),
    ("ANZ12.pdf", "ANZ13.pdf"),
    # Bendigo's filenames are not in date order: Ben1 precedes Ben3, which
    # precedes Ben2.
    ("Ben1.pdf", "Ben3.pdf"),
    ("Ben3.pdf", "Ben2.pdf"),
    # M1 and M2 are the same statement, so M2 -> M3 would assert nothing new.
    ("M1.pdf", "M3.pdf"),
]

BY_NAME = {e["name"]: e for e in EXEMPLARS}


def require_any(test, names):
    """Skip rather than pass when none of ``names`` is on disk.

    A loop over absent fixtures asserts nothing, and a test that asserts
    nothing must not report success -- that is how missing coverage comes to
    look like passing coverage.
    """
    present = [n for n in names if available(n)]
    if not present:
        test.skipTest(
            f"none of these fixtures are present in {FIXTURE_DIR}: "
            + ", ".join(names)
        )
    return present


def fixture_path(name):
    return os.path.join(FIXTURE_DIR, name)


def available(name):
    return os.path.exists(fixture_path(name))


@lru_cache(maxsize=None)
def read(name):
    with open(fixture_path(name), "rb") as fh:
        return fh.read()


@lru_cache(maxsize=None)
def parse(name):
    """Run one fixture through the real direct-parse pipeline.

    Cached: the assertions below each look at a different property of the same
    parse, and a 14-page statement costs seconds to re-read. Results are
    immutable as far as these tests are concerned.
    """
    return extract_transactions_from_pdf_direct(read(name), name)


class ExemplarInventoryTests(SimpleTestCase):
    """The table itself has to stay honest."""

    def test_every_gap_says_why(self):
        for entry in EXEMPLARS:
            if entry["status"] in ("gap", "legacy", "vision"):
                self.assertTrue(
                    entry.get("gap"),
                    f"{entry['name']} is marked {entry['status']} with no reason",
                )

    def test_no_healthy_entry_carries_a_gap(self):
        for entry in EXEMPLARS:
            if entry["status"] == "healthy":
                self.assertIsNone(
                    entry.get("gap"),
                    f"{entry['name']} is healthy but still records a gap",
                )

    def test_missing_fixtures_are_reported_not_hidden(self):
        missing = [e["name"] for e in EXEMPLARS if not available(e["name"])]
        if missing:
            raise unittest.SkipTest(
                "statement fixtures absent from "
                f"{FIXTURE_DIR}: {', '.join(missing)}. "
                "These are client documents and are gitignored; set "
                "STATEMENT_FIXTURE_DIR to point at them."
            )


class HealthyExemplarTests(SimpleTestCase):
    """Statements that parse correctly today must keep doing so."""

    def _healthy(self):
        entries = [e for e in EXEMPLARS if e["status"] == "healthy"]
        require_any(self, [e["name"] for e in entries])
        return [e for e in entries if available(e["name"])]

    def test_each_is_routed_to_the_expected_bank(self):
        for entry in self._healthy():
            if not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                self.assertEqual(detect_bank(read(entry["name"])), entry["bank"])

    def test_each_yields_its_recorded_transaction_count(self):
        for entry in self._healthy():
            if not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                result = parse(entry["name"])
                self.assertEqual(len(result["transactions"]), entry["rows"])

    def test_each_yields_its_recorded_anchors(self):
        for entry in self._healthy():
            if not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                result = parse(entry["name"])
                self.assertAlmostEqual(
                    float(result["opening_balance"]), entry["opening"], places=2)
                self.assertAlmostEqual(
                    float(result["closing_balance"]), entry["closing"], places=2)

    def test_each_reconciles_to_the_cent(self):
        for entry in self._healthy():
            if not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                result = parse(entry["name"])
                movements = sum(t["amount"] for t in result["transactions"])
                self.assertAlmostEqual(
                    float(result["opening_balance"]) + movements,
                    float(result["closing_balance"]),
                    places=2,
                )

    def test_each_is_accepted_by_the_import_gate(self):
        for entry in self._healthy():
            if not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                result = parse(entry["name"])
                assert_importable(
                    result["transactions"],
                    result["opening_balance"],
                    result["closing_balance"],
                )

    def test_each_carries_the_format_it_was_detected_as(self):
        """The preview badge is the only place whoever accepts an import can
        see which parser claimed the file, and it read "Unknown Bank" for
        every statement of every bank: no parser ever set it. Misdetection is
        a real failure mode -- Bank of Melbourne is a Westpac subsidiary and
        detect_bank orders those two checks deliberately -- so a wrong badge
        is worth more than a missing one."""
        from .pdf_parsers import bank_label
        for entry in self._healthy():
            if not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                result = parse(entry["name"])
                self.assertEqual(result.get("bank"), entry["bank"])
                self.assertEqual(result.get("bank_label"),
                                 bank_label(entry["bank"]))
                self.assertNotIn(result["bank_label"],
                                 ("", None, "Unrecognised format"))

    def test_no_description_carries_page_furniture(self):
        for entry in self._healthy():
            if not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                for txn in parse(entry["name"])["transactions"]:
                    # CARRIEDFORWARD and the bank's own footer are page
                    # furniture: Bank of Melbourne prints a carried-forward
                    # subtotal and then its ABN at every page break, either
                    # side of the boundary, and both landed in the
                    # description of the transaction next to them.
                    for junk in ("YourStatement", "Your Statement",
                                 "TransactionDebitCredit", "CARRIEDFORWARD",
                                 "ADivisionofWestpac"):
                        self.assertNotIn(junk, txn["description"])


class GeometryEngineCoverageTests(SimpleTestCase):
    """Which parser handled a statement is part of the result.

    CBA1 and CBA2 reconcile, which is why nobody noticed they had fallen back
    to the legacy text parser. Asserting the engine -- not just the answer --
    is what makes that visible.
    """

    def test_statements_marked_geometry_parse_through_the_engine(self):
        wanted = [e["name"] for e in EXEMPLARS if e.get("geometry")]
        require_any(self, wanted)
        for entry in EXEMPLARS:
            if not entry.get("geometry") or not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                result = parse_cba_geometry(read(entry["name"]))
                self.assertEqual(len(result["transactions"]), entry["rows"])

    def test_no_cba_statement_carries_a_dateless_transaction(self):
        """A transaction with no date is dropped by confirm_import, which
        breaks reconciliation and gets the whole statement refused. The older
        layout printed its date as two tokens, so every row on those
        statements was dateless until Phase 1 -- 108 of 108 on CBA1."""
        names = [e["name"] for e in EXEMPLARS
                 if e["bank"] == "cba" and e.get("geometry")]
        require_any(self, names)
        for name in names:
            if not available(name):
                continue
            with self.subTest(name):
                dateless = [t for t in parse_cba_geometry(read(name))["transactions"]
                            if not t.get("date")]
                self.assertEqual(dateless, [])


class KnownGapTests(SimpleTestCase):
    """The gaps, asserted as gaps. Each becomes a passing case in its phase."""

    def test_ing_amounts_are_the_movement_not_the_running_balance(self):
        """The defect Phase 2 fixed: every ING amount was the balance column.
        Summed, the 56 rows came to 137,814.07 against a true movement of
        1,358.00 -- and with the anchors reading 0/0 there was nothing to
        reconcile against, so neither fault was visible."""
        if not available("ING.pdf"):
            self.skipTest("ING.pdf absent")
        result = parse("ING.pdf")
        movements = sum(t["amount"] for t in result["transactions"])
        self.assertAlmostEqual(movements, 1358.00, places=2)

    def test_ing_carries_a_balance_on_every_row(self):
        """ING prints one on every line, so the chain check can verify the
        whole statement rather than only its total."""
        names = ["ING.pdf", "ING19.pdf", "ING20.pdf"]
        require_any(self, names)
        for name in names:
            if not available(name):
                continue
            with self.subTest(name):
                self.assertTrue(all(t.get("balance") is not None
                                    for t in parse(name)["transactions"]))

    def test_westpac_recovers_the_wrapped_row_it_used_to_lose(self):
        """WBC2's 3,300.00 Osko payment has a description that wraps to a
        second line, which put the figure on a different line from the date.
        The text parser needed both on one line and dropped the transaction."""
        if not available("WBC2.pdf"):
            self.skipTest("WBC2.pdf absent")
        result = parse("WBC2.pdf")
        self.assertEqual(len(result["transactions"]), 3)
        self.assertTrue(any(abs(t["amount"] + 3300.00) < 0.011
                            for t in result["transactions"]))

    def test_anz_recovers_the_credit_it_used_to_drop(self):
        """ANZ marks an empty column with the literal word "blank" and uses its
        position to tell withdrawals from deposits. On one row the placeholder
        was simply absent, so the text parser could not classify the figure and
        dropped it -- a 5,706.00 deposit. Reading the column by coordinate makes
        the placeholder irrelevant. The recovered total matches the statement's
        own printed TOTALS AT END OF PERIOD exactly."""
        if not available("ANZ11.pdf"):
            self.skipTest("ANZ11.pdf absent")
        result = parse("ANZ11.pdf")
        movements = sum(t["amount"] for t in result["transactions"])
        # printed: debits 14,400.93, credits 19,086.00
        self.assertAlmostEqual(movements, 19086.00 - 14400.93, places=2)
        self.assertTrue(any(abs(t["amount"] - 5706.00) < 0.011
                            for t in result["transactions"]))

    def test_the_bendigo_multi_period_bundle_reads_a_reversal_as_money_in(self):
        """Thirteen monthly statements in one 31-page file, and the whole
        bundle turned on one row.

        On 24 Oct 2023 the withdrawals column holds "-10.00" -- a reversed
        OSKO payment -- and the balance beside it RISES, 323,190.14 to
        323,200.14. Forcing every withdrawal negative booked that as a 10.00
        debit, so the bundle came out 20.00 light over ~1,200 rows and the
        gate refused all 31 pages. It is the only signed figure in any
        statement we hold, which is why no other exemplar ever showed it.
        """
        if not available("Ben4.pdf"):
            self.skipTest("Ben4.pdf absent")
        result = parse("Ben4.pdf")
        reversal = [t for t in result["transactions"]
                    if t["date"] == "2023-10-24"
                    and abs(t["amount"] - 10.00) < 0.011]
        self.assertEqual(len(reversal), 1,
                         "the reversed OSKO payment should be money in")
        self.assertAlmostEqual(reversal[0]["balance"], 323200.14, places=2)
        # And with it the bundle reconciles end to end, across all thirteen
        # periods: the first period's opening to the last period's closing.
        movements = sum(t["amount"] for t in result["transactions"])
        self.assertAlmostEqual(98572.63 + movements, 114902.08, places=2)

    def test_bendigos_in_table_fee_summary_is_not_collected(self):
        """April's fee summary is printed inside May's transaction table.

        Five figures in the transaction columns -- in-branch fees 1.75, an
        account rebate 1.75, a total for each, and a net of 0.00 -- with the
        balance the same on both sides of the block, so no money moved. They
        net to zero, which is exactly why reconciliation cannot catch them:
        they would have imported as four phantom 1.75 entries and a 0.00,
        four of the five with no date at all. A month whose rebate did not
        cover its fee would not net, and would refuse all 31 pages.
        """
        if not available("Ben4.pdf"):
            self.skipTest("Ben4.pdf absent")
        txns = parse("Ben4.pdf")["transactions"]
        self.assertEqual([t for t in txns if not t["date"]], [],
                         "every imported row must carry a date")
        for junk in ("TotalTransactionFees", "TotalRebates",
                     "MonthlyTransactionSummary", "NetTransactionFees"):
            self.assertEqual(
                [t["description"] for t in txns if junk in t["description"]],
                [], f"{junk} is a summary line, not a transaction")

    def test_bank_of_melbourne_matches_the_totals_it_prints_itself(self):
        """Ground truth from outside the parser.

        BOM3 holds three six-month periods, and each one prints its own
        summary: opening, total credits, total debits, closing. Added up they
        come to 24,983.84 in and 18,763.49 out, a net of 6,220.35, which
        carries 1,365.48 to 7,585.83. Matching on BOTH sides is much stronger
        than reconciling: a debit read as a credit reconciles wrongly by
        double, but it cannot leave both totals right.
        """
        if not available("BOM3.pdf"):
            self.skipTest("BOM3.pdf absent")
        txns = parse("BOM3.pdf")["transactions"]
        credits = sum(t["amount"] for t in txns if t["amount"] > 0)
        debits = sum(t["amount"] for t in txns if t["amount"] < 0)
        # printed: 5,593.16 + 8,395.60 + 10,995.08 in
        self.assertAlmostEqual(credits, 24983.84, places=2)
        # printed: 4,309.18 + 6,917.57 + 7,536.74 out
        self.assertAlmostEqual(debits, -18763.49, places=2)

    def test_bank_of_melbourne_dates_the_period_that_crosses_a_new_year(self):
        """Its rows carry no year -- "20MAY" -- so the year comes from the
        statement period, and BOM3's second period runs 20/11/2022 to
        19/05/2023. Taking the period's first year for every row would date
        January to May twelve months early, and reconciliation would never
        notice: it does not look at dates at all."""
        if not available("BOM3.pdf"):
            self.skipTest("BOM3.pdf absent")
        txns = parse("BOM3.pdf")["transactions"]
        self.assertEqual([t for t in txns if not t["date"]], [])
        dates = [t["date"] for t in txns]
        self.assertEqual(dates, sorted(dates), "a statement runs forwards")
        second_period = [d for d in dates if "2022-11-20" <= d <= "2023-05-19"]
        self.assertTrue(any(d < "2023-01-01" for d in second_period))
        self.assertTrue(any(d >= "2023-01-01" for d in second_period))

    def test_bank_of_melbourne_keeps_a_continuation_with_its_own_row(self):
        """BOM prints a wrapped description BELOW the figures it explains --
        "Transfer to CBA account" sits under the withdrawal it describes,
        where Westpac and ANZ wrap above. Accumulating forward filed every
        continuation against the FOLLOWING transaction: each description one
        row late, and one payment's reference against the next payment."""
        if not available("BOM3.pdf"):
            self.skipTest("BOM3.pdf absent")
        txns = parse("BOM3.pdf")["transactions"]
        june = {t["date"]: t["description"] for t in txns
                if t["date"] in ("2022-06-25", "2022-06-30")}
        self.assertIn("TransfertoCBAaccount", june["2022-06-25"])
        self.assertNotIn("TransfertoCBAaccount", june["2022-06-30"])

    def test_a_dormant_statement_reads_as_zero_rows_not_as_a_failure(self):
        """BOM4 is three periods of an account holding 0.01 and nothing
        happening. Zero transactions is the correct answer, and it is
        indistinguishable from a failed parse at the parser boundary -- the
        anchors are what tell them apart, so they are asserted here."""
        if not available("BOM4.pdf"):
            self.skipTest("BOM4.pdf absent")
        result = parse("BOM4.pdf")
        self.assertEqual(result["transactions"], [])
        self.assertAlmostEqual(float(result["opening_balance"]), 0.01, places=2)
        self.assertAlmostEqual(float(result["closing_balance"]), 0.01, places=2)

    def test_the_scanned_bank_of_melbourne_files_carry_no_text_at_all(self):
        """Why BOM1 and BOM2 are gaps rather than parser bugs: they are
        photographs of a statement. One image per page and not a single word,
        so there is nothing for any parser to read -- the failure is at the
        wrong layer to fix here, and the Vision OCR path is where they belong.
        """
        import io
        import pdfplumber
        names = ["BOM1.pdf", "BOM2.pdf"]
        require_any(self, names)
        for name in names:
            if not available(name):
                continue
            with self.subTest(name):
                with pdfplumber.open(io.BytesIO(read(name))) as pdf:
                    page = pdf.pages[0]
                    self.assertEqual(page.extract_words(), [])
                    self.assertTrue(page.images)

    def test_macquarie_chains_against_every_balance_printed_on_the_page(self):
        """The strongest check available on a parser that emits no balances.

        Macquarie prints a running balance beside every transaction, but its
        figures sit a fraction of a point above their own line, so row
        grouping files each one with the row above and the parser reports
        balance=None. That leaves the import gate's row-to-row chain check
        with nothing to work on, and reconciliation alone cannot tell a
        missing row from a pair of offsetting errors.

        So the balances are read straight off the page here and compared with
        the running total the parse implies. Agreement on every figure, in
        order, means no row is missing, duplicated or the wrong way round.
        """
        import io
        import pdfplumber
        from .statement_geometry import (
            _column_of, _row_money, _rows, _table_columns,
        )
        names = ["M1.pdf", "M2.pdf", "M3.pdf"]
        require_any(self, names)
        for name in names:
            if not available(name):
                continue
            with self.subTest(name):
                with pdfplumber.open(io.BytesIO(read(name))) as pdf:
                    rows = _rows(pdf)
                columns = _table_columns(rows, ("DEBITS", "CREDITS", "BALANCE"))
                self.assertIsNotNone(columns)
                printed = [value for row in rows
                           for word, value in _row_money(row)
                           if _column_of(word, columns) == "balance"]
                result = parse(name)
                running = float(result["opening_balance"])
                implied = []
                for txn in result["transactions"]:
                    running = round(running + txn["amount"], 2)
                    implied.append(running)
                # printed[0] is the headline balance on page 1 and printed[1]
                # is the opening balance; the rest are the transaction rows.
                self.assertEqual(printed[2:2 + len(implied)], implied)

    def test_the_two_copies_of_one_macquarie_statement_agree(self):
        """M1 and M2 are the same statement no. 25 downloaded twice -- the
        files differ byte for byte. Two renderings of one document must not
        parse to two different answers."""
        names = ["M1.pdf", "M2.pdf"]
        for name in names:
            if not available(name):
                self.skipTest(f"{name} absent")
        self.assertNotEqual(hashlib.sha256(read("M1.pdf")).hexdigest(),
                            hashlib.sha256(read("M2.pdf")).hexdigest())
        first, second = parse("M1.pdf"), parse("M2.pdf")
        self.assertEqual(
            [(t["date"], t["amount"], t["description"]) for t in first["transactions"]],
            [(t["date"], t["amount"], t["description"]) for t in second["transactions"]],
        )

    def test_the_netbank_export_matches_the_totals_it_prints(self):
        """Ground truth from outside the parser, on both sides.

        The export footers its own totals: debits 8,177.61 and credits
        15,361.00. Their difference is 7,183.39, which is also 7,988.66 less
        805.27 -- so the anchors and the totals corroborate each other, and a
        parse matching both sides cannot have a debit read as a credit.
        """
        if not available("CBA_1.pdf"):
            self.skipTest("CBA_1.pdf absent")
        txns = parse("CBA_1.pdf")["transactions"]
        self.assertAlmostEqual(
            sum(t["amount"] for t in txns if t["amount"] > 0), 15361.00, places=2)
        self.assertAlmostEqual(
            sum(t["amount"] for t in txns if t["amount"] < 0), -8177.61, places=2)

    def test_the_netbank_export_is_turned_the_right_way_round(self):
        """It prints newest first. Left that way the running balance never
        follows row to row and the dates run backwards, so the import gate
        refuses it -- correctly, because it cannot tell a document in reverse
        from one with its rows out of order. The parser turns it, so
        everything downstream sees one direction."""
        if not available("CBA_1.pdf"):
            self.skipTest("CBA_1.pdf absent")
        txns = parse("CBA_1.pdf")["transactions"]
        dates = [t["date"] for t in txns]
        self.assertEqual(dates, sorted(dates))
        # The row printed first in the document is the last one chronologically,
        # and it is the row that lands on the closing balance.
        self.assertEqual(dates[-1], "2026-06-15")
        self.assertEqual(dates[0], "2024-08-16")
        self.assertAlmostEqual(txns[-1]["balance"], 7988.66, places=2)

    def test_a_document_only_half_in_order_is_not_turned_round(self):
        """Reversing is for a document that descends the whole way. Rows that
        are merely out of order somewhere in the middle are a fault, and
        rearranging them would hide exactly what the gate exists to catch."""
        from .statement_geometry import _is_descending
        descending = [{"date": "2026-06-15"}, {"date": "2026-05-13"},
                      {"date": "2026-04-13"}]
        self.assertTrue(_is_descending(descending))
        self.assertFalse(_is_descending([{"date": "2024-01-01"},
                                         {"date": "2024-02-01"}]))
        # one row the wrong way is enough to refuse to touch it
        self.assertFalse(_is_descending([{"date": "2026-06-15"},
                                         {"date": "2026-04-13"},
                                         {"date": "2026-05-13"}]))
        # and nothing to go on is not a direction
        self.assertFalse(_is_descending([{"date": "2026-06-15"}]))
        self.assertFalse(_is_descending([]))

    def test_the_netbank_export_takes_direction_from_the_column(self):
        """Ten of its 35 rows are labelled "Direct Credit" and are money OUT.

        The label describes the counterparty's instruction, not the direction
        on this account: 16/08/2024 reads "Direct Credit 301500 ALIC" and
        takes the balance from 805.27 to 629.27. Any parser keying on the
        words would get all ten backwards -- and by exactly double, which is
        what makes the printed totals above worth asserting.
        """
        if not available("CBA_1.pdf"):
            self.skipTest("CBA_1.pdf absent")
        txns = parse("CBA_1.pdf")["transactions"]
        first = txns[0]
        self.assertIn("Direct Credit", first["description"])
        self.assertAlmostEqual(first["amount"], -176.00, places=2)
        self.assertAlmostEqual(first["balance"], 629.27, places=2)

    def test_the_netbank_report_footer_is_not_read_as_a_description(self):
        """Every page ends with a report footer and the last adds a totals
        block -- report ID, page number, "No. of transactions". With
        descriptions wrapping below their figures, all of it would have
        attached to the transaction above it."""
        if not available("CBA_1.pdf"):
            self.skipTest("CBA_1.pdf absent")
        for txn in parse("CBA_1.pdf")["transactions"]:
            for junk in ("ReportID", "Pagenumber", "No.oftransactions",
                         "Totaldebits", "Totalcredits", "Accountnumber"):
                self.assertNotIn(junk, txn["description"].replace(" ", ""))

    def test_cba_2_and_cba_3_are_copies_and_not_extra_coverage(self):
        """CBA_2 and CBA_3 are byte-for-byte copies of CBA1 and CBA2, so they
        are deliberately NOT pinned as exemplars of their own: they would cost
        four parses per test method and assert nothing new. If either is ever
        replaced by a genuinely different statement, this fails and it should
        then be pinned properly."""
        for original, copy in (("CBA1.pdf", "CBA_2.pdf"),
                               ("CBA2.pdf", "CBA_3.pdf")):
            if not (available(original) and available(copy)):
                self.skipTest(f"{original} or {copy} absent")
            with self.subTest(copy):
                self.assertEqual(hashlib.sha256(read(original)).hexdigest(),
                                 hashlib.sha256(read(copy)).hexdigest())


class CrossStatementChainTests(SimpleTestCase):
    """Consecutive statements must join up.

    A parse can reconcile against its own anchors and still be wrong. Two
    statements agreeing at the boundary is independent evidence, and it is what
    justified trusting the CBA engine on two exemplars in the first place.
    """

    def _anchors(self, name):
        entry = BY_NAME[name]
        if entry["status"] == "gap":
            # Use the figures printed on the statement: the parser cannot yet
            # produce them, but the boundary is still a fact about the document.
            return entry["opening"], entry["closing"]
        result = parse(name)
        return float(result["opening_balance"]), float(result["closing_balance"])

    def test_each_statement_closes_where_the_next_one_opens(self):
        require_any(self, [n for pair in CHAINS for n in pair])
        checked = 0
        for earlier, later in CHAINS:
            if not (available(earlier) and available(later)):
                continue
            with self.subTest(f"{earlier} -> {later}"):
                _, earlier_closing = self._anchors(earlier)
                later_opening, _ = self._anchors(later)
                self.assertAlmostEqual(
                    earlier_closing, later_opening, places=2,
                    msg=(f"{earlier} closes at {earlier_closing:,.2f} but "
                         f"{later} opens at {later_opening:,.2f}"),
                )
                checked += 1
        if not checked:
            self.skipTest("no consecutive pair had both statements present")


class ScannedStatementTests(SimpleTestCase):
    """The two statements that no parser can read.

    BOM1 and BOM2 are photographs: one image per page, not a single word of
    text. They go to Claude Vision, and the first test here proves the routing
    without spending anything. The second re-measures against the real API and
    is opt-in, because a test suite must not bill an account or depend on a
    non-deterministic service to pass.
    """

    def test_the_scans_reach_vision_because_no_parser_can_claim_them(self):
        """Two halves of the routing: the direct parser must refuse them, and
        the fallback must then produce a statement. A scan detects as
        "unknown" -- there is no text to find a bank name in -- so the
        ValueError is what hands it to Vision."""
        from unittest.mock import patch
        from .pdf_parsers import extract_transactions_from_pdf_direct
        from .views import _try_vision_fallback
        names = ["BOM1.pdf", "BOM2.pdf"]
        require_any(self, names)
        for name in names:
            if not available(name):
                continue
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    extract_transactions_from_pdf_direct(read(name), name)
                canned = {
                    "opening_balance": 100.00, "closing_balance": 150.00,
                    "transactions": [{"date": "01/05/2024", "amount": 50.00,
                                      "balance": 150.00, "description": "X"}],
                }
                with patch("review.email_ingestion."
                           "extract_transactions_from_pdf",
                           return_value=canned) as vision:
                    extracted, error = _try_vision_fallback(
                        read(name), name, direct_error="unsupported")
                self.assertIsNone(error)
                self.assertEqual(vision.call_count, 1)
                self.assertEqual(len(extracted["transactions"]), 1)
                # It reconciles, so it must not be flagged for a human.
                self.assertIsNone(extracted.get("unverified"))

    @unittest.skipUnless(os.environ.get("STATEMENT_VISION_LIVE"),
                         "set STATEMENT_VISION_LIVE=1 to spend API budget "
                         "re-measuring the scanned statements")
    def test_live_vision_extraction_matches_the_recorded_figures(self):
        """Opt-in: the real thing, against the figures in the table above.

        Row counts are asserted because dropping the statements' own balance
        lines made the extraction deterministic -- three consecutive runs of
        BOM1 returned byte-identical rows. Before that it alternated between
        57 and 55. If this starts failing on the count alone, check whether
        those marker rows are back before changing the number.
        """
        import base64
        from .email_ingestion import extract_transactions_from_pdf
        for name in ("BOM1.pdf", "BOM2.pdf"):
            if not available(name):
                self.skipTest(f"{name} absent")
            entry = BY_NAME[name]
            with self.subTest(name):
                result = extract_transactions_from_pdf(
                    base64.b64encode(read(name)).decode(), name)
                txns = result["transactions"]
                self.assertEqual(len(txns), entry["rows"])
                self.assertAlmostEqual(
                    float(result["opening_balance"]), entry["opening"], places=2)
                self.assertAlmostEqual(
                    float(result["closing_balance"]), entry["closing"], places=2)
                movements = sum(t["amount"] for t in txns)
                self.assertAlmostEqual(
                    entry["opening"] + movements, entry["closing"], places=2)
                self.assertFalse(result.get("chain_broken"))
                self.assertFalse(result.get("dates_out_of_order"))
                for txn in txns:
                    self.assertNotIn("BALANCE", txn["description"].upper())


class DormantStatementTests(SimpleTestCase):
    """An account with nothing in it is not an unreadable file.

    BOM4 is the case: three periods of an account holding 0.01, not one
    transaction. It parses perfectly and produces an empty list, and both
    upload paths used to read that as failure and send the file to Claude
    Vision -- an API call and half a minute to re-read a statement already
    read correctly, and a model asked to find transactions in a document that
    has none, which is how one gets invented.
    """

    def test_a_dormant_statement_is_recognised_from_its_own_balances(self):
        from .statement_geometry import is_dormant_statement
        if not available("BOM4.pdf"):
            self.skipTest("BOM4.pdf absent")
        self.assertTrue(is_dormant_statement(parse("BOM4.pdf")))

    def test_a_statement_with_transactions_is_never_dormant(self):
        from .statement_geometry import is_dormant_statement
        if not available("BOM3.pdf"):
            self.skipTest("BOM3.pdf absent")
        self.assertFalse(is_dormant_statement(parse("BOM3.pdf")))

    def test_an_empty_parse_without_agreeing_balances_is_still_a_failure(self):
        """The balances are the whole evidence. Absent or contradictory, an
        empty result means the file was not read, and Vision should see it."""
        from .statement_geometry import is_dormant_statement
        self.assertFalse(is_dormant_statement(
            {"transactions": [], "opening_balance": 0.01,
             "closing_balance": 900.00}))
        self.assertFalse(is_dormant_statement(
            {"transactions": [], "opening_balance": None,
             "closing_balance": None}))
        self.assertFalse(is_dormant_statement({"transactions": []}))
        self.assertFalse(is_dormant_statement(None))
        self.assertFalse(is_dormant_statement(
            {"transactions": [], "opening_balance": "x",
             "closing_balance": "y"}))

    def test_a_dormant_statement_is_not_sent_to_vision(self):
        """The defect itself: BOM4 must not reach the API."""
        from .views import _should_try_vision
        if not available("BOM4.pdf"):
            self.skipTest("BOM4.pdf absent")
        self.assertFalse(
            _should_try_vision("BOM4.pdf", parse("BOM4.pdf"), None))

    def test_everything_that_did_reach_vision_still_does(self):
        """The guard must not close the door on real failures: a scan, a parse
        error, and an empty result with no balances to vouch for it."""
        from .views import _should_try_vision
        self.assertTrue(_should_try_vision("BOM1.pdf", None, ValueError("x")))
        self.assertTrue(_should_try_vision("x.pdf", {"transactions": []}, None))
        self.assertTrue(_should_try_vision(
            "x.pdf", {"transactions": [], "opening_balance": 1.0,
                      "closing_balance": 99.0}, None))
        # Not a PDF: there is nothing for Vision to read.
        self.assertFalse(_should_try_vision("x.xlsx", None, ValueError("x")))

    def test_the_message_says_it_was_read_not_that_it_failed(self):
        """"No transactions could be extracted" sends someone looking for a
        fault in a file that is perfectly fine."""
        from .views import _empty_statement_message
        if not available("BOM4.pdf"):
            self.skipTest("BOM4.pdf absent")
        message = _empty_statement_message(parse("BOM4.pdf"))
        self.assertIn("read in full", message)
        self.assertIn("0.01", message)
        self.assertNotIn("could not", message.lower())
        # A real failure keeps the old wording.
        self.assertEqual(_empty_statement_message({"transactions": []}),
                         "No transactions could be extracted")

