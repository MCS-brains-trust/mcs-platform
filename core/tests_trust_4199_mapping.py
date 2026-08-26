"""Trust charts must map 4199 to the trust undistributed-income line."""
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from core.models import (
    AccountMapping, ChartOfAccount, Client, Entity, EntityChartOfAccount,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class CrossEntityTypeMappingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Mapping Test Client")
        cls.trust = Entity.objects.create(
            entity_name="Mapping Test Trust",
            entity_type="trust",
            client=cls.client_obj,
        )
        cls.partnership_line = AccountMapping.objects.create(
            standard_code="BS-EQ-007",
            line_item_label="Partners' current accounts",
            statement_section="Equity",
            applicable_entities=["partnership"],
        )
        # get_or_create, not create: migration 0148 seeds this same
        # standard_code so a fresh test database already has it — see
        # "Deviations from the brief" in task-1-report.md.
        cls.trust_line, _ = AccountMapping.objects.get_or_create(
            standard_code="BS-EQ-005",
            defaults={
                "line_item_label": "Undistributed income",
                "statement_section": "Equity",
                "applicable_entities": ["trust"],
            },
        )

    def test_mapping_a_trust_account_to_a_partnership_line_is_refused(self):
        eca = EntityChartOfAccount(
            entity=self.trust,
            account_code="4199",
            account_name="Undistributed income",
            section="pl_appropriation",
            maps_to=self.partnership_line,
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            eca.full_clean(exclude=["display_order"])

    def test_mapping_a_trust_account_to_the_trust_line_is_allowed(self):
        # The post_save signal on Entity auto-seeds a trust's chart from the
        # master template (core/signals.py:handle_trust_entity_created), and
        # migration 0148 gives that template a real 4199 row -- so self.trust
        # already has one. Clear it so the unsaved instance below is legitimately
        # unique under EntityChartOfAccount's (entity, account_code) constraint;
        # we are testing clean()'s validation, not seed_from_template.
        EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4199",
        ).delete()
        eca = EntityChartOfAccount(
            entity=self.trust,
            account_code="4199",
            account_name="Undistributed income",
            section="pl_appropriation",
            maps_to=self.trust_line,
            is_active=True,
        )
        eca.full_clean(exclude=["display_order"])  # must not raise


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TrustTemplateMappingTests(TestCase):
    def test_master_trust_chart_maps_4199_to_undistributed_income(self):
        tpl = ChartOfAccount.objects.filter(
            entity_type="trust", account_code="4199",
        ).first()
        self.assertIsNotNone(tpl, "master trust chart has no 4199 row")
        self.assertIsNotNone(
            tpl.maps_to,
            "trust 4199 is unmapped — entities will each guess a line",
        )
        self.assertEqual(tpl.maps_to.standard_code, "BS-EQ-005")
