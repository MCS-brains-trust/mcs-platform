"""Vision OCR must be told which year the statement covers.

A real 12-page CBA statement (1 May - 30 Jul 2026) was read two pages at a
time. CBA prints the year only in the statement header and on its anchor rows;
transaction rows carry a bare '10Jun'. Pages 5-6 therefore contained no year
token at all, and Vision dated all 53 of those rows to 2025.

Nothing downstream could see it. Only the dates were wrong, so the balance
column still chained perfectly row to row and reconciliation still footed. The
financial-year filter then dropped all 53 rows as out of period, silently
taking 22,399.41 of real movement and leaving a closing balance that could not
match the statement.

Two defences are tested here: every chunk is handed the statement's own period
so it never has to guess, and dates are checked for statement order so a chunk
that still comes back a year out cannot pass unnoticed.
"""
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import reportlab.rl_config as rl_config

rl_config.invariant = 1

from django.test import TestCase
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .email_ingestion import (
    _date_anomalies,
    _merge_chunk_results,
    _statement_date_context,
    extract_transactions_from_pdf,
)
from .tests_vision_chunking import _StreamContext, txn, vision_response


def build_statement_pdf(n_pages=4, header_lines=("Period 1May2026-30Jul2026",)):
    """A multi-page PDF carrying its period on page 1 and nothing on the rest.

    This is the shape that caused the defect: the year is stated once, on a page
    most chunks never see.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 9)
    for page in range(n_pages):
        if page == 0:
            y = 780
            for line in header_lines:
                c.drawString(40, y, line)
                y -= 18
        else:
            c.drawString(40, 780, f"10Jun DIRECT CREDIT PAGE {page + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


class Recorder:
    """Records every messages.stream call and returns an empty extraction."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return _StreamContext(vision_response({
            "opening_balance": None, "closing_balance": None,
            "account_name": "", "bsb": "", "account_number": "",
            "period_start": "", "period_end": "",
            "transactions": [txn("row", 10.0, 110.0, "01/06/2026")],
        }))

    def install(self):
        client = SimpleNamespace(messages=SimpleNamespace(stream=self))
        return patch(
            "review.email_ingestion._get_anthropic_client", return_value=client
        )

    def prompt_texts(self):
        out = []
        for kwargs in self.calls:
            for block in kwargs["messages"][0]["content"]:
                if block["type"] == "text":
                    out.append(block["text"])
        return out


class StatementDateContextTests(TestCase):

    def test_the_period_line_is_read_off_the_statement(self):
        context = _statement_date_context(build_statement_pdf())
        self.assertIn("1May2026", context)
        self.assertIn("30Jul2026", context)

    def test_a_statement_with_no_period_falls_back_to_its_year(self):
        pdf = build_statement_pdf(
            header_lines=("Statement 11", "Issued 14 August 2026"))
        self.assertIn("2026", _statement_date_context(pdf))

    def test_a_statement_stating_nothing_yields_no_context(self):
        pdf = build_statement_pdf(header_lines=("Business Transaction Account",))
        self.assertEqual(_statement_date_context(pdf), "")

    def test_an_unreadable_pdf_does_not_raise(self):
        self.assertEqual(_statement_date_context(b"not a pdf"), "")


class EveryChunkIsToldTheYearTests(TestCase):

    def test_the_period_reaches_every_chunk_including_those_that_never_see_it(self):
        """The regression: the chunk that dated 53 rows to 2025 was the one
        chunk whose own pages carried no year."""
        pdf = build_statement_pdf(n_pages=6)
        recorder = Recorder()
        with recorder.install():
            extract_transactions_from_pdf(
                base64.b64encode(pdf).decode("utf-8"), "July.pdf")

        texts = recorder.prompt_texts()
        self.assertGreater(len(texts), 1, "expected more than one chunk")
        for text in texts:
            self.assertIn("1May2026", text)
            self.assertIn("30Jul2026", text)

    def test_a_statement_with_no_period_still_extracts(self):
        pdf = build_statement_pdf(
            n_pages=4, header_lines=("Business Transaction Account",))
        recorder = Recorder()
        with recorder.install():
            result = extract_transactions_from_pdf(
                base64.b64encode(pdf).decode("utf-8"), "nodate.pdf")
        self.assertTrue(result["transactions"])


class DateOrderIsCheckedSeparatelyTests(TestCase):
    """The balance chain cannot see a wrong year, so dates get their own check."""

    def _year_slipped_payloads(self):
        """Three chunks whose balances chain perfectly, with the middle chunk a
        year early -- exactly the shape of the real failure."""
        first = {
            "opening_balance": 100.0, "closing_balance": None,
            "account_name": "", "bsb": "", "account_number": "",
            "period_start": "", "period_end": "",
            "transactions": [txn("a", 10.0, 110.0, "01/06/2026")],
        }
        middle = {
            "opening_balance": None, "closing_balance": None,
            "account_name": "", "bsb": "", "account_number": "",
            "period_start": "", "period_end": "",
            "transactions": [txn("b", 10.0, 120.0, "05/06/2025")],
        }
        last = {
            "opening_balance": None, "closing_balance": 130.0,
            "account_name": "", "bsb": "", "account_number": "",
            "period_start": "", "period_end": "",
            "transactions": [txn("c", 10.0, 130.0, "22/06/2026")],
        }
        return [first, middle, last]

    def test_a_year_slipped_chunk_leaves_the_balance_chain_intact(self):
        """Guard the premise: if the chain caught this, no date check would be
        needed and the test below would prove nothing."""
        merged = _merge_chunk_results(self._year_slipped_payloads())
        self.assertFalse(merged["chain_broken"])

    def test_a_year_slipped_chunk_is_reported_as_out_of_order(self):
        merged = _merge_chunk_results(self._year_slipped_payloads())
        self.assertTrue(merged["dates_out_of_order"])
        self.assertIn("05/06/2025", merged["date_gap_detail"])

    def test_a_statement_in_order_is_not_flagged(self):
        payloads = self._year_slipped_payloads()
        payloads[1]["transactions"][0]["date"] = "05/06/2026"
        merged = _merge_chunk_results(payloads)
        self.assertFalse(merged["dates_out_of_order"])
        self.assertEqual(merged["date_gap_detail"], "")

    def test_unparseable_dates_are_not_treated_as_disorder(self):
        self.assertEqual(
            _date_anomalies([txn("a", 1.0, None, ""),
                             txn("b", 1.0, None, "not a date")]),
            [],
        )
