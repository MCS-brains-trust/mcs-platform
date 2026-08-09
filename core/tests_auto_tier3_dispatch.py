"""
Tests for the automatic Tier 3 AI pass that fires when a year enters review.

The pass had never run: _build_entity_context raised FieldError on every call and a
log-only try/except swallowed it. With that fixed it runs for real — several
Anthropic API calls, 6-12 seconds each, on the request thread, so an accountant
finalising a year watched the page hang; and the nightly e2e suite, which finalises a
year against a production-derived database, sent real client financials to the API on
every run.

It is now queued to Celery like every other AI job in this codebase, and gated by
AUTO_TIER3_ON_IN_REVIEW so the e2e settings can switch it off entirely.
"""

from datetime import date
from unittest import mock

from django.test import TestCase, override_settings

from accounts.models import User
from core.models import (
    Client as ClientModel,
    Entity,
    FinancialYear,
)
from core.views import _dispatch_auto_tier3

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class AutoTier3DispatchTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="tier3_dispatch_admin",
            password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-tier3",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Tier 3 Dispatch Client")
        cls.entity = Entity.objects.create(
            entity_name="Tier 3 Dispatch Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
            primary_accountant=cls.user,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )

    @override_settings(AUTO_TIER3_ON_IN_REVIEW=True)
    def test_the_ai_pass_is_queued_not_run_on_the_request_thread(self):
        """
        Finalising must not wait on the AI. The work is handed to Celery, exactly as
        core/tasks.py already does for every other AI job.
        """
        with mock.patch("core.tasks.batch_analyse_flags_task.delay") as delay, \
                mock.patch("core.ai_service.batch_analyse_flags") as inline:
            queued = _dispatch_auto_tier3(self.fy)

        self.assertTrue(queued)
        delay.assert_called_once_with(str(self.fy.pk))
        inline.assert_not_called()

    @override_settings(AUTO_TIER3_ON_IN_REVIEW=False)
    def test_nothing_is_dispatched_when_the_pass_is_switched_off(self):
        """
        The e2e settings switch this off so the nightly suite sends no client data
        to the API and incurs no spend.
        """
        with mock.patch("core.tasks.batch_analyse_flags_task.delay") as delay, \
                mock.patch("core.ai_service.batch_analyse_flags") as inline:
            queued = _dispatch_auto_tier3(self.fy)

        self.assertFalse(queued)
        delay.assert_not_called()
        inline.assert_not_called()

    @override_settings(AUTO_TIER3_ON_IN_REVIEW=True)
    def test_a_broker_failure_does_not_block_finalisation(self):
        """
        Queueing is best-effort — a year must still finalise if the broker is down —
        but the failure is logged as an exception rather than swallowed silently,
        which is how the original defect stayed invisible.
        """
        with mock.patch(
            "core.tasks.batch_analyse_flags_task.delay",
            side_effect=OSError("broker unreachable"),
        ):
            with self.assertLogs("core.views", level="ERROR") as logs:
                queued = _dispatch_auto_tier3(self.fy)

        self.assertFalse(queued)
        self.assertTrue(
            any("broker unreachable" in message for message in logs.output),
            f"the underlying error must reach the log: {logs.output}",
        )

    def test_the_task_runs_the_analysis_for_the_year_it_was_given(self):
        """The task body resolves the id and calls the real analysis function."""
        from core.tasks import batch_analyse_flags_task

        with mock.patch("core.ai_service.batch_analyse_flags") as analyse:
            analyse.return_value = {"analysed": 3, "skipped": 1}
            result = batch_analyse_flags_task(str(self.fy.pk))

        analyse.assert_called_once()
        self.assertEqual(analyse.call_args[0][0].pk, self.fy.pk)
        self.assertEqual(result["analysed"], 3)
