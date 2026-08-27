"""trust_unit is a real entity type that inherits trust behaviour."""
from django.test import TestCase

from core.models import TRUST_LIKE_TYPES, Entity


class TrustLikeTypesTests(TestCase):
    def test_unit_trust_is_a_selectable_entity_type(self):
        self.assertIn(("trust_unit", "Unit Trust"), Entity.EntityType.choices)

    def test_trust_like_types_covers_both_trust_kinds(self):
        self.assertEqual(TRUST_LIKE_TYPES, ("trust", "trust_unit"))

    def test_discretionary_trust_is_trust_like(self):
        e = Entity.objects.create(entity_name="Vincent Family Trust", entity_type="trust")
        self.assertTrue(e.is_trust_like)
        self.assertFalse(e.is_unit_trust)

    def test_unit_trust_is_both_trust_like_and_a_unit_trust(self):
        e = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        self.assertTrue(e.is_trust_like)
        self.assertTrue(e.is_unit_trust)

    def test_company_is_neither(self):
        e = Entity.objects.create(entity_name="DJLH", entity_type="company")
        self.assertFalse(e.is_trust_like)
        self.assertFalse(e.is_unit_trust)
