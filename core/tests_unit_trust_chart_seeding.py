"""A unit trust seeds its chart from the trust template, not from nothing."""
from django.test import TestCase

from core.models import (
    AccountMapping,
    ChartOfAccount,
    Entity,
    EntityChartOfAccount,
    template_entity_type,
)


class TemplateEntityTypeTests(TestCase):
    def test_unit_trust_resolves_to_trust(self):
        self.assertEqual(template_entity_type("trust_unit"), "trust")

    def test_other_types_pass_through(self):
        for value in ("trust", "company", "partnership", "sole_trader", "smsf"):
            self.assertEqual(template_entity_type(value), value)


class UnitTrustChartSeedingTests(TestCase):
    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="620",
            account_name="Rents received", section="revenue",
        )
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="4000",
            account_name="Opening balance - Beneficiary", section="equity",
        )
        # Migration 0148_trust_4199_mapping unconditionally seeds a
        # ("trust", "4199") ChartOfAccount row on any fresh database (its
        # docstring calls this out explicitly for "a fresh test database").
        # A test-fixture chart mirroring reality has to include it too.
        self.expected_codes = {"620", "4000", "4199"}

    def test_unit_trust_seeds_from_the_trust_template(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        EntityChartOfAccount.seed_from_template(entity)

        codes = set(
            EntityChartOfAccount.objects.filter(entity=entity)
            .values_list("account_code", flat=True)
        )
        self.assertEqual(codes, self.expected_codes)

    def test_discretionary_trust_seeding_is_unchanged(self):
        entity = Entity.objects.create(entity_name="Vincent", entity_type="trust")
        EntityChartOfAccount.seed_from_template(entity)

        codes = set(
            EntityChartOfAccount.objects.filter(entity=entity)
            .values_list("account_code", flat=True)
        )
        self.assertEqual(codes, self.expected_codes)


class UnitTrustImportMappingResolvesFromTrustTemplateTests(TestCase):
    """End-to-end proof for the import-mapping path (core/views.py), not just
    chart seeding: a unit trust importing a statement must still find its
    account names/mappings against the trust master template.

    This is the fixture that gave _resolve_account_name its docstring example
    (Minli Enterprise Unit Trust) — reused here as a real regression guard for
    the entity_type-keyed lookups fixed in core/views.py by this task.
    """

    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="620",
            account_name="Rents received", section="revenue",
        )

    def test_unit_trust_resolves_account_name_from_trust_template(self):
        from core.views import _resolve_account_name

        entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        # No EntityChartOfAccount row yet, and the imported name is blank/
        # code-shaped — this is exactly the case that falls through to the
        # master ChartOfAccount template (Attempt 2 in the docstring).
        resolved_name = _resolve_account_name(entity, "620", "620")

        self.assertEqual(
            resolved_name, "Rents received",
            "a trust_unit entity failed to resolve its account name against "
            "the trust master template — the import-mapping path is dead for "
            "unit trusts",
        )


class UnitTrustAccountMappingApplicabilityTests(TestCase):
    """The headline case: production's 70 AccountMapping rows all list only
    'trust' in applicable_entities, never 'trust_unit' (verified against the
    live database by the controller). Every membership test against that
    field must resolve trust_unit through the trust template, or a unit
    trust's financial statements come out with every line unmapped.
    """

    def test_unit_trust_resolves_a_trust_only_account_mapping(self):
        from core.mapping_engine import auto_map_account

        mapping = AccountMapping.objects.create(
            standard_code="IS-REV-001",
            line_item_label="Sales revenue",
            financial_statement="profit_and_loss",
            statement_section="Revenue",
            display_order=10,
            applicable_entities=["trust"],
        )

        result = auto_map_account(
            classification="Sales revenue",
            account_code="200",
            account_name="Sales",
            entity_type="trust_unit",
        )

        self.assertEqual(
            result, mapping,
            "a trust_unit account failed to resolve a mapping whose "
            "applicable_entities lists only 'trust' -- every financial "
            "statement line item would come out unmapped for a unit trust",
        )
