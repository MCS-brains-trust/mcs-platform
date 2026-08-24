"""The HandiLedger ZIP import must leave a chart row behind every TB code.

This is the path that produced the live orphans: Berwick Mechanical Services
carries 16 codes loaded 2026-03-24 with source='tb_import' and no
EntityChartOfAccount row, so none of them can be picked in the allocation
dropdown. It writes its trial balance with bulk_create, which does not send
post_save — so the receiver in core/signals.py cannot cover it and the
import has to ask for the sync itself.
"""
import io
import zipfile

from django.test import TestCase

from core.models import Entity, EntityChartOfAccount, TrialBalanceLine
from .access_ledger_import import import_access_ledger_zip

CODE = "TEST0002"
NAME = "Chartless Holdings Pty Ltd"
# (code, name, type) — 1612 is the DJLH code that started this; it is not in
# the standard company chart, which is the whole point.
ACCOUNTS = [(1612, "Development cost", "D"), (3546, "Loan - Jim Penman", "C")]


def build_zip_with_balances(year=2024):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        folder = f"HL_{CODE}_{year}"
        zf.writestr(f"{folder}/Client.txt", f'"0","{CODE}","{NAME}",""\n')
        zf.writestr(
            f"{folder}/Year.txt",
            f'"{CODE}",{year},"01/07/{year - 1}","30/06/{year}","Y","Y","Y"\n',
        )
        # Chart.txt: [0] code, [1] sub, [3] name, [6] type
        zf.writestr(f"{folder}/Chart.txt", "".join(
            f'{code},0,0,"{name}",0,0,"{typ}"\n' for code, name, typ in ACCOUNTS
        ))
        # Balance.txt: 20 cols — [2] code, [3] sub, [4] opening,
        # [5]-[16] months, [18] net. Movement parked in month 12.
        rows = []
        for code, _name, typ in ACCOUNTS:
            amount = "-1000.00" if typ == "D" else "1000.00"
            cols = ([str(year), "0", str(code), "0", "0.00"]
                    + ["0.00"] * 11 + [amount, "0.00", amount, "0"])
            rows.append(",".join(cols))
        zf.writestr(f"{folder}/Balance.txt", "\n".join(rows) + "\n")
    buf.seek(0)
    return buf


class HandiLedgerImportSyncsTheChartTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name=NAME, entity_type="company")

    def test_the_import_produces_trial_balance_lines(self):
        """Fixture sanity — without this the real assertion proves nothing."""
        import_access_ledger_zip(build_zip_with_balances(), client=None,
                                 entity=self.entity, replace_existing=False)
        codes = set(TrialBalanceLine.objects
                    .filter(financial_year__entity=self.entity)
                    .values_list("account_code", flat=True))
        self.assertIn("1612", codes)

    def test_every_imported_code_lands_in_the_chart(self):
        import_access_ledger_zip(build_zip_with_balances(), client=None,
                                 entity=self.entity, replace_existing=False)
        tb_codes = set(TrialBalanceLine.objects
                       .filter(financial_year__entity=self.entity)
                       .values_list("account_code", flat=True))
        chart_codes = set(EntityChartOfAccount.objects
                          .filter(entity=self.entity)
                          .values_list("account_code", flat=True))
        self.assertEqual(tb_codes - chart_codes, set())
