"""Reallocating from the BAS screen must move the trial balance.

These two endpoints had no posting logic at all: they updated the transaction's
confirmed fields and returned. The BAS reads those fields and saw the change;
the financial statements read the trial balance and did not.

Auth setup: can_do_accounting is a read-only property derived from role,
Require2FAMiddleware needs 2fa_verified set after force_login, and
SECURE_SSL_REDIRECT 301s any post without secure=True.
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
        self.txn.posted_to_tb = True
        self.txn.save(update_fields=["posted_to_tb"])

        User = get_user_model()
        self.user = User.objects.create_user(
            username="reallocator", password="pw", email="r@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-realloc", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _debit(self, code):
        line = bs_line(self.fy, code)
        return line.debit if line else None

    def _post_single(self, code, name, tax_type):
        return self.client.post(
            reverse("core:bas_reallocate_transaction", args=[self.fy.pk]),
            data=json.dumps({"txn_id": str(self.txn.pk), "account_code": code,
                             "account_name": name, "tax_type": tax_type}),
            content_type="application/json",
            secure=True,
        )

    def _second_txn(self):
        second = make_txn(self.job, date_str="2025-08-15", amount="-2200.00",
                          code="0400", tax_type="GST on Expenses", gst="200.00")
        _post_txn_to_tb(second, resolve_fy_for_txn(second), has_gst=True)
        second.posted_to_tb = True
        second.save(update_fields=["posted_to_tb"])
        return second

    def _post_bulk(self, ids, code="0450", name="Repairs",
                   tax_type="GST on Expenses"):
        return self.client.post(
            reverse("core:bas_bulk_reallocate", args=[self.fy.pk]),
            data=json.dumps({"txn_ids": ids, "account_code": code,
                             "account_name": name, "tax_type": tax_type}),
            content_type="application/json",
            secure=True,
        )

    def test_single_reallocation_moves_the_trial_balance(self):
        self.assertEqual(self._debit("0400"), D("1000.00"))

        response = self._post_single("0450", "Repairs", "GST on Expenses")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._debit("0400"), D("0.00"))
        self.assertEqual(self._debit("0450"), D("1000.00"))

    def test_bulk_reallocation_moves_the_trial_balance(self):
        second = self._second_txn()

        response = self._post_bulk([str(self.txn.pk), str(second.pk)])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._debit("0400"), D("0.00"))
        self.assertEqual(self._debit("0450"), D("3000.00"))

    def test_bulk_rebuilds_once_not_once_per_transaction(self):
        """The rebuild is O(all posted transactions); n of them is n rebuilds.

        Patched at core.views_bas, which is why the function is imported at
        module level there rather than inside the view.
        """
        from unittest.mock import patch

        import core.views_bas as views_bas

        second = self._second_txn()

        with patch.object(views_bas, "_recalculate_bank_tb_lines",
                          wraps=views_bas._recalculate_bank_tb_lines) as spy:
            self._post_bulk([str(self.txn.pk), str(second.pk)])

        self.assertEqual(spy.call_count, 1)

    def test_reallocating_to_a_gst_free_type_clears_the_gst_control(self):
        self.assertEqual(self._debit("3380"), D("100.00"))

        self._post_single("0400", "Office costs", "GST Free Expenses")

        self.assertEqual(self._debit("3380"), D("0.00"))
        self.assertEqual(self._debit("0400"), D("1100.00"))
