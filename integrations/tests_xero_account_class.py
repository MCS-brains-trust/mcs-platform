"""Xero cloud import: the account code, and what it is allowed to decide.

The bug these cover, found on a live import: every balance-sheet account came
in at its FULL position rather than its movement for the year -- land at
1,006,627.56, retained earnings, GST, every loan. The balance-sheet
differencing was implemented and correct, and it never ran on a single line,
because Xero's TrialBalance report identifies rows by AccountID (a GUID) and
the parser used that GUID as the account code. A GUID matches no chart and no
code range, so every account classified as P&L and passed straight through.

The chart is the fix: Xero's /Accounts endpoint gives the real Code and the
account Class, and Class is the only classification that survives a foreign
chart. The client that exposed this runs MYOB-style codes inside Xero
(1-6100 Land, 2-2200 Bank Loans, 3-4000 Retained Earnings) next to Xero
defaults (820 GST, 960 Retained Earnings): the first match no range at all,
and the second fall in the range that reads as income.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from core.models import Entity
from integrations.providers import XeroProvider
from integrations.views import _apply_bs_movement_differencing


def _response(payload, status=200):
    class R:
        status_code = status

        def json(self):
            return payload

        def raise_for_status(self):
            if status >= 400:
                raise AssertionError(f"unexpected raise_for_status at {status}")
    return R()


ACCOUNTS = {"Accounts": [
    {"AccountID": "guid-land", "Code": "1-6100", "Name": "Land", "Class": "ASSET"},
    {"AccountID": "guid-gst", "Code": "820", "Name": "GST", "Class": "LIABILITY"},
    {"AccountID": "guid-re", "Code": "960", "Name": "Retained Earnings",
     "Class": "EQUITY"},
    {"AccountID": "guid-rent", "Code": "4-1900", "Name": "Rental Income",
     "Class": "REVENUE"},
]}


def _report(rows):
    return {"Reports": [{"Rows": [
        {"RowType": "Header", "Cells": [
            {"Value": "Account"}, {"Value": "Debit"}, {"Value": "Credit"},
        ]},
        {"RowType": "Section", "Rows": rows},
    ]}]}


def _row(account_id, display_name, debit="0", credit="0"):
    return {"RowType": "Row", "Cells": [
        {"Value": display_name, "Attributes": [
            {"Id": "account", "Value": account_id}]},
        {"Value": debit},
        {"Value": credit},
    ]}


class XeroAccountCodeTests(TestCase):
    def setUp(self):
        self.provider = XeroProvider()

    def _fetch(self, rows, accounts=ACCOUNTS):
        def fake_get(url, **kwargs):
            if url.endswith("/Accounts"):
                return _response(accounts)
            return _response(_report(rows))
        with patch("integrations.providers.requests.get", side_effect=fake_get):
            return self.provider.fetch_trial_balance(
                "token", "tenant", date(2025, 6, 30))

    def test_the_account_code_is_the_code_not_the_guid(self):
        lines = self._fetch([_row("guid-land", "Land (1-6100)", debit="1006627.56")])
        self.assertEqual(lines[0]["account_code"], "1-6100")
        self.assertEqual(lines[0]["account_name"], "Land")
        self.assertEqual(lines[0]["provider_account_id"], "guid-land")

    def test_the_section_comes_from_xeros_own_class(self):
        lines = self._fetch([
            _row("guid-land", "Land (1-6100)", debit="1006627.56"),
            _row("guid-gst", "GST (820)", debit="120401.13"),
            _row("guid-re", "Retained Earnings (960)", credit="490234.54"),
            _row("guid-rent", "Rental Income (4-1900)", credit="20663.65"),
        ])
        sections = {l["account_code"]: l["provider_section"] for l in lines}
        self.assertEqual(sections, {
            "1-6100": "balance_sheet",   # no code range would place this
            "820": "balance_sheet",      # a range reads 820 as income
            "960": "balance_sheet",      # and 960 too
            "4-1900": "profit_and_loss",
        })

    def test_without_the_catalogue_the_code_comes_from_the_name(self):
        """An older connection without accounting.settings.read still reads,
        just without a section — the import then classifies by code as before
        rather than failing."""
        lines = self._fetch([_row("guid-land", "Land (1-6100)", debit="10.00")],
                            accounts={"Accounts": []})
        self.assertEqual(lines[0]["account_code"], "1-6100")
        self.assertEqual(lines[0]["account_name"], "Land")
        self.assertIsNone(lines[0]["provider_section"])

    def test_a_name_with_no_code_keeps_the_guid_rather_than_inventing_one(self):
        lines = self._fetch([_row("guid-x", "Suspense", debit="1.00")],
                            accounts={"Accounts": []})
        self.assertEqual(lines[0]["account_code"], "guid-x")
        self.assertEqual(lines[0]["account_name"], "Suspense")

    def test_the_catalogue_is_fetched_once_for_both_calls(self):
        """A period-movement import reads the report twice; the chart does not
        change between them."""
        calls = {"accounts": 0}

        def fake_get(url, **kwargs):
            if url.endswith("/Accounts"):
                calls["accounts"] += 1
                return _response(ACCOUNTS)
            return _response(_report([_row("guid-land", "Land (1-6100)", debit="5")]))

        with patch("integrations.providers.requests.get", side_effect=fake_get):
            self.provider.fetch_period_movement(
                "token", "tenant", date(2024, 7, 1), date(2025, 6, 30))
        self.assertEqual(calls["accounts"], 1)

    def test_period_movement_carries_the_section_through_the_merge(self):
        """The merge rebuilds each entry from a fixed key set, so the section
        has to be carried explicitly or the view never sees it — and
        period_movement is the mode the UI uses."""
        def fake_get(url, **kwargs):
            if url.endswith("/Accounts"):
                return _response(ACCOUNTS)
            return _response(_report([_row("guid-land", "Land (1-6100)", debit="5")]))

        with patch("integrations.providers.requests.get", side_effect=fake_get):
            lines = self.provider.fetch_period_movement(
                "token", "tenant", date(2024, 7, 1), date(2025, 6, 30))
        self.assertEqual(lines[0]["provider_section"], "balance_sheet")


class BalanceSheetDifferencingTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(entity_name="Cloudworth Pty Ltd",
                                            entity_type="company")

    def test_a_balance_sheet_account_imports_its_movement_not_its_position(self):
        """Land sitting at 1,006,627.56 all year moved by nothing, so it must
        not import at all. Before the fix it imported in full, on top of the
        rolled-forward opening balance."""
        rows = _apply_bs_movement_differencing(self.entity, [{
            "account_code": "1-6100", "account_name": "Land",
            "provider_section": "balance_sheet",
            "period_debit": Decimal("1006627.56"),
            "period_credit": Decimal("0"),
            "opening_debit": Decimal("1006627.56"),
            "opening_credit": Decimal("0"),
        }])
        self.assertEqual(rows, [])

    def test_a_balance_sheet_movement_is_the_difference(self):
        rows = _apply_bs_movement_differencing(self.entity, [{
            "account_code": "820", "account_name": "GST",
            "provider_section": "balance_sheet",
            "period_debit": Decimal("120401.13"),
            "period_credit": Decimal("0"),
            "opening_debit": Decimal("100000.00"),
            "opening_credit": Decimal("0"),
        }])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["debit"], Decimal("20401.13"))
        self.assertEqual(rows[0]["credit"], Decimal("0"))

    def test_a_profit_and_loss_account_passes_through_in_full(self):
        rows = _apply_bs_movement_differencing(self.entity, [{
            "account_code": "4-1900", "account_name": "Rental Income",
            "provider_section": "profit_and_loss",
            "period_debit": Decimal("0"),
            "period_credit": Decimal("20663.65"),
            "opening_debit": Decimal("0"),
            "opening_credit": Decimal("0"),
        }])
        self.assertEqual(rows[0]["credit"], Decimal("20663.65"))

    def test_without_a_section_it_still_classifies_by_code(self):
        """Unchanged behaviour where the provider supplies nothing: 2800 is a
        balance-sheet code by the HandiLedger range, so it differences."""
        rows = _apply_bs_movement_differencing(self.entity, [{
            "account_code": "2800", "account_name": "Property",
            "period_debit": Decimal("500.00"),
            "period_credit": Decimal("0"),
            "opening_debit": Decimal("400.00"),
            "opening_credit": Decimal("0"),
        }])
        self.assertEqual(rows[0]["debit"], Decimal("100.00"))
