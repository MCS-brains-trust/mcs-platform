"""Is this BAS period accounted for?

get_bank_coverage answers a bank question. Once a cashbook journal carries its
own GST, "does this period have complete source data" stops being the same
question, and get_period_coverage is the one the BAS page actually needs.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.bas_utils import (
    get_bank_coverage, get_period_coverage, compute_period_status,
)
from core.models import (
    AdjustingJournal, Entity, EntityChartOfAccount, FinancialYear,
)
from review.models import PendingTransaction, ReviewJob

Q2_START = date(2025, 10, 1)
Q2_END = date(2025, 12, 31)


class PeriodCoverageTestBase(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Cashbook Client",
            entity_type=Entity.EntityType.SOLE_TRADER,
            is_gst_registered=True,
        )
        for code, name, tax, section in [
            ("105", "Sales", "GST", "revenue"),
            ("3380", "GST payable control account", "", "liabilities"),
            ("4080", "Drawings", "", "capital_accounts"),
        ]:
            EntityChartOfAccount.objects.create(
                entity=self.entity, account_code=code, account_name=name,
                tax_code=tax, section=section,
            )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )

    def make_journal(self, *, journal_date=date(2025, 12, 31), status="posted",
                     journal_type=None, reference="JE-001"):
        """A balanced two-line cashbook journal. Not split -- these tests are
        about period evidence, not about GST arithmetic."""
        if journal_type is None:
            journal_type = AdjustingJournal.JournalType.CASHBOOK
        j = AdjustingJournal.objects.create(
            financial_year=self.fy, reference_number=reference,
            journal_type=journal_type, journal_date=journal_date,
            description="Oct-Dec cash book", status=status,
        )
        j.lines.create(line_number=1, account_code="105", account_name="Sales",
                       debit=Decimal("0"), credit=Decimal("23187.00"),
                       tax_code="GST")
        j.lines.create(line_number=2, account_code="4080",
                       account_name="Drawings", debit=Decimal("23187.00"),
                       credit=Decimal("0"), tax_code="N-T")
        return j

    def make_bank_months(self, *iso_dates):
        """One confirmed bank transaction per given date."""
        job = ReviewJob.objects.create(
            entity=self.entity, financial_year=self.fy,
            client_name="Cashbook Client", is_gst_registered=True,
        )
        for i, d in enumerate(iso_dates):
            PendingTransaction.objects.create(
                job=job, date=d, description="Bank txn %d" % i,
                amount=Decimal("110.00"), gst_amount=Decimal("10.00"),
                net_amount=Decimal("100.00"),
                confirmed_code="105", confirmed_name="Sales",
                confirmed_tax_type="GST on Income",
                confirmed_gst_amount=Decimal("10.00"), is_confirmed=True,
            )
        return job


class JournalledPeriodTest(PeriodCoverageTestBase):
    def test_a_posted_cashbook_journal_makes_the_period_complete(self):
        self.make_journal()
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "complete")
        self.assertEqual(cov["source"], "journal")
        self.assertEqual(cov["journal_refs"], ["JE-001"])

    def test_a_journalled_period_names_no_months(self):
        """Month coverage is not derivable from a journal -- JournalLine has no
        date. Reporting months here would be inventing data."""
        self.make_journal()
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["months"], [])
        self.assertEqual(cov["missing"], [])

    def test_a_journalled_period_computes_as_ready(self):
        self.make_journal()
        self.assertEqual(
            compute_period_status(self.fy, Q2_START, Q2_END), "ready",
        )

    def test_a_cashbook_journal_with_no_gst_still_counts(self):
        """An all-N-T quarter is a legitimate nil BAS."""
        j = self.make_journal()
        j.lines.update(tax_code="N-T")
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "complete")
        self.assertEqual(cov["source"], "journal")


class JournalsThatDoNotCountTest(PeriodCoverageTestBase):
    def test_a_draft_cashbook_journal_does_not_count(self):
        self.make_journal(status="draft")
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "none")
        self.assertEqual(cov["source"], "none")

    def test_a_general_journal_carrying_gst_does_not_count(self):
        """A general journal makes no claim to be the period's cash book."""
        self.make_journal(journal_type=AdjustingJournal.JournalType.GENERAL)
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "none")
        self.assertEqual(cov["source"], "none")

    def test_a_journal_dated_after_period_end_does_not_count(self):
        self.make_journal(journal_date=date(2026, 1, 5))
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "none")
        self.assertEqual(cov["source"], "none")


class BankPeriodsUnchangedTest(PeriodCoverageTestBase):
    def test_a_fully_banked_period_is_complete_and_sourced_bank(self):
        self.make_bank_months("2025-10-15", "2025-11-15", "2025-12-15")
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "complete")
        self.assertEqual(cov["source"], "bank")
        self.assertEqual(cov["journal_refs"], [])
        self.assertEqual(cov["missing"], [])

    def test_a_partly_banked_period_with_a_journal_stays_partial(self):
        """The mixed-period rule, and the most important guard in this file:
        one bank month present and the month-by-month rule governs, so a
        forgotten December import is still flagged."""
        self.make_bank_months("2025-10-15", "2025-11-15")
        self.make_journal()
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "partial")
        self.assertEqual(cov["source"], "bank")
        self.assertEqual(cov["missing"], ["Dec 2025"])

    def test_an_empty_period_is_none(self):
        cov = get_period_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(cov["status"], "none")
        self.assertEqual(cov["source"], "none")
        self.assertEqual(cov["journal_refs"], [])

    def test_get_bank_coverage_still_answers_only_the_bank_question(self):
        """The primitive must stay honest whatever journals exist."""
        self.make_journal()
        bank = get_bank_coverage(self.fy, Q2_START, Q2_END)
        self.assertEqual(bank["status"], "none")
        self.assertNotIn("source", bank)


from django.contrib.auth import get_user_model
from django.urls import reverse

from core.bas_utils import ensure_bas_periods
from core.models import BASPeriod
from core.test_support import Require2FAMixin


class JournalledPeriodViewTest(Require2FAMixin, PeriodCoverageTestBase):
    """The three view-level consequences: which quarter opens, whether the
    lodge gate demands an override, and what the endpoint reports."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        # Require2FAMiddleware checks has_2fa before it looks at the session
        # flag, so the user needs the secret as well as the verified session.
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant", totp_secret="TESTSECRET", totp_confirmed=True,
        )
        # An accountant reaches only entities assigned to them.
        self.entity.assigned_accountant = self.user
        self.entity.save(update_fields=["assigned_accountant"])
        self.login_as(self.user)
        self.make_journal()

    def test_the_dashboard_opens_on_the_journalled_quarter(self):
        """Auto-selection looks for a 'ready' period. Q2 is journalled, so it
        is ready, and the page must not fall back to the empty Q1."""
        # SECURE_SSL_REDIRECT is on; without secure=True this is a bare 301.
        r = self.client.get(
            reverse("core:gst_activity_statement", args=[self.fy.pk]),
            secure=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["selected_period"]["period"].period_number, 2)
        self.assertEqual(r.context["selected_period"]["status"], "ready")

    def test_a_journalled_period_lodges_without_an_override_reason(self):
        r = self.client.post(
            reverse("core:bas_lodge_period",
                    args=[self.fy.pk, 2]),
            {}, secure=True, follow=True,
        )
        self.assertEqual(r.status_code, 200)
        bp = BASPeriod.objects.get(
            financial_year=self.fy, period_type="quarterly", period_number=2,
        )
        self.assertEqual(bp.status, "lodged")

    def test_an_empty_period_still_cannot_lodge_without_an_override(self):
        r = self.client.post(
            reverse("core:bas_lodge_period", args=[self.fy.pk, 1]),
            {}, secure=True, follow=True,
        )
        bp = BASPeriod.objects.get(
            financial_year=self.fy, period_type="quarterly", period_number=1,
        )
        self.assertNotEqual(bp.status, "lodged")

    def test_the_coverage_endpoint_reports_the_journal_source(self):
        # Unlike the dashboard and the lodge gate, this endpoint does not call
        # ensure_bas_periods -- it 404s on a period row that was never created.
        # The frontend only polls it from an already-rendered dashboard, which
        # has created them.
        ensure_bas_periods(self.fy, "quarterly")
        r = self.client.get(
            reverse("core:bas_coverage_check", args=[self.fy.pk, 2]),
            secure=True,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "complete")
        self.assertEqual(body["source"], "journal")
        self.assertEqual(body["journal_refs"], ["JE-001"])

    def test_the_status_card_does_not_claim_all_months_are_covered(self):
        """The 'ready' branch says 'All months covered'. For a journalled
        period no months were covered -- there is no bank feed at all."""
        html = self.client.get(
            reverse("core:gst_activity_statement", args=[self.fy.pk]),
            secure=True,
        ).content.decode()
        self.assertNotIn("All months covered", html)
        self.assertIn("Journalled", html)
        self.assertIn("JE-001", html)

    def test_a_banked_period_still_says_all_months_covered(self):
        self.make_bank_months("2026-01-15", "2026-02-15", "2026-03-15")
        html = self.client.get(
            reverse("core:gst_activity_statement", args=[self.fy.pk])
            + "?period=3",
            secure=True,
        ).content.decode()
        self.assertIn("All months covered", html)
