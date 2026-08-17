"""Reallocating a transaction that cannot post says so.

A reallocation changes a transaction's account and tax treatment but never its
date, so a transaction that could not post still cannot. The response must not
imply the ledger moved.
"""
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job, make_txn,
)

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BasReallocateUnpostableTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        self.txn = make_txn(self.job, date_str="2026-07-15", amount="-1100.00",
                            code="0400", tax_type="GST on Expenses", gst="100.00")

        User = get_user_model()
        self.user = User.objects.create_user(
            username="realloc_unpostable", password="pw", email="ru@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-ru", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def test_single_reallocation_reports_it_did_not_post(self):
        response = self.client.post(
            reverse("core:bas_reallocate_transaction", args=[self.fy.pk]),
            data=json.dumps({"txn_id": str(self.txn.pk), "account_code": "0450",
                             "account_name": "Repairs",
                             "tax_type": "GST on Expenses"}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["posted"])
        self.assertIn("No financial year", body["post_warning"])
        self.assertEqual(
            TrialBalanceLine.objects.filter(financial_year=self.fy).count(), 0)

    def test_bulk_reallocation_reports_how_many_did_not_post(self):
        response = self.client.post(
            reverse("core:bas_bulk_reallocate", args=[self.fy.pk]),
            data=json.dumps({"txn_ids": [str(self.txn.pk)], "account_code": "0450",
                             "account_name": "Repairs",
                             "tax_type": "GST on Expenses"}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unposted_count"], 1)
