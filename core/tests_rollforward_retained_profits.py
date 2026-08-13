"""
Regression tests for identifying the retained-profits account during roll forward.

_populate_rolled_forward_fy closes the year's net P&L result into the entity's
retained-profits account. It found that account by numeric code alone --
`code_prefix == "4199"` -- which is a HandiLedger code that a hyphenated
MYOB/Xero-style chart never carries. On such a chart no retained_profits_line was
found, so the result was closed into a NEW synthesised 4199 line instead: total
equity came out right, but the entity ended up with two retained-earnings accounts
on its balance sheet and its real one kept its prior closing balance untouched
where the year's result should have been absorbed.

The income-tax line in the same function was already detected by account NAME and
by mapped line item as well as by code. Retained profits now is too.

Found by the Tier 2 e2e roll-forward spec; see e2e/tier2/known_failures.json.
"""

from datetime import date
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import (
    Client as ClientModel,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
)
from core.views import _expected_next_year_openings, _populate_rolled_forward_fy

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# The shape core/e2e_fixture_data.py seeds, and what a MYOB export looks like.
# Equity is 3-1000, never 4199.
HYPHENATED_CHART = [
    ("1-1000", "Cash at Bank", "current_assets"),
    ("1-2000", "Plant and Equipment", "non_current_assets"),
    ("1-2100", "Accumulated Depreciation", "non_current_assets"),
    ("2-1000", "Trade Creditors", "current_liabilities"),
    ("3-1000", "Retained Earnings", "equity"),
    ("4-1000", "Sales", "revenue"),
    ("6-1000", "Administration", "expenses"),
]

# Sales 30,000 credit less Administration 10,000 debit = 20,000 net profit.
PRIOR_FIGURES = [
    ("1-1000", "Cash at Bank", Decimal("70000.00")),
    ("1-2000", "Plant and Equipment", Decimal("20000.00")),
    ("1-2100", "Accumulated Depreciation", Decimal("-4000.00")),
    ("2-1000", "Trade Creditors", Decimal("-6000.00")),
    ("3-1000", "Retained Earnings", Decimal("-60000.00")),
    ("4-1000", "Sales", Decimal("-30000.00")),
    ("6-1000", "Administration", Decimal("10000.00")),
]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RetainedProfitsIdentificationTests(TestCase):
    """Each test builds its own entity: entity_type drives the detection."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="rollfwd_rp_admin",
            password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-rollfwd-rp",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Retained Profits Test Client")

    def _entity(self, entity_type, equity_account_name, equity_code="3-1000"):
        entity = Entity.objects.create(
            entity_name=f"RP Test {entity_type}",
            entity_type=entity_type,
            client=self.client_obj,
            primary_accountant=self.user,
        )
        for code, name, section in HYPHENATED_CHART:
            EntityChartOfAccount.objects.create(
                entity=entity,
                account_code=equity_code if code == "3-1000" else code,
                account_name=equity_account_name if code == "3-1000" else name,
                section=section,
                is_active=True,
            )
        return entity

    def _roll(self, entity, equity_account_name, equity_code="3-1000", extra_lines=()):
        prior_fy = FinancialYear.objects.create(
            entity=entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED,
        )
        new_fy = FinancialYear.objects.create(
            entity=entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        figures = [
            (
                equity_code if code == "3-1000" else code,
                equity_account_name if code == "3-1000" else name,
                closing,
            )
            for code, name, closing in PRIOR_FIGURES
        ]
        figures.extend(extra_lines)
        for code, name, closing in figures:
            TrialBalanceLine.objects.create(
                financial_year=prior_fy,
                account_code=code,
                account_name=name,
                closing_balance=closing,
                debit=closing if closing > 0 else Decimal("0"),
                credit=-closing if closing < 0 else Decimal("0"),
                source="tb_import",
            )

        _populate_rolled_forward_fy(prior_fy, new_fy)

        return {
            line.account_code: line
            for line in TrialBalanceLine.objects.filter(financial_year=new_fy)
        }

    def _assert_absorbed(self, rolled, equity_code="3-1000"):
        """The year's result lands in the entity's own equity account, and no
        second retained-earnings account is invented beside it."""
        self.assertIn(equity_code, rolled, f"{equity_code} did not roll forward at all")
        self.assertEqual(
            rolled[equity_code].opening_balance,
            Decimal("-80000.00"),
            f"{equity_code} did not absorb the year's $20,000 result",
        )
        self.assertNotIn(
            "4199", rolled,
            "a second retained-earnings account was synthesised alongside the "
            "entity's own",
        )
        self.assertEqual(
            sum(line.opening_balance for line in rolled.values()),
            Decimal("0.00"),
            "the rolled-forward opening balances must balance",
        )

    def test_a_company_closes_the_year_into_its_named_retained_earnings_account(self):
        entity = self._entity("company", "Retained Earnings")

        self._assert_absorbed(self._roll(entity, "Retained Earnings"))

    def test_a_trust_closes_the_year_into_undistributed_income(self):
        entity = self._entity("trust", "Undistributed Income")

        self._assert_absorbed(self._roll(entity, "Undistributed Income"))

    def test_a_partnership_closes_the_year_into_partners_current_accounts(self):
        entity = self._entity("partnership", "Partners' Current Accounts")

        self._assert_absorbed(self._roll(entity, "Partners' Current Accounts"))

    def test_a_sole_trader_closes_the_year_into_proprietors_funds(self):
        entity = self._entity("sole_trader", "Proprietor's Funds")

        self._assert_absorbed(self._roll(entity, "Proprietor's Funds"))

    def test_retained_profits_is_matched_however_the_name_is_spelled(self):
        for name in (
            "Retained Profits",
            "Accumulated Profits / (Losses)",
            "Unappropriated Profits",
        ):
            with self.subTest(name=name):
                entity = self._entity("company", name)

                self._assert_absorbed(self._roll(entity, name))

    def test_a_4199_account_still_wins_over_a_name_match(self):
        """
        A HandiLedger chart carrying both keeps its appropriation clearing account
        as the destination -- that is what Pass 5 later resets to nil.
        """
        entity = self._entity("company", "Retained Earnings")
        EntityChartOfAccount.objects.create(
            entity=entity, account_code="4199", account_name="Retained profits",
            section="equity", is_active=True,
        )

        rolled = self._roll(
            entity, "Retained Earnings",
            extra_lines=[("4199", "Retained profits", Decimal("0.00"))],
        )

        self.assertEqual(
            rolled["4199"].opening_balance, Decimal("-20000.00"),
            "the 4199 appropriation account should have taken the year's result",
        )
        self.assertEqual(
            rolled["3-1000"].opening_balance, Decimal("-60000.00"),
            "3-1000 should have carried its closing balance unchanged",
        )

    def test_other_equity_accounts_are_not_mistaken_for_retained_profits(self):
        """
        Guard on the name matching: an equity account that is not the retained
        earnings account must carry its own closing balance forward untouched, and
        must not swallow the year's result.
        """
        entity = self._entity("company", "Retained Earnings")
        for code, name in [
            ("3-2000", "Issued Capital"),
            ("3-3000", "Drawings"),
            ("3-4000", "Asset Revaluation Reserve"),
            ("1-1500", "Accumulated Interest Receivable"),
        ]:
            EntityChartOfAccount.objects.create(
                entity=entity, account_code=code, account_name=name,
                section="equity" if code.startswith("3-") else "current_assets",
                is_active=True,
            )

        rolled = self._roll(
            entity, "Retained Earnings",
            extra_lines=[
                ("3-2000", "Issued Capital", Decimal("-1000.00")),
                ("3-3000", "Drawings", Decimal("400.00")),
                ("3-4000", "Asset Revaluation Reserve", Decimal("-500.00")),
                ("1-1500", "Accumulated Interest Receivable", Decimal("1100.00")),
            ],
        )

        for code, expected in [
            ("3-2000", Decimal("-1000.00")),
            ("3-3000", Decimal("400.00")),
            ("3-4000", Decimal("-500.00")),
            ("1-1500", Decimal("1100.00")),
        ]:
            self.assertEqual(
                rolled[code].opening_balance, expected,
                f"{code} did not carry its own closing balance forward",
            )
        self.assertEqual(rolled["3-1000"].opening_balance, Decimal("-80000.00"))
        self.assertNotIn("4199", rolled)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RerollForwardAgreesWithRollForwardTests(TestCase):
    """
    reroll_forward carries its own two copies of the roll-forward algorithm — one
    for the POST that re-applies it, one for the GET that previews the diff — and
    each had its own `code_prefix == "4199"`. If either disagrees with
    _populate_rolled_forward_fy about which account holds retained profits, the
    reconciliation modal reports drift against figures that are actually correct,
    and re-rolling silently rewrites them.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="rollfwd_rp_reroll",
            password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-rollfwd-rp-reroll",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Reroll RP Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Reroll RP Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
            primary_accountant=cls.user,
        )
        for code, name, section in HYPHENATED_CHART:
            EntityChartOfAccount.objects.create(
                entity=cls.entity, account_code=code, account_name=name,
                section=section, is_active=True,
            )

    def setUp(self):
        self.http = Client()
        self.http.force_login(self.user)
        # Require2FAMiddleware requires TOTP to have been completed this session;
        # force_login skips that flow, so mark the session as 2FA-verified.
        session = self.http.session
        session["2fa_verified"] = True
        session.save()

        self.prior_fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED,
        )
        self.next_fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
            prior_year=self.prior_fy,
        )
        for code, name, closing in PRIOR_FIGURES:
            TrialBalanceLine.objects.create(
                financial_year=self.prior_fy,
                account_code=code,
                account_name=name,
                closing_balance=closing,
                debit=closing if closing > 0 else Decimal("0"),
                credit=-closing if closing < 0 else Decimal("0"),
                source="tb_import",
            )
        # The state a normal Roll Forward leaves behind.
        _populate_rolled_forward_fy(self.prior_fy, self.next_fy)

    def _openings(self):
        return {
            line.account_code: line.opening_balance
            for line in TrialBalanceLine.objects.filter(
                financial_year=self.next_fy, is_adjustment=False
            )
        }

    def test_the_reroll_diff_reports_no_drift_against_a_fresh_roll_forward(self):
        response = self.http.get(
            reverse("core:reroll_forward", args=[self.prior_fy.pk]), secure=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["diff_added"], [],
            "the preview invented rows the roll forward did not write",
        )
        self.assertEqual(
            response.context["diff_updated"], [],
            "the preview disagrees with the openings the roll forward wrote",
        )
        self.assertEqual(
            response.context["diff_removed"], [],
            "the preview would delete rows the roll forward wrote",
        )
        self.assertFalse(response.context["has_changes"])

    def test_re_rolling_reproduces_the_same_openings(self):
        before = self._openings()

        response = self.http.post(
            reverse("core:reroll_forward", args=[self.prior_fy.pk]), secure=True
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self._openings(), before,
            "re-rolling an unamended year changed the opening balances",
        )
        self.assertEqual(before["3-1000"], Decimal("-80000.00"))
        self.assertNotIn("4199", before)

    def test_an_amended_prior_year_moves_the_entitys_own_equity_account(self):
        """
        The point of the diff: amend the prior year and the drift must show up on
        3-1000 itself, which is where the corrected result now lands.
        """
        TrialBalanceLine.objects.create(
            financial_year=self.prior_fy,
            account_code="6-1000",
            account_name="Administration",
            closing_balance=Decimal("5000.00"),
            debit=Decimal("5000.00"),
            credit=Decimal("0"),
            source="manual_journal",
            is_adjustment=True,
        )

        response = self.http.get(
            reverse("core:reroll_forward", args=[self.prior_fy.pk]), secure=True
        )

        updated = {row["code"]: row for row in response.context["diff_updated"]}
        self.assertIn(
            "3-1000", updated,
            "the extra $5,000 expense did not show as drift on the equity account",
        )
        self.assertEqual(updated["3-1000"]["current_opening"], Decimal("-80000.00"))
        self.assertEqual(updated["3-1000"]["proposed_opening"], Decimal("-75000.00"))
        self.assertEqual(response.context["diff_added"], [])

    def test_the_reconciliation_endpoint_reports_no_phantom_drift(self):
        """
        reroll_forward_diff compares the prior year's closing balances against the
        next year's openings account by account. Retained earnings legitimately
        differs between the two -- it absorbed the year's result -- so a raw
        comparison reports $20,000 of drift on an account that is exactly right.
        """
        response = self.http.get(
            reverse("core:reroll_forward_diff", args=[self.prior_fy.pk]), secure=True
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["changes"], [],
            "the reconciliation modal reported drift on a correctly rolled year",
        )
        self.assertEqual(payload["change_count"], 0)

    def test_the_reconciliation_endpoint_counts_prior_year_journals(self):
        """
        The roll nets adjustment journals into each account's closing balance, so a
        diff that ignores them disagrees with the roll it is reconciling against.
        """
        TrialBalanceLine.objects.create(
            financial_year=self.prior_fy,
            account_code="2-1000",
            account_name="Trade Creditors",
            closing_balance=Decimal("-1000.00"),
            debit=Decimal("0"),
            credit=Decimal("1000.00"),
            source="manual_journal",
            is_adjustment=True,
        )

        payload = self.http.get(
            reverse("core:reroll_forward_diff", args=[self.prior_fy.pk]), secure=True
        ).json()

        changes = {c["account_code"]: c for c in payload["changes"]}
        self.assertIn(
            "2-1000", changes,
            "the journal against Trade Creditors was invisible to the reconciliation",
        )
        self.assertEqual(changes["2-1000"]["new_closing"], "-7000.00")

    def test_applying_the_reconciliation_leaves_the_year_in_balance(self):
        """
        The corruption path the phantom drift opens: reroll_forward_apply trusts its
        own recomputed diff and writes the prior closing straight onto the opening,
        so a phantom change on retained earnings would strip the year's result out of
        equity and leave the trial balance $20,000 out.
        """
        response = self.http.post(
            reverse("core:reroll_forward_apply", args=[self.prior_fy.pk]), secure=True
        )

        self.assertEqual(response.status_code, 200)
        openings = self._openings()
        self.assertEqual(
            openings["3-1000"], Decimal("-80000.00"),
            "apply overwrote retained earnings with the prior closing balance",
        )
        self.assertEqual(
            sum(openings.values()), Decimal("0.00"),
            "the rolled-forward opening balances no longer balance",
        )


# A trust-shaped chart in the HandiLedger codes every real entity in this book uses.
# 4199 exists on the chart but the prior year never posted to it -- the ordinary case
# for a first year, and for any year whose result has not yet been appropriated.
TRUST_CHART = [
    ("2000", "Cash at bank", "assets"),
    ("3048", "Trade creditors", "liabilities"),
    ("4199", "Undistributed income", "pl_appropriation"),
    ("0105", "Sales", "revenue"),
    ("1510", "Accountancy", "expenses"),
]

# Sales 30,000 credit less Accountancy 10,000 debit = 20,000 net profit. No 4199 line.
TRUST_PRIOR_FIGURES = [
    ("2000", "Cash at bank", Decimal("100000.00")),
    ("3048", "Trade creditors", Decimal("-80000.00")),
    ("0105", "Sales", Decimal("-30000.00")),
    ("1510", "Accountancy", Decimal("10000.00")),
]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class ExpectedOpeningsWithoutAPriorRetainedProfitsLineTests(TestCase):
    """The prior year has no retained-profits LINE, only a chart entry.

    _populate_rolled_forward_fy handles this: finding no retained_profits_line, it
    CREATES a 4199 line to hold the year's result (views.py's "If no retained profits
    line existed" branch).

    _expected_next_year_openings did not. It builds its account map from the prior
    year's own trial-balance lines, so with no 4199 line to find, retained_code stayed
    None, net_pl_result was applied to nothing, and 4199 was absent from the expected
    openings entirely -- leaving them out of balance by the year's whole result.

    Because reroll_forward_diff iterates expected.items(), the account was never
    compared. After a prior-year amendment that changes the year's profit, the diff
    reported the trade-creditors half of the correction and silently omitted the
    retained-profits half; applying it would have left the next year's openings out of
    balance by the amendment.

    Found by the Tier 2 trust roll-forward spec (e2e/tier2/roll_forward_trust.spec.ts),
    which is exactly the case the company fixture cannot reach: its 3-1000 carries a
    prior balance, so retained_code is always found.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="rollfwd_rp_noline",
            password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-rollfwd-rp-noline",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="No-RP-Line Test Client")
        cls.entity = Entity.objects.create(
            entity_name="No-RP-Line Family Trust",
            entity_type="trust",
            client=cls.client_obj,
            primary_accountant=cls.user,
        )
        # The trust COA auto-seed signal fires on creation; the fixture's own chart is
        # layered over it so 4199 keeps the name and section under test.
        for code, name, section in TRUST_CHART:
            EntityChartOfAccount.objects.update_or_create(
                entity=cls.entity, account_code=code,
                defaults={"account_name": name, "section": section, "is_active": True},
            )

    def setUp(self):
        self.prior_fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED,
        )
        self.next_fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
            prior_year=self.prior_fy,
        )
        for code, name, closing in TRUST_PRIOR_FIGURES:
            TrialBalanceLine.objects.create(
                financial_year=self.prior_fy,
                account_code=code,
                account_name=name,
                closing_balance=closing,
                debit=closing if closing > 0 else Decimal("0"),
                credit=-closing if closing < 0 else Decimal("0"),
                source="tb_import",
            )

    def test_the_expected_openings_include_the_retained_profits_account(self):
        expected = _expected_next_year_openings(self.prior_fy)

        self.assertIn(
            "4199", expected,
            "the year's result has nowhere to go: the account the roll forward would "
            "create to hold it is missing from the expected openings",
        )
        self.assertEqual(expected["4199"][1], Decimal("-20000.00"))

    def test_the_expected_openings_balance(self):
        expected = _expected_next_year_openings(self.prior_fy)

        self.assertEqual(
            sum(opening for _name, opening in expected.values()),
            Decimal("0.00"),
            "expected openings that do not balance would roll an unbalanced year",
        )

    def test_the_expected_openings_match_what_the_roll_actually_writes(self):
        """The contract this function exists to keep."""
        expected = _expected_next_year_openings(self.prior_fy)
        _populate_rolled_forward_fy(self.prior_fy, self.next_fy)

        written = {
            line.account_code: line.opening_balance
            for line in TrialBalanceLine.objects.filter(
                financial_year=self.next_fy, is_adjustment=False, source="rollover"
            )
            if line.opening_balance != Decimal("0")
        }
        self.assertEqual(
            {code: opening for code, (_name, opening) in expected.items()
             if opening != Decimal("0")},
            written,
        )

    def test_the_reroll_diff_reports_the_retained_profits_account_after_an_amendment(self):
        """The user-visible symptom.

        Roll forward, then correct the prior year with an unrecorded $1,000 expense
        invoice (Dr Accountancy, Cr Trade creditors). Both halves land on the balance
        sheet -- creditors directly, and retained profits because the expense reduces
        the year's profit -- so the reconciliation has to report both. Reporting only
        creditors would leave the next year 1,000 out of balance.
        """
        _populate_rolled_forward_fy(self.prior_fy, self.next_fy)

        creditors = TrialBalanceLine.objects.get(
            financial_year=self.prior_fy, account_code="3048"
        )
        creditors.closing_balance = Decimal("-81000.00")
        creditors.credit = Decimal("81000.00")
        creditors.save(update_fields=["closing_balance", "credit"])
        accountancy = TrialBalanceLine.objects.get(
            financial_year=self.prior_fy, account_code="1510"
        )
        accountancy.closing_balance = Decimal("11000.00")
        accountancy.debit = Decimal("11000.00")
        accountancy.save(update_fields=["closing_balance", "debit"])

        http = Client()
        http.force_login(self.user)
        session = http.session
        session["2fa_verified"] = True
        session.save()

        response = http.get(
            reverse("core:reroll_forward_diff", args=[self.prior_fy.pk]), secure=True
        )
        self.assertEqual(response.status_code, 200)
        changed = sorted(c["account_code"] for c in response.json()["changes"])

        self.assertEqual(
            changed, ["3048", "4199"],
            "the reconciliation is blind to the retained-profits half of the "
            "amendment, so applying it would leave the next year out of balance",
        )
