"""Correcting an already-posted transaction must move the trial balance.

confirm_transaction guards posting on posted_to_tb — correct for stopping a
double-click double-post, and wrong for a correction, because the guard cannot
tell "post this twice" from "this changed, post it again".

Auth setup is not decoration. can_do_accounting is a read-only property derived
from role, Require2FAMiddleware needs 2fa_verified set by hand after
force_login, and SECURE_SSL_REDIRECT 301s any post without secure=True.
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
        self.txn.confirmed_gst_amount = D("0")
        self.txn.gst_amount = D("0")
        self.txn.save(update_fields=[
            "is_confirmed", "confirmed_gst_amount", "gst_amount"])

        User = get_user_model()
        self.user = User.objects.create_user(
            username="corrector", password="pw", email="c@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-correction", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _confirm(self, code, tax_type, name="Account"):
        return self.client.post(
            reverse("review:confirm_transaction", args=[self.txn.pk]),
            data=json.dumps({"confirmed_code": code, "confirmed_name": name,
                             "confirmed_tax_type": tax_type}),
            content_type="application/json",
            secure=True,
        )

    def _debit(self, code):
        line = bs_line(self.fy, code)
        return line.debit if line else None

    def _credit(self, code):
        line = bs_line(self.fy, code)
        return line.credit if line else None

    def test_a_second_confirm_moves_the_trial_balance(self):
        self.assertEqual(self._confirm("0400", "GST on Expenses").status_code, 200)
        self.assertEqual(self._debit("0400"), D("1000.00"))

        self._confirm("0450", "GST on Expenses", name="Repairs")

        self.assertEqual(self._debit("0400"), D("0.00"),
                         "the vacated account must go to zero")
        self.assertEqual(self._debit("0450"), D("1000.00"))

    def test_changing_only_the_tax_type_moves_the_gst_control_account(self):
        self._confirm("0400", "GST on Expenses")
        self.assertEqual(self._debit("3380"), D("100.00"))

        self._confirm("0400", "GST Free Expenses")

        self.assertEqual(self._debit("3380"), D("0.00"))
        self.assertEqual(self._debit("0400"), D("1100.00"),
                         "with no GST the full gross hits the expense account")

    def test_the_double_post_guard_still_holds(self):
        """Confirming twice with identical values must not double the figure."""
        self._confirm("0400", "GST on Expenses")
        self._confirm("0400", "GST on Expenses")
        self.assertEqual(self._debit("0400"), D("1000.00"))

    def test_the_bank_contra_follows_the_correction(self):
        self._confirm("0400", "GST on Expenses")
        self._confirm("0450", "GST on Expenses", name="Repairs")
        self.assertEqual(self._credit("1100"), D("1100.00"),
                         "the gross that moved through the bank is unchanged")
