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
        # A 75/25 register's percentages are the clean fractions 3/4 and
        # 1/4: independent per-row quantizing happens to tie out for
        # almost any total on a split that clean, so this test would not
        # actually catch a naive ``total * percentage`` implementation
        # (see test_three_way_odd_split_ties_on_every_stream, and FIX 5 of
        # the round-1 review). Use a 1/1/1 register (33.34/33.33/33.33,
        # not a clean fraction) and figures verified to make the naive
        # per-row-quantize implementation land 1 cent short on every
        # stream, so this test genuinely bites.
        self.entity.officers.all().delete()
        for name in ("X", "Y", "Z"):
            EntityOfficer.objects.create(
                entity=self.entity, full_name=name,
                role="unit_holder", roles=["unit_holder"], units_held=1,
            )
        EntityOfficer.recalculate_unit_percentages(self.entity)

        self.distribution.distributable_income = Decimal("100.01")
        self.distribution.capital_gains = Decimal("10.03")
        self.distribution.franked_dividends = Decimal("1.01")
        self.distribution.foreign_income = Decimal("0.01")
        self.distribution.other_income = Decimal("3.02")
        self.distribution.save()

        allocate_unit_trust_distribution(self.distribution)
        rows = BeneficiaryAllocation.objects.filter(distribution=self.distribution)

        self.assertEqual(
            sum(row.allocated_capital_gains for row in rows), Decimal("10.03"),
        )
        self.assertEqual(
            sum(row.allocated_franked_dividends for row in rows), Decimal("1.01"),
        )
        self.assertEqual(
            sum(row.allocated_foreign_income for row in rows), Decimal("0.01"),
        )
        self.assertEqual(
            sum(row.allocated_other_income for row in rows), Decimal("3.02"),
        )
        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("100.01"),
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
        # A 75/25 loss on this fixture would tie under a naive per-row
        # quantize just as readily as a profit does (see FIX 5 of the
        # round-1 review) -- use a 1/1/1 register and a non-divisible
        # loss (-10.01) so the assertion genuinely exercises the
        # largest-remainder arithmetic, not just its sign handling.
        self.entity.officers.all().delete()
        for name in ("X", "Y", "Z"):
            EntityOfficer.objects.create(
                entity=self.entity, full_name=name,
                role="unit_holder", roles=["unit_holder"], units_held=1,
            )
        EntityOfficer.recalculate_unit_percentages(self.entity)

        self.distribution.distributable_income = Decimal("-10.01")
        self.distribution.capital_gains = Decimal("-10.01")
        self.distribution.franked_dividends = Decimal("0")
        self.distribution.foreign_income = Decimal("0")
        self.distribution.other_income = Decimal("0")
        self.distribution.save()

        allocate_unit_trust_distribution(self.distribution)
        rows = BeneficiaryAllocation.objects.filter(distribution=self.distribution)

        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("-10.01"),
        )
        self.assertEqual(
            sum(row.allocated_capital_gains for row in rows), Decimal("-10.01"),
        )
        # Every row is negative -- a loss is shared by all holders, not
        # netted against one.
        for row in rows:
            self.assertLess(row.total_distribution, Decimal("0"))
            self.assertLess(row.allocated_capital_gains, Decimal("0"))

    def test_empty_register_is_refused(self):
        # Allocate first against a real, non-empty register, THEN empty it
        # and re-run -- with no prior rows, "still zero rows afterward" is
        # vacuously true and cannot distinguish "refused" from "never
        # tried". The real assertion is that the PRE-EXISTING rows survive
        # untouched (raising happens before any write in this run, not
        # mid-way through zeroing/rewriting them).
        allocate_unit_trust_distribution(self.distribution)
        before = {
            row.beneficiary_id: (row.total_distribution, row.allocated_capital_gains)
            for row in BeneficiaryAllocation.objects.filter(distribution=self.distribution)
        }
        self.assertEqual(len(before), 2)

        EntityOfficer.objects.filter(entity=self.entity).update(units_held=None)
        with self.assertRaises(ValueError):
            allocate_unit_trust_distribution(self.distribution)

        after = {
            row.beneficiary_id: (row.total_distribution, row.allocated_capital_gains)
            for row in BeneficiaryAllocation.objects.filter(distribution=self.distribution)
        }
        self.assertEqual(before, after)

    def test_ceased_holder_row_is_zeroed_not_deleted_and_sums_still_tie(self):
        """FIX 2 repro/regression: a stale row must not double-count."""
        allocate_unit_trust_distribution(self.distribution)
        b = EntityOfficer.objects.get(entity=self.entity, full_name="B")
        b.units_held = None
        b.save(update_fields=["units_held"])

        allocate_unit_trust_distribution(self.distribution)

        rows = BeneficiaryAllocation.objects.filter(distribution=self.distribution)
        self.assertEqual(rows.count(), 2, "the departed holder's row must be kept, not deleted")

        b_row = rows.get(beneficiary__full_name="B")
        self.assertEqual(b_row.total_distribution, Decimal("0.00"))
        self.assertEqual(b_row.allocated_capital_gains, Decimal("0.00"))
        self.assertEqual(b_row.allocated_franked_dividends, Decimal("0.00"))
        self.assertEqual(b_row.allocated_foreign_income, Decimal("0.00"))
        self.assertEqual(b_row.allocated_other_income, Decimal("0.00"))

        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("100000.00"),
        )
        self.assertEqual(
            sum(row.allocated_capital_gains for row in rows), Decimal("20000.00"),
        )

    def test_ceased_row_keeps_section_100a_audit_fields(self):
        """FIX 2: zeroing must not erase the Section 100A audit trail."""
        allocate_unit_trust_distribution(self.distribution)
        b_row = BeneficiaryAllocation.objects.get(
            distribution=self.distribution, beneficiary__full_name="B",
        )
        b_row.section_100a_flag = True
        b_row.section_100a_notes = "Reimbursement arrangement suspected."
        b_row.save(update_fields=["section_100a_flag", "section_100a_notes"])

        b = EntityOfficer.objects.get(pk=b_row.beneficiary_id)
        b.units_held = None
        b.save(update_fields=["units_held"])
        allocate_unit_trust_distribution(self.distribution)

        b_row.refresh_from_db()
        self.assertTrue(b_row.section_100a_flag)
        self.assertEqual(b_row.section_100a_notes, "Reimbursement arrangement suspected.")
        self.assertEqual(b_row.total_distribution, Decimal("0.00"))

    def test_future_ceased_holder_stays_in_the_split(self):
        """FIX 3 repro/regression: a future date_ceased must not exclude a holder
        from the split while Entity.total_units still counts their units."""
        from datetime import timedelta
        from django.utils import timezone

        self.entity.officers.all().delete()
        a = EntityOfficer.objects.create(
            entity=self.entity, full_name="A",
            role="unit_holder", roles=["unit_holder"], units_held=50,
        )
        b = EntityOfficer.objects.create(
            entity=self.entity, full_name="B",
            role="unit_holder", roles=["unit_holder"], units_held=50,
            date_ceased=timezone.now().date() + timedelta(days=30),
        )
        EntityOfficer.recalculate_unit_percentages(self.entity)

        self.assertEqual(self.entity.total_units, 100, "B is still active (future ceased)")

        allocate_unit_trust_distribution(self.distribution)
        rows = BeneficiaryAllocation.objects.filter(distribution=self.distribution)
        self.assertEqual(rows.count(), 2, "a future-ceased holder must still receive a row")

        a_row = rows.get(beneficiary=a)
        b_row = rows.get(beneficiary=b)
        self.assertEqual(a_row.total_distribution, Decimal("50000.00"))
        self.assertEqual(b_row.total_distribution, Decimal("50000.00"))

    def test_percentage_sums_to_exactly_100_and_marks_fully_allocated(self):
        """FIX 4 repro/regression: percentage must come from the stored,
        largest-remainder distribution_percentage, not the live per-row
        unit_percentage property, so a 1/1/1 register still sums to 100.00."""
        self.entity.officers.all().delete()
        for name in ("X", "Y", "Z"):
            EntityOfficer.objects.create(
                entity=self.entity, full_name=name,
                role="unit_holder", roles=["unit_holder"], units_held=1,
            )
        EntityOfficer.recalculate_unit_percentages(self.entity)

        allocate_unit_trust_distribution(self.distribution)
        rows = BeneficiaryAllocation.objects.filter(distribution=self.distribution)
        total_pct = sum(row.percentage for row in rows)
        self.assertEqual(total_pct, Decimal("100.00"))

        self.distribution.refresh_from_db()
        self.assertTrue(self.distribution.is_fully_allocated)
