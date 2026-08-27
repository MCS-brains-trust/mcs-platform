"""Units are the register; the percentage is derived from them."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import Entity, EntityOfficer, OfficerDistributionHistory


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


class UnitRegisterFixRoundTests(TestCase):
    """Fix-round tests: each one corresponds to a defect the reviewer found
    empirically and every test above passed straight through."""

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )

    def _holder(self, name, units):
        return EntityOfficer.objects.create(
            entity=self.entity, full_name=name,
            role="unit_holder", roles=["unit_holder"], units_held=units,
        )

    # -- FIX 5: clean()'s units guard must narrow on `role` only, not widen
    # to match the `roles`-aware distribution_percentage guard. -----------

    def test_clean_nulls_units_for_trustee_who_also_lists_unit_holder_role(self):
        # role="trustee" with "unit_holder" in the roles list must still
        # lose units_held: the units guard checks `role` only, mirroring
        # what actually assigns role="unit_holder" (core/forms.py).
        officer = EntityOfficer(
            entity=self.entity, full_name="T",
            role="trustee", roles=["trustee", "unit_holder"], units_held=10,
        )
        officer.clean()
        self.assertIsNone(officer.units_held)
        # And the pre-existing distribution_percentage guard (role-only)
        # keeps behaving the same way: nulled too.
        officer.distribution_percentage = Decimal("50.00")
        officer.clean()
        self.assertIsNone(officer.distribution_percentage)

    def test_clean_nulls_units_for_plain_trustee(self):
        # roles=["trustee"] alone (no "unit_holder" anywhere): still
        # nulled. Proves deleting the old roles-list clause changes
        # nothing observable -- the role-only check already covers this.
        officer = EntityOfficer(
            entity=self.entity, full_name="T",
            role="trustee", roles=["trustee"], units_held=10,
        )
        officer.clean()
        self.assertIsNone(officer.units_held)

    # -- FIX 1: no wrong insert-time percentage, no inverted-date history --

    def test_recalculate_writes_correct_history_with_no_inverted_dates(self):
        a = self._holder("A", 75)
        b = self._holder("B", 25)
        EntityOfficer.recalculate_unit_percentages(self.entity)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("75.00"))
        self.assertEqual(b.distribution_percentage, Decimal("25.00"))

        a_history = list(
            OfficerDistributionHistory.objects.filter(officer=a).order_by("effective_from")
        )
        # Exactly one row, correct percentage, no wrong intermediate 0.00%
        # or 100% row ever booked, and no inverted (effective_to before
        # effective_from) period.
        self.assertEqual(len(a_history), 1)
        self.assertEqual(a_history[0].distribution_pct, Decimal("75.00"))
        for row in a_history:
            if row.effective_to is not None:
                self.assertGreaterEqual(row.effective_to, row.effective_from)

        b_history = list(OfficerDistributionHistory.objects.filter(officer=b))
        self.assertEqual(len(b_history), 1)
        self.assertEqual(b_history[0].distribution_pct, Decimal("25.00"))

    def test_individual_save_does_not_book_wrong_percentage_or_history(self):
        # A unit holder's own save() must never derive distribution_
        # percentage from units_held (only recalculate_unit_percentages
        # does) and must never write history for it either -- both are
        # left for the batch recompute, which alone sees the true total.
        a = self._holder("A", 75)
        a.refresh_from_db()
        self.assertIsNone(a.distribution_percentage)
        self.assertEqual(OfficerDistributionHistory.objects.filter(officer=a).count(), 0)

    # -- FIX 2: ceased holders must not blow up / overflow the recompute --

    def test_recalculate_excludes_ceased_holder_and_does_not_overflow(self):
        ceased = self._holder("Ceased", 9000)
        ceased.date_ceased = date(2020, 1, 1)
        ceased.save()
        active = self._holder("Active", 1)
        # Must not raise decimal.InvalidOperation / DataError.
        EntityOfficer.recalculate_unit_percentages(self.entity)
        active.refresh_from_db()
        ceased.refresh_from_db()
        self.assertEqual(active.distribution_percentage, Decimal("100.00"))
        # Ceased holder's percentage is frozen (untouched), not nulled and
        # not recomputed against the active-only total.
        self.assertIsNone(ceased.distribution_percentage)

    # -- FIX 3: total_units must not disenfranchise a future-ceased holder -

    def test_total_units_includes_future_ceased_holder(self):
        self._holder("Leaving later", 50)
        self._holder("Staying", 50)
        leaving = EntityOfficer.objects.get(full_name="Leaving later")
        leaving.date_ceased = timezone.now().date() + timedelta(days=30)
        leaving.save()
        self.assertEqual(self.entity.total_units, 100)
        EntityOfficer.recalculate_unit_percentages(self.entity)
        leaving.refresh_from_db()
        self.assertEqual(leaving.distribution_percentage, Decimal("50.00"))

    # -- FIX 4: stored percentages must sum to exactly 100.00 --------------

    def test_three_equal_holders_sum_to_exactly_100(self):
        self._holder("A", 1)
        self._holder("B", 1)
        self._holder("C", 1)
        EntityOfficer.recalculate_unit_percentages(self.entity)
        total = sum(
            (o.distribution_percentage for o in EntityOfficer.objects.filter(entity=self.entity)),
            Decimal("0"),
        )
        self.assertEqual(total, Decimal("100.00"))
