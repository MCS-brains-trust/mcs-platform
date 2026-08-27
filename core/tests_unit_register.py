"""Units are the register; the percentage is derived from them."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Entity, EntityOfficer


class UnitRegisterTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )

    def _holder(self, name, units):
        return EntityOfficer.objects.create(
            entity=self.entity, full_name=name,
            role="unit_holder", roles=["unit_holder"], units_held=units,
        )

    def test_total_units_sums_active_holders(self):
        self._holder("Double Water International Pty Ltd", 50)
        self._holder("Penman Property Nominees Pty Ltd", 50)
        self.assertEqual(self.entity.total_units, 100)

    def test_percentage_is_derived_from_units(self):
        a = self._holder("Double Water International Pty Ltd", 50)
        b = self._holder("Penman Property Nominees Pty Ltd", 50)
        self.assertEqual(a.unit_percentage, Decimal("50.0000"))
        self.assertEqual(b.unit_percentage, Decimal("50.0000"))

    def test_uneven_split_derives_exactly(self):
        a = self._holder("A", 1)
        self._holder("B", 2)
        self.assertEqual(a.unit_percentage, Decimal("33.3333"))

    def test_distribution_percentage_is_stored_from_units_on_save(self):
        # Stored, not a pure property: existing consumers read this field.
        #
        # unit_percentage divides by entity.total_units, a live DB
        # aggregate. A is saved before B exists, so A's stored percentage
        # is computed against a total of just A's units (100%). Adding B
        # afterwards does not retroactively fix A's stored value -- only
        # recalculate_unit_percentages() rewrites every holder from the
        # final register. So we call it explicitly, rather than asserting
        # a value that only holds by luck of insertion order.
        a = self._holder("A", 75)
        self._holder("B", 25)
        EntityOfficer.recalculate_unit_percentages(self.entity)
        a.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("75.00"))

    def test_percentage_is_zero_when_no_units_on_issue(self):
        a = EntityOfficer.objects.create(
            entity=self.entity, full_name="A",
            role="unit_holder", roles=["unit_holder"],
        )
        self.assertEqual(a.unit_percentage, Decimal("0"))

    def test_non_unit_holders_may_not_hold_units(self):
        officer = EntityOfficer(
            entity=self.entity, full_name="T", role="trustee", units_held=10,
        )
        officer.clean()
        self.assertIsNone(officer.units_held)

    def test_ceased_holders_are_excluded_from_total(self):
        self._holder("A", 50)
        ceased = self._holder("B", 50)
        ceased.date_ceased = date(2025, 1, 1)
        ceased.save()
        self.assertEqual(self.entity.total_units, 50)

    def test_discretionary_trust_beneficiary_unaffected(self):
        """A beneficiary of a discretionary trust must be untouched by any
        of this: distribution_percentage stays freely typed and units_held
        stays null."""
        disc_trust = Entity.objects.create(
            entity_name="Ordinary Family Trust", entity_type="trust",
        )
        beneficiary = EntityOfficer.objects.create(
            entity=disc_trust, full_name="Jane Beneficiary",
            role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("40.00"),
        )
        beneficiary.refresh_from_db()
        # The typed value is preserved exactly -- not overwritten by any
        # unit-derived computation, because units_held was never set.
        self.assertEqual(beneficiary.distribution_percentage, Decimal("40.00"))
        self.assertIsNone(beneficiary.units_held)
        # clean() must not touch units_held for a beneficiary either.
        beneficiary.clean()
        self.assertIsNone(beneficiary.units_held)
