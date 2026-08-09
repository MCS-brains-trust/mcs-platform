"""Tests for the trial-balance workbook fixtures."""
import tempfile
from decimal import Decimal
from pathlib import Path

import openpyxl
from django.test import SimpleTestCase

from core.e2e_tb_workbooks import write_tb_workbooks


def _totals(path):
    rows = list(openpyxl.load_workbook(path, data_only=True).active.iter_rows(values_only=True))
    debit = sum(Decimal(str(r[2])) for r in rows[1:] if r[2] is not None)
    credit = sum(Decimal(str(r[3])) for r in rows[1:] if r[3] is not None)
    return debit, credit


class WriteTbWorkbooksTests(SimpleTestCase):
    def test_writes_three_workbooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_tb_workbooks(tmp)
            self.assertEqual(set(paths), {"balanced", "unbalanced", "rounding"})
            for path in paths.values():
                self.assertTrue(Path(path).exists())

    def test_balanced_workbook_balances_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            debit, credit = _totals(write_tb_workbooks(tmp)["balanced"])
            self.assertEqual(debit, credit)

    def test_unbalanced_workbook_exceeds_the_two_cent_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            debit, credit = _totals(write_tb_workbooks(tmp)["unbalanced"])
            self.assertGreater(abs(debit - credit), Decimal("0.02"))

    def test_rounding_workbook_sits_inside_the_tolerance_but_is_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            debit, credit = _totals(write_tb_workbooks(tmp)["rounding"])
            diff = abs(debit - credit)
            self.assertGreater(diff, Decimal("0"))
            self.assertLessEqual(diff, Decimal("0.02"))

    def test_the_workbook_directory_is_not_world_readable(self):
        """
        The workbooks themselves are written 0600 by atomic_write, but the directory
        holding them was created with mkdir's default 0755, so any account on the box
        could list it. Matches the 0700 that refresh_e2e_db.sh applies to .e2e/dumps.
        """
        import os
        import stat

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tb"
            write_tb_workbooks(target)
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o700)

    def test_an_existing_directory_is_tightened_too(self):
        """
        .e2e/tb already exists on every box this has ever run on, and mkdir with
        exist_ok=True leaves an existing directory's mode alone.
        """
        import os
        import stat

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tb"
            target.mkdir(mode=0o755)
            os.chmod(target, 0o755)

            write_tb_workbooks(target)
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o700)
