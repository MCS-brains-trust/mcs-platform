"""set_gst_treatment must not clobber the confirmation flags.

It read the row unlocked and saved every column from its in-memory copy, so when
the review screen fired it concurrently with /confirm/ its later save reverted
is_confirmed and posted_to_tb to their pre-confirm values — silently
unconfirming the transaction and orphaning its trial-balance entries.

HOW THE LOST UPDATE IS REPRODUCED HERE: the race needs the view's read to
happen before the concurrent confirm commits, which no single-threaded test can
arrange by ordinary means. So _recalculate_gst — which the endpoint calls after
its read and before its save — is wrapped to commit the confirm at exactly that
moment, via a queryset .update() that leaves the view's in-memory copy stale.
That is the real shape of the defect, and it fails deterministically against the
unfixed code.

WHAT THESE TESTS CANNOT PROVE: select_for_update is a no-op on sqlite. These
tests prove the narrowed update_fields save. Only the Postgres end-to-end soak
proves the lock.
"""
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job,
    make_txn,
)
from review.models import PendingTransaction

D = Decimal


def _confirm_in_the_database(pk):
    """A concurrent /confirm/ committing, invisible to an already-read copy."""
    PendingTransaction.objects.filter(pk=pk).update(
        is_confirmed=True, posted_to_tb=True)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class SetGstTreatmentDoesNotClobberTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        self.txn = make_txn(self.job, date_str="2025-08-01", amount="-110.00",
                            code="0400", tax_type="GST on Expenses", gst="10.00")
        # The view's copy must predate the confirm, so the row starts unconfirmed
        # and unposted. _confirm_in_the_database flips it mid-request.
        PendingTransaction.objects.filter(pk=self.txn.pk).update(
            is_confirmed=False, posted_to_tb=False)

        User = get_user_model()
        self.user = User.objects.create_user(
            username="gsttester", password="pw", email="gst@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-gst-race", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        # Require2FAMiddleware requires TOTP to have been completed this session
        # (security fix B4); force_login skips that flow.
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _post(self, treatment="gst_free"):
        return self.client.post(
            reverse("review:set_gst_treatment", args=[self.txn.pk]),
            data=json.dumps({"gst_treatment": treatment, "is_manual": True}),
            content_type="application/json",
            secure=True,  # SECURE_SSL_REDIRECT is on; a plain post 301s
        )

    def _with_concurrent_confirm(self, pk):
        """Patch _recalculate_gst so the confirm lands after the view's read."""
        from review import views_enhanced

        real = views_enhanced._recalculate_gst

        def wrapper(txn, is_gst_registered):
            _confirm_in_the_database(pk)
            return real(txn, is_gst_registered)

        return real, wrapper

    def test_a_stale_in_memory_copy_cannot_unconfirm_the_row(self):
        """The exact lost-update shape: the view's copy predates the confirm."""
        from unittest.mock import patch
        from review import views_enhanced

        real, wrapper = self._with_concurrent_confirm(self.txn.pk)
        with patch.object(views_enhanced, "_recalculate_gst", wrapper):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.is_confirmed)
        self.assertTrue(self.txn.posted_to_tb)

    def test_the_update_statement_does_not_name_the_confirmation_flags(self):
        """Direct proof of the narrowed save, independent of any interleaving."""
        with CaptureQueriesContext(connection) as captured:
            self._post()

        updates = [q["sql"] for q in captured.captured_queries
                   if q["sql"].lstrip().upper().startswith("UPDATE")
                   and "pendingtransaction" in q["sql"].lower()]
        self.assertTrue(updates, "the endpoint issued no UPDATE on the transaction")
        for sql in updates:
            self.assertNotIn("is_confirmed", sql)
            self.assertNotIn("posted_to_tb", sql)

    def test_it_still_writes_the_fields_it_owns(self):
        self._post("gst_free")
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.gst_treatment, "gst_free")
        self.assertEqual(self.txn.confirmed_tax_type, "GST Free Expenses")
        self.assertEqual(self.txn.gst_amount, D("0.00"))

    def test_bulk_endpoint_does_not_clobber_either(self):
        from unittest.mock import patch
        from review import views_enhanced

        real, wrapper = self._with_concurrent_confirm(self.txn.pk)
        with patch.object(views_enhanced, "_recalculate_gst", wrapper):
            response = self.client.post(
                reverse("review:bulk_gst", args=[self.job.pk]),
                data=json.dumps({"transaction_ids": [str(self.txn.pk)],
                                 "gst_treatment": "gst_free"}),
                content_type="application/json",
                secure=True,
            )

        self.assertEqual(response.status_code, 200)
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.is_confirmed)
        self.assertTrue(self.txn.posted_to_tb)
        self.assertEqual(self.txn.gst_treatment, "gst_free")
