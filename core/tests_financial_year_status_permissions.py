"""
Regression tests — financial_year_status privilege gate.

core/views.py financial_year_status previously had only @login_required while
its transition table allows in_review→finalised and finalised→reopened. Any
user with entity access (e.g. a standard accountant assigned to the entity)
could finalise or reopen a year via the status dropdown, bypassing the
can_finalise gate enforced by the dedicated endpoints
(financial_year_finalise_full / reopen_financial_year).

The fix gates transitions to "finalised"/"reopened" on request.user.can_finalise.
"""
from datetime import date
from unittest.mock import patch

from django.test import Client as TestClient, TestCase

from accounts.models import User
from core.models import Client as ClientModel, Entity, FinancialYear


def _make_user(username, role):
    # 2FA satisfied so Require2FAMiddleware doesn't intercept
    # (has_2fa = bool(totp_secret) and totp_confirmed).
    return User.objects.create_user(
        username=username,
        password="x",
        role=role,
        totp_secret="dummy-secret-for-test",
        totp_confirmed=True,
    )


class FinancialYearStatusPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.accountant = _make_user("fy_status_accountant", User.Role.ACCOUNTANT)
        cls.senior = _make_user("fy_status_senior", User.Role.SENIOR_ACCOUNTANT)

        cls.client_obj = ClientModel.objects.create(name="FY Status Perm Test Client")
        cls.entity = Entity.objects.create(
            entity_name="FY Status Perm Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
            # Assigned so the standard accountant has entity access —
            # that access alone must NOT allow finalising.
            assigned_accountant=cls.accountant,
        )

    def setUp(self):
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

    def _client_for(self, user):
        c = TestClient()
        c.force_login(user)
        # Require2FAMiddleware requires TOTP completed this session;
        # force_login skips that flow, so mark the session verified.
        session = c.session
        session["2fa_verified"] = True
        session.save()
        return c

    def _post_status(self, user, status):
        c = self._client_for(user)
        return c.post(
            f"/years/{self.fy.pk}/status/", {"status": status}, secure=True,
        )

    # ── Finalise ────────────────────────────────────────────────────────

    def test_accountant_cannot_finalise_via_status_dropdown(self):
        self.fy.status = FinancialYear.Status.IN_REVIEW
        self.fy.save()

        response = self._post_status(self.accountant, "finalised")

        self.assertEqual(response.status_code, 302)
        self.fy.refresh_from_db()
        self.assertEqual(
            self.fy.status, FinancialYear.Status.IN_REVIEW,
            "A standard accountant must not be able to finalise via the "
            "status dropdown",
        )
        self.assertIsNone(self.fy.finalised_at)

    def test_senior_can_finalise_via_status_dropdown(self):
        self.fy.status = FinancialYear.Status.IN_REVIEW
        self.fy.save()

        response = self._post_status(self.senior, "finalised")

        self.assertEqual(response.status_code, 302)
        self.fy.refresh_from_db()
        self.assertEqual(self.fy.status, FinancialYear.Status.FINALISED)
        self.assertIsNotNone(self.fy.finalised_at)

    # ── Reopen ──────────────────────────────────────────────────────────

    def test_accountant_cannot_reopen_via_status_dropdown(self):
        self.fy.status = FinancialYear.Status.FINALISED
        self.fy.save()

        response = self._post_status(self.accountant, "reopened")

        self.assertEqual(response.status_code, 302)
        self.fy.refresh_from_db()
        self.assertEqual(
            self.fy.status, FinancialYear.Status.FINALISED,
            "A standard accountant must not be able to reopen a finalised "
            "year via the status dropdown",
        )

    def test_senior_can_reopen_via_status_dropdown(self):
        self.fy.status = FinancialYear.Status.FINALISED
        self.fy.save()

        response = self._post_status(self.senior, "reopened")

        self.assertEqual(response.status_code, 302)
        self.fy.refresh_from_db()
        self.assertEqual(self.fy.status, FinancialYear.Status.REOPENED)

    # ── Non-privileged transitions must still work for accountants ─────

    def test_accountant_can_still_move_draft_to_in_review(self):
        self.fy.status = FinancialYear.Status.DRAFT
        self.fy.save()

        # The in_review path triggers the risk engine + Tier 3 AI analysis;
        # stub both so the test exercises only the permission/transition logic.
        with patch("core.signals.trigger_risk_recalc"), \
             patch("core.ai_service.batch_analyse_flags", return_value={}):
            response = self._post_status(self.accountant, "in_review")

        self.assertEqual(response.status_code, 302)
        self.fy.refresh_from_db()
        self.assertEqual(self.fy.status, FinancialYear.Status.IN_REVIEW)
