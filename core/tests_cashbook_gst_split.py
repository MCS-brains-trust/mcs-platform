from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import AdjustingJournal, JournalLine


class CashbookModelFieldsTest(TestCase):
    def test_cashbook_journal_type_exists(self):
        self.assertEqual(AdjustingJournal.JournalType.CASHBOOK, "cashbook")
        self.assertIn(
            ("cashbook", "Cashbook (Cash Basis)"),
            AdjustingJournal.JournalType.choices,
        )

    def test_journal_line_gst_fields_default_to_today_behaviour(self):
        line = JournalLine()
        self.assertEqual(line.tax_code, "")
        self.assertEqual(line.gst_amount, Decimal("0"))
        self.assertIsNone(line.gst_override)
        self.assertIs(line.is_gst_control, False)


from core.models import Entity, EntityChartOfAccount, FinancialYear


def _entity_with_chart():
    """A GST-registered sole trader with the accounts JE-001 actually uses.

    Tools deliberately carries lowercase 'inp' — that is what the live chart
    holds, and a bare `tax_code in TAXABLE_CODES` test silently treats it as
    GST-free.
    """
    entity = Entity.objects.create(
        entity_name="Test Sole Trader",
        entity_type=Entity.EntityType.SOLE_TRADER,
        is_gst_registered=True,
    )
    chart = [
        ("105", "Sales", "GST", "revenue"),
        ("1510", "Accountancy", "INP", "expenses"),
        ("1804", "M/V car - Fuel & oil", "INP", "expenses"),
        ("1946", "Tools", "inp", "expenses"),
        ("1990", "Bank charges", "FRE", "expenses"),
        ("3380", "GST payable control account", "", "liabilities"),
        ("4080", "Drawings", "", "capital_accounts"),
    ]
    for code, name, tax, section in chart:
        EntityChartOfAccount.objects.create(
            entity=entity, account_code=code, account_name=name,
            tax_code=tax, section=section,
        )
    return entity


def _cashbook_journal(entity, rows):
    """rows: list of (account_code, account_name, dr_gross, cr_gross, tax_code)."""
    fy = FinancialYear.objects.create(
        entity=entity, year_label="Q2 2026",
        start_date=date(2025, 10, 1), end_date=date(2025, 12, 31),
    )
    journal = AdjustingJournal.objects.create(
        financial_year=fy,
        journal_type=AdjustingJournal.JournalType.CASHBOOK,
        journal_date=date(2025, 12, 31),
        description="Oct-Dec 2025 Income & Expenses",
    )
    for i, (code, name, dr, cr, tax) in enumerate(rows, start=1):
        JournalLine.objects.create(
            journal=journal, line_number=i,
            account_code=code, account_name=name,
            debit=Decimal(dr), credit=Decimal(cr), tax_code=tax,
        )
    return journal


class ResolveLineTaxCodeTest(TestCase):
    def setUp(self):
        self.entity = _entity_with_chart()

    def test_resolves_from_entity_chart(self):
        from core.gst_journal import resolve_line_tax_code
        self.assertEqual(resolve_line_tax_code(self.entity, "105"), "GST")

    def test_normalises_lowercase_chart_value(self):
        """Account 1946 Tools carries 'inp' lowercase on the live file."""
        from core.gst_journal import resolve_line_tax_code
        self.assertEqual(resolve_line_tax_code(self.entity, "1946"), "INP")

    def test_unknown_account_returns_blank(self):
        from core.gst_journal import resolve_line_tax_code
        self.assertEqual(resolve_line_tax_code(self.entity, "9999"), "")


class LineGstTest(TestCase):
    def test_taxable_line_gets_one_eleventh_rounded_half_up(self):
        from core.gst_journal import line_gst
        self.assertEqual(line_gst(Decimal("1990.40"), "INP"), Decimal("180.95"))
        self.assertEqual(line_gst(Decimal("23187.00"), "GST"), Decimal("2107.91"))
        self.assertEqual(line_gst(Decimal("250.00"), "INP"), Decimal("22.73"))

    def test_lowercase_tax_code_is_still_taxable(self):
        from core.gst_journal import line_gst
        self.assertEqual(line_gst(Decimal("510.00"), "inp"), Decimal("46.36"))

    def test_non_taxable_codes_get_nil(self):
        from core.gst_journal import line_gst
        for code in ("FRE", "N-T", ""):
            self.assertEqual(line_gst(Decimal("500.00"), code), Decimal("0.00"))

    def test_override_wins_over_the_computed_figure(self):
        from core.gst_journal import line_gst
        self.assertEqual(
            line_gst(Decimal("1990.40"), "INP", override=Decimal("145.50")),
            Decimal("145.50"),
        )

    def test_override_on_a_non_taxable_line_is_forced_to_nil(self):
        """Changing a line from GST to FRE must not leave a stale override."""
        from core.gst_journal import line_gst
        self.assertEqual(
            line_gst(Decimal("1990.40"), "FRE", override=Decimal("180.95")),
            Decimal("0.00"),
        )


class SplitCashbookJournalTest(TestCase):
    def setUp(self):
        self.entity = _entity_with_chart()
        # JE-001's real shape, trimmed to four lines plus the drawings plug.
        self.journal = _cashbook_journal(self.entity, [
            ("105", "Sales", "0", "23187.00", "GST"),
            ("1510", "Accountancy", "250.00", "0", "INP"),
            ("1804", "M/V car - Fuel & oil", "1990.40", "0", "INP"),
            ("1946", "Tools", "510.00", "0", "inp"),
            ("4080", "Drawings", "20436.60", "0", "N-T"),
        ])

    def _lines(self):
        return list(self.journal.lines.order_by("line_number", "id"))

    def test_lines_become_net_and_two_control_lines_are_appended(self):
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        by_code = {l.account_code: l for l in self._lines() if not l.is_gst_control}
        self.assertEqual(by_code["105"].credit, Decimal("21079.09"))
        self.assertEqual(by_code["105"].gst_amount, Decimal("2107.91"))
        self.assertEqual(by_code["1510"].debit, Decimal("227.27"))
        self.assertEqual(by_code["1804"].debit, Decimal("1809.45"))
        self.assertEqual(by_code["1946"].debit, Decimal("463.64"))
        self.assertEqual(by_code["4080"].debit, Decimal("20436.60"))
        self.assertEqual(by_code["4080"].gst_amount, Decimal("0.00"))

        controls = [l for l in self._lines() if l.is_gst_control]
        self.assertEqual(len(controls), 2)
        collected = next(l for l in controls if l.credit)
        paid = next(l for l in controls if l.debit)
        self.assertEqual(collected.account_code, "3380")
        self.assertEqual(collected.credit, Decimal("2107.91"))
        self.assertEqual(paid.account_code, "3380")
        self.assertEqual(paid.debit, Decimal("250.04"))  # 22.73 + 180.95 + 46.36

    def test_split_journal_still_balances(self):
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        lines = self._lines()
        self.assertEqual(
            sum(l.debit for l in lines), sum(l.credit for l in lines),
        )

    def test_control_lines_sort_last(self):
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        lines = self._lines()
        self.assertFalse(lines[0].is_gst_control)
        self.assertTrue(lines[-1].is_gst_control)
        self.assertTrue(lines[-2].is_gst_control)

    def test_splitting_twice_equals_splitting_once(self):
        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        first = [
            (l.account_code, l.debit, l.credit, l.gst_amount, l.is_gst_control)
            for l in self._lines()
        ]
        split_cashbook_journal(self.journal)
        second = [
            (l.account_code, l.debit, l.credit, l.gst_amount, l.is_gst_control)
            for l in self._lines()
        ]
        self.assertEqual(first, second)

    def test_hand_override_survives_a_resplit_and_net_absorbs_the_rest(self):
        from core.gst_journal import split_cashbook_journal
        fuel = self.journal.lines.get(account_code="1804")
        fuel.gst_override = Decimal("145.50")  # 80% business use
        fuel.save(update_fields=["gst_override"])
        split_cashbook_journal(self.journal)
        fuel.refresh_from_db()
        self.assertEqual(fuel.gst_amount, Decimal("145.50"))
        self.assertEqual(fuel.debit, Decimal("1844.90"))
        split_cashbook_journal(self.journal)
        fuel.refresh_from_db()
        self.assertEqual(fuel.gst_amount, Decimal("145.50"))
        self.assertEqual(fuel.debit, Decimal("1844.90"))

    def test_an_accountants_own_3380_line_survives_the_split(self):
        JournalLine.objects.create(
            journal=self.journal, line_number=99,
            account_code="3380", account_name="ATO payment",
            debit=Decimal("500.00"), credit=Decimal("0"),
            tax_code="N-T", is_gst_control=False,
        )
        # Rebalance so the journal is postable.
        drawings = self.journal.lines.get(account_code="4080")
        drawings.debit = Decimal("19936.60")
        drawings.save(update_fields=["debit"])

        from core.gst_journal import split_cashbook_journal
        split_cashbook_journal(self.journal)
        manual = self.journal.lines.filter(
            account_code="3380", is_gst_control=False,
        )
        self.assertEqual(manual.count(), 1)
        self.assertEqual(manual.first().debit, Decimal("500.00"))

    def test_blank_tax_code_falls_back_to_the_chart(self):
        from core.gst_journal import split_cashbook_journal
        line = self.journal.lines.get(account_code="1804")
        line.tax_code = ""
        line.save(update_fields=["tax_code"])
        split_cashbook_journal(self.journal)
        line.refresh_from_db()
        self.assertEqual(line.tax_code, "INP")
        self.assertEqual(line.gst_amount, Decimal("180.95"))

    def test_non_cashbook_journal_is_left_completely_alone(self):
        """A Hazaway JE-002-shaped migration journal posts already-net figures."""
        from core.gst_journal import split_cashbook_journal
        self.journal.journal_type = AdjustingJournal.JournalType.GENERAL
        self.journal.save(update_fields=["journal_type"])
        before = [
            (l.account_code, l.debit, l.credit, l.gst_amount, l.is_gst_control)
            for l in self._lines()
        ]
        split_cashbook_journal(self.journal)
        after = [
            (l.account_code, l.debit, l.credit, l.gst_amount, l.is_gst_control)
            for l in self._lines()
        ]
        self.assertEqual(before, after)

    def test_unbalanced_split_raises_rather_than_saving(self):
        from core.gst_journal import split_cashbook_journal
        self.journal.lines.get(account_code="4080").delete()
        with self.assertRaises(ValueError):
            split_cashbook_journal(self.journal)
