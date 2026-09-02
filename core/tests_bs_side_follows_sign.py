"""An account is presented on the side its balance dictates, per column.

HandiLedger is the specification. DJLH Properties Pty Ltd's FY2024 HandiLedger
report shows ``Loan - Li Penman Property Family Trust`` in BOTH sections:

    Non-Current Assets → Receivables            3,331,159   --
    Non-Current Liabilities → Financial Liab.          --   66,421

Each column is classified on its own balance, and an account that crossed sides
between the years appears in both places with a dash in the year it does not
apply to.

``_reclassify_sign_flips`` instead let "the current year decide the section; the
comparative follows it, keeping its own sign". On DJLH FY2025 that put the GST
control account's 2024 credit of 316,467 into Other Current Assets as
(316,467), producing a **negative Total Current Assets of (315,650)** -- and
every subtotal above Net Assets disagreed with HandiLedger:

    2024                    HandiLedger     StatementHub
    Total Current Assets            817        (315,650)
    Total Non-Current Assets  4,337,787       1,006,628
    Total Assets              4,338,604         690,978
    Total Current Liabilities   317,758           1,291
    Total Non-Current Liab.   3,827,292         496,133
    Net Assets                  193,554         193,554   <- only this agreed

The errors cancelled in the bottom line, which is why it went unnoticed.

Sign convention: current/non-current assets are debit-normal (positive =
asset); liabilities are credit-normal (negative = liability, so a positive
amount there means the balance is really a debit).
"""
from decimal import Decimal

from django.test import SimpleTestCase

from core.fs_template_service import _reclassify_sign_flips

D = Decimal


class TrustBeneficiaryNettingIsUntouchedTests(SimpleTestCase):
    """The trust convention is a different rule, and must survive this one.

    ``_net_beneficiary_accounts`` runs BEFORE this function and routes each
    officer's netted position on ``net_cy`` alone -- one row, the comparative
    following with its own sign. That is the presentation The Cleary Family
    Trust FY2025 uses for a beneficiary loan that changed sides.

    Its rows are labelled "Beneficiary loan: <name>", so the "loan" keyword
    here would otherwise split them in two and silently override it.
    """

    def test_a_netted_beneficiary_loan_is_not_split(self):
        sections = {
            "current_assets": [],
            "current_liabilities": [
                {"account_code": "BEN_1a2b3c4d",
                 "account_name": "Beneficiary loan: Ronnie Davidov",
                 "cy_amount": Decimal("-40000.00"),
                 "py_amount": Decimal("14774.00"),
                 "standard_code": None},
            ],
        }
        _reclassify_sign_flips(sections)
        self.assertEqual(len(sections["current_liabilities"]), 1)
        self.assertEqual(
            sections["current_assets"], [],
            "the trust's netted beneficiary loan was split across sections",
        )
        row = sections["current_liabilities"][0]
        self.assertEqual(row["cy_amount"], Decimal("-40000.00"))
        self.assertEqual(
            row["py_amount"], Decimal("14774.00"),
            "the comparative must keep its own sign, per the trust convention",
        )

    def test_an_ordinary_company_loan_is_still_split(self):
        """The exemption is for BEN_ rows only, not for loans generally."""
        sections = {
            "noncurrent_assets": [],
            "noncurrent_liabilities": [
                {"account_code": "3565",
                 "account_name": "Loan - Li Penman Property Family Trust",
                 "cy_amount": Decimal("3331159.00"),
                 "py_amount": Decimal("-66421.00"),
                 "standard_code": None},
            ],
        }
        _reclassify_sign_flips(sections)
        self.assertEqual(len(sections["noncurrent_assets"]), 1)
        self.assertEqual(len(sections["noncurrent_liabilities"]), 1)


def _item(name, cy, py, code="", standard_code=None):
    return {
        "account_code": code,
        "account_name": name,
        "cy_amount": D(cy),
        "py_amount": D(py),
        "standard_code": standard_code,
    }


def _find(sections, section, name):
    return [i for i in sections.get(section, [])
            if i["account_name"] == name]


class GstControlAccountTests(SimpleTestCase):
    """DJLH FY2025: debit 9,869 this year, credit 316,467 last year."""

    def setUp(self):
        self.sections = {
            "current_assets": [_item("Cash at bank", "987.23", "805.27")],
            "current_liabilities": [
                _item("GST payable control account", "9869.00", "-316467.12"),
            ],
        }
        _reclassify_sign_flips(self.sections)

    def test_the_current_year_debit_is_presented_as_an_asset(self):
        rows = _find(self.sections, "current_assets",
                     "GST payable control account")
        self.assertEqual(len(rows), 1, "the debit balance is not shown as an asset")
        self.assertEqual(rows[0]["cy_amount"], D("9869.00"))

    def test_the_comparative_credit_stays_a_liability(self):
        rows = _find(self.sections, "current_liabilities",
                     "GST payable control account")
        self.assertEqual(
            len(rows), 1,
            "the prior-year credit was dragged into assets with the current year",
        )
        self.assertEqual(rows[0]["py_amount"], D("-316467.12"))

    def test_neither_copy_carries_the_other_years_figure(self):
        """The dash: each copy shows nil in the column it does not apply to."""
        asset = _find(self.sections, "current_assets",
                      "GST payable control account")[0]
        liab = _find(self.sections, "current_liabilities",
                     "GST payable control account")[0]
        self.assertEqual(asset["py_amount"], D("0"))
        self.assertEqual(liab["cy_amount"], D("0"))

    def test_total_current_assets_is_not_negative(self):
        """(315,650) was the visible symptom."""
        total = sum(i["cy_amount"] for i in self.sections["current_assets"])
        prior = sum(i["py_amount"] for i in self.sections["current_assets"])
        self.assertEqual(total, D("10856.23"))
        self.assertEqual(
            prior, D("805.27"),
            "the comparative Total Current Assets is still wrong",
        )


class NonCurrentLoanTests(SimpleTestCase):
    """A debit-balance loan is a receivable, not a negative payable."""

    def test_a_debit_loan_moves_to_non_current_assets(self):
        sections = {
            "noncurrent_assets": [
                _item("7 Tinarra Court Kilsyth", "1006627.56", "1006627.56"),
            ],
            "noncurrent_liabilities": [
                _item("Loan - Li Penman Property Family Trust",
                      "2752809.00", "3331158.98"),
                _item("Loan - Jim's Group", "-1467400.00", "-1467400.00"),
            ],
        }
        _reclassify_sign_flips(sections)

        moved = _find(sections, "noncurrent_assets",
                      "Loan - Li Penman Property Family Trust")
        self.assertEqual(
            len(moved), 1,
            "a debit-balance loan stayed in liabilities as a negative",
        )
        self.assertEqual(moved[0]["cy_amount"], D("2752809.00"))
        self.assertEqual(moved[0]["py_amount"], D("3331158.98"))
        self.assertEqual(
            _find(sections, "noncurrent_liabilities",
                  "Loan - Li Penman Property Family Trust"),
            [],
            "both years are debits, so nothing should remain in liabilities",
        )

    def test_a_loan_that_crossed_sides_appears_in_both(self):
        """The HandiLedger FY2024 case, exactly: 3,331,159 / -- and -- / 66,421."""
        sections = {
            "noncurrent_assets": [],
            "noncurrent_liabilities": [
                _item("Loan - Li Penman Property Family Trust",
                      "3331159.00", "-66421.00"),
            ],
        }
        _reclassify_sign_flips(sections)

        asset = _find(sections, "noncurrent_assets",
                      "Loan - Li Penman Property Family Trust")
        liab = _find(sections, "noncurrent_liabilities",
                     "Loan - Li Penman Property Family Trust")
        self.assertEqual(len(asset), 1)
        self.assertEqual(len(liab), 1)
        self.assertEqual(asset[0]["cy_amount"], D("3331159.00"))
        self.assertEqual(asset[0]["py_amount"], D("0"))
        self.assertEqual(liab[0]["cy_amount"], D("0"))
        self.assertEqual(liab[0]["py_amount"], D("-66421.00"))

    def test_an_ordinary_loan_is_untouched(self):
        """Regression guard: credit balances stay where they are."""
        sections = {
            "noncurrent_assets": [],
            "noncurrent_liabilities": [
                _item("Loan - Jim's Group", "-1467400.00", "-1467400.00"),
                _item("Bank loans", "-676911.78", "-689568.55"),
            ],
        }
        _reclassify_sign_flips(sections)
        self.assertEqual(len(sections["noncurrent_liabilities"]), 2)
        self.assertEqual(sections["noncurrent_assets"], [])


class ExistingBehaviourTests(SimpleTestCase):
    """The overdraft rule already worked and must keep working."""

    def test_a_bank_overdraft_still_moves_to_current_liabilities(self):
        sections = {
            "current_assets": [_item("ANZ #11733", "-67360.00", "-50000.00")],
            "current_liabilities": [],
        }
        _reclassify_sign_flips(sections)
        self.assertEqual(sections["current_assets"], [])
        moved = _find(sections, "current_liabilities", "ANZ #11733")
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["cy_amount"], D("-67360.00"))

    def test_a_bank_account_that_went_overdrawn_appears_in_both(self):
        sections = {
            "current_assets": [_item("ANZ #11733", "-67360.00", "12000.00")],
            "current_liabilities": [],
        }
        _reclassify_sign_flips(sections)
        self.assertEqual(len(_find(sections, "current_assets", "ANZ #11733")), 1)
        self.assertEqual(
            len(_find(sections, "current_liabilities", "ANZ #11733")), 1)
        self.assertEqual(
            _find(sections, "current_assets", "ANZ #11733")[0]["cy_amount"], D("0"))
        self.assertEqual(
            _find(sections, "current_assets", "ANZ #11733")[0]["py_amount"],
            D("12000.00"))
