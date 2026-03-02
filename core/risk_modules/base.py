"""
BaseDetectionModule — Abstract base class for dedicated detection modules.
=========================================================================

All dedicated detection modules (Division 7A, Going Concern, Section 100A,
and future modules) inherit from this class.  It enforces a consistent
lifecycle:

    1. should_run()  — pre-flight: does this module apply to this entity?
    2. load_data()    — gather TB, reference data, cross-entity data
    3. assess()       — run all rules in sequence, populate self.assessment
    4. persist()      — save/update the module's assessment model record
    5. create_or_update_finding() — upsert one consolidated EvaFinding card

The public entry point is run(), which calls the above in order.

Module Registration
-------------------
Modules are registered in DETECTION_MODULES (see registry.py).  The
orchestrator in risk_engine.py iterates over registered modules and calls
module.run() for each entity being assessed.
"""

import logging
from decimal import Decimal
from django.utils import timezone

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


class BaseDetectionModule:
    """Abstract base class for all dedicated detection modules."""

    # --- Subclass MUST override these ---
    module_id = None            # e.g. "div7a", "going_concern"
    display_name = None         # e.g. "Division 7A Assessment"
    entity_types = []           # e.g. ["company"]  — lowercase entity_type values
    assessment_model = None     # Django model class for persisting results

    # --- Subclass MAY override ---
    # Maps module_id to the Eva compliance check_name used in COMPLIANCE_CHECKS
    # (e.g. "div7a" → "div7a", "going_concern" → "going_concern")
    check_name_mapping = {
        "div7a": "div7a",
        "going_concern": "going_concern",
        "section100a": "trust_distribution",
        "cluster_rp": "related_party",
        "cluster_sgc": "super_guarantee",
        "cluster_tpar": "tpar",
    }

    def __init__(self, financial_year):
        self.fy = financial_year
        self.entity = financial_year.entity
        self.rules_fired = []
        self.overall_severity = "CLEAR"
        self.finding_lines = []
        self._assessment_record = None

    @property
    def eva_check_name(self):
        """Return the Eva compliance check_name for this module."""
        return self.check_name_mapping.get(self.module_id, self.module_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def should_run(self):
        """Pre-flight check: does this module apply to this entity type?

        Override for more complex gating (e.g. checking data availability).
        """
        if not self.entity_types:
            return True  # applies to all entity types
        return self.entity.entity_type in self.entity_types

    def load_data(self):
        """Load all data the module needs (TB, reference data, etc.).

        Override in subclass.  Called before assess().
        """
        pass

    def assess(self):
        """Execute all rules in sequence.  Populate self.rules_fired and
        self.overall_severity.

        MUST be overridden in subclass.  Returns an assessment dict that
        will be passed to persist() and create_or_update_finding().
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement assess()"
        )

    def persist(self, assessment):
        """Save or update the module's assessment model record.

        Default implementation calls _build_model_kwargs() and does
        update_or_create on the assessment_model.  Override if the
        model requires special handling.
        """
        if self.assessment_model is None:
            return None

        kwargs = self._build_model_kwargs(assessment)
        obj, created = self.assessment_model.objects.update_or_create(
            financial_year=self.fy,
            defaults=kwargs,
        )
        self._assessment_record = obj
        action = "Created" if created else "Updated"
        logger.info(
            "%s %s assessment for %s — %s",
            action, self.display_name, self.entity.entity_name,
            self.overall_severity,
        )
        return obj

    def create_or_update_finding(self, assessment):
        """Create or update a single consolidated EvaFinding card.

        Uses build_finding_card() for content.  Links to the assessment
        record via eva_finding FK on the assessment model.

        Field mapping to EvaFinding model:
            eva_review      — ForeignKey to EvaReview
            check_name      — matches COMPLIANCE_CHECKS id (e.g. "div7a")
            severity        — "critical" or "advisory"
            title           — max 255 chars
            plain_english_explanation — TextField
            recommendation  — TextField
            legislation_reference — max 255 chars
            confidence      — "high" / "medium" / "low"
            source          — "risk_engine" or "eva_analysis"
            status          — "open" / "addressed" / "closed" / "reopened"
        """
        from core.models import EvaFinding, EvaReview

        card = self.build_finding_card(assessment)
        if not card:
            return None

        # Find the latest EvaReview for this FY
        review = EvaReview.objects.filter(
            financial_year=self.fy,
        ).order_by("-triggered_at").first()

        if not review:
            # Create a lightweight review record for module findings
            review = EvaReview.objects.create(
                financial_year=self.fy,
                status="findings_raised",
            )

        # Map severity to lowercase (model choices are lowercase)
        severity = (self.overall_severity or "advisory").lower()
        if severity not in ("critical", "advisory"):
            severity = "advisory"

        # Determine the check_name for this module
        check_name = self.eva_check_name

        # Upsert finding by eva_review + check_name
        finding, created = EvaFinding.objects.update_or_create(
            eva_review=review,
            check_name=check_name,
            source="risk_engine",
            defaults={
                "severity": severity,
                "title": (card.get("title", self.display_name) or "")[:255],
                "plain_english_explanation": card.get("description", "") or "",
                "recommendation": card.get("recommended_action", "") or "",
                "legislation_reference": (card.get("legislation_ref", "") or "")[:255],
                "confidence": "high",
                "status": "open" if self.overall_severity != "CLEAR" else "closed",
            },
        )

        action = "Created" if created else "Updated"
        logger.info(
            "%s EvaFinding for %s [%s]: %s — %s",
            action, self.entity.entity_name, check_name,
            card.get("title", "")[:60], severity,
        )

        # Link assessment record to finding
        if self._assessment_record and hasattr(self._assessment_record, "eva_finding"):
            self._assessment_record.eva_finding = finding
            self._assessment_record.save(update_fields=["eva_finding"])

        return finding

    def build_finding_card(self, assessment):
        """Build the content dict for the consolidated EvaFinding card.

        MUST be overridden in subclass.  Returns a dict with keys:
            title, description, recommended_action, legislation_ref
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement build_finding_card()"
        )

    def log_activity(self, assessment):
        """Log the assessment to the entity's activity feed."""
        from core.models import ActivityLog

        try:
            ActivityLog.objects.create(
                entity=self.entity,
                financial_year=self.fy,
                event_type="audit_run",
                title=f"{self.display_name}: {self.overall_severity}",
                description=(
                    f"{self.display_name} for {self.entity.entity_name}: "
                    f"{self.overall_severity} "
                    f"({len(self.rules_fired)} rules fired: "
                    f"{', '.join(self.rules_fired)})"
                ),
                url=f"/entities/years/{self.fy.pk}/",
            )
        except Exception:
            # ActivityLog may have different fields across versions
            logger.debug("Could not log activity for %s", self.module_id)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self):
        """Execute the full module lifecycle.

        Returns the assessment dict, or None if the module doesn't apply.
        """
        if not self.should_run():
            logger.debug(
                "%s skipped for %s (entity_type=%s)",
                self.display_name, self.entity.entity_name,
                self.entity.entity_type,
            )
            return None

        self.load_data()
        assessment = self.assess()

        if assessment is None:
            return None

        self.persist(assessment)

        if self.overall_severity != "CLEAR":
            self.create_or_update_finding(assessment)

        self.log_activity(assessment)

        logger.info(
            "%s complete: %s — %s (rules: %s)",
            self.display_name, self.entity.entity_name,
            self.overall_severity, self.rules_fired,
        )

        return assessment

    # ------------------------------------------------------------------
    # Helpers (subclass may override)
    # ------------------------------------------------------------------

    def _build_model_kwargs(self, assessment):
        """Build kwargs dict for assessment_model.update_or_create().

        Override in subclass to map assessment dict to model fields.
        """
        return {
            "assessed_at": timezone.now(),
            "rules_fired": self.rules_fired,
            "overall_severity": self.overall_severity,
        }

    def load_trial_balance(self):
        """Convenience: load TB data using the risk engine's loader."""
        from core.risk_engine import _load_trial_balance
        return _load_trial_balance(self.fy)

    def load_reference_data(self):
        """Convenience: load reference data for this FY."""
        from core.risk_engine import _load_reference_data
        return _load_reference_data(self.fy.year_label)
