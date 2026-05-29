"""
Eva Reflection Engine — Phase 2A regression tests.

Three tests covering the bug surfaces fixed in commits d67448b, 97e4598,
and the fail-loudly wrapper change:

1. _extract_signals returns non-zero results across all four signal sources
   when fixture rows exist. Catches any future field-name drift on
   EvaClarification / EvaFindingSuppression / ActivityLog / EvaMessage.

2. _build_check_context (Reader 3) injects an active EvaLearnedLesson into
   the per-check context string. Catches any future schema drift on
   EvaLearnedLesson fields used by the per-check reader.

3. run_nightly_reflection re-raises on a signal-extraction exception
   rather than swallowing it into the returned errors dict. Catches any
   future re-introduction of an outer try/except around _extract_signals.
"""
from datetime import date, timedelta
from unittest.mock import patch
from django.core.exceptions import FieldError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from core.models import (
    Client, Entity, FinancialYear,
    ActivityLog, EvaClarification, EvaConversation, EvaFinding,
    EvaFindingSuppression, EvaLearnedLesson, EvaMessage, EvaReview,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class ExtractSignalsTests(TestCase):
    """_extract_signals must return one signal per source against corrected fields."""

    @classmethod
    def setUpTestData(cls):
        two_fa = {"totp_secret": "TESTSECRET", "totp_confirmed": True}
        cls.user = User.objects.create_user(
            username="reflection_tester",
            password="testpass123",
            role=User.Role.ADMIN,
            first_name="Reflection",
            last_name="Tester",
            **two_fa,
        )
        cls.client_obj = Client.objects.create(name="Reflection Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Reflection Test Co",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        cls.review = EvaReview.objects.create(
            financial_year=cls.fy, status="findings_raised",
        )
        cls.finding = EvaFinding.objects.create(
            eva_review=cls.review,
            check_name="div7a",
            finding_key="div7a_1200",
            severity="critical",
            plain_english_explanation="test",
            recommendation="test",
            status="open",
        )

    def test_extract_signals_returns_one_per_source(self):
        from core.eva_reflection import _extract_signals

        # EvaClarification — answered today
        EvaClarification.objects.create(
            finding=self.finding,
            question_id="div7a_relationship",
            question_text="What is the relationship of the borrower?",
            answer_value="related_company",
            answer_label="Related company",
            answer_detail="Sister company under common control.",
            outcome="confirmed",
            answered_by=self.user,
        )

        # EvaFindingSuppression — suppressed today
        EvaFindingSuppression.objects.create(
            financial_year=self.fy,
            fingerprint="a" * 64,
            rule_category="div7a",
            suppressed_by=self.user,
            accountant_note="Loan repaid in full before year end.",
        )

        # ActivityLog — eva_finding_addressed today
        ActivityLog.objects.create(
            user=self.user,
            event_type="eva_finding_addressed",
            title="Finding addressed",
            description="Reclassified loan as a genuine commercial advance.",
            entity=self.entity,
            financial_year=self.fy,
            eva_finding=self.finding,
        )

        # EvaMessage — user correction in the chat (correction-keyword matched)
        conv = EvaConversation.objects.create(
            financial_year=self.fy, user=self.user,
        )
        EvaMessage.objects.create(
            conversation=conv,
            role="user",
            content="Actually that should be classified as a vehicle expense.",
        )

        since = timezone.now() - timedelta(hours=24)
        signals = _extract_signals(since)

        types_seen = sorted(s["type"] for s in signals)
        self.assertEqual(
            types_seen,
            ["ActivityLog", "EvaClarification", "EvaFindingSuppression", "EvaMessage"],
            f"Expected one signal per source, got: {types_seen}",
        )

        # Spot-check field mappings — proves the renames stuck.
        clar = next(s for s in signals if s["type"] == "EvaClarification")
        self.assertEqual(clar["question"], "What is the relationship of the borrower?")
        self.assertEqual(clar["answer"], "Related company")
        self.assertEqual(clar["answer_detail"], "Sister company under common control.")

        supp = next(s for s in signals if s["type"] == "EvaFindingSuppression")
        self.assertEqual(supp["check_name"], "div7a")
        self.assertEqual(supp["reason"], "Loan repaid in full before year end.")
        self.assertEqual(supp["entity"], "Reflection Test Co")

        act = next(s for s in signals if s["type"] == "ActivityLog")
        self.assertEqual(act["event_type"], "eva_finding_addressed")
        self.assertIn("Reclassified", act["description"])


@override_settings(STORAGES=STORAGES_OVERRIDE)
class Reader3LessonInjectionTests(TestCase):
    """_build_check_context (Reader 3) must surface populated EvaLearnedLesson rows."""

    @classmethod
    def setUpTestData(cls):
        two_fa = {"totp_secret": "TESTSECRET", "totp_confirmed": True}
        cls.user = User.objects.create_user(
            username="reader3_tester",
            password="testpass123",
            role=User.Role.ADMIN,
            **two_fa,
        )
        cls.client_obj = Client.objects.create(name="Reader 3 Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Reader 3 Test Co",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

    def test_entity_scoped_lesson_injected_into_context(self):
        from core.eva_engine import _build_check_context

        EvaLearnedLesson.objects.create(
            lesson_text="When classifying motor vehicle expenses for this entity, prefer account 6-2100.",
            category="classification",
            priority_weight=2.0,
            source_entity=self.entity,
            source_signal_type="nightly_reflection",
            is_active=True,
        )

        context = _build_check_context(self.fy, "gst_reconciliation")

        self.assertIn("FIRM LEARNED RULES", context)
        self.assertIn("prefer account 6-2100", context)
        self.assertIn("[Entity: Reader 3 Test Co]", context)

    def test_firm_wide_lesson_injected_when_source_entity_null(self):
        from core.eva_engine import _build_check_context

        EvaLearnedLesson.objects.create(
            lesson_text="Always disclose related-party transactions over $10,000.",
            category="compliance",
            priority_weight=1.8,
            source_entity=None,
            source_signal_type="nightly_reflection",
            is_active=True,
        )

        context = _build_check_context(self.fy, "gst_reconciliation")

        self.assertIn("Always disclose related-party transactions", context)
        self.assertIn("[Firm-wide]", context)

    def test_inactive_lesson_excluded(self):
        from core.eva_engine import _build_check_context

        EvaLearnedLesson.objects.create(
            lesson_text="This rule should not appear in the context.",
            category="general",
            priority_weight=3.0,
            source_entity=self.entity,
            source_signal_type="nightly_reflection",
            is_active=False,
        )

        context = _build_check_context(self.fy, "gst_reconciliation")

        self.assertNotIn("should not appear", context)
        self.assertNotIn("FIRM LEARNED RULES", context)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class ReflectionFailLoudlyTests(TestCase):
    """run_nightly_reflection must re-raise on signal-extraction exception."""

    def test_signal_extraction_exception_propagates(self):
        from core import eva_reflection

        with patch.object(
            eva_reflection, "_extract_signals",
            side_effect=FieldError("forced — simulated schema drift"),
        ):
            with self.assertRaises(FieldError):
                eva_reflection.run_nightly_reflection(hours_back=24)

    def test_empty_signal_window_returns_cleanly(self):
        """Sanity: zero signals is still a clean SUCCESS path with no errors."""
        from core.eva_reflection import run_nightly_reflection

        # No fixtures created — _extract_signals walks empty querysets and returns [].
        result = run_nightly_reflection(hours_back=24)

        self.assertEqual(result["signals_processed"], 0)
        self.assertEqual(result["lessons_extracted"], 0)
        self.assertEqual(result["lessons_stored"], 0)
        self.assertEqual(result["errors"], [])
