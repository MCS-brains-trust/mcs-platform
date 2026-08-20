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
import os
import unittest
from functools import lru_cache

from django.conf import settings
from django.test import SimpleTestCase

from .pdf_parsers import detect_bank, extract_transactions_from_pdf_direct
from .statement_geometry import (
    StatementNotImportable,
    StatementParseError,
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
    dict(name="CBA_1.pdf", bank="unknown", rows=None,
         opening=805.27, closing=7988.66, status="gap", geometry=False,
         gap="Transaction History export: unsupported format, and reverse "
             "chronological so the gate's date-order check would refuse it"),
    dict(name="WBC1.pdf", bank="westpac", rows=2,
         opening=14649.20, closing=21338.83, status="healthy", geometry=False),
    dict(name="WBC2.pdf", bank="westpac", rows=3,
         opening=11259.57, closing=14649.20, status="gap", geometry=False,
         gap="loses the 3,300.00 Osko payment whose description wraps to a "
             "second line, so the statement fails reconciliation"),
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
    dict(name="ING.pdf", bank="ing", rows=56,
         opening=2156.82, closing=3514.82, status="gap", geometry=False,
         gap="extracts no anchors (returns 0/0), so the import gate refuses "
             "every ING statement"),
]

# One statement's closing balance is the next one's opening balance.
CHAINS = [
    ("cba_stmt9.pdf", "cba_stmt10.pdf"),
    ("cba_stmt10.pdf", "July.pdf"),
    ("CBA1.pdf", "CBA2.pdf"),
    ("WBC2.pdf", "WBC1.pdf"),
    ("NAB2.pdf", "NAB1.pdf"),
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
            if entry["status"] in ("gap", "legacy"):
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

    def test_no_description_carries_page_furniture(self):
        for entry in self._healthy():
            if not available(entry["name"]):
                continue
            with self.subTest(entry["name"]):
                for txn in parse(entry["name"])["transactions"]:
                    for junk in ("YourStatement", "Your Statement",
                                 "TransactionDebitCredit"):
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

    def test_ing_is_refused_by_the_gate_for_want_of_anchors(self):
        if not available("ING.pdf"):
            self.skipTest("ING.pdf absent")
        result = parse("ING.pdf")
        self.assertEqual(len(result["transactions"]), BY_NAME["ING.pdf"]["rows"])
        with self.assertRaises(StatementNotImportable):
            assert_importable(
                result["transactions"],
                result["opening_balance"],
                result["closing_balance"],
            )

    def test_westpac_wbc2_fails_because_a_wrapped_row_is_lost(self):
        if not available("WBC2.pdf"):
            self.skipTest("WBC2.pdf absent")
        with self.assertRaises(StatementParseError):
            parse("WBC2.pdf")

    def test_the_cba_transaction_history_export_is_not_recognised(self):
        if not available("CBA_1.pdf"):
            self.skipTest("CBA_1.pdf absent")
        self.assertEqual(detect_bank(read("CBA_1.pdf")), "unknown")


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
