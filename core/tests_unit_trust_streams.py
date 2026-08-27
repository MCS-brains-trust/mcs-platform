"""Every stream splits by units; franking credits follow franked dividends.

Discretionary trusts keep using ``BeneficiaryAllocation.calculate_allocation``,
which quantizes each row independently (rows do not need to tie to the
stream total for a discretionary trust, since streaming is a choice, not an
arithmetic consequence of a unit register). Unit trusts have no streaming
choice at all -- every stream must split in exactly the same proportion as
the register, and the parts must sum EXACTLY to the whole. That is what
``allocate_unit_trust_distribution`` (built on Task 7's
``allocate_by_units``) guarantees and ``calculate_allocation`` does not.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    BeneficiaryAllocation, Entity, EntityOfficer, FinancialYear, TrustDistribution,
)
from core.views_trust import allocate_unit_trust_distribution


class UnitTrustStreamTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        for name, units in [("A", 75), ("B", 25)]:
            EntityOfficer.objects.create(
                entity=self.entity, full_name=name,
                role="unit_holder", roles=["unit_holder"], units_held=units,
            )
        EntityOfficer.recalculate_unit_percentages(self.entity)
        self.distribution = TrustDistribution.objects.create(
            financial_year=self.fy,
            distributable_income=Decimal("100000.00"),
            capital_gains=Decimal("20000.00"),
            franked_dividends=Decimal("10000.00"),
            foreign_income=Decimal("5000.00"),
            other_income=Decimal("2000.00"),
        )

    def test_each_stream_splits_in_unit_proportion(self):
        allocate_unit_trust_distribution(self.distribution)

        a = BeneficiaryAllocation.objects.get(
            distribution=self.distribution, beneficiary__full_name="A",
        )
        self.assertEqual(a.percentage, Decimal("75.0000"))
        self.assertEqual(a.allocated_capital_gains, Decimal("15000.00"))
        self.assertEqual(a.allocated_franked_dividends, Decimal("7500.00"))
        self.assertEqual(a.allocated_foreign_income, Decimal("3750.00"))
        self.assertEqual(a.allocated_other_income, Decimal("1500.00"))
        self.assertEqual(a.total_distribution, Decimal("75000.00"))
        self.assertIsNone(a.fixed_amount)

    def test_allocations_tie_to_the_stream_total(self):
        allocate_unit_trust_distribution(self.distribution)
        rows = BeneficiaryAllocation.objects.filter(distribution=self.distribution)

        self.assertEqual(
            sum(row.allocated_capital_gains for row in rows), Decimal("20000.00"),
        )
        self.assertEqual(
            sum(row.allocated_franked_dividends for row in rows), Decimal("10000.00"),
        )
        self.assertEqual(
            sum(row.allocated_foreign_income for row in rows), Decimal("5000.00"),
        )
        self.assertEqual(
            sum(row.allocated_other_income for row in rows), Decimal("2000.00"),
        )
        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("100000.00"),
        )

    def test_three_way_odd_split_ties_on_every_stream(self):
        # Replace the register with an awkward 1/1/1 split on an odd amount.
        self.entity.officers.all().delete()
        for name in ("X", "Y", "Z"):
            EntityOfficer.objects.create(
                entity=self.entity, full_name=name,
                role="unit_holder", roles=["unit_holder"], units_held=1,
            )
        EntityOfficer.recalculate_unit_percentages(self.entity)

        self.distribution.distributable_income = Decimal("100000.01")
        self.distribution.capital_gains = Decimal("10.03")
        self.distribution.franked_dividends = Decimal("0.01")
        self.distribution.foreign_income = Decimal("1.00")
        self.distribution.other_income = Decimal("0.02")
        self.distribution.save()

        allocate_unit_trust_distribution(self.distribution)
        rows = BeneficiaryAllocation.objects.filter(distribution=self.distribution)

        self.assertEqual(rows.count(), 3)
        self.assertEqual(
            sum(row.allocated_capital_gains for row in rows), Decimal("10.03"),
        )
        self.assertEqual(
            sum(row.allocated_franked_dividends for row in rows), Decimal("0.01"),
        )
        self.assertEqual(
            sum(row.allocated_foreign_income for row in rows), Decimal("1.00"),
        )
        self.assertEqual(
            sum(row.allocated_other_income for row in rows), Decimal("0.02"),
        )
        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("100000.01"),
        )

    def test_rerunning_refreshes_rather_than_duplicates(self):
        allocate_unit_trust_distribution(self.distribution)
        first_a = BeneficiaryAllocation.objects.get(
            distribution=self.distribution, beneficiary__full_name="A",
        )
        allocate_unit_trust_distribution(self.distribution)
        second_a = BeneficiaryAllocation.objects.get(
            distribution=self.distribution, beneficiary__full_name="A",
        )

        self.assertEqual(
            BeneficiaryAllocation.objects.filter(distribution=self.distribution).count(),
            2,
        )
        self.assertEqual(first_a.pk, second_a.pk)
        self.assertEqual(first_a.allocated_capital_gains, second_a.allocated_capital_gains)
        self.assertEqual(first_a.total_distribution, second_a.total_distribution)

    def test_loss_distributes_correctly(self):
        self.distribution.distributable_income = Decimal("-100000.00")
        self.distribution.capital_gains = Decimal("-20000.00")
        self.distribution.save()

        allocate_unit_trust_distribution(self.distribution)
        rows = BeneficiaryAllocation.objects.filter(distribution=self.distribution)

        a = rows.get(beneficiary__full_name="A")
        b = rows.get(beneficiary__full_name="B")
        self.assertEqual(a.total_distribution, Decimal("-75000.00"))
        self.assertEqual(b.total_distribution, Decimal("-25000.00"))
        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("-100000.00"),
        )
        self.assertEqual(
            sum(row.allocated_capital_gains for row in rows), Decimal("-20000.00"),
        )

    def test_empty_register_is_refused(self):
        EntityOfficer.objects.filter(entity=self.entity).update(units_held=None)
        with self.assertRaises(ValueError):
            allocate_unit_trust_distribution(self.distribution)

        self.assertEqual(
            BeneficiaryAllocation.objects.filter(distribution=self.distribution).count(),
            0,
        )
