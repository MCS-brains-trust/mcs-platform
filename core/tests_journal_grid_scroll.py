"""Both journal grids must load the scroll-follow script and reserve room for it.

static/js/journal_grid.js keeps the row being entered in the top half of the
screen. Its arithmetic is covered by e2e/fixtures/journal_grid.unit.spec.ts and
the real scrolling by Playwright; what neither of those catches is the wiring
quietly disappearing from one of the two templates that carry a line grid.

The .journal-scroll-room padding is asserted for the same reason it exists: the
browser cannot scroll the last row up to a third of the way down unless there is
space below it to scroll into. Drop the padding and the feature silently reverts
to the old behaviour on exactly the rows it was written for -- with no error, no
failing assertion anywhere else, and nothing to notice until an accountant
complains again.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    AdjustingJournal, Entity, EntityChartOfAccount, FinancialYear, JournalLine,
)
from core.test_support import Require2FAMixin


class JournalGridLoadsScrollFollowTest(Require2FAMixin, TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Grid Client",
            entity_type=Entity.EntityType.COMPANY,
        )
        EntityChartOfAccount.objects.create(
            entity=self.entity, account_code="1000",
            account_name="Sales", section="revenue",
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2024",
            start_date=date(2023, 7, 1), end_date=date(2024, 6, 30),
        )
        User = get_user_model()
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant", totp_secret="TESTSECRET", totp_confirmed=True,
        )
        self.entity.assigned_accountant = self.user
        self.entity.save(update_fields=["assigned_accountant"])
        self.login_as(self.user)

        self.journal = AdjustingJournal.objects.create(
            financial_year=self.fy,
            journal_type=AdjustingJournal.JournalType.GENERAL,
            status=AdjustingJournal.JournalStatus.POSTED,
            journal_date=date(2024, 6, 30),
            description="Existing journal",
            total_debit=Decimal("100.00"), total_credit=Decimal("100.00"),
        )
        JournalLine.objects.create(
            journal=self.journal, line_number=1, account_code="1000",
            account_name="Sales", debit=Decimal("100.00"), credit=Decimal("0"),
        )

    def _assert_wired(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js/journal_grid.js")
        self.assertContains(response, "journal-scroll-room")

    def test_the_new_journal_form_loads_the_scroll_follow_script(self):
        self._assert_wired(self.client.get(
            reverse("core:adjustment_create", args=[self.fy.pk]), secure=True,
        ))

    def test_the_journal_edit_form_loads_the_scroll_follow_script(self):
        self._assert_wired(self.client.get(
            reverse("core:journal_edit", args=[self.journal.pk]), secure=True,
        ))
