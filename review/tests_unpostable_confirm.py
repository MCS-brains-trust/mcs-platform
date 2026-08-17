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

    def test_the_review_page_renders_the_badge_after_a_reload(self):
        """The plan's compile check does not render the page — this does.

        Without this the server-rendered half of the warning is untested: the
        badge would appear when the row was confirmed and vanish on refresh,
        which is the failure mode the badge exists to prevent.
        """
        self._confirm()
        response = self.client.get(
            reverse("review:review_detail", args=[self.job.pk]), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Not posted", html)
        self.assertIn("No financial year covers 15 Jul 2026", html)

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
