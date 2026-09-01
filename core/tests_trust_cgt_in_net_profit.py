"""Every Income-range account belongs in ``net_profit``, character notwithstanding.

``_calculate_income_streams`` classified each account through a single
``if/elif`` chain: an account whose name contained "capital gain" was routed to
a CGT stream and, because the chain stopped there, never reached
``total_revenue``. Character and P&L accumulation are orthogonal, but the chain
made them mutually exclusive.

HandiLedger is unambiguous. ``601``/``0905`` "Capital gains/Loss" and
``550``/``551`` "Dividends - Franked/Unfranked" all carry ``section=revenue`` in
the master chart for every entity type, and all sit in the ``0-999`` **Income**
range of ``_HL_RANGE_SECTION``. They are ordinary P&L revenue.

Minli Enterprise Unit Trust FY2027 is the live case: a property settlement
credited 744,189.00 to 601 against a 527,945.52 cost base, a 216,243.48 net
gain, with 141.82 of subscriptions the only other movement. The planner
reported ``net_profit`` of -141.82 while the financial statements printed
216,101.66 from the same trial balance.

Recomputing the P&L from the HandiLedger code ranges alone reproduces the
planner exactly for 11 of the 12 trust-years in production; FY2027 is the sole
divergence, and it resolves to the figure the statements already showed.

A second hole closes with it: ``total_revenue`` was driven off
``mapped_line_item.statement_section``, so an Income account with no mapping
contributed to neither revenue nor expenses and vanished from net profit
silently.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.eva_trust_planning import _calculate_income_streams
from core.models import (
    AccountMapping, Client, Entity, FinancialYear, TrialBalanceLine,
)

ZERO = Decimal("0")

# The only other movement in Minli FY2027.
SUBSCRIPTIONS = ("1925", "Subscriptions", Decimal("141.82"))


class TrustCgtInNetProfitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="CGT Client")
        cls.trust = Entity.objects.create(
            entity_name="CGT Test Unit Trust", entity_type="trust_unit",
            client=cls.client_obj,
        )

    @staticmethod
    def _mapping(section):
        m, _ = AccountMapping.objects.get_or_create(
            standard_code=f"CGT-{section}",
            defaults={"line_item_label": section.title(),
                      "financial_statement": "income_statement",
                      "statement_section": section},
        )
        return m

    def _build(self, rows):
        """rows: (code, name, closing_balance, mapped_section-or-None)."""
        fy = FinancialYear.objects.create(
            entity=self.trust, year_label="FY2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        for code, name, cb, section in rows:
            TrialBalanceLine.objects.create(
                financial_year=fy, account_code=code, account_name=name,
                closing_balance=cb,
                debit=cb if cb > ZERO else ZERO,
                credit=-cb if cb < ZERO else ZERO,
                source="tb_import",
                mapped_line_item=self._mapping(section) if section else None,
            )
        return fy

    def test_capital_gain_reaches_net_profit(self):
        """Minli FY2027 to the cent: 216,243.48 gain less 141.82 of expenses."""
        fy = self._build([
            ("601", "Capital gains/Loss - Sale of Assets",
             Decimal("-216243.48"), "revenue"),
            (*SUBSCRIPTIONS, "expenses"),
        ])

        data = _calculate_income_streams(fy)

        self.assertEqual(Decimal(data["net_profit"]), Decimal("216101.66"))
        self.assertEqual(Decimal(data["total_revenue"]), Decimal("216243.48"))

    def test_capital_gain_is_still_classified_as_a_cgt_stream(self):
        """Reaching net_profit must not cost the account its character."""
        fy = self._build([
            ("601", "Capital gains/Loss - Sale of Assets",
             Decimal("-216243.48"), "revenue"),
            (*SUBSCRIPTIONS, "expenses"),
        ])

        streams = _calculate_income_streams(fy)["income_streams"]

        self.assertEqual(Decimal(streams["cgt_non_discount"]),
                         Decimal("216243.48"))
        self.assertEqual(Decimal(streams["ordinary_income"]), ZERO)

    def test_franked_dividend_reaches_net_profit(self):
        """550 Dividends - Franked is section=revenue in the master chart.

        Its *character* is a separate matter, and a separate defect: the
        stream matcher looks for the substring "franked dividend" while the
        chart names the account "Dividends - Franked", so it can never fire on
        the real chart and the amount falls to ordinary income. Out of scope
        here -- rerouting a stream changes beneficiary tax treatment, so it
        needs its own decision. This test asserts only the P&L question.
        """
        fy = self._build([
            ("550", "Dividends - Franked", Decimal("-8000.00"), "revenue"),
            (*SUBSCRIPTIONS, "expenses"),
        ])

        data = _calculate_income_streams(fy)

        self.assertEqual(Decimal(data["net_profit"]), Decimal("7858.18"))
        self.assertEqual(Decimal(data["total_revenue"]), Decimal("8000.00"))

    def test_income_account_with_no_mapping_reaches_net_profit(self):
        """An unmapped Income-range code fell through to neither side."""
        fy = self._build([
            ("620", "Rents received", Decimal("-50000.00"), None),
            (*SUBSCRIPTIONS, "expenses"),
        ])

        data = _calculate_income_streams(fy)

        self.assertEqual(Decimal(data["net_profit"]), Decimal("49858.18"))
        self.assertEqual(Decimal(data["income_streams"]["ordinary_income"]),
                         Decimal("50000.00"))

    def test_expense_account_with_no_mapping_reaches_net_profit(self):
        """The same hole on the debit side: 1500-1999 is Expenses."""
        fy = self._build([
            ("620", "Rents received", Decimal("-50000.00"), "revenue"),
            ("1925", "Subscriptions", Decimal("141.82"), None),
        ])

        data = _calculate_income_streams(fy)

        self.assertEqual(Decimal(data["net_profit"]), Decimal("49858.18"))
        self.assertEqual(Decimal(data["total_expenses"]), Decimal("141.82"))

    def test_capital_gain_lifts_distributable_income(self):
        """With nothing carried forward, the gain is distributable."""
        fy = self._build([
            ("601", "Capital gains/Loss - Sale of Assets",
             Decimal("-216243.48"), "revenue"),
            (*SUBSCRIPTIONS, "expenses"),
        ])

        data = _calculate_income_streams(fy)

        self.assertEqual(Decimal(data["brought_forward_losses"]), ZERO)
        self.assertEqual(Decimal(data["net_distributable_income"]),
                         Decimal("216101.66"))

    def test_a_net_capital_loss_reduces_net_profit(self):
        """601 nets debit-side when the disposal is a loss."""
        fy = self._build([
            ("601", "Capital gains/Loss - Sale of Assets",
             Decimal("30000.00"), "revenue"),
            (*SUBSCRIPTIONS, "expenses"),
        ])

        data = _calculate_income_streams(fy)

        self.assertEqual(Decimal(data["net_profit"]), Decimal("-30141.82"))
        self.assertEqual(
            Decimal(data["income_streams"]["cgt_non_discount"]),
            Decimal("-30000.00"),
        )

    def test_balance_sheet_accounts_stay_out_of_net_profit(self):
        """2000+ is the balance sheet; only 0-1999 moves the P&L."""
        fy = self._build([
            ("601", "Capital gains/Loss - Sale of Assets",
             Decimal("-216243.48"), "revenue"),
            (*SUBSCRIPTIONS, "expenses"),
            ("2000", "Cash at bank", Decimal("744189.00"), None),
            ("3048", "Loan - unitholder", Decimal("-744189.00"), None),
        ])

        data = _calculate_income_streams(fy)

        self.assertEqual(Decimal(data["net_profit"]), Decimal("216101.66"))
