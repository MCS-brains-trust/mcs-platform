"""Editing a posted journal must recompute from the lines that were just saved.

``journal_edit`` loads the journal with ``prefetch_related("lines")`` so it can
snapshot the *before* state for the audit log.  Everything after the formset
save that reads ``journal.lines.all()`` gets that same snapshot back — Django
serves a bare ``.all()`` on a related manager straight from
``_prefetched_objects_cache`` without touching the database.  So the balance
check, the cached header totals and the audit diff were all still looking at
the pre-edit lines.

Kinross Builders FY2024 JE-002 is the live case: edited from $286,477.00 down
to $131,656.00, the lines and the trial balance both correct, and the totals
on the journal screen still reading $286,477.00.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    AdjustingJournal, AuditLog, Entity, EntityChartOfAccount, FinancialYear,
    JournalLine,
)
from core.test_support import Require2FAMixin


class JournalEditRecomputesFromSavedLinesTest(Require2FAMixin, TestCase):
    CHART = [
        ("1000", "Sales/Fees/Commissions", "", "revenue"),
        ("3565", "Loan - Director", "", "liabilities"),
    ]

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Kinross Builders Pty Ltd",
            entity_type=Entity.EntityType.COMPANY,
        )
        for code, name, tax, section in self.CHART:
            EntityChartOfAccount.objects.create(
                entity=self.entity, account_code=code, account_name=name,
                tax_code=tax, section=section,
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
            description="Added to statementhub",
            total_debit=Decimal("286477.00"),
            total_credit=Decimal("286477.00"),
        )
        self.dr = JournalLine.objects.create(
            journal=self.journal, line_number=1,
            account_code="3565", account_name="Loan - Director",
            debit=Decimal("286477.00"), credit=Decimal("0"),
        )
        self.cr = JournalLine.objects.create(
            journal=self.journal, line_number=2,
            account_code="1000", account_name="Sales/Fees/Commissions",
            debit=Decimal("0"), credit=Decimal("286477.00"),
        )

    def _edit(self, dr_amount, cr_amount):
        data = {
            "journal_type": AdjustingJournal.JournalType.GENERAL,
            "journal_date": "2024-06-30",
            "description": "Added to statementhub",
            "narration": "",
            "lines-TOTAL_FORMS": "2",
            "lines-INITIAL_FORMS": "2",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-id": str(self.dr.pk),
            "lines-0-account_code": "3565",
            "lines-0-account_name": "Loan - Director",
            "lines-0-description": "",
            "lines-0-debit": dr_amount,
            "lines-0-credit": "0",
            "lines-0-tax_code": "",
            "lines-0-gst_override": "",
            "lines-1-id": str(self.cr.pk),
            "lines-1-account_code": "1000",
            "lines-1-account_name": "Sales/Fees/Commissions",
            "lines-1-description": "",
            "lines-1-debit": "0",
            "lines-1-credit": cr_amount,
            "lines-1-tax_code": "",
            "lines-1-gst_override": "",
        }
        return self.client.post(
            reverse("core:journal_edit", args=[self.journal.pk]),
            data, secure=True, follow=True,
        )

    def test_cached_totals_follow_the_edited_lines(self):
        self._edit("131656.00", "131656.00")

        self.journal.refresh_from_db()
        self.assertEqual(self.journal.total_debit, Decimal("131656.00"))
        self.assertEqual(self.journal.total_credit, Decimal("131656.00"))

    def test_an_edit_that_unbalances_the_journal_is_rejected(self):
        # The balance check has to see the posted figures, not the ones the
        # journal held when the page was loaded — those still balance.
        self._edit("131656.00", "99999.00")

        self.journal.refresh_from_db()
        self.dr.refresh_from_db()
        self.assertEqual(self.dr.debit, Decimal("286477.00"))
        self.assertEqual(self.journal.total_debit, Decimal("286477.00"))

    def test_the_audit_log_records_what_the_lines_changed_to(self):
        self._edit("131656.00", "131656.00")

        entry = (
            AuditLog.objects
            .filter(action="adjustment", description__startswith="Edited")
            .latest("timestamp")
        )
        self.assertIn("131656.00", entry.description)
        self.assertNotIn("No changes detected", entry.description)
