"""The audit command reports; it never writes."""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import TrialBalanceLine
from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, bs_line, make_bank_mapping, make_entity, make_fy,
    make_job, make_txn,
)
from core.txn_periods import resolve_fy_for_txn
from core.views import _post_txn_to_tb

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class AuditBankTbDesyncTests(TestCase):
    def setUp(self):
        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)

    def _post(self, date_str, amount, code="0400"):
        txn = make_txn(self.job, date_str=date_str, amount=amount, code=code)
        _post_txn_to_tb(txn, resolve_fy_for_txn(txn), has_gst=False)
        return txn

    def _run(self):
        out, err = StringIO(), StringIO()
        try:
            call_command("audit_bank_tb_desync", "--entity", str(self.entity.pk),
                         stdout=out, stderr=err)
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue() + err.getvalue()

    def test_clean_book_exits_zero(self):
        self._post("2025-08-01", "-110.00")
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("no variance", output.lower())

    def test_variance_is_reported_and_exits_non_zero(self):
        self._post("2025-08-01", "-110.00")
        line = bs_line(self.fy, "0400")
        line.debit = D("999.00")          # simulate the desync defect
        line.save(update_fields=["debit"])

        code, output = self._run()

        self.assertEqual(code, 1)
        self.assertIn("0400", output)
        self.assertIn("999.00", output)
        self.assertIn("110.00", output)

    def test_the_command_writes_nothing(self):
        self._post("2025-08-01", "-110.00")
        line = bs_line(self.fy, "0400")
        line.debit = D("999.00")
        line.save(update_fields=["debit"])

        self._run()

        line.refresh_from_db()
        self.assertEqual(line.debit, D("999.00"), "the audit must not repair")

    def test_entanglement_is_reported_as_its_own_category(self):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan account", source="manual_journal",
            is_adjustment=True, debit=D("62500.00"), closing_balance=D("62500.00"),
        )
        txn = make_txn(self.job, date_str="2025-08-01", amount="-500.00", code="3565")
        txn.posted_to_tb = True
        txn.save(update_fields=["posted_to_tb"])

        code, output = self._run()

        self.assertEqual(code, 1)
        self.assertIn("ENTANGLED", output)
        self.assertIn("3565", output)
        self.assertIn("manual_journal", output)

    def test_a_reopened_year_is_reported_as_not_audited_not_as_variance(self):
        """fy_resolvable=False must not be read as 'every account vacated'."""
        self._post("2025-08-01", "-110.00")
        self.fy.status = "reopened"
        self.fy.save(update_fields=["status"])

        code, output = self._run()

        self.assertNotIn("VARIANCE", output)
        self.assertIn(self.fy.year_label, output)
        self.assertIn("not audited", output.lower())
