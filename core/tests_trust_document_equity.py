"""A trust's legal-document context must not mask the balance-sheet imbalance.

``DocumentContextBuilder._financial_data_context`` (core/document_context_builder.py)
carries the same masking bug that Task 3 removed from the financial-statements
builder (core/fs_template_service.py, commit e137e58): it computes ``net_profit``
but never injects a "Current year profit / (loss)" row into ``sections["equity"]``,
and then unconditionally overrides ``total_equity``/``total_equity_py`` to
``net_assets``/``net_assets_py`` for entity_type == "trust". That override masks
the exact gap the missing profit row creates instead of surfacing it.

This test builds a trust financial year with revenue, expenses, one asset
account, one liability account, one equity (corpus) account, and a posted
distribution appropriation debit (4199, "Undistributed income") -- the same
fixture shape as core/tests_trust_balance_sheet_balances.py, adapted to the
document builder's own row shape (dict keys "cy"/"py", not "cy_amount"/
"py_amount").

The test calls ``DocumentContextBuilder._financial_data_context()`` directly
(this method does not touch EntityOfficer/JSONField queries, so no sqlite
JSONField ``contains`` workaround is needed here, unlike the balance-sheet
builder test).

NOTE: the classified TB sections are NOT exposed on the returned context
(no ``ctx["_sections"]`` here -- unlike core/fs_template_service.py's
``_sections``, this builder's sections have no production consumer, and
returning them would serialise the entire classified trial balance --
income, expenses, assets, liabilities, equity -- into every legal
document's audit trail via ``_write_audit_trail``). Instead, these tests
spy on ``DocumentContextBuilder._inject_profit_into_equity`` (the small,
already-private method that mutates ``sections["equity"]`` in place) to
observe the *actual* equity rows used by the real build, without adding
any new exposure to the built context itself.

A plain ``total_equity == net_assets`` assertion is NOT a sufficient
regression guard here: for any balanced trial balance,
``net_assets == -sum(equity rows without profit) + net_profit`` is an
accounting identity that holds regardless of *how* total_equity was
computed. The pre-fix code's unconditional
``total_equity = net_assets`` override for trusts therefore reproduces
that same number exactly -- a naive equality check would pass against
both the fixed code and the pre-fix bug. The only observable difference
is structural: whether "Current year profit / (loss)" actually landed in
``sections["equity"]``, which is what the spy below inspects directly.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.document_context_builder import DocumentContextBuilder
from core.models import EntityChartOfAccount, FinancialYear, TrialBalanceLine
from core.tests_beneficiary_accounts import BeneficiaryAccountTestBase

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# (account_code, account_name, cy_amount) -- debit-positive, credit-negative.
# Net profit = 150,000 - 60,000 = 90,000. Unlike core/fs_template_service.py,
# this builder's _classify_tb_lines() classifies purely by numeric account
# code range and does NOT strip 4199 ("Undistributed income") out of equity,
# nor does it route 4110.NN ("Funds loaned to trust", a beneficiary loan)
# into liabilities despite the chart's "section": "liabilities" metadata for
# that code (core/beneficiary_account_service.py's BENEFICIARY_PARENT_CODES)
# -- both land in the 4000-4999 equity range by pure code number. That
# discrepancy is out of this task's scope (core/document_context_builder.py
# lines 1036/1071-1080 only). So the posted distribution here models BOTH
# legs of the journal -- debit 4199, credit 4110.01 -- which nets to zero
# within equity in this builder and keeps the fixture self-consistent
# without depending on a liability-side reclassification this task doesn't
# make. TB rows other than the distribution pair sum to zero on their own
# (-150000 + 60000 + 200000 - 20000 - 90000 = 0).
TRUST_TB_ROWS = [
    ("0500", "Consulting fees", Decimal("-150000")),
    ("1500", "Rent expense", Decimal("60000")),
    ("2000", "Cash at bank", Decimal("200000")),
    ("3000", "Trade creditors", Decimal("-20000")),
    ("4200", "Trust corpus", Decimal("-90000")),
    ("4199", "Undistributed income", Decimal("90000")),
    ("4110.01", "Funds loaned to trust - Beneficiary", Decimal("-90000")),
]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TrustDocumentEquityTests(BeneficiaryAccountTestBase):
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

    def _build_ctx_and_capture_equity_rows(self, builder):
        """Build the real context, spying on
        ``DocumentContextBuilder._inject_profit_into_equity`` to capture
        the *actual* ``sections["equity"]`` list it mutated inside the
        real ``_financial_data_context()`` call.

        This calls straight through to the real implementation (the spy
        does not change behaviour), so it observes exactly what the
        production build path does -- unlike recomputing the equity
        composition independently in the test, which would keep passing
        even if the real pipeline stopped calling the injection method at
        all (see module docstring for why a numeric-only check can't
        catch that).
        """
        captured = {}
        original = DocumentContextBuilder._inject_profit_into_equity

        def spy(self_, sections, *args, **kwargs):
            result = original(self_, sections, *args, **kwargs)
            captured["equity_rows"] = result["equity"]
            return result

        with patch.object(DocumentContextBuilder, "_inject_profit_into_equity", spy):
            ctx = builder._financial_data_context()

        self.assertIn(
            "equity_rows", captured,
            "DocumentContextBuilder._inject_profit_into_equity was never "
            "called by _financial_data_context()",
        )
        return ctx, captured["equity_rows"]

    def test_equity_section_contains_current_year_profit_row(self):
        """The equity rows actually used by the real build must carry the
        year's profit -- not just the printed total_equity figure via an
        override.

        Before the fix: no "Current year profit / (loss)" row is ever
        injected into sections["equity"] for this builder, so this fails
        outright regardless of the override.
        """
        builder = DocumentContextBuilder(entity=self.trust, financial_year=self.fy)
        _, equity_rows = self._build_ctx_and_capture_equity_rows(builder)

        profit_rows = [
            item for item in equity_rows
            if "current year profit" in item.get("account_name", "").lower()
        ]
        self.assertTrue(
            profit_rows,
            f"expected a 'Current year profit / (loss)' row in equity, "
            f"found rows: {equity_rows}",
        )

    def test_total_equity_equals_sum_of_equity_rows_not_an_override(self):
        """total_equity must equal net assets *because the real equity rows
        sum that way*, not because a trust-only override forces it.

        Before the fix, total_equity == net_assets held anyway (via the
        override at document_context_builder.py:1076-1080) even though the
        equity rows themselves never carried the profit -- so this test
        cross-checks total_equity against the *actual* rows the real build
        produced, not against net_assets directly, and the previous test
        proves those rows don't contain the profit pre-fix.
        """
        builder = DocumentContextBuilder(entity=self.trust, financial_year=self.fy)
        ctx, equity_rows = self._build_ctx_and_capture_equity_rows(builder)

        equity_from_rows = -sum(
            (item.get("cy") or Decimal("0")) for item in equity_rows
        )
        self.assertEqual(
            equity_from_rows, ctx["total_equity"],
            f"total_equity ({ctx['total_equity']}) must equal -sum(equity "
            f"rows) ({equity_from_rows}) computed directly, not via a "
            f"trust-only override to net_assets",
        )
        self.assertEqual(ctx["total_equity"], ctx["net_assets"])
