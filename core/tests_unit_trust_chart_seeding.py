"""A unit trust seeds its chart from the trust template, not from nothing."""
from datetime import date
from decimal import Decimal

from django.test import Client as HttpClient, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import (
    AccountMapping,
    ChartOfAccount,
    Client as ClientModel,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
    template_entity_type,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class TemplateEntityTypeTests(TestCase):
    def test_unit_trust_resolves_to_trust(self):
        self.assertEqual(template_entity_type("trust_unit"), "trust")

    def test_other_types_pass_through(self):
        for value in ("trust", "company", "partnership", "sole_trader", "smsf"):
            self.assertEqual(template_entity_type(value), value)


class UnitTrustChartSeedingTests(TestCase):
    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="620",
            account_name="Rents received", section="revenue",
        )
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="4000",
            account_name="Opening balance - Beneficiary", section="equity",
        )
        # Migration 0148_trust_4199_mapping unconditionally seeds a
        # ("trust", "4199") ChartOfAccount row on any fresh database (its
        # docstring calls this out explicitly for "a fresh test database").
        # A test-fixture chart mirroring reality has to include it too.
        self.expected_codes = {"620", "4000", "4199"}

    def test_unit_trust_seeds_from_the_trust_template(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        EntityChartOfAccount.seed_from_template(entity)

        codes = set(
            EntityChartOfAccount.objects.filter(entity=entity)
            .values_list("account_code", flat=True)
        )
        self.assertEqual(codes, self.expected_codes)

    def test_discretionary_trust_seeding_is_unchanged(self):
        entity = Entity.objects.create(entity_name="Vincent", entity_type="trust")
        EntityChartOfAccount.seed_from_template(entity)

        codes = set(
            EntityChartOfAccount.objects.filter(entity=entity)
            .values_list("account_code", flat=True)
        )
        self.assertEqual(codes, self.expected_codes)


class UnitTrustImportMappingResolvesFromTrustTemplateTests(TestCase):
    """End-to-end proof for the import-mapping path (core/views.py), not just
    chart seeding: a unit trust importing a statement must still find its
    account names/mappings against the trust master template.

    This is the fixture that gave _resolve_account_name its docstring example
    (Minli Enterprise Unit Trust) — reused here as a real regression guard for
    the entity_type-keyed lookups fixed in core/views.py by this task.
    """

    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="620",
            account_name="Rents received", section="revenue",
        )

    def test_unit_trust_resolves_account_name_from_trust_template(self):
        from core.views import _resolve_account_name

        entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        # No EntityChartOfAccount row yet, and the imported name is blank/
        # code-shaped — this is exactly the case that falls through to the
        # master ChartOfAccount template (Attempt 2 in the docstring).
        resolved_name = _resolve_account_name(entity, "620", "620")

        self.assertEqual(
            resolved_name, "Rents received",
            "a trust_unit entity failed to resolve its account name against "
            "the trust master template — the import-mapping path is dead for "
            "unit trusts",
        )


class UnitTrustAccountMappingApplicabilityTests(TestCase):
    """The headline case: production's 70 AccountMapping rows all list only
    'trust' in applicable_entities, never 'trust_unit' (verified against the
    live database by the controller). Every membership test against that
    field must resolve trust_unit through the trust template, or a unit
    trust's financial statements come out with every line unmapped.
    """

    def test_unit_trust_resolves_a_trust_only_account_mapping(self):
        from core.mapping_engine import auto_map_account

        mapping = AccountMapping.objects.create(
            standard_code="IS-REV-001",
            line_item_label="Sales revenue",
            financial_statement="profit_and_loss",
            statement_section="Revenue",
            display_order=10,
            applicable_entities=["trust"],
        )

        result = auto_map_account(
            classification="Sales revenue",
            account_code="200",
            account_name="Sales",
            entity_type="trust_unit",
        )

        self.assertEqual(
            result, mapping,
            "a trust_unit account failed to resolve a mapping whose "
            "applicable_entities lists only 'trust' -- every financial "
            "statement line item would come out unmapped for a unit trust",
        )


class UnitTrustClosesProfitToUndistributedIncomeTests(TestCase):
    """A year's net P&L result must close into 'Undistributed income' (4199 /
    BS-EQ-005) for a unit trust, exactly as it does for a discretionary trust
    -- never into the generic 'Retained profits' default. This codebase has
    already shipped two separate balance-sheet errors from this exact class
    of bug (the trust equity presentation and trust profit-in-equity fixes),
    so a visible accounting error here is the highest-value regression this
    task can guard against.
    """

    def test_default_retained_profits_account_is_undistributed_income(self):
        from core.views import _default_retained_profits_account

        self.assertEqual(
            _default_retained_profits_account("trust_unit"),
            ("Undistributed income", "4199"),
            "a trust_unit's year-end result would close into the generic "
            "'Retained profits' account instead of Undistributed income -- "
            "a visible error on the face of the balance sheet",
        )

    def test_mapping_engine_equity_range_resolves_to_undistributed_income(self):
        from core.mapping_engine import auto_map_account

        # Migration 0148_trust_4199_mapping already seeds BS-EQ-005 with
        # applicable_entities=["trust"] on a fresh database -- get_or_create
        # reuses that row rather than colliding on the unique standard_code.
        mapping, _ = AccountMapping.objects.get_or_create(
            standard_code="BS-EQ-005",
            defaults={
                "line_item_label": "Undistributed income",
                "financial_statement": "balance_sheet",
                "statement_section": "Equity",
                "display_order": 510,
                "applicable_entities": ["trust"],
            },
        )

        result = auto_map_account(
            classification="", account_code="4050", account_name="",
            entity_type="trust_unit",
        )

        self.assertEqual(
            result, mapping,
            "a trust_unit account in the 4000-4199 equity range failed to "
            "resolve BS-EQ-005 (Undistributed income) via the trust "
            "equity_map -- it would fall through unmapped",
        )


class UnitTrustRetainedProfitsAccountRecognitionTests(TestCase):
    """_is_retained_profits_account is the third of the three equity dicts and
    the only reader of _RETAINED_PROFITS_STANDARD_CODES. Its ``code_prefix ==
    "4199"`` short-circuit masks the common case, so these exercise the
    standard-code branch on a chart that does not carry 4199 -- the
    MYOB/Xero shape the helper's own docstring was written for.
    """

    def _mapping(self, standard_code, label):
        # Migrations already seed some of these standard codes on a fresh test
        # database; get_or_create reuses the row rather than colliding on the
        # unique standard_code.
        mapping, _ = AccountMapping.objects.get_or_create(
            standard_code=standard_code,
            defaults={
                "line_item_label": label,
                "financial_statement": "balance_sheet",
                "statement_section": "Equity",
                "display_order": 510,
                "applicable_entities": ["trust"],
            },
        )
        return mapping

    def test_unit_trust_recognises_its_undistributed_income_account(self):
        from core.views import _is_retained_profits_account

        mapping = self._mapping("BS-EQ-005", "Undistributed income")

        # Deliberately not "4199" (that code short-circuits before the dict is
        # read) and deliberately named without any retained-profits keyword,
        # so the standard-code branch is the only thing that can match.
        rank = _is_retained_profits_account(
            "3-1000", "Unitholder entitlements", mapping, "trust_unit",
        )

        self.assertEqual(
            rank, 1,
            "a trust_unit's BS-EQ-005 equity account was not recognised as its "
            "retained-profits account -- the year's result would be closed into "
            "a synthesised second equity line instead",
        )

    def test_discretionary_trust_recognition_is_unchanged(self):
        from core.views import _is_retained_profits_account

        mapping = self._mapping("BS-EQ-005", "Undistributed income")

        self.assertEqual(
            _is_retained_profits_account(
                "3-1000", "Unitholder entitlements", mapping, "trust",
            ),
            1,
        )

    def test_company_still_keys_off_its_own_standard_code(self):
        from core.views import _is_retained_profits_account

        company_mapping = self._mapping("BS-EQ-002", "Retained profits")
        trust_mapping = self._mapping("BS-EQ-005", "Undistributed income")

        self.assertEqual(
            _is_retained_profits_account(
                "3-1000", "Shareholder funds", company_mapping, "company",
            ),
            1,
        )
        # Resolution is not a blanket pass: a trust-only standard code is still
        # not a company's retained-profits account.
        self.assertEqual(
            _is_retained_profits_account(
                "3-1000", "Shareholder funds", trust_mapping, "company",
            ),
            0,
        )


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RerollForwardNamesTheUnitTrustClosingLineTests(TestCase):
    """reroll_forward hand-inlined its own copy of "which equity account does
    the year's result close into", and that copy still answered "Retained
    profits" for a unit trust while _populate_rolled_forward_fy and
    _expected_next_year_openings both answered "Undistributed income". Roll and
    re-roll disagreeing about that name is the same divergence
    _default_retained_profits_account's docstring records happening once
    already, so reroll_forward now asks the one helper too.
    """

    # No 4199 and no equity account at all, so the roll has to synthesise the
    # closing line -- which is the branch that names it.
    CHART = [
        ("1-1000", "Cash at Bank", "current_assets"),
        ("4-1000", "Rents received", "revenue"),
        ("6-1000", "Administration", "expenses"),
    ]
    FIGURES = [
        ("1-1000", "Cash at Bank", Decimal("20000.00")),
        ("4-1000", "Rents received", Decimal("-30000.00")),
        ("6-1000", "Administration", Decimal("10000.00")),
    ]

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="reroll_unit_trust",
            password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-reroll-unit-trust",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Reroll Unit Trust Client")

    def setUp(self):
        self.http = HttpClient()
        self.http.force_login(self.user)
        # Require2FAMiddleware wants TOTP completed for the session; force_login
        # skips that flow, so mark the session as 2FA-verified.
        session = self.http.session
        session["2fa_verified"] = True
        session.save()

    def _reroll(self, entity_type):
        entity = Entity.objects.create(
            entity_name=f"Reroll {entity_type}",
            entity_type=entity_type,
            client=self.client_obj,
            primary_accountant=self.user,
        )
        for code, name, section in self.CHART:
            EntityChartOfAccount.objects.create(
                entity=entity, account_code=code, account_name=name,
                section=section, is_active=True,
            )
        prior_fy = FinancialYear.objects.create(
            entity=entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED,
        )
        next_fy = FinancialYear.objects.create(
            entity=entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
            prior_year=prior_fy,
        )
        for code, name, closing in self.FIGURES:
            TrialBalanceLine.objects.create(
                financial_year=prior_fy,
                account_code=code,
                account_name=name,
                closing_balance=closing,
                debit=closing if closing > 0 else Decimal("0"),
                credit=-closing if closing < 0 else Decimal("0"),
                source="tb_import",
            )

        response = self.http.post(
            reverse("core:reroll_forward", args=[prior_fy.pk]), secure=True
        )
        self.assertEqual(response.status_code, 302)

        return {
            line.account_code: line.account_name
            for line in TrialBalanceLine.objects.filter(financial_year=next_fy)
        }

    def test_a_unit_trust_closes_into_undistributed_income(self):
        rolled = self._reroll("trust_unit")

        self.assertEqual(
            rolled.get("4199"), "Undistributed income",
            "re-rolling a unit trust named its closing equity line "
            f"{rolled.get('4199')!r} -- the roll forward calls it "
            "'Undistributed income', so roll and re-roll disagree",
        )

    def test_a_discretionary_trust_is_unchanged(self):
        self.assertEqual(self._reroll("trust").get("4199"), "Undistributed income")

    def test_a_company_is_unchanged(self):
        self.assertEqual(self._reroll("company").get("4199"), "Retained profits")

    def test_a_partnership_is_unchanged(self):
        self.assertEqual(
            self._reroll("partnership").get("4199"), "Partners' current accounts",
        )

    def test_a_sole_trader_is_unchanged(self):
        self.assertEqual(
            self._reroll("sole_trader").get("4199"), "Proprietor's funds",
        )
