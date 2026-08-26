"""A trust's balance sheet must balance: net assets equals total equity.

``build_company_context`` (reached for trusts via ``build_trust_context``)
deliberately strips the injected "Current year profit / (loss)" row back
out of equity for entity_type == "trust", on the theory that the profit is
already absorbed into the beneficiary loan balances in current liabilities.
That theory does not hold: the strip fires unconditionally, whether or not
a distribution was ever posted, so a trust balance sheet is short by
exactly the year's profit -- every trust that has posted a distribution
shows net assets exceeding total equity by exactly its net profit for the
year (see the task brief's Dr Services / Chiaravalle measurements).

This test builds a trust financial year with revenue, expenses, one asset
account, one liability account, one equity (corpus) account, one
beneficiary (so account-code suffix ".01" resolves to a real officer), and
a posted distribution appropriation debit (4199, "Undistributed income")
with its credit leg modeled as the real production convention: 4004.01
"Funds loaned to trust" -- a beneficiary loan account that
``_net_beneficiary_accounts`` nets out of equity into current liabilities
(NOT 4110, which HandiLedger keeps in equity along with the rest of
4000-4999, and which no trust in production has ever posted to -- an
earlier attempt to route beneficiary loans through 4110 was reverted, see
commits dab084a and 69050f9). A separate fix (see
core/tests_trust_4199_carried_forward.py) keeps 4199 itself in equity
rather than stripping it out; this test's TB must therefore be genuinely
balanced -- both legs of the distribution present -- rather than relying
on 4199 as a free-floating, uncountered debit.

The test calls ``build_trust_context``, the entry point the .docx renderer
actually uses for trusts (NOT ``_get_tb_sections``, which returns the raw
sections *before* the profit row is injected and so would pass vacuously).
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings

from core.fs_template_service import build_trust_context
from core.models import (
    EntityChartOfAccount, EntityOfficer, FinancialYear, TrialBalanceLine,
)
from core.tests_beneficiary_accounts import BeneficiaryAccountTestBase

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _sqlite_json_contains_as_sql(self, compiler, connection):
    """Stand-in for JSONField's ``contains`` lookup under SQLite.

    ``build_trust_context`` queries ``EntityOfficer`` with
    ``Q(role="trustee") | Q(roles__contains="trustee")`` (and similarly for
    beneficiaries) to find trustees/signatories. Django's SQLite backend
    sets ``supports_json_field_contains = False`` and ``contains`` raises
    ``NotSupportedError`` at SQL-compile time -- unconditionally, regardless
    of row count -- so any test that reaches this code path crashes even
    with zero EntityOfficer rows (as here). core/tests.py:953 works around
    the same limitation with a substitute Q for a different query; this
    does the equivalent for a query built inline inside
    build_trust_context, where there is no module-level Q to substitute.
    This fixture's one EntityOfficer has role="beneficiary", which the
    ``role=`` half of each Q already excludes/includes correctly on its
    own; the ``roles__contains`` half exists only to match a *secondary*
    role stored in the JSON ``roles`` list, and this fixture never sets
    one, so "no match" is always the right answer for that half -- a
    constant-false SQL fragment reproduces that without needing real JSON
    support.
    """
    return "0", ()

# (account_code, account_name, cy_amount) -- debit-positive, credit-negative,
# matching what _get_tb_sections reads. Net profit = 150,000 - 60,000 =
# 90,000. 4199 is the distribution's debit leg (90,000); "4004.01" is its
# credit leg -- "Funds loaned to trust", the real production account code
# for a beneficiary loan (matched in core/fs_template_service.py by suffix
# ".01" to the first officer by display_order, i.e. the one beneficiary
# created in setUp below). _net_beneficiary_accounts nets 4004.01 out of
# sections["equity"] and into sections["current_liabilities"] before this
# test's assertions run, so this exercises the real netting code path, not
# just a code-range placement. 4199 now stays in equity (see
# core/fs_template_service.py), so this TB must be genuinely balanced --
# all rows sum to zero (-150000 + 60000 + 200000 - 20000 - 90000 + 90000
# - 90000 = 0) -- rather than relying on 4199 being a free-floating,
# uncountered debit.
TRUST_TB_ROWS = [
    ("0500", "Consulting fees", Decimal("-150000")),
    ("1500", "Rent expense", Decimal("60000")),
    ("2000", "Cash at bank", Decimal("200000")),
    ("3000", "Trade creditors", Decimal("-20000")),
    ("4200", "Trust corpus", Decimal("-90000")),
    ("4199", "Undistributed income", Decimal("90000")),
    ("4004.01", "Funds loaned to trust - Beneficiary A", Decimal("-90000")),
]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TrustBalanceSheetBalancesTests(BeneficiaryAccountTestBase):
    """``self.trust`` (entity_type="trust") comes from BeneficiaryAccountTestBase."""

    def setUp(self):
        self.fy = FinancialYear.objects.create(
            entity=self.trust,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        # One beneficiary so account-code suffix ".01" resolves to a real
        # officer -- required for _net_beneficiary_accounts to net 4004.01
        # into current_liabilities rather than silently stripping it as an
        # unmatched suffix.
        EntityOfficer.objects.create(
            entity=self.trust,
            full_name="Beneficiary A",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            display_order=0,
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

    def test_trust_balance_sheet_balances(self):
        """Net assets must equal total equity in the context the .docx
        renderer uses -- both the summed equity rows and the printed total.

        Before the e137e58 fix: net assets = 200,000 - (20,000 + 90,000) =
        90,000, but equity was stripped down to the 90,000 corpus row only
        (the injected 90,000 profit row was removed by the trust-only
        suppression that fix removed). The 90,000 gap was exactly the
        year's net profit (150,000 - 60,000).
        """
        with patch(
            "django.db.models.fields.json.DataContains.as_sql",
            _sqlite_json_contains_as_sql,
        ):
            context = build_trust_context(self.fy)
        sections = context["_sections"]

        equity_from_rows = -sum(
            (item.get("cy_amount") or Decimal("0")) for item in sections["equity"]
        )
        assets = sum(
            (item.get("cy_amount") or Decimal("0"))
            for item in sections["current_assets"] + sections["noncurrent_assets"]
        )
        liabilities = -sum(
            (item.get("cy_amount") or Decimal("0"))
            for item in sections["current_liabilities"] + sections["noncurrent_liabilities"]
        )
        net_assets = assets - liabilities

        self.assertEqual(
            net_assets, equity_from_rows,
            f"net_assets={net_assets} != equity(rows)={equity_from_rows}; "
            f"diff={net_assets - equity_from_rows} should be 0 "
            f"(the year's net profit is 90000)",
        )

        # The printed total (context["total_equity_cy"]) must not be forced
        # to equal net assets by a trust-only override while the equity rows
        # themselves stay short -- it must reflect the same figure the rows
        # sum to.
        self.assertEqual(context["total_equity_cy"], context["net_assets_cy"])
