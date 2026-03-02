"""
Division 7A Detection Module — BaseDetectionModule adapter.
============================================================

This module wraps the existing eva_div7a.py engine into the
BaseDetectionModule lifecycle pattern.  The core detection logic
remains in eva_div7a.py; this class provides the standard interface
that the orchestrator expects.

Rules: T2-D7A-01 through T2-D7A-08
Entity types: Company (with cross-entity trust lookups)
Assessment model: Div7AAssessment
"""

import logging
from core.risk_modules.base import BaseDetectionModule

logger = logging.getLogger(__name__)


class Div7ADetectionModule(BaseDetectionModule):
    module_id = "div7a"
    display_name = "Division 7A Assessment"
    entity_types = ["company"]
    finding_category = "COMPLIANCE"

    def __init__(self, financial_year):
        super().__init__(financial_year)
        from core.models import Div7AAssessment
        self.assessment_model = Div7AAssessment

    def assess(self):
        """Delegate to the existing eva_div7a engine."""
        from core.eva_div7a import run_div7a_assessment

        result = run_div7a_assessment(
            str(self.fy.pk),
            triggered_by="module_orchestrator",
        )

        # Map results back to base class attributes
        if result.get("skipped"):
            return None

        self.rules_fired = result.get("rules_fired", [])
        self.overall_severity = result.get("overall_severity", "CLEAR")

        return result

    def persist(self, assessment):
        """Assessment is already persisted by eva_div7a.run_div7a_assessment.

        Just load the record so the base class can link it to findings.
        """
        from core.models import Div7AAssessment

        self._assessment_record = Div7AAssessment.objects.filter(
            financial_year=self.fy,
        ).order_by("-assessed_at").first()

        return self._assessment_record

    def create_or_update_finding(self, assessment):
        """Finding is already created by eva_div7a._create_consolidated_finding.

        Skip the base class implementation to avoid duplicates.
        """
        return None

    def build_finding_card(self, assessment):
        """Not used — finding card is built by eva_div7a."""
        return None

    def log_activity(self, assessment):
        """Activity is already logged by eva_div7a._log_activity."""
        pass
