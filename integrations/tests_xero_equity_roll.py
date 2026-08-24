"""Xero cloud import: keeping Xero's year-end roll out of a period movement.

Found on a live re-import of DJLH Properties FY2025. Two things were wrong on
the review screen and only one of them was visible as a number: a
``960 Retained Earnings9`` row for Dr 97,731.92, and a footer reading
0.00 / 0.00 against staged figures of Dr 303,053.57 / Cr 205,321.65.

The retained-earnings row is Xero's year-end roll. Four figures agreed to the
cent, which is what identified it:

    the staged Retained Earnings row              97,731.92
    the staged Dr - Cr imbalance                  97,731.92
    the FY24 -> FY25 rollover on 4199             97,731.92
    the FY2024 net loss (119,879.92 - 22,148.00)  97,731.92

Xero moves the prior-year result onto Retained Earnings on 1 July and resets
the P&L accounts at the same instant. The differencing computes a true
movement for balance-sheet accounts but passes P&L accounts through at their
YTD figures, so the P&L half of that roll is invisible and the equity half
arrives with no contra -- it is the entire imbalance. StatementHub performs
the same roll in the FY rollover, so importing Xero's copy posts the
prior-year result twice.

Keyed on Xero's own SystemAccount field, never on the code or the name: 960
is only the default code, and a chart that already has a retained earnings
account of its own gets Xero's renamed -- which is where the stray digit in
"Retained Earnings9" came from. This client runs MYOB-style codes in Xero and
has its own 3-4000 Retained Earnings, which must still import.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from integrations.providers import (
    XERO_EQUITY_ROLL_SYSTEM_ACCOUNTS,
    XeroProvider,
)
from integrations.views import _drop_xero_equity_roll_rows


def _response(payload, status=200):
    class R:
        status_code = status

        def json(self):
            return payload

        def raise_for_status(self):
            if status >= 400:
                raise AssertionError(f"unexpected raise_for_status at {status}")
    return R()


def _roll_row(**over):
    """The live DJLH row: credit 291,273.76 at 30 Jun 2024 falling to credit
    193,541.84 at 30 Jun 2025 -- a Dr 97,731.92 movement."""
    row = {
        "account_code": "960",
        "account_name": "Retained Earnings9",
        "provider_section": "balance_sheet",
        "provider_system_account": "RETAINEDEARNINGS",
        "period_debit": Decimal("0"),
        "period_credit": Decimal("193541.84"),
        "opening_debit": Decimal("0"),
        "opening_credit": Decimal("291273.76"),
    }
    row.update(over)
    return row


class ChartCarriesTheSystemAccountTests(TestCase):
    """The SystemAccount has to survive every hop from /Accounts to the view."""

    ACCOUNTS = {"Accounts": [
        {"AccountID": "guid-re", "Code": "960", "Name": "Retained Earnings9",
         "Class": "EQUITY", "SystemAccount": "RETAINEDEARNINGS"},
        {"AccountID": "guid-gst", "Code": "820", "Name": "GST",
         "Class": "LIABILITY", "SystemAccount": "GST"},
        {"AccountID": "guid-rent", "Code": "4-1900", "Name": "Rental Income",
         "Class": "REVENUE"},
    ]}

    def test_fetch_accounts_reads_the_system_account(self):
        with patch("integrations.providers.requests.get",
                   return_value=_response(self.ACCOUNTS)):
            chart = XeroProvider().fetch_accounts("tok", "tenant")
        self.assertEqual(chart["guid-re"]["system_account"], "RETAINEDEARNINGS")
        self.assertEqual(chart["guid-gst"]["system_account"], "GST")
        # Ordinary user-created accounts carry no system account at all.
        self.assertEqual(chart["guid-rent"]["system_account"], "")

    def test_a_parsed_report_row_carries_the_system_account(self):
        report = {"Reports": [{"Rows": [
            {"RowType": "Header", "Cells": [
                {"Value": "Account"}, {"Value": "Debit"}, {"Value": "Credit"},
            ]},
            {"RowType": "Section", "Rows": [
                {"RowType": "Row", "Cells": [
                    {"Value": "Retained Earnings9",
                     "Attributes": [{"Id": "account", "Value": "guid-re"}]},
                    {"Value": "0.00"}, {"Value": "193541.84"},
                ]},
            ]},
        ]}]}

        def fake_get(url, **kwargs):
            return _response(
                self.ACCOUNTS if url.endswith("/Accounts") else report)

        with patch("integrations.providers.requests.get", side_effect=fake_get):
            lines = XeroProvider().fetch_trial_balance(
                "tok", "tenant", date(2025, 6, 30))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["account_code"], "960")
        self.assertEqual(lines[0]["provider_system_account"],
                         "RETAINEDEARNINGS")

    def test_the_two_call_merge_carries_the_system_account_through(self):
        """period_movement is the mode the UI uses. Dropping the field in the
        merge would leave the exclusion inert -- the same way the account code
        was dropped before it."""
        period = [{
            "account_code": "960", "account_name": "Retained Earnings9",
            "provider_account_id": "guid-re",
            "provider_section": "balance_sheet",
            "provider_system_account": "RETAINEDEARNINGS",
            "debit": Decimal("0"), "credit": Decimal("193541.84"),
        }]
        opening = [{
            "account_code": "960", "account_name": "Retained Earnings9",
            "provider_account_id": "guid-re",
            "provider_section": "balance_sheet",
            "provider_system_account": "RETAINEDEARNINGS",
            "debit": Decimal("0"), "credit": Decimal("291273.76"),
        }]
        provider = XeroProvider()
        with patch.object(provider, "fetch_trial_balance",
                          side_effect=[period, opening]):
            merged = provider.fetch_period_movement(
                "tok", "tenant", date(2024, 7, 1), date(2025, 6, 30))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["provider_system_account"],
                         "RETAINEDEARNINGS")


class EquityRollExclusionTests(TestCase):
    def test_the_retained_earnings_roll_is_dropped(self):
        kept, notes = _drop_xero_equity_roll_rows([_roll_row()])
        self.assertEqual(kept, [])
        self.assertEqual(len(notes), 1)

    def test_the_current_year_earnings_roll_is_dropped(self):
        kept, _ = _drop_xero_equity_roll_rows([
            _roll_row(account_code="970", account_name="Current Year Earnings",
                      provider_system_account="CURRENTYEAREARNINGS"),
        ])
        self.assertEqual(kept, [])

    def test_it_is_never_silent(self):
        """An account carrying Xero's roll can also carry a genuine manual
        journal, and this removes both. The accountant has to be told."""
        _, notes = _drop_xero_equity_roll_rows([_roll_row()])
        self.assertIn("960", notes[0])
        self.assertIn("Retained Earnings9", notes[0])
        self.assertIn("97,731.92", notes[0])
        self.assertIn("Dr", notes[0])

    def test_gst_is_a_system_account_and_must_still_import(self):
        """820 GST is a Xero system account too, and a real one: DJLH carries
        -316,467.12 on it. Excluding every system account would gut the
        import."""
        gst = _roll_row(account_code="820", account_name="GST",
                        provider_system_account="GST")
        kept, notes = _drop_xero_equity_roll_rows([gst])
        self.assertEqual(kept, [gst])
        self.assertEqual(notes, [])

    def test_the_clients_own_retained_earnings_account_still_imports(self):
        """3-4000 Retained Earnings is user-created in this chart -- same name,
        no SystemAccount. Matching on the name would have dropped it."""
        own = _roll_row(account_code="3-4000",
                        account_name="Retained Earnings",
                        provider_system_account="")
        kept, notes = _drop_xero_equity_roll_rows([own])
        self.assertEqual(kept, [own])
        self.assertEqual(notes, [])

    def test_a_provider_without_the_field_is_untouched(self):
        """QuickBooks and a Xero connection with no chart access supply no
        system account. Neither may be filtered."""
        rows = [{"account_code": "960", "account_name": "Retained Earnings",
                 "period_debit": Decimal("0"),
                 "period_credit": Decimal("193541.84")}]
        kept, notes = _drop_xero_equity_roll_rows(rows)
        self.assertEqual(kept, rows)
        self.assertEqual(notes, [])

    def test_the_roll_was_the_whole_imbalance(self):
        """The DJLH regression, in the figures off the live review screen:
        Dr 303,053.57 / Cr 205,321.65 balances the moment the roll comes out."""
        others = [{
            "account_code": "OTHERS", "account_name": "everything else",
            "provider_section": "balance_sheet",
            "provider_system_account": "",
            "period_debit": Decimal("205321.65"),
            "period_credit": Decimal("205321.65"),
            "opening_debit": Decimal("0"),
            "opening_credit": Decimal("0"),
        }]
        before_dr = Decimal("303053.57")
        before_cr = Decimal("205321.65")
        self.assertEqual(before_dr - before_cr, Decimal("97731.92"))

        kept, notes = _drop_xero_equity_roll_rows(others + [_roll_row()])

        self.assertEqual(len(kept), 1)
        self.assertEqual(len(notes), 1)
        after_dr = sum(r["period_debit"] for r in kept)
        after_cr = sum(r["period_credit"] for r in kept)
        self.assertEqual(after_dr - after_cr, Decimal("0"))
        self.assertEqual(before_dr - Decimal("97731.92"), after_dr)

    def test_the_constant_is_exactly_the_two_roll_accounts(self):
        self.assertEqual(
            set(XERO_EQUITY_ROLL_SYSTEM_ACCOUNTS),
            {"RETAINEDEARNINGS", "CURRENTYEAREARNINGS"},
        )
