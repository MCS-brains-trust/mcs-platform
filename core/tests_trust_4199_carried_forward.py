"""4199 "Undistributed income" must not be stripped from a trust's equity.

``build_company_context`` (reached for trusts via ``build_trust_context``)
used to remove every 4199* line from ``sections["equity"]`` unconditionally,
on the theory that the account only ever carries the current year's
appropriation journal (which moves profit to a beneficiary loan and so
"shouldn't" appear as a separate equity line). That theory is wrong: 4199
is a real balance-sheet (not P&L) account, so its closing balance is the
current-year appropriation PLUS whatever genuine brought-forward
undistributed income or deficit survived from prior years. Stripping the
whole account discards that carried-forward balance and the balance sheet
no longer balances.

This is the companion to core/tests_trust_balance_sheet_balances.py, which
covers the *other* two trust-only strips fixed by e137e58 (the injected
current-year-profit row, and the total_equity-forced-to-net_assets
override) using a fixture where 4199 is exactly offset by the current
year's profit — deliberately harmless to the code path this test targets,
per that file's own docstring. Here, 4199 carries an *additional*
brought-forward component (4199.02) on top of the current year's
appropriation debit (4199.01), so the bug is visible: with the old strip,
that brought-forward balance vanishes from equity and total_equity_cy
diverges from net_assets_cy by exactly its amount.

The TB below is deliberately unclosed (revenue/expense accounts are not
closed to retained earnings), matching the platform's unclosed-TB
convention, so that build_company_context's "TB not yet closed" branch
injects the current-year profit row -- see fs_template_service.py's
"Retained profit roll-forward" section.

    Consulting fees            -150,000   (revenue, credit-normal)
    Rent expense                 60,000   (expense)
    Cash at bank                  50,000  (asset)
    Trade creditors              -20,000  (liability, credit-normal)
    4199.01 Distribution — appropriation   90,000  (debit: this year's
                                                      profit moved out to
                                                      a beneficiary loan)
    4199.02 Undistributed income b/fwd    -30,000  (credit: genuine
                                                      brought-forward
                                                      undistributed income
                                                      from prior years)
    -------------------------------------------------------------
    Sum = 0 (TB balances)

Net profit this year = 150,000 - 60,000 = 90,000.
Net assets = 50,000 (cash) - 20,000 (creditors) = 30,000.

With the strip (bug): both 4199 lines are removed from equity entirely.
Equity is left with nothing until the current-year-profit row is injected
(-90,000 credit-normal raw -> +90,000 display), so total_equity_cy = 90,000
while net_assets_cy = 30,000 -- off by 60,000, exactly the combined 4199
balance (90,000 debit - 30,000 credit... as raw values: +90,000 + -30,000
= +60,000) that was discarded.

Without the strip (fix): equity holds 4199.01 (+90,000) + 4199.02
(-30,000) = +60,000 raw, plus the injected profit row (-90,000 raw),
netting to -30,000 raw -> total_equity_cy = 30,000 = net_assets_cy.

Those three rows are then collapsed into a single "Accumulated profits"
row of -30,000 raw by _collapse_trust_equity_to_accumulated, so the
brought-forward balance is now observable in that row's amount rather
than in a surviving 4199.02 line. Discarding 4199.02 would leave the
collapsed row at 0 and total_equity_cy at 90,000, so the guard still
bites.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings

from core.fs_template_service import build_trust_context
from core.models import EntityChartOfAccount, FinancialYear, TrialBalanceLine
from core.tests_beneficiary_accounts import BeneficiaryAccountTestBase

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _sqlite_json_contains_as_sql(self, compiler, connection):
    """Constant-false stand-in for JSONField ``contains`` under SQLite.

    See core/tests_trust_balance_sheet_balances.py's identical helper for
    the full explanation: build_trust_context queries EntityOfficer with a
    ``roles__contains`` lookup that Django's SQLite backend cannot compile,
    and this fixture has no EntityOfficer rows so "no match" is always the
    right answer.
    """
    return "0", ()


# (account_code, account_name, cy_amount) -- debit-positive, credit-negative.
TRUST_TB_ROWS = [
    ("0500", "Consulting fees", Decimal("-150000")),
    ("1500", "Rent expense", Decimal("60000")),
    ("2000", "Cash at bank", Decimal("50000")),
    ("3000", "Trade creditors", Decimal("-20000")),
    ("4199.01", "Distribution - Appropriation", Decimal("90000")),
    ("4199.02", "Undistributed income b/fwd", Decimal("-30000")),
]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class Trust4199CarriedForwardTests(BeneficiaryAccountTestBase):
    """``self.trust`` (entity_type="trust") comes from BeneficiaryAccountTestBase."""

    def setUp(self):
        self.fy = FinancialYear.objects.create(
            entity=self.trust,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        for code, name, cy in TRUST_TB_ROWS:
            EntityChartOfAccount.objects.get_or_create(
                entity=self.trust, account_code=code,
                defaults={"account_name": name, "is_active": True},
            )
            TrialBalanceLine.objects.create(
                financial_year=self.fy,
                account_code=code,
                account_name=name,
                closing_balance=cy,
                debit=cy if cy > 0 else Decimal("0"),
                credit=-cy if cy < 0 else Decimal("0"),
                source="tb_import",
                is_adjustment=False,
            )

    def test_4199_brought_forward_balance_stays_in_equity(self):
        """total_equity_cy must equal net_assets_cy, and a 4199 equity row
        must survive into the rendered context.

        Before the fix: 4199.01 and 4199.02 are both stripped from equity,
        discarding the -30,000 brought-forward balance along with the
        current-year appropriation. total_equity_cy (90,000) no longer
        equals net_assets_cy (30,000) -- off by 60,000, the combined raw
        value of the two 4199 lines that were removed.
        """
        with patch(
            "django.db.models.fields.json.DataContains.as_sql",
            _sqlite_json_contains_as_sql,
        ):
            context = build_trust_context(self.fy)

        self.assertEqual(
            context["total_equity_cy"], context["net_assets_cy"],
            f"total_equity_cy={context['total_equity_cy']!r} != "
            f"net_assets_cy={context['net_assets_cy']!r}; the balance "
            f"sheet does not balance -- 4199's brought-forward balance is "
            f"likely being stripped from equity",
        )

        sections = context["_sections"]
        # A trust's appropriation rows are now collapsed into one cumulative
        # line (_collapse_trust_equity_to_accumulated) to match the firm's
        # HandiLedger presentation, so 4199.01/4199.02 no longer appear as
        # separate codes. What this test guards is unchanged: the
        # brought-forward balance must survive rather than be discarded, which
        # is observable in the collapsed row's amount.
        accum = [
            item for item in sections["equity"]
            if item.get("account_code") == "ACCUM_PL"
        ]
        self.assertEqual(
            len(accum), 1,
            f"expected one collapsed accumulated-P&L row in "
            f"sections['equity'], found "
            f"{[i.get('account_name') for i in sections['equity']]}",
        )
        # 4199.01 (+90,000) + 4199.02 (-30,000) + injected profit (-90,000)
        # = -30,000 raw. A credit balance, so undistributed income survives as
        # accumulated profits. Drop the brought-forward -30,000 and this is 0.
        self.assertEqual(
            accum[0]["cy_amount"], Decimal("-30000"),
            "the brought-forward 4199.02 balance was discarded",
        )
        self.assertEqual(accum[0]["account_name"], "Accumulated profits")
        self.assertFalse(
            [i for i in sections["equity"]
             if (i.get("account_code") or "").startswith("4199")],
            "raw 4199* codes must not survive the collapse",
        )
