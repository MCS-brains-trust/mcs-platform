"""A refund reduces its side of the BAS; it does not add to the other one.

core/bas_utils.py inferred direction from the account's section and discarded
the actual sign — ``gross = abs(txn.amount)``. That is right for an ordinary
expense, where money out on an expense account is a purchase. It is wrong for a
contra-movement: a customer refund is money OUT on a revenue account, and
abs() turned it into another sale.

Found live on the June 2026 quarter for a real partnership. Two customer
refunds totalling 1,872.96 were coded to 630 Sales. Instead of G1 falling by
1,872.96 it rose by 1,872.96 — an error of twice the refund — and 1A came out
340.53 too high. The trial balance had it right the whole time; only the BAS
worksheet was wrong, so the two disagreed by 340.57 on a figure about to be
lodged.

The rule these tests pin: the account's SECTION decides which side of the BAS a
line belongs to, the SIGN of the movement decides whether it adds or subtracts,
and the tax code decides the treatment.
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from core.bas_utils import calculate_gst_for_period
from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job, make_txn,
)

D = Decimal

SALES = "0510"          # revenue
EXPENSE = "1800"        # expenses


@override_settings(STORAGES=STORAGES_OVERRIDE)
class ContraMovementTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)          # 2025-07-01 .. 2026-06-30
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _bas(self):
        return calculate_gst_for_period(self.fy)["bas_data"]

    def _sale(self, amount, gst, date_str="2025-08-14"):
        return make_txn(self.job, date_str=date_str, amount=amount,
                        code=SALES, tax_type="GST on Income", gst=gst)

    def _expense(self, amount, gst, date_str="2025-08-20"):
        return make_txn(self.job, date_str=date_str, amount=amount,
                        code=EXPENSE, tax_type="GST on Expenses", gst=gst)

    # ── the defect ──────────────────────────────────────────────────────────

    def test_a_customer_refund_reduces_total_sales(self):
        self._sale("11000.00", "1000.00")
        before = D(str(self._bas()["G1"]))

        self._sale("-1100.00", "100.00", date_str="2025-09-10")

        after = D(str(self._bas()["G1"]))
        self.assertEqual(after, before - D("1100.00"))

    def test_a_customer_refund_reduces_the_gst_you_owe(self):
        self._sale("11000.00", "1000.00")
        before = D(str(self._bas()["1A"]))

        self._sale("-1100.00", "100.00", date_str="2025-09-10")

        after = D(str(self._bas()["1A"]))
        self.assertLess(after, before)
        self.assertEqual(after, (before - D("100.00")).quantize(D("0.01")))

    def test_a_refund_does_not_become_an_input_tax_credit(self):
        """The refund belongs on the sales side. Reporting its GST at 1B would
        net out to the same payable while overstating both turnover and
        purchases — which is what the ATO would query."""
        self._sale("11000.00", "1000.00")
        before = D(str(self._bas()["1B"]))

        self._sale("-1100.00", "100.00", date_str="2025-09-10")

        self.assertEqual(D(str(self._bas()["1B"])), before)

    def test_a_supplier_refund_reduces_purchases_not_sales(self):
        """The same bug in the other direction: money IN on an expense account."""
        self._expense("-5500.00", "500.00")
        g11_before = D(str(self._bas()["G11"]))
        g1_before = D(str(self._bas()["G1"]))

        self._expense("550.00", "50.00", date_str="2025-09-11")

        bas = self._bas()
        self.assertEqual(D(str(bas["G11"])), g11_before - D("550.00"))
        self.assertEqual(D(str(bas["G1"])), g1_before)

    def test_the_live_june_quarter_figures(self):
        """The case as found: 103,635.34 of sales less 1,872.96 of refunds."""
        self._sale("103635.34", "9421.35")
        self._sale("-800.00", "72.73", date_str="2025-09-10")
        self._sale("-1072.96", "97.54", date_str="2025-09-11")

        bas = self._bas()
        self.assertEqual(D(str(bas["G1"])), D("101762.38"))
        self.assertEqual(D(str(bas["1A"])), D("9251.13"))

    # ── the regression that matters most ────────────────────────────────────

    def test_an_ordinary_sale_is_unchanged(self):
        self._sale("11000.00", "1000.00")

        bas = self._bas()
        self.assertEqual(D(str(bas["G1"])), D("11000.00"))
        self.assertEqual(D(str(bas["1A"])), D("1000.00"))

    def test_an_ordinary_expense_is_unchanged(self):
        self._expense("-5500.00", "500.00")

        bas = self._bas()
        self.assertEqual(D(str(bas["G11"])), D("5500.00"))
        self.assertEqual(D(str(bas["1B"])), D("500.00"))

    def test_a_gst_free_refund_stays_out_of_the_taxable_labels(self):
        self._sale("11000.00", "1000.00")
        one_a_before = D(str(self._bas()["1A"]))

        make_txn(self.job, date_str="2025-09-12", amount="-500.00",
                 code=SALES, tax_type="GST Free Income", gst="0")

        bas = self._bas()
        self.assertEqual(D(str(bas["1A"])), one_a_before)
        self.assertEqual(D(str(bas["G1"])), D("11000.00") - D("500.00"))

    def test_refunds_exceeding_sales_produce_a_negative_figure_not_a_positive_one(self):
        """A quarter that refunded more than it sold is unusual but real. The
        figure must go negative rather than silently flip sign."""
        self._sale("1100.00", "100.00")
        self._sale("-3300.00", "300.00", date_str="2025-09-10")

        self.assertEqual(D(str(self._bas()["G1"])), D("-2200.00"))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TrialBalancePathTests(TestCase):
    """The aggregated path, used for entities with no confirmed transactions.

    closing_balance is debit - credit, so revenue normally sits negative. A
    revenue account with a POSITIVE closing balance is a net refund position
    and must reduce G1.
    """

    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        self.fy.status = "draft"
        self.fy.save(update_fields=["status"])
        make_bank_mapping(self.entity)

    def _line(self, code, debit, credit, tax_type=""):
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code,
            account_name=f"Account {code}",
            debit=D(str(debit)), credit=D(str(credit)),
            closing_balance=D(str(debit)) - D(str(credit)),
            source="bank_statement", tax_type=tax_type,
        )

    # Aggregated lines hold NET amounts with the GST in the 3380 control
    # account, so this path grosses up by 11/10 on the way to G1. That is
    # existing intended behaviour; these tests assert the SIGN, and carry the
    # gross-up in their expected figures so a later change to either is visible.

    def test_a_revenue_account_in_credit_adds_to_sales(self):
        self._line(SALES, 0, "11000.00", tax_type="GST on Income")

        self.assertEqual(D(str(calculate_gst_for_period(self.fy)["bas_data"]["G1"])),
                         D("12100.00"))          # 11,000 net grossed up

    def test_a_revenue_account_in_debit_reduces_sales(self):
        self._line(SALES, "1100.00", 0, tax_type="GST on Income")

        self.assertEqual(D(str(calculate_gst_for_period(self.fy)["bas_data"]["G1"])),
                         D("-1210.00"))          # 1,100 net grossed up, negative
