"""A profit earned against carried-forward losses is absorbed, not distributed.

Specification: the HandiLedger pack for Minli Enterprise Unit Trust FY2024
(``Financial Statements MINL0002.pdf``, HandiSoft HandiLedger 2026 v1.00), a
year with a 114,554 profit standing against 1,800,906 of brought-forward
losses. It reports:

* the profit in full on the face of the Detailed Profit and Loss Statement;
* ``Accumulated Losses`` moving (1,800,906) -> (1,686,352) on the Detailed
  Balance Sheet, the profit having been absorbed into the deficit;
* and on the Beneficiaries Profit Distribution Summary::

      Beneficiaries Share of Profit
      - Penman Property Nominees Pty Ltd ...            -
      - Double Water International Pty Ltd ...          -
      Undistributed income (loss)               (1,686,352)
      Total Profit (Loss)                       (1,686,352)

Two things follow that this codebase did not do.

**The summary carries the cumulative position, not the year's result.**
``_build_beneficiary_distribution_summary`` built page 1 as the per-holder
share rows plus a ``Total Profit`` line that was merely their sum, so a year
like this one printed a column of dashes and stopped. The pack's total is the
closing accumulated deficit, and the shares plus the undistributed line foot
to it.

**The balance-sheet collapse skipped unit trusts.**
``_collapse_trust_equity_to_accumulated`` presents 4199 and the injected
"Current year profit / (loss)" row as one cumulative line, which is the
``Accumulated Losses`` figure above -- but it was gated on
``entity_type == "trust"`` while ``TRUST_LIKE_TYPES`` is
``("trust", "trust_unit")``. Minli, the platform's only unit trust and the very
entity this pack belongs to, was the one entity excluded from it.

The fixture is Minli FY2024 itself, at the platform's own precision: income
272,277.62, expenses 157,724.00, net profit 114,553.62, and 4199 carrying
1,800,905.72 brought forward. The trial balance sums to nil.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.fs_template_service import (
    _build_beneficiary_distribution_summary, build_trust_context,
)
from core.models import (
    Client, ClientAccountMapping, Entity, EntityChartOfAccount, EntityOfficer,
    FinancialYear, TrialBalanceLine,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _sqlite_json_contains_as_sql(self, compiler, connection):
    """Constant-false stand-in for JSONField ``contains`` under SQLite.

    ``build_trust_context`` filters EntityOfficer on
    ``Q(role=...) | Q(roles__contains=...)``; SQLite raises
    ``NotSupportedError`` at SQL-compile time for the second half regardless
    of row count. This fixture never populates the JSON ``roles`` list, so
    "no match" is always the right answer for that half. Mirrors the same
    workaround in core/tests_trust_balance_sheet_balances.py.
    """
    return "0", ()


def _pdf_text(buffer):
    from pypdf import PdfReader

    buffer.seek(0)
    return "\n".join(page.extract_text() or "" for page in PdfReader(buffer).pages)


# (account_code, account_name, closing_balance) -- debit-positive.
# -272,277.62 + 157,724.00 + 1,800,905.72 - 843,176.05 - 843,176.05 = 0.00
MINLI_FY2024_TB = [
    ("0584", "Other income", Decimal("-272277.62")),
    ("1500", "Rates & taxes", Decimal("157724.00")),
    ("4199", "Undistributed income", Decimal("1800905.72")),
    ("4000.01", "Opening balance - Unit Holder", Decimal("-843176.05")),
    ("4000.02", "Opening balance - Unit Holder", Decimal("-843176.05")),
]

# 1,800,905.72 brought forward less the 114,553.62 the year earned.
CLOSING_DEFICIT = Decimal("1686352.10")


@override_settings(STORAGES=STORAGES_OVERRIDE)
class UndistributedIncomePresentationTests(TestCase):
    """Minli Enterprise Unit Trust FY2024, as HandiLedger issued it."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Undistributed Income Client")
        cls.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust",
            entity_type="trust_unit",
            client=cls.client_obj,
        )
        cls.holders = [
            EntityOfficer.objects.create(
                entity=cls.entity,
                full_name=name,
                role="unit_holder",
                display_order=order,
            )
            for order, name in enumerate(
                ["Double Water International Pty Ltd",
                 "Penman Property Nominees Pty Ltd"],
                start=1,
            )
        ]
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="2024",
            start_date=date(2023, 7, 1),
            end_date=date(2024, 6, 30),
        )
        for idx, holder in enumerate(cls.holders, start=1):
            ClientAccountMapping.objects.create(
                entity=cls.entity,
                client_account_code=f"4000.{idx:02d}",
                client_account_name="Opening balance - Unit Holder",
                beneficiary_officer=holder,
            )
        for code, name, closing in MINLI_FY2024_TB:
            EntityChartOfAccount.objects.get_or_create(
                entity=cls.entity, account_code=code,
                defaults={"account_name": name, "is_active": True},
            )
            TrialBalanceLine.objects.create(
                financial_year=cls.fy,
                account_code=code,
                account_name=name,
                closing_balance=closing,
                debit=closing if closing > 0 else Decimal("0"),
                credit=-closing if closing < 0 else Decimal("0"),
                source="tb_import",
                is_adjustment=False,
            )

    def test_summary_reports_the_cumulative_undistributed_loss(self):
        """Page 1 carries "Undistributed income (loss)" at the closing deficit.

        The pack prints (1,686,352) -- the deficit after absorbing the year's
        114,554 -- not the year's own result and not a dash.
        """
        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))

        self.assertIn("Undistributed income (loss)", text)
        self.assertIn(f"({CLOSING_DEFICIT:,.0f})", text)

    def test_summary_total_is_labelled_and_foots_to_shares_plus_undistributed(self):
        """"Total Profit (Loss)" equals the shares plus the undistributed line.

        Both holders take a nil share in a year with nothing distributable, so
        the total is the undistributed deficit alone.
        """
        text = _pdf_text(_build_beneficiary_distribution_summary({"_fy": self.fy}))

        self.assertIn("Total Profit (Loss)", text)
        # Once on the undistributed row, once on the total beneath it.
        self.assertEqual(text.count(f"({CLOSING_DEFICIT:,.0f})"), 2)

    def test_unit_trust_equity_collapses_to_one_accumulated_losses_line(self):
        """A unit trust presents equity as HandiLedger does: one cumulative line.

        4199 and the injected "Current year profit / (loss)" row are replaced
        by a single "Accumulated losses" row carrying 1,686,352.10.
        """
        with patch(
            "django.db.models.fields.json.DataContains.as_sql",
            _sqlite_json_contains_as_sql,
        ):
            context = build_trust_context(self.fy)
        equity = context["_sections"]["equity"]

        self.assertEqual(
            [r.get("account_code") for r in equity], ["ACCUM_PL"],
            f"expected one collapsed row, got {[r.get('account_name') for r in equity]}",
        )
        self.assertEqual(equity[0]["account_name"], "Accumulated losses")
        self.assertEqual(equity[0]["cy_amount"], CLOSING_DEFICIT)
