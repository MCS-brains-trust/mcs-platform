"""A unit trust seeds its chart from the trust template, not from nothing."""
from django.test import TestCase

from core.models import (
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
