"""An expense line must not pick up a balance sheet note reference.

Dr Services Family Trust FY2026 printed:

    Home office                          3        1,722        —

against a Note 3 that is Property, Plant and Equipment. There is no home
office note in the pack at all.

sections["expenses"] was classified with the balance sheet classifier, whose
PP&E keyword list contains "office" -- so any occupancy expense naming an
office ("Home office", "Office rent", "Office cleaning") claimed the PP&E note.
Only the related-party rules can meaningfully apply to an expense; the
receivables, inventories and PP&E notes describe balance sheet items.
"""
from django.test import SimpleTestCase

from core.fs_template_service import (
    _classify_balance_sheet_note, _classify_expense_note,
)


def item(name, code="1750"):
    return {"account_code": code, "account_name": name, "standard_code": None}


class ExpenseNoteRefTests(SimpleTestCase):
    def test_home_office_expense_gets_no_note(self):
        """The defect this test exists for."""
        self.assertIsNone(_classify_expense_note(item("Home office")))

    def test_other_office_expenses_get_no_note(self):
        for name in ("Office rent", "Office cleaning", "Computer consumables"):
            with self.subTest(name=name):
                self.assertIsNone(_classify_expense_note(item(name)))

    def test_management_fees_still_reach_the_related_party_note(self):
        self.assertEqual(
            _classify_expense_note(item("Management fees paid")), "related_party"
        )

    def test_director_loan_interest_still_reaches_the_related_party_note(self):
        self.assertEqual(
            _classify_expense_note(item("Interest - director loan")), "related_party"
        )

    def test_motor_vehicle_expense_gets_no_note(self):
        """'M/V car - Other' is an expense; the vehicle asset is the noted item."""
        self.assertIsNone(_classify_expense_note(item("M/V car - Other")))


class BalanceSheetClassifierUnchangedTests(SimpleTestCase):
    """The balance sheet classifier must keep every rule it had."""

    def test_motor_vehicles_at_cost_is_still_ppe(self):
        self.assertEqual(
            _classify_balance_sheet_note(item("Motor vehicles (cost)", "2890")), "ppe"
        )

    def test_office_equipment_is_still_ppe(self):
        self.assertEqual(
            _classify_balance_sheet_note(item("Office equipment", "2880")), "ppe"
        )

    def test_trade_debtors_is_still_receivables(self):
        self.assertEqual(
            _classify_balance_sheet_note(item("Trade debtors", "2101")), "receivables"
        )

    def test_accumulated_depreciation_still_gets_no_note(self):
        self.assertIsNone(
            _classify_balance_sheet_note(item("Less: Accumulated depreciation", "2895"))
        )
