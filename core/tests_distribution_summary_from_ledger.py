"""The distribution summary must report what was posted, not what was planned.

``_figures_for_year`` took "Profit distribution for year" from
``workspace.selected_tax_scenario.distributions`` -- the Tax Planning
allocation. Its own docstring said so: "Profit_dist = beneficiary's allocated
share from selected tax scenario." That is a proposal. It says nothing about
whether a distribution was ever posted, and nothing about whether it was later
reversed.

Minli Enterprise Unit Trust FY2026 is where that showed. JE-007 distributed
626,802.51 against nil distributable income; JE-008 reversed it, taking 4199
back to 2,255,231.40 and both 4004 accounts to nil. The balance sheet followed
immediately -- accumulated losses moved to (1,628,429), the profit absorbed
into the deficit. The distribution summary went on printing 313,401 to each
unitholder, because the scenario still said so, with the reversal disappearing
into the "Funds loaned to trust" plug that _resolve back-solves.

Reading the ledger instead cannot be done by netting the beneficiary's 4004
account: Dr Services Family Trust FY2026 nets 85,897.03 there, being a 61,848
distribution plus 24,049 of genuine loan movement. The distribution is the part
that came from distribution journals, so that is what is summed -- together
with any journal reversing one, which is what ``AdjustingJournal.reverses``
records.

The scenario remains the source while no distribution journal exists, so a
draft year still previews what Stage 2 proposes.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.fs_template_service import _build_beneficiary_distribution_summary
from core.models import (
    AdjustingJournal, Client, ClientAccountMapping, Entity, EntityOfficer,
    FinancialYear, JournalLine, TaxPlanningScenario, TrialBalanceLine,
    TrustWorkspace,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

D = Decimal

LABELS = {
    "Opening balance - Beneficiary", "Funds loaned to trust",
    "Profit distribution for year", "Physical distribution", "Closing balance",
    "Total of beneficiary loans", "Total Beneficiary Funds",
}


def _num(text):
    text = text.strip()
    if text in ("-", "—", ""):
        return 0
    neg = text.startswith("(") and text.endswith(")")
    return (-1 if neg else 1) * int(text.strip("()").replace(",", ""))


def _pairs(fy):
    from pypdf import PdfReader

    buf = _build_beneficiary_distribution_summary({"_fy": fy})
    buf.seek(0)
    text = "\n".join(p.extract_text() or "" for p in PdfReader(buf).pages)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return [
        (l, _num(lines[i + 1]))
        for i, l in enumerate(lines)
        if l in LABELS and i + 1 < len(lines)
    ]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DistributionSummaryReadsTheLedgerTests(TestCase):
    """One beneficiary: 1,000 opening, a 600 distribution, 100 of real loan."""

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Ledger Trust", entity_type="trust",
            client=Client.objects.create(name="Ledger Client"),
        )
        self.officer = EntityOfficer.objects.create(
            entity=self.entity, full_name="Lee Ledger",
            role="beneficiary", display_order=1,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        for code, name in (
            ("4000.01", "Opening balance - Beneficiary"),
            ("4004.01", "Funds loaned to trust"),
        ):
            ClientAccountMapping.objects.create(
                entity=self.entity, client_account_code=code,
                client_account_name=name, beneficiary_officer=self.officer,
            )
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="4000.01",
            account_name="Opening balance - Beneficiary",
            closing_balance=D("-1000.00"), prior_credit=D("1000.00"),
            source="rollover",
        )
        # Genuine loan movement on the same account the distribution credits.
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="4004.01",
            account_name="Funds loaned to trust",
            closing_balance=D("-100.00"), credit=D("100.00"),
            source="manual_journal",
        )
        # The scenario proposes 600 and is deliberately left in place.
        self.workspace = TrustWorkspace.objects.create(financial_year=self.fy)
        self.scenario = TaxPlanningScenario.objects.create(
            financial_year=self.fy, scenario_name="Proposal",
            distributions=[{
                "beneficiary_id": str(self.officer.pk),
                "proposed_distribution": "600.00",
            }],
        )
        self.workspace.selected_tax_scenario = self.scenario
        self.workspace.save()

    def _post_distribution(self, amount):
        journal = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-D01",
            journal_type=AdjustingJournal.JournalType.YEAR_END,
            status=AdjustingJournal.JournalStatus.POSTED,
            is_trust_distribution=True, journal_date=self.fy.end_date,
            description="Trust distribution",
            total_debit=amount, total_credit=amount,
        )
        JournalLine.objects.create(
            journal=journal, line_number=1, account_code="4199",
            account_name="Undistributed income", debit=amount, credit=D("0"),
        )
        JournalLine.objects.create(
            journal=journal, line_number=2, account_code="4004.01",
            account_name="Funds loaned to trust", debit=D("0"), credit=amount,
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="4004.01",
            account_name="Funds loaned to trust",
            closing_balance=-amount, credit=amount,
            source="manual_journal", source_journal=journal,
        )
        return journal

    def _reverse(self, journal):
        reversal = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number="JE-D02",
            journal_type=AdjustingJournal.JournalType.YEAR_END,
            status=AdjustingJournal.JournalStatus.POSTED,
            is_trust_distribution=False, journal_date=self.fy.end_date,
            description=f"Reversal of {journal.reference_number}",
            reverses=journal,
            total_debit=journal.total_credit, total_credit=journal.total_debit,
        )
        JournalLine.objects.create(
            journal=reversal, line_number=1, account_code="4004.01",
            account_name="Funds loaned to trust",
            debit=journal.total_debit, credit=D("0"),
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="4004.01",
            account_name="Funds loaned to trust",
            closing_balance=journal.total_debit, debit=journal.total_debit,
            source="manual_journal", source_journal=reversal,
        )
        return reversal

    def test_a_posted_distribution_is_reported(self):
        self._post_distribution(D("600.00"))
        got = dict(_pairs(self.fy))
        self.assertEqual(got["Profit distribution for year"], 600)
        self.assertEqual(got["Closing balance"], 1700)

    def test_a_reversed_distribution_reports_nothing(self):
        """The Minli shape: the scenario still says 600, the ledger says nil."""
        journal = self._post_distribution(D("600.00"))
        self._reverse(journal)

        got = dict(_pairs(self.fy))
        self.assertEqual(
            got["Profit distribution for year"], 0,
            "a reversed distribution is still being reported as distributed",
        )
        # 1,000 opening + 100 genuine loan, the distribution in and back out.
        self.assertEqual(got["Closing balance"], 1100)
        self.assertEqual(got["Funds loaned to trust"], 100)

    def test_the_ledger_beats_the_scenario(self):
        """Posted 250 against a scenario proposing 600."""
        self._post_distribution(D("250.00"))
        got = dict(_pairs(self.fy))
        self.assertEqual(got["Profit distribution for year"], 250)

    def test_the_scenario_is_used_while_nothing_is_posted(self):
        """A draft year still previews what Stage 2 proposes."""
        got = dict(_pairs(self.fy))
        self.assertEqual(got["Profit distribution for year"], 600)

    def test_loan_movement_on_the_same_account_is_not_a_distribution(self):
        """Dr Services nets 85,897 on 4004: a 61,848 distribution plus loans."""
        self._post_distribution(D("600.00"))
        got = dict(_pairs(self.fy))
        # The 100 credited outside any distribution journal stays a loan.
        self.assertEqual(got["Funds loaned to trust"], 100)
        self.assertEqual(got["Profit distribution for year"], 600)
