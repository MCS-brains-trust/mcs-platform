"""Addressing an Eva finding has to make it stay away.

Found 2026-08-25 on DJLH Properties FY2025. The same six issues had been
addressed repeatedly across five reviews and came back every time: 30 findings
under 20 distinct finding_keys, 14 already marked addressed, 10 suppression
records on file that never matched.

``build_finding_key`` composes ``{check_id}_{sorted account codes}``, and the
account codes were taken from ``result["affected_account_codes"]`` — the LLM's
own account list, which varies run to run. One Div 7A exposure on account 3565
was recorded under five different keys (``div7a_3565``, ``div7a_3565_3625``,
``div7a_0575_3565_3625``, ``div7a_3545_3565_3625``,
``div7a_3565_3625_4160``). Suppression and the addressed check both look up by
finding_key, so neither could ever match.

The risk engine runs before the LLM and flags the same accounts
deterministically. Keying on RiskFlag.affected_accounts makes the key stable
across runs while keeping a genuinely different account on its own key.

A suppression that never expires is its own hazard — a Div 7A balance can grow
after being accepted. Suppressions therefore record the amount at the time and
lapse when it moves materially.

The second bug in here is separate but was hit at the same time:
``eva_review_status`` returned the in-process ``_eva_review_tasks`` entry
without checking it belonged to the review now running. The key is the
financial year, entries are never cleared on completion, and gunicorn runs
three workers, so a poll could be served a previous run's terminal state and
the UI would stop polling and report a completed or failed review while the
real one was still going.
"""
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.models import (
    Client as ClientModel,
    Entity,
    EvaFinding,
    EvaFindingSuppression,
    EvaReview,
    FinancialYear,
    RiskFlag,
)


class StableFindingKeyTests(TestCase):
    """The key that identifies a finding must not move between runs."""

    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Key Stability Client")
        self.entity = Entity.objects.create(
            entity_name="Keyed Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)

    def _flag(self, rule_id="D7A-001", accounts=("3565",), title="Div 7A loan"):
        return RiskFlag.objects.create(
            financial_year=self.fy, rule_id=rule_id, title=title,
            description="deterministic risk engine flag",
            affected_accounts=list(accounts), status="open",
            run_id=uuid.uuid4(), tier=1, severity="high",
        )

    def test_the_key_ignores_which_accounts_the_llm_happened_to_cite(self):
        """The whole bug: three runs, three LLM account lists, one issue."""
        from core.eva_engine import _stable_account_codes
        flags = [self._flag(accounts=["3565"])]

        run1 = _stable_account_codes("div7a", {"div7a": flags},
                                     llm_codes=["3565", "3625"])
        run2 = _stable_account_codes("div7a", {"div7a": flags},
                                     llm_codes=["3565", "3625", "4160"])
        run3 = _stable_account_codes("div7a", {"div7a": flags},
                                     llm_codes=["0575", "3565"])

        self.assertEqual(run1, run2)
        self.assertEqual(run2, run3)

    def test_the_key_comes_from_the_risk_engine_accounts(self):
        from core.eva_engine import _stable_account_codes
        flags = [self._flag(accounts=["3565"])]

        codes = _stable_account_codes("div7a", {"div7a": flags},
                                      llm_codes=["9999"])

        self.assertEqual(codes, ["3565"])

    def test_several_flags_on_one_check_are_combined_and_sorted(self):
        from core.eva_engine import _stable_account_codes
        flags = [self._flag(accounts=["3565"]), self._flag(accounts=["3546"])]

        codes = _stable_account_codes("div7a", {"div7a": flags}, llm_codes=[])

        self.assertEqual(codes, ["3546", "3565"])

    def test_a_different_loan_account_still_gets_its_own_key(self):
        """Stability must not collapse genuinely separate findings."""
        from core.eva_engine import _stable_account_codes
        first = _stable_account_codes(
            "div7a", {"div7a": [self._flag(accounts=["3565"])]}, llm_codes=[])
        second = _stable_account_codes(
            "div7a", {"div7a": [self._flag(accounts=["3546"])]}, llm_codes=[])

        self.assertNotEqual(first, second)

    def test_accounts_recorded_as_dicts_are_read_too(self):
        """RiskFlag.affected_accounts holds dicts in places and bare strings in
        others; the key must not depend on which shape was written."""
        from core.eva_engine import _stable_account_codes
        as_dicts = [self._flag(accounts=[{"account_code": "3565"}])]
        as_strings = [self._flag(accounts=["3565"])]

        self.assertEqual(
            _stable_account_codes("div7a", {"div7a": as_dicts}, llm_codes=[]),
            _stable_account_codes("div7a", {"div7a": as_strings}, llm_codes=[]),
        )

    def test_a_check_with_no_risk_flags_falls_back_to_the_bare_check_id(self):
        """Stable-but-coarse beats precise-but-unstable for LLM-only findings."""
        from core.eva_engine import _stable_account_codes

        codes = _stable_account_codes("related_party", {"related_party": []},
                                      llm_codes=["1798", "3545"])

        self.assertEqual(codes, [])

    def test_the_djlh_div7a_variants_all_collapse_to_one_key(self):
        """Regression: the five real keys seen on DJLH FY2025."""
        from core.eva_engine import _stable_account_codes
        flags = [self._flag(accounts=["3565"])]
        observed = [
            ["3565"], ["3565", "3625"], ["0575", "3565", "3625"],
            ["3545", "3565", "3625"], ["3565", "3625", "4160"],
        ]

        keys = {
            EvaFinding.build_finding_key(
                "div7a",
                account_codes=_stable_account_codes(
                    "div7a", {"div7a": flags}, llm_codes=codes),
            )
            for codes in observed
        }

        self.assertEqual(keys, {"div7a_3565"})


class SuppressionExpiresOnMaterialChangeTests(TestCase):
    """An accepted finding must come back if the numbers move under it."""

    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Expiry Client")
        self.entity = Entity.objects.create(
            entity_name="Expiring Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)

    def _suppress(self, key="div7a_3565", amount=Decimal("2752809")):
        return EvaFindingSuppression.objects.create(
            financial_year=self.fy,
            fingerprint=EvaFindingSuppression.generate_fingerprint(
                str(self.fy.entity_id), str(self.fy.pk), key),
            rule_category="div7a",
            fingerprint_version=2,
            requires_review=False,
            amount_at_suppression=amount,
        )

    def test_an_unchanged_balance_stays_suppressed(self):
        from core.eva_engine import _is_finding_suppressed
        self._suppress()

        self.assertTrue(_is_finding_suppressed(
            self.fy, "div7a_3565", current_amount=Decimal("2752809")))

    def test_a_materially_larger_balance_is_raised_again(self):
        from core.eva_engine import _is_finding_suppressed
        self._suppress()

        self.assertFalse(_is_finding_suppressed(
            self.fy, "div7a_3565", current_amount=Decimal("3400000")))

    def test_an_immaterial_drift_stays_suppressed(self):
        """A rounding-scale move must not undo the accountant's decision."""
        from core.eva_engine import _is_finding_suppressed
        self._suppress()

        self.assertTrue(_is_finding_suppressed(
            self.fy, "div7a_3565", current_amount=Decimal("2753000")))

    def test_a_small_percentage_move_on_a_small_balance_stays_suppressed(self):
        """5% of $2,000 is under the dollar floor, so it must not re-raise."""
        from core.eva_engine import _is_finding_suppressed
        self._suppress(key="gst_reconciliation_3380", amount=Decimal("2000"))

        self.assertTrue(_is_finding_suppressed(
            self.fy, "gst_reconciliation_3380", current_amount=Decimal("2150")))

    def test_a_suppression_with_no_recorded_amount_still_suppresses(self):
        """Existing rows predate the field and must not start re-raising."""
        from core.eva_engine import _is_finding_suppressed
        self._suppress(amount=None)

        self.assertTrue(_is_finding_suppressed(
            self.fy, "div7a_3565", current_amount=Decimal("9999999")))

    def test_omitting_the_amount_keeps_the_old_behaviour(self):
        from core.eva_engine import _is_finding_suppressed
        self._suppress()

        self.assertTrue(_is_finding_suppressed(self.fy, "div7a_3565"))


class EvaReviewStatusIdentityTests(TestCase):
    """Polling must describe the review that is actually running."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="eva_status_admin", password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-eva-status", totp_confirmed=True)
        self.client.force_login(self.user)
        s = self.client.session
        s["2fa_verified"] = True
        s.save()
        self.client_obj = ClientModel.objects.create(name="Status Client")
        self.entity = Entity.objects.create(
            entity_name="Polled Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)

    def _status(self):
        return self.client.get(
            reverse("core:eva_review_status", kwargs={"pk": self.fy.pk}),
            secure=True).json()

    def test_a_stale_cached_result_is_not_served_for_a_newer_review(self):
        """The reported bug: a finished run's entry answered for a live one."""
        from core import eva_engine
        finished = EvaReview.objects.create(
            financial_year=self.fy, status="findings_raised",
            triggered_by=self.user)
        running = EvaReview.objects.create(
            financial_year=self.fy, status="pending", triggered_by=self.user,
            raw_response={"progress": {"current_check": "GST Reconciliation",
                                       "total_checks": 8,
                                       "completed_checks": 3}})
        self.assertGreater(running.triggered_at, finished.triggered_at)

        with patch.dict(eva_engine._eva_review_tasks,
                        {str(self.fy.pk): {"status": "complete",
                                           "review_id": str(finished.pk),
                                           "review_status": "findings_raised"}},
                        clear=True):
            data = self._status()

        self.assertEqual(data["status"], "running")
        self.assertEqual(data["review_id"], str(running.pk))

    def test_a_stale_error_does_not_report_a_live_review_as_failed(self):
        """This is what made a healthy re-run look like it had failed."""
        from core import eva_engine
        failed = EvaReview.objects.create(
            financial_year=self.fy, status="error", triggered_by=self.user)
        EvaReview.objects.create(
            financial_year=self.fy, status="pending", triggered_by=self.user,
            raw_response={"progress": {"current_check": "Div 7A"}})

        with patch.dict(eva_engine._eva_review_tasks,
                        {str(self.fy.pk): {"status": "error",
                                           "review_id": str(failed.pk),
                                           "error": "boom"}},
                        clear=True):
            data = self._status()

        self.assertEqual(data["status"], "running")

    def test_the_cache_is_still_used_when_it_matches_the_current_review(self):
        """The fast path must survive — it carries the live progress."""
        from core import eva_engine
        running = EvaReview.objects.create(
            financial_year=self.fy, status="pending", triggered_by=self.user)

        with patch.dict(eva_engine._eva_review_tasks,
                        {str(self.fy.pk): {"status": "running",
                                           "review_id": str(running.pk),
                                           "current_check": "Div 7A",
                                           "total_checks": 8,
                                           "completed_checks": 2}},
                        clear=True):
            data = self._status()

        self.assertEqual(data["current_check"], "Div 7A")
        self.assertEqual(data["completed_checks"], 2)

    def test_a_cache_entry_with_no_review_id_is_not_trusted(self):
        """Older entries carry no id; they cannot be shown to be current."""
        from core import eva_engine
        EvaReview.objects.create(
            financial_year=self.fy, status="pending", triggered_by=self.user,
            raw_response={"progress": {"current_check": "TPAR"}})

        with patch.dict(eva_engine._eva_review_tasks,
                        {str(self.fy.pk): {"status": "complete"}},
                        clear=True):
            data = self._status()

        self.assertEqual(data["status"], "running")

