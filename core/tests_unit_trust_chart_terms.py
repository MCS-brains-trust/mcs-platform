"""
Chart terminology for unit trusts (Task 11).

Two sources of "Beneficiary" text feed a chart:

1. ChartOfAccount template rows, copied verbatim by
   EntityChartOfAccount.seed_from_template. A unit trust seeds from the
   shared "trust" template (Task 2's template_entity_type), so without an
   overlay it would inherit "Beneficiary" wording unchanged.
2. The hardcoded BENEFICIARY_PARENT_CODES list in
   core/beneficiary_account_service.py, which drives the per-officer
   4000.01/4053.02-style sub-accounts.

A discretionary trust must be completely unaffected by either overlay.
"""
from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.beneficiary_account_service import provision_beneficiary_accounts
from core.models import (
    ChartOfAccount,
    Entity,
    EntityChartOfAccount,
    EntityOfficer,
    FinancialYear,
    TrialBalanceLine,
)


class UnitTrustChartTermsTests(TestCase):
    """Source 1: ChartOfAccount template rows via seed_from_template."""

    def setUp(self):
        ChartOfAccount.objects.create(
            entity_type="trust", account_code="4000",
            account_name="Opening balance - Beneficiary", section="equity",
        )

    def test_seeding_a_unit_trust_renames_the_term(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        EntityChartOfAccount.seed_from_template(entity)
        account = EntityChartOfAccount.objects.get(entity=entity, account_code="4000")
        self.assertEqual(account.account_name, "Opening balance - Unit Holder")

    def test_seeding_a_discretionary_trust_does_not(self):
        entity = Entity.objects.create(entity_name="Vincent", entity_type="trust")
        EntityChartOfAccount.seed_from_template(entity)
        account = EntityChartOfAccount.objects.get(entity=entity, account_code="4000")
        self.assertEqual(account.account_name, "Opening balance - Beneficiary")


class UnitTrustSubAccountTermsTests(TestCase):
    """Source 2: the hardcoded BENEFICIARY_PARENT_CODES list, materialised
    per-officer by core/beneficiary_account_service.py."""

    def setUp(self):
        # Minimal trust master template + entity chart parent rows so
        # provision_beneficiary_accounts can find parent ECAs to inherit
        # maps_to from (mirrors core/tests_beneficiary_accounts.py).
        for code, name, section in [
            ("4000", "Opening balance - Beneficiary", "capital_accounts"),
            ("4100", "Beneficiary current account", "equity"),
        ]:
            ChartOfAccount.objects.update_or_create(
                entity_type="trust", account_code=code,
                defaults={"account_name": name, "section": section, "is_active": True},
            )

    def _seed_parent(self, entity, code, name, section):
        EntityChartOfAccount.objects.update_or_create(
            entity=entity, account_code=code,
            defaults={
                "account_name": name, "section": section,
                "is_active": True, "is_custom": False, "auto_provisioned": False,
            },
        )

    def test_unit_trust_sub_account_uses_unit_holder_and_keeps_officer_suffix(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        self._seed_parent(entity, "4000", "Opening balance - Unit Holder", "capital_accounts")
        officer = EntityOfficer.objects.create(
            entity=entity,
            full_name="Double Water International Pty Ltd",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER,
            beneficiary_type="company",
        )
        provision_beneficiary_accounts(officer.pk)

        child = EntityChartOfAccount.objects.get(
            entity=entity, account_code=f"4000.{officer.display_order:02d}",
        )
        self.assertEqual(
            child.account_name,
            "Opening balance - Unit Holder — Double Water International Pty Ltd",
        )

    def test_discretionary_trust_sub_account_still_says_beneficiary(self):
        entity = Entity.objects.create(entity_name="Vincent", entity_type="trust")
        self._seed_parent(entity, "4000", "Opening balance - Beneficiary", "capital_accounts")
        officer = EntityOfficer.objects.create(
            entity=entity,
            full_name="Elvis Chiaravalle",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        provision_beneficiary_accounts(officer.pk)

        child = EntityChartOfAccount.objects.get(
            entity=entity, account_code=f"4000.{officer.display_order:02d}",
        )
        self.assertEqual(
            child.account_name,
            "Opening balance - Beneficiary — Elvis Chiaravalle",
        )


class RenameUnitTrustChartTermsCommandTests(TestCase):
    """core.management.commands.rename_unit_trust_chart_terms"""

    def setUp(self):
        self.unit_trust = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        self.discretionary = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
        )

    def _eca(self, entity, code, name, suffix_officer=None):
        return EntityChartOfAccount.objects.create(
            entity=entity, account_code=code, account_name=name,
            section="capital_accounts", is_active=True, is_custom=False,
            beneficiary_officer=suffix_officer,
            auto_provisioned=suffix_officer is not None,
        )

    def _fy(self, entity, year, status="draft"):
        return FinancialYear.objects.create(
            entity=entity,
            start_date=date(year - 1, 7, 1), end_date=date(year, 6, 30),
            status=status,
        )

    def _line(self, fy, code, name):
        return TrialBalanceLine.objects.create(
            financial_year=fy, account_code=code, account_name=name,
            source="tb_import",
        )

    def _run(self, *args):
        out = StringIO()
        call_command("rename_unit_trust_chart_terms", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        parent = self._eca(self.unit_trust, "4000", "Opening balance - Beneficiary")
        fy = self._fy(self.unit_trust, 2026)
        line = self._line(fy, "4000", "Opening balance - Beneficiary")

        self._run("--dry-run")

        parent.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(parent.account_name, "Opening balance - Beneficiary")
        self.assertEqual(line.account_name, "Opening balance - Beneficiary")

    def test_default_run_with_no_flags_also_writes_nothing(self):
        # Default is dry-run: --apply is required to touch anything.
        parent = self._eca(self.unit_trust, "4000", "Opening balance - Beneficiary")

        self._run()

        parent.refresh_from_db()
        self.assertEqual(parent.account_name, "Opening balance - Beneficiary")

    def test_apply_renames_the_chart_and_keeps_the_officer_suffix(self):
        # bulk_create deliberately bypasses the post_save signal that would
        # otherwise auto-provision this officer's 4000.01 with the (now
        # already-correct) "Unit Holder" wording — this test is simulating
        # a pre-Task-11 row that still says "Beneficiary" and needs the
        # command to fix it, not a freshly-provisioned one.
        officer = EntityOfficer(
            entity=self.unit_trust,
            full_name="Double Water International Pty Ltd",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER,
            beneficiary_type="company",
            display_order=1,
        )
        EntityOfficer.objects.bulk_create([officer])
        parent = self._eca(self.unit_trust, "4000", "Opening balance - Beneficiary")
        child = self._eca(
            self.unit_trust, "4000.01",
            "Opening balance - Beneficiary — Double Water International Pty Ltd",
            suffix_officer=officer,
        )

        self._run("--apply")

        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(parent.account_name, "Opening balance - Unit Holder")
        self.assertEqual(
            child.account_name,
            "Opening balance - Unit Holder — Double Water International Pty Ltd",
        )

    def test_finalised_years_are_skipped_by_default(self):
        self._eca(self.unit_trust, "4000", "Opening balance - Beneficiary")
        fy = self._fy(self.unit_trust, 2025, status="finalised")
        line = self._line(fy, "4000", "Opening balance - Beneficiary")

        self._run("--apply")

        line.refresh_from_db()
        self.assertEqual(line.account_name, "Opening balance - Beneficiary")

    def test_finalised_years_are_renamed_when_explicitly_asked_for(self):
        self._eca(self.unit_trust, "4000", "Opening balance - Beneficiary")
        fy = self._fy(self.unit_trust, 2025, status="finalised")
        line = self._line(fy, "4000", "Opening balance - Beneficiary")

        self._run("--apply", "--include-finalised")

        line.refresh_from_db()
        self.assertEqual(line.account_name, "Opening balance - Unit Holder")

    def test_non_finalised_year_is_renamed_by_default(self):
        self._eca(self.unit_trust, "4000", "Opening balance - Beneficiary")
        fy = self._fy(self.unit_trust, 2026, status="draft")
        line = self._line(fy, "4000", "Opening balance - Beneficiary")

        self._run("--apply")

        line.refresh_from_db()
        self.assertEqual(line.account_name, "Opening balance - Unit Holder")

    def test_discretionary_trust_is_never_touched(self):
        parent = self._eca(self.discretionary, "4000", "Opening balance - Beneficiary")
        fy = self._fy(self.discretionary, 2025, status="finalised")
        line = self._line(fy, "4000", "Opening balance - Beneficiary")

        self._run("--apply", "--include-finalised")

        parent.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(parent.account_name, "Opening balance - Beneficiary")
        self.assertEqual(line.account_name, "Opening balance - Beneficiary")

    def test_entity_filter_limits_the_blast_radius(self):
        other_unit_trust = Entity.objects.create(
            entity_name="Other Unit Trust", entity_type="trust_unit",
        )
        mine = self._eca(self.unit_trust, "4000", "Opening balance - Beneficiary")
        other = self._eca(other_unit_trust, "4000", "Opening balance - Beneficiary")

        self._run("--apply", "--entity", "Minli")

        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(mine.account_name, "Opening balance - Unit Holder")
        self.assertEqual(other.account_name, "Opening balance - Beneficiary")
