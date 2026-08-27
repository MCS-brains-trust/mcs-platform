"""A sign flip is classified on the current year, not the prior year.

``_reclassify_sign_flips`` tested both years -- ``cy > 0 or py > 0`` for the
GST/refund rule, ``cy < 0 or py < 0`` for the overdraft rule -- so a single
prior-year balance on the opposite side dragged the current year into the wrong
section and displayed it with a negative sign.

Dr Services Family Trust FY2026 hit this. FY2025's GST control account was a
6,102.20 debit (an asset, exactly as the signed FY2025 statements present it
under "Current Tax Assets"); FY2026's is a 12,767.02 credit. The balance sheet
rendered:

    Current Assets                        2026        2025
       GST payable control account  -12,767.02    6,102.20

a negative asset, when FY2026 owes the ATO 12,767.02.

The firm's own convention is visible in The Cleary Family Trust FY2025, where a
beneficiary loan that flipped sides is presented under Receivables with the
prior year shown as (14,774) -- the current year decides the section and the
comparative follows it, carrying its own sign. ``_net_beneficiary_accounts``
already routes on ``net_cy`` alone for that reason.

Where the current year is nil the prior year decides, so a closed account still
lands somewhere sensible rather than defaulting to its natural section.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from core.fs_template_service import _reclassify_sign_flips


def _sections(**kw):
    base = {"current_assets": [], "current_liabilities": [],
            "noncurrent_assets": [], "noncurrent_liabilities": [], "equity": []}
    base.update(kw)
    return base


def _item(name, cy, py):
    return {"account_code": "3380", "account_name": name,
            "cy_amount": Decimal(cy), "py_amount": Decimal(py)}


def _names(rows):
    return [r["account_name"] for r in rows]


class GstSignFlipTests(SimpleTestCase):
    def test_credit_this_year_stays_a_liability_though_last_year_was_an_asset(self):
        """Dr Services FY2026: cy credit 12,767.02, py debit 6,102.20."""
        s = _sections(current_liabilities=[
            _item("GST payable control account", "-12767.02", "6102.20")])
        _reclassify_sign_flips(s)
        self.assertEqual(_names(s["current_liabilities"]),
                         ["GST payable control account"],
                         f"moved to assets: {_names(s['current_assets'])}")
        self.assertEqual(s["current_liabilities"][0]["cy_amount"],
                         Decimal("-12767.02"))
        self.assertEqual(s["current_liabilities"][0]["py_amount"],
                         Decimal("6102.20"),
                         "the comparative must follow with its own sign")

    def test_debit_this_year_still_becomes_an_asset(self):
        """The rule's real purpose is unaffected: a refund owed is an asset."""
        s = _sections(current_liabilities=[
            _item("GST payable control account", "6102.20", "-2665.00")])
        _reclassify_sign_flips(s)
        self.assertEqual(_names(s["current_assets"]),
                         ["GST payable control account"])
        self.assertEqual(s["current_assets"][0]["cy_amount"], Decimal("6102.20"))
        self.assertEqual(s["current_liabilities"], [])

    def test_nil_current_year_falls_back_to_the_prior_year(self):
        s = _sections(current_liabilities=[
            _item("GST payable control account", "0", "6102.20")])
        _reclassify_sign_flips(s)
        self.assertEqual(_names(s["current_assets"]),
                         ["GST payable control account"])


class BankOverdraftSignFlipTests(SimpleTestCase):
    @staticmethod
    def _bank(cy, py):
        return {"account_code": "2000", "account_name": "Cash at bank",
                "cy_amount": Decimal(cy), "py_amount": Decimal(py)}

    def test_positive_this_year_stays_an_asset_though_last_year_overdrawn(self):
        s = _sections(current_assets=[self._bank("134421.17", "-5000.00")])
        _reclassify_sign_flips(s)
        self.assertEqual(_names(s["current_assets"]), ["Cash at bank"],
                         f"moved to liabilities: {_names(s['current_liabilities'])}")
        self.assertEqual(s["current_assets"][0]["py_amount"], Decimal("-5000.00"))

    def test_overdrawn_this_year_still_becomes_a_liability(self):
        s = _sections(current_assets=[self._bank("-67360.00", "12000.00")])
        _reclassify_sign_flips(s)
        self.assertEqual(_names(s["current_liabilities"]), ["Cash at bank"])
        self.assertEqual(s["current_liabilities"][0]["cy_amount"],
                         Decimal("-67360.00"),
                         "credit_normal display negates this to a positive")
        self.assertEqual(s["current_assets"], [])
