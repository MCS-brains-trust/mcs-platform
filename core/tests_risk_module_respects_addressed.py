"""A finding the accountant has dealt with must not be raised again.

Found 2026-08-25 on DJLH Properties FY2025, after the finding_key stability fix
had already landed. Eva's LLM loop was correctly skipping addressed findings —
the log for one review shows ``SUPPRESSED finding for related_party`` — yet the
same related party and Division 7A cards appeared on every review anyway.

The detection modules in core/risk_modules run at step 1 of a review, before
the guarded LLM loop, and ``create_or_update_finding`` upserts its card without
consulting either guard. It also writes ``status="open"`` unconditionally, so
work already marked addressed was reset on every run. Six modules do this:
div7a, cluster_rp, cluster_sgc, cluster_tpar, section100a and going_concern.

Div 7A is an exposure only while it is unaddressed — a s.109N agreement
executed before lodgement deals with it. Related party balances are an issue
only where they are not at arm's length, and that documentation frequently
lives off-platform. In both cases the accountant's judgement that the matter is
dealt with is the authoritative fact, and a review that keeps re-raising it
sends them round in circles.

Addressed is therefore permanent here. A suppression, which records the balance
at the time it was accepted, is what lapses on material movement — that is a
separate mechanism and is left alone.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    Client as ClientModel,
    Entity,
    EvaFinding,
    EvaFindingSuppression,
    EvaReview,
    FinancialYear,
)
from core.risk_modules.base import BaseDetectionModule


class _CardModule(BaseDetectionModule):
    """Smallest module that produces a card, standing in for cluster_rp."""

    module_id = "cluster_rp"
    display_name = "Related Party Cluster"
    entity_types = ["company"]

    def build_finding_card(self, assessment):
        return {
            "title": "Related Party Transactions — AASB 124",
            "description": "Inter-entity balances require disclosure.",
            "recommended_action": "Confirm arm's length terms.",
            "legislation_ref": "AASB 124",
        }


class ModuleRespectsAddressedFindingsTests(TestCase):
    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Module Guard Client")
        self.entity = Entity.objects.create(
            entity_name="Guarded Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)
        self.module = _CardModule(self.fy)
        self.module.overall_severity = "ADVISORY"

    def _review(self):
        return EvaReview.objects.create(
            financial_year=self.fy, status="findings_raised")

    def _addressed_finding(self, key="related_party"):
        return EvaFinding.objects.create(
            eva_review=self._review(),
            check_name="related_party",
            source="risk_engine",
            severity="advisory",
            title="Related Party Transactions — AASB 124",
            finding_key=key,
            status="addressed",
        )

    def test_an_addressed_finding_is_not_reopened_by_the_module(self):
        """The circle the accountant was stuck in."""
        self._addressed_finding()
        self._review()  # the new review the module will write into

        self.module.create_or_update_finding(assessment=None)

        statuses = set(
            EvaFinding.objects.filter(
                eva_review__financial_year=self.fy,
                check_name="related_party",
            ).values_list("status", flat=True)
        )
        self.assertNotIn("open", statuses)

    def test_the_card_is_still_written_so_it_stays_visible(self):
        """Skipping outright would make a Div 7A card vanish from the review."""
        self._addressed_finding()
        review = self._review()

        self.module.create_or_update_finding(assessment=None)

        card = EvaFinding.objects.filter(
            eva_review=review, check_name="related_party").first()
        self.assertIsNotNone(card)
        self.assertEqual(card.status, "addressed")

    def test_an_unaddressed_finding_is_still_raised(self):
        """The guard must not stop Eva reporting genuine new issues."""
        review = self._review()

        self.module.create_or_update_finding(assessment=None)

        card = EvaFinding.objects.filter(
            eva_review=review, check_name="related_party").first()
        self.assertIsNotNone(card)
        self.assertEqual(card.status, "open")

    def test_a_suppressed_finding_is_not_written_at_all(self):
        review = self._review()
        EvaFindingSuppression.objects.create(
            financial_year=self.fy,
            fingerprint=EvaFindingSuppression.generate_fingerprint(
                str(self.fy.entity_id), str(self.fy.pk), "related_party"),
            rule_category="related_party",
            fingerprint_version=2,
            requires_review=False,
        )

        result = self.module.create_or_update_finding(assessment=None)

        self.assertIsNone(result)
        self.assertFalse(EvaFinding.objects.filter(
            eva_review=review, check_name="related_party").exists())

    def test_addressing_survives_repeated_reviews(self):
        """Three runs after addressing must not resurrect it once."""
        self._addressed_finding()

        for _ in range(3):
            self._review()
            self.module.create_or_update_finding(assessment=None)

        self.assertFalse(
            EvaFinding.objects.filter(
                eva_review__financial_year=self.fy,
                check_name="related_party",
                status="open",
            ).exists())

    def test_a_closed_finding_is_also_respected(self):
        """Auto-closed findings count as dealt with, same as addressed."""
        self._addressed_finding()
        EvaFinding.objects.filter(
            eva_review__financial_year=self.fy).update(status="closed")
        review = self._review()

        self.module.create_or_update_finding(assessment=None)

        card = EvaFinding.objects.filter(
            eva_review=review, check_name="related_party").first()
        self.assertNotEqual(getattr(card, "status", None), "open")
