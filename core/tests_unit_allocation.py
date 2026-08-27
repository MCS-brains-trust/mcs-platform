"""Unit allocation must tie to the cent, including the awkward splits."""
from decimal import Decimal

from django.test import SimpleTestCase

from core.unit_allocation import allocate_by_units


class AllocateByUnitsTests(SimpleTestCase):
    def test_even_split(self):
        result = allocate_by_units(Decimal("1000.00"), [("a", 50), ("b", 50)])
        self.assertEqual(result, {"a": Decimal("500.00"), "b": Decimal("500.00")})

    def test_thirds_tie_to_the_cent(self):
        result = allocate_by_units(
            Decimal("100000.00"), [("a", 1), ("b", 1), ("c", 1)]
        )
        self.assertEqual(sum(result.values()), Decimal("100000.00"))
        self.assertEqual(sorted(result.values()), [
            Decimal("33333.33"), Decimal("33333.33"), Decimal("33333.34"),
        ])

    def test_uneven_holdings(self):
        result = allocate_by_units(Decimal("900.00"), [("a", 2), ("b", 1)])
        self.assertEqual(result, {"a": Decimal("600.00"), "b": Decimal("300.00")})

    def test_a_loss_allocates_too(self):
        result = allocate_by_units(Decimal("-1000.00"), [("a", 50), ("b", 50)])
        self.assertEqual(sum(result.values()), Decimal("-1000.00"))

    def test_negative_thirds_tie_to_the_cent(self):
        result = allocate_by_units(Decimal("-100.00"), [("a", 1), ("b", 1), ("c", 1)])
        self.assertEqual(sum(result.values()), Decimal("-100.00"))

    def test_zero_total_allocates_zero(self):
        result = allocate_by_units(Decimal("0.00"), [("a", 1), ("b", 1)])
        self.assertEqual(sum(result.values()), Decimal("0.00"))

    def test_no_units_on_issue_is_refused(self):
        with self.assertRaises(ValueError):
            allocate_by_units(Decimal("100.00"), [("a", 0)])

    def test_empty_register_is_refused(self):
        with self.assertRaises(ValueError):
            allocate_by_units(Decimal("100.00"), [])

    # --- Additional coverage beyond the brief ---

    def test_one_cent_across_two_holders_sums_and_does_not_lose_the_cent(self):
        result = allocate_by_units(Decimal("0.01"), [("a", 1), ("b", 1)])
        self.assertEqual(sum(result.values()), Decimal("0.01"))
        self.assertEqual(sorted(result.values()), [Decimal("0.00"), Decimal("0.01")])

    def test_large_amount_no_precision_loss(self):
        result = allocate_by_units(
            Decimal("9999999.99"), [("a", 1), ("b", 1), ("c", 1)]
        )
        self.assertEqual(sum(result.values()), Decimal("9999999.99"))

    def test_dominant_holder_leaves_negligible_holder_at_zero_but_ties_out(self):
        result = allocate_by_units(Decimal("100.00"), [("a", 999999), ("b", 1)])
        self.assertEqual(result["b"], Decimal("0.00"))
        self.assertEqual(sum(result.values()), Decimal("100.00"))

    def test_result_is_deterministic_across_repeated_calls(self):
        total = Decimal("100000.00")
        holdings = [("a", 1), ("b", 1), ("c", 1)]
        first = allocate_by_units(total, holdings)
        second = allocate_by_units(total, holdings)
        self.assertEqual(first, second)
