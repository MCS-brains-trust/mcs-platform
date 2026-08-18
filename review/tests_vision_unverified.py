"""A broken balance chain must flag the import, even when the totals foot.

Whole-statement reconciliation can pass while the detail is wrong: drop a row
and duplicate another of the same value and the sum still lands on the closing
balance. The chain catches what the sum cannot.
"""
from unittest.mock import patch

from django.test import TestCase


class ChainBrokenFlagsUnverifiedTests(TestCase):

    PDF = b"%PDF-1.4 image-only scan"

    def _vision_result(self, **overrides):
        # These figures reconcile: 1000 + 100 - 50 = 1050.
        result = {
            "opening_balance": 1000.00,
            "closing_balance": 1050.00,
            "account_name": "Test", "bsb": "", "account_number": "",
            "period_start": "", "period_end": "",
            "transactions": [
                {"date": "01/07/2025", "description": "a",
                 "amount": 100.00, "balance": 1100.00},
                {"date": "02/07/2025", "description": "b",
                 "amount": -50.00, "balance": 1050.00},
            ],
        }
        result.update(overrides)
        return result

    def test_a_footing_statement_with_a_broken_chain_is_flagged(self):
        from .views import _try_vision_fallback

        with patch("review.email_ingestion.extract_transactions_from_pdf",
                   return_value=self._vision_result(chain_broken=True)):
            extracted, message = _try_vision_fallback(self.PDF, "ANZ.pdf")

        self.assertIsNone(message)
        self.assertTrue(extracted["unverified"])

    def test_a_footing_statement_with_a_clean_chain_is_not_flagged(self):
        from .views import _try_vision_fallback

        with patch("review.email_ingestion.extract_transactions_from_pdf",
                   return_value=self._vision_result(chain_broken=False)):
            extracted, message = _try_vision_fallback(self.PDF, "ANZ.pdf")

        self.assertIsNone(message)
        self.assertFalse(extracted.get("unverified"))
