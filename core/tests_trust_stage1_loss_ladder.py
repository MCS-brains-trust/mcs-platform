"""Stage 1 must show the profit, then the losses that absorb it.

The Income Calculation jumped straight to Net Distributable Income, so a trust
whose profit had been swallowed by carried-forward losses showed a bare $0 with
no explanation. The profit, the absorption and the remaining carried balance
were all invisible.

Its Total Revenue and Total Expenses cards were never populated at all --
``trust_tab.html`` has the elements, but nothing in the JS or the serializer
ever wrote to them, so every trust on the platform read $0 in both.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    AccountMapping, Entity, FinancialYear, TrialBalanceLine, TrustWorkspace,
)


class Stage1LossLadderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ladder", email="ladder@example.com", password="secret123",
            role="senior_accountant",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        # A unit trust, as Minli is. _entity_has_unitholders short-circuits on
        # entity_type for a unit trust; for a discretionary trust it falls
        # through to a JSONField `roles__contains` lookup that sqlite cannot
        # execute, so the workspace API is unreachable under the test rig.
        # The ladder itself is entity-type agnostic.
        self.entity = Entity.objects.create(
            entity_name="Ladder Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
        )

    @staticmethod
    def _mapping(section):
        m, _ = AccountMapping.objects.get_or_create(
            standard_code=f"LDR-{section}",
            defaults={"line_item_label": section.title(),
                      "financial_statement": "income_statement",
                      "statement_section": section},
        )
        return m

    def _build(self, revenue, expenses, brought_forward=None):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="0630", account_name="Sales",
            closing_balance=-Decimal(revenue), debit=Decimal("0"),
            credit=Decimal(revenue), source="tb_import",
            mapped_line_item=self._mapping("revenue"),
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="1510",
            account_name="Accountancy", closing_balance=Decimal(expenses),
            debit=Decimal(expenses), credit=Decimal("0"), source="tb_import",
            mapped_line_item=self._mapping("expenses"),
        )
        if brought_forward is not None:
            bf = Decimal(brought_forward)
            TrialBalanceLine.objects.create(
                financial_year=self.fy, account_code="4199",
                account_name="Undistributed income", closing_balance=bf,
                debit=bf if bf > 0 else Decimal("0"),
                credit=-bf if bf < 0 else Decimal("0"), source="rollover",
            )

    def _workspace_payload(self):
        response = self.client.get(
            reverse("core:trust_workspace_api", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_the_ladder_follows_the_profit_through_the_losses(self):
        self._build("216101.66", "0.00", "1628428.89")
        data = self._workspace_payload()
        self.assertEqual(Decimal(data["total_revenue"]), Decimal("216101.66"))
        self.assertEqual(Decimal(data["total_expenses"]), Decimal("0"))
        self.assertEqual(Decimal(data["net_profit"]), Decimal("216101.66"))
        self.assertEqual(
            Decimal(data["losses_absorbed"]), Decimal("216101.66"),
            "the absorption step is missing, so the $0 has no explanation",
        )
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("0"))
        self.assertEqual(
            Decimal(data["losses_carried_forward"]), Decimal("1412327.23"))

    def test_revenue_and_expenses_are_reported_separately(self):
        """The two cards that have always read $0."""
        self._build("300000.00", "120000.00")
        data = self._workspace_payload()
        self.assertEqual(Decimal(data["total_revenue"]), Decimal("300000.00"))
        self.assertEqual(Decimal(data["total_expenses"]), Decimal("120000.00"))
        self.assertEqual(Decimal(data["net_profit"]), Decimal("180000.00"))

    def test_partial_absorption_leaves_the_excess_distributable(self):
        self._build("100000.00", "0.00", "30000.00")
        data = self._workspace_payload()
        self.assertEqual(Decimal(data["losses_absorbed"]), Decimal("30000.00"))
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("70000.00"))
        self.assertEqual(
            Decimal(data["losses_carried_forward"]), Decimal("0"))

    def test_a_trust_with_no_losses_absorbs_nothing(self):
        """Regression guard for every trust already on the platform."""
        self._build("100000.00", "40000.00")
        data = self._workspace_payload()
        self.assertEqual(Decimal(data["losses_absorbed"]), Decimal("0"))
        self.assertEqual(Decimal(data["losses_carried_forward"]), Decimal("0"))
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("60000.00"))

    def test_a_loss_year_adds_its_own_loss_to_the_carried_balance(self):
        self._build("0.00", "568879.30", "1686352.10")
        data = self._workspace_payload()
        self.assertEqual(Decimal(data["net_profit"]), Decimal("-568879.30"))
        self.assertEqual(Decimal(data["losses_absorbed"]), Decimal("0"))
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("0"))
        self.assertEqual(
            Decimal(data["losses_carried_forward"]), Decimal("2255231.40"))

    def test_undistributed_income_brought_forward_is_added_not_absorbed(self):
        self._build("100000.00", "0.00", "-10000.00")
        data = self._workspace_payload()
        self.assertEqual(
            Decimal(data["undistributed_brought_forward"]), Decimal("10000.00"))
        self.assertEqual(Decimal(data["losses_absorbed"]), Decimal("0"))
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("110000.00"))

    def test_an_existing_workspace_still_reports_a_truthful_ladder(self):
        """The ladder must not read 0/0/0 above a non-zero NDI.

        Every workspace already on the platform predates these fields, so
        their snapshots default to nil while net_distributable_income keeps
        its stored value. Six of the seven live workspaces also have Stage 1
        marked completed, and trust_recalculate_income refuses to run on a
        completed stage -- so a snapshot-driven ladder could not be refreshed
        by the user at all. It is derived from the ledger instead.
        """
        self._build("216101.66", "0.00", "1628428.89")
        TrustWorkspace.objects.create(
            financial_year=self.fy,
            stage_1_status=TrustWorkspace.StageStatus.COMPLETED,
            net_distributable_income=Decimal("876322.95"),  # stale
        )
        data = self._workspace_payload()
        self.assertEqual(Decimal(data["net_profit"]), Decimal("216101.66"))
        self.assertEqual(Decimal(data["total_revenue"]), Decimal("216101.66"))
        self.assertEqual(
            Decimal(data["losses_absorbed"]), Decimal("216101.66"))
        self.assertEqual(
            Decimal(data["losses_carried_forward"]), Decimal("1412327.23"))

    def test_a_stale_stored_figure_does_not_survive_into_the_ladder(self):
        """Minli's workspace held 876,322.95 from superseded calculations.

        The post gate already refuses to trust the stored figure and
        recomputes from the ledger; Stage 1 must show the same number the
        gate will enforce, or it invites a distribution that cannot post.
        """
        self._build("216101.66", "0.00", "1628428.89")
        TrustWorkspace.objects.create(
            financial_year=self.fy,
            stage_1_status=TrustWorkspace.StageStatus.COMPLETED,
            net_distributable_income=Decimal("876322.95"),
        )
        data = self._workspace_payload()
        self.assertEqual(
            Decimal(data["net_distributable_income"]), Decimal("0"),
            "Stage 1 offered a stale figure the post gate would refuse",
        )

    def test_the_ladder_survives_a_trial_balance_change(self):
        """Derived, not snapshotted: a later TB edit must be reflected."""
        self._build("216101.66", "0.00", "1628428.89")
        self._workspace_payload()
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="0631",
            account_name="Other income", closing_balance=Decimal("-1000.00"),
            debit=Decimal("0"), credit=Decimal("1000.00"), source="tb_import",
            mapped_line_item=self._mapping("revenue"),
        )
        data = self._workspace_payload()
        self.assertEqual(
            Decimal(data["total_revenue"]), Decimal("217101.66"),
            "the ladder was read from a stale snapshot",
        )
        self.assertEqual(Decimal(data["net_profit"]), Decimal("217101.66"))
