"""A sign flip is classified per column, and never renders a negative asset.

``_reclassify_sign_flips`` originally tested both years together -- ``cy > 0 or
py > 0`` for the GST/refund rule, ``cy < 0 or py < 0`` for the overdraft rule --
so a single prior-year balance on the opposite side dragged the current year
into the wrong section and displayed it with a negative sign.

Dr Services Family Trust FY2026 hit this. FY2025's GST control account was a
6,102.20 debit; FY2026's is a 12,767.02 credit. The balance sheet rendered:

    Current Assets                        2026        2025
       GST payable control account  -12,767.02    6,102.20

a negative asset, when FY2026 owes the ATO 12,767.02. That must never come
back, and every test below still holds the line on it.

The fix was then "the current year decides the section; the comparative follows
it, carrying its own sign" -- one row, negative comparative. That is corrected
here to the HandiLedger presentation, which classifies EACH COLUMN on its own
balance and shows an account that crossed sides in BOTH sections with a dash in
the year it does not apply to. DJLH Properties Pty Ltd's FY2024 HandiLedger
report does exactly that with ``Loan - Li Penman Property Family Trust``::

    Non-Current Assets → Receivables            3,331,159        --
    Non-Current Liabilities → Financial Liab.          --    66,421

See core/tests_bs_side_follows_sign.py, which is the primary specification for
that rule and records what the old behaviour did to DJLH's subtotals.

**The trust convention is deliberately NOT this one.** A netted beneficiary
loan is routed by ``_net_beneficiary_accounts`` on ``net_cy`` alone -- one row,
the comparative keeping its own sign -- which is the presentation The Cleary
Family Trust FY2025 uses. Those rows are exempt, and
``TrustBeneficiaryNettingIsUntouchedTests`` guards it.
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
    def test_a_credit_this_year_is_never_rendered_as_a_negative_asset(self):
        """Dr Services FY2026: cy credit 12,767.02, py debit 6,102.20.

        The original defect. FY2026 owes the ATO, so the current year must sit
        in liabilities as a liability -- never in assets carrying a minus.
        """
        s = _sections(current_liabilities=[
            _item("GST payable control account", "-12767.02", "6102.20")])
        _reclassify_sign_flips(s)

        liab = [r for r in s["current_liabilities"]
                if r["account_name"] == "GST payable control account"]
        self.assertEqual(len(liab), 1,
                         f"moved to assets: {_names(s['current_assets'])}")
        self.assertEqual(liab[0]["cy_amount"], Decimal("-12767.02"))
        # No negative may appear in current assets, in either column.
        for row in s["current_assets"]:
            self.assertGreaterEqual(row["cy_amount"], Decimal("0"))
            self.assertGreaterEqual(row["py_amount"], Decimal("0"))

    def test_the_prior_year_debit_is_shown_as_the_asset_it_was(self):
        """Per column: FY2025 was a refund owed, so it belongs in assets."""
        s = _sections(current_liabilities=[
            _item("GST payable control account", "-12767.02", "6102.20")])
        _reclassify_sign_flips(s)

        asset = [r for r in s["current_assets"]
                 if r["account_name"] == "GST payable control account"]
        self.assertEqual(len(asset), 1)
        self.assertEqual(asset[0]["py_amount"], Decimal("6102.20"))
        self.assertEqual(asset[0]["cy_amount"], Decimal("0"),
                         "the current-year credit must not appear in assets")

    def test_debit_this_year_still_becomes_an_asset(self):
        """The rule's real purpose is unaffected: a refund owed is an asset."""
        s = _sections(current_liabilities=[
            _item("GST payable control account", "6102.20", "-2665.00")])
        _reclassify_sign_flips(s)
        asset = [r for r in s["current_assets"]
                 if r["account_name"] == "GST payable control account"]
        self.assertEqual(len(asset), 1)
        self.assertEqual(asset[0]["cy_amount"], Decimal("6102.20"))
        # The prior-year credit stays a liability rather than a negative asset.
        self.assertEqual(asset[0]["py_amount"], Decimal("0"))
        liab = [r for r in s["current_liabilities"]
                if r["account_name"] == "GST payable control account"]
        self.assertEqual(len(liab), 1)
        self.assertEqual(liab[0]["py_amount"], Decimal("-2665.00"))

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

    def test_positive_this_year_stays_an_asset(self):
        s = _sections(current_assets=[self._bank("134421.17", "-5000.00")])
        _reclassify_sign_flips(s)
        asset = [r for r in s["current_assets"] if r["account_name"] == "Cash at bank"]
        self.assertEqual(len(asset), 1,
                         f"moved to liabilities: {_names(s['current_liabilities'])}")
        self.assertEqual(asset[0]["cy_amount"], Decimal("134421.17"))
        # Last year's overdraft is shown as the overdraft it was, not as a
        # negative bank asset.
        self.assertEqual(asset[0]["py_amount"], Decimal("0"))
        od = [r for r in s["current_liabilities"] if r["account_name"] == "Cash at bank"]
        self.assertEqual(len(od), 1)
        self.assertEqual(od[0]["py_amount"], Decimal("-5000.00"))

    def test_overdrawn_this_year_still_becomes_a_liability(self):
        s = _sections(current_assets=[self._bank("-67360.00", "12000.00")])
        _reclassify_sign_flips(s)
        od = [r for r in s["current_liabilities"] if r["account_name"] == "Cash at bank"]
        self.assertEqual(len(od), 1)
        self.assertEqual(od[0]["cy_amount"], Decimal("-67360.00"),
                         "credit_normal display negates this to a positive")
        # No negative may appear in current assets, in either column.
        for row in s["current_assets"]:
            self.assertGreaterEqual(row["cy_amount"], Decimal("0"))
            self.assertGreaterEqual(row["py_amount"], Decimal("0"))
