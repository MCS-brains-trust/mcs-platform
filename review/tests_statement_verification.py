"""Verification of the direct-parse path.

Until this module existed, ``_reconcile`` was called on exactly one code path:
the Claude Vision OCR fallback (review/views.py), which only runs after the
direct parser has already raised or returned nothing. A statement that parsed
to plausible-but-wrong figures was never balance-checked and never flagged, so
it imported clean.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from e2e.fixtures.statements import make_nab
from review.statement_geometry import (
    StatementParseError,
    verify_direct_parse,
    verify_nab_columns,
)
from review.pdf_parsers import extract_transactions_from_pdf_direct, parse_nab_statement


class NabColumnCrossCheckTests(SimpleTestCase):
    """NAB prints debits and credits in separate columns, so the page itself
    states the sign of every figure. The parser throws that away -- it reads
    flat text and recovers signs by subset-sum against each day's closing
    balance -- so the column positions are an independent second opinion."""

    def test_a_statement_whose_columns_agree_with_its_signs_passes(self):
        pdf = make_nab.build_pdf()
        result = parse_nab_statement(pdf)

        self.assertIsNone(verify_nab_columns(pdf, result["transactions"]))

    def test_a_flipped_pair_is_caught_even_though_the_statement_foots(self):
        """The defect reconciliation cannot see.

        The fixture's last day holds a $100.00 debit and a $100.00 credit, so
        the day's net change is zero under either sign assignment and the
        subset-sum has two valid answers. It keeps the first, which here is the
        wrong one: the row printed in the debit column comes back +100.00 and
        the row printed in the credit column comes back -100.00. Both signs are
        backwards and the statement still foots to the cent, so opening + sum
        == closing proves nothing. Only the columns know.
        """
        pdf = make_nab.build_pdf(ambiguous_day=True)
        result = parse_nab_statement(pdf)

        # Establish the premise: the parse really is wrong, and really does foot.
        by_description = {t["description"]: t["amount"] for t in result["transactions"]}
        self.assertEqual(by_description["HARDWARE SUPPLIES PAKENHAM"], 100.00)
        self.assertEqual(by_description["REFUND HARDWARE SUPPLIES"], -100.00)
        total = sum(t["amount"] for t in result["transactions"])
        self.assertAlmostEqual(
            result["opening_balance"] + total, result["closing_balance"], places=2
        )

        with self.assertRaises(StatementParseError) as caught:
            verify_nab_columns(pdf, result["transactions"])

        self.assertIn("HARDWARE SUPPLIES PAKENHAM", str(caught.exception))

    def test_the_word_debits_in_running_text_does_not_move_the_column(self):
        """Found on the real statements, not imagined.

        Page 14 of both exemplars carries the transaction table header AND a
        closing note reading "Bank Accounts Debits (BAD) Tax or State Debits
        Duty has been ...". Anchoring on the last word called "Debits" put the
        debit column at x=208 instead of x=396, so the eight real debits on
        that page matched no column at all and a clean 413-transaction
        statement was rejected for showing only 405 figures. The anchor has to
        come from the header row, not from any word that says "Debits".
        """
        pdf = make_nab.build_pdf(trailing_prose=True)
        result = parse_nab_statement(pdf)

        self.assertIsNone(verify_nab_columns(pdf, result["transactions"]))

    def test_the_check_reports_the_row_it_disagrees_about(self):
        """A bare "columns disagree" tells whoever reads the log nothing about
        which figure to look at on a 413-transaction statement."""
        pdf = make_nab.build_pdf(ambiguous_day=True)
        result = parse_nab_statement(pdf)

        with self.assertRaises(StatementParseError) as caught:
            verify_nab_columns(pdf, result["transactions"])

        message = str(caught.exception)
        self.assertIn("debit", message.lower())
        self.assertIn("100.00", message)


class DirectParseReconciliationTests(SimpleTestCase):
    """Every direct PDF parse now has to foot against its own printed
    balances. Before this, only the Vision OCR fallback did."""

    def test_a_statement_that_foots_is_returned(self):
        result = extract_transactions_from_pdf_direct(make_nab.build_pdf(), "nab.pdf")

        self.assertEqual(len(result["transactions"]), 6)
        self.assertFalse(result.get("unverified", False))

    def test_a_statement_that_does_not_foot_is_rejected(self):
        """The printed closing balance says 99,999.00; the transactions say
        12,228.00. One of them is wrong and the parser cannot tell which, so
        the statement does not get to import on a guess."""
        pdf = make_nab.build_pdf(tamper_closing=99999.00)

        with self.assertRaises(StatementParseError) as caught:
            extract_transactions_from_pdf_direct(pdf, "nab.pdf")

        self.assertIn("Reconciliation failed", str(caught.exception))

    def test_the_rejection_names_the_variance(self):
        """Whoever reads the log needs the number to chase, not just a verdict."""
        pdf = make_nab.build_pdf(tamper_closing=99999.00)

        with self.assertRaises(StatementParseError) as caught:
            extract_transactions_from_pdf_direct(pdf, "nab.pdf")

        self.assertIn("99999.00", str(caught.exception).replace(",", ""))

    def test_missing_balance_anchors_are_flagged_not_blocked(self):
        """A statement with no printed balances cannot be checked either way.
        Refusing it would be punishing absence of evidence, so it imports
        carrying the flag the preview renders."""
        pdf = make_nab.build_pdf(omit_anchors=True)

        result = extract_transactions_from_pdf_direct(pdf, "nab.pdf")

        self.assertTrue(result["unverified"])
        self.assertIn("balance", result["reconciliation_warning"].lower())

    def test_the_column_check_runs_on_the_direct_path_too(self):
        """The flipped pair has to be caught through the real entry point, not
        only when verify_nab_columns is called directly."""
        pdf = make_nab.build_pdf(ambiguous_day=True)

        with self.assertRaises(StatementParseError) as caught:
            extract_transactions_from_pdf_direct(pdf, "nab.pdf")

        self.assertIn("Column cross-check failed", str(caught.exception))

    def test_the_gate_applies_to_every_bank_not_just_nab(self):
        """Elio's call: block on any bank whose statement does not foot. Only
        NAB has real exemplars, but the reconciliation gate is bank-agnostic --
        it reads figures the parser itself produced."""
        result = {
            "opening_balance": 1000.00,
            "closing_balance": 2000.00,
            "transactions": [{"date": "2026-05-02", "description": "X", "amount": 10.00}],
        }

        with self.assertRaises(StatementParseError):
            verify_direct_parse(result, "anz", filename="anz.pdf")


class ParseEndpointContractTests(TestCase):
    """A guard, not a red test.

    The preview UI can only warn about what the endpoint tells it. The two
    fields were already in the JSON response before this work -- only the
    Vision fallback ever populated them, and nothing rendered them -- so this
    pins that a direct parse now populates them too, which is the contract the
    preview banner reads.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_user(
            username="verify", password="pw", email="v@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-verify", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _post(self, pdf):
        upload = SimpleUploadedFile("nab.pdf", pdf, content_type="application/pdf")
        return self.client.post(
            reverse("review:parse_statement"), {"file": upload}, secure=True
        )

    def test_an_unverifiable_statement_reaches_the_preview_flagged(self):
        response = self._post(make_nab.build_pdf(omit_anchors=True))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["unverified"])
        self.assertIn("balance", payload["reconciliation_warning"].lower())

    def test_a_blocked_statement_escalates_to_vision_rather_than_dying(self):
        """What "block" actually means to a user.

        The rejection is a StatementParseError, which both callers of the
        direct parser already treat as the trigger for the Claude Vision OCR
        fallback -- so a rejected statement is re-read, not lost. Vision is
        stubbed here because it is a paid API call; without the stub this test
        really does bill an API request.

        When Vision cannot help either, the endpoint answers 400 carrying both
        diagnoses, including the variance.
        """
        with patch("review.email_ingestion.extract_transactions_from_pdf") as vision:
            vision.side_effect = RuntimeError("vision unavailable")
            response = self._post(make_nab.build_pdf(tamper_closing=99999.00))

        self.assertTrue(vision.called)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Reconciliation failed", response.json()["message"])

    def test_when_vision_can_read_it_the_statement_imports_flagged(self):
        """The consequence of "block" that is easy to state wrongly.

        In production Vision IS available, so a rejected statement does not
        stop -- it gets re-read, and if Vision's own figures do not foot either
        it imports carrying ``unverified``. Blocking the direct parse buys a
        second opinion and a flag, not a refusal. A statement only fails
        outright when Vision fails too (the test above).
        """
        with patch("review.email_ingestion.extract_transactions_from_pdf") as vision:
            vision.return_value = {
                "opening_balance": 10000.00,
                "closing_balance": 99999.00,
                "transactions": [
                    {"date": "2026-05-02", "description": "EFTPOS SALES", "amount": 2228.00},
                ],
            }
            response = self._post(make_nab.build_pdf(tamper_closing=99999.00))

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["unverified"])
        self.assertTrue(payload["used_vision_ocr"])

    def test_a_verified_statement_carries_no_warning(self):
        response = self._post(make_nab.build_pdf())

        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertFalse(payload["unverified"])
        self.assertEqual(payload["reconciliation_warning"], "")


class NabFixtureTests(SimpleTestCase):
    """The fixture must keep reproducing the real statements' shape, or the
    tests built on it stop meaning anything."""

    def test_the_pdf_regenerates_byte_for_byte(self):
        """Every test here builds the fixture from scratch, so two builds
        differing would make failures depend on which build a test happened to
        get. reportlab embeds a /CreationDate unless invariant mode is on."""
        self.assertEqual(make_nab.build_pdf(), make_nab.build_pdf())

    def test_the_running_balance_appears_once_per_day_not_once_per_row(self):
        """The property that makes the subset-sum have to choose. If every row
        carried its own balance, each transaction would get its own checkpoint
        and no sign would ever be ambiguous -- the fixture would then be
        incapable of reproducing the defect it exists for."""
        result = parse_nab_statement(make_nab.build_pdf())
        dates = [t["date"] for t in result["transactions"]]

        self.assertEqual(len(dates), 6)
        self.assertEqual(len(set(dates)), 4)
