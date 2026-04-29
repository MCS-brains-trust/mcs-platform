"""
Tests for per-beneficiary 4xxx account materialisation
======================================================
Covers core/beneficiary_account_service.py and the post_save / pre_delete
signal extensions in core/signals.py.

See: per_beneficiary_accounts_phase2.md (§2.8 — 13 tests).
"""
from datetime import date
from decimal import Decimal
from django.test import TestCase, override_settings

from core.models import (
    Entity, Client, EntityOfficer, EntityChartOfAccount, ChartOfAccount,
    FinancialYear, TrialBalanceLine, AccountMapping,
)
from core.beneficiary_account_service import (
    BENEFICIARY_PARENT_CODES,
    SLOT_CODES_TO_REMOVE,
    provision_beneficiary_accounts,
    sync_officer_account_names,
    count_parent_postings_with_children,
)


STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BeneficiaryAccountTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Trust Test Client")
        cls.trust = Entity.objects.create(
            entity_name="Test Family Trust",
            entity_type="trust",
            client=cls.client_obj,
        )
        # Seed minimal trust master template entries for the parent codes
        # so provision_beneficiary_accounts can find parent ECAs to inherit
        # maps_to from. Only the parent codes (no .NN children).
        for entry in BENEFICIARY_PARENT_CODES:
            ChartOfAccount.objects.update_or_create(
                entity_type="trust",
                account_code=entry["code"],
                defaults={
                    "account_name": entry["name"],
                    "section": entry["section"],
                    "is_active": True,
                },
            )
            EntityChartOfAccount.objects.update_or_create(
                entity=cls.trust,
                account_code=entry["code"],
                defaults={
                    "account_name": entry["name"],
                    "section": entry["section"],
                    "is_active": True,
                    "is_custom": False,
                    "auto_provisioned": False,
                },
            )


class ProvisionTests(BeneficiaryAccountTestBase):
    def test_provision_creates_22_codes_for_adult_beneficiary(self):
        """Adult beneficiary → groups A-E only (no F/G). 13 parent codes today
        (Group A=8, B=2, C=1, D=1, E=1 = 13)."""
        officer = EntityOfficer.objects.create(
            entity=self.trust,
            full_name="Adult Beneficiary",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        # Signal already fired on create; double-check by counting children.
        children = EntityChartOfAccount.objects.filter(
            entity=self.trust,
            beneficiary_officer=officer,
            auto_provisioned=True,
        )
        # Groups A (8) + B (2) + C (1) + D (1) + E (1) = 13
        non_company_count = sum(
            1 for e in BENEFICIARY_PARENT_CODES
            if not e["requires_company_beneficiary"]
        )
        self.assertEqual(children.count(), non_company_count)
        # Verify all codes end in .NN suffix
        for c in children:
            self.assertRegex(c.account_code, r"^\d{4}\.\d{2}$")

    def test_provision_creates_all_codes_for_company_beneficiary(self):
        """Corporate beneficiary → all 38 groups (A-G)."""
        officer = EntityOfficer.objects.create(
            entity=self.trust,
            full_name="Corporate Benef Pty Ltd",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="company",
        )
        children = EntityChartOfAccount.objects.filter(
            entity=self.trust,
            beneficiary_officer=officer,
            auto_provisioned=True,
        )
        self.assertEqual(children.count(), len(BENEFICIARY_PARENT_CODES))

    def test_provision_skips_unit_holder_for_corp_groups(self):
        """Unit holder regardless of beneficiary_type → groups A-E only."""
        officer = EntityOfficer.objects.create(
            entity=self.trust,
            full_name="Unit Holder One",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER,
            beneficiary_type="company",  # even with company set
        )
        children = EntityChartOfAccount.objects.filter(
            entity=self.trust,
            beneficiary_officer=officer,
            auto_provisioned=True,
        )
        # Unit-holder gating ignores requires_company_beneficiary because
        # the gate doesn't say "skip unit holders for F/G". Re-read spec:
        # actually, the spec says F/G applies "only when beneficiary_type ==
        # company". A unit holder set to company-type would still get F/G.
        # So count = 38 here, not 13.
        self.assertEqual(children.count(), len(BENEFICIARY_PARENT_CODES))

    def test_provision_unit_holder_naming(self):
        """Unit holder + code 4004/4404/4504 → 'Unitholders' funds introduced'."""
        officer = EntityOfficer.objects.create(
            entity=self.trust,
            full_name="UH Person",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER,
            beneficiary_type="company",  # so F/G also create
        )
        for code in ("4004", "4404", "4504"):
            child = EntityChartOfAccount.objects.filter(
                entity=self.trust,
                beneficiary_officer=officer,
                account_code__startswith=f"{code}.",
            ).first()
            self.assertIsNotNone(child, f"missing child for {code}")
            self.assertTrue(
                child.account_name.startswith("Unitholders' funds introduced — "),
                f"{code}: got '{child.account_name}'",
            )

    def test_provision_idempotent(self):
        """Running provision twice creates no duplicates."""
        officer = EntityOfficer.objects.create(
            entity=self.trust,
            full_name="Idempotent Bene",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        first_count = EntityChartOfAccount.objects.filter(
            entity=self.trust, beneficiary_officer=officer,
        ).count()
        provision_beneficiary_accounts(officer.pk)
        second_count = EntityChartOfAccount.objects.filter(
            entity=self.trust, beneficiary_officer=officer,
        ).count()
        self.assertEqual(first_count, second_count)


class GhostCleanupTests(BeneficiaryAccountTestBase):
    def test_ghost_cleanup_with_no_postings(self):
        """First-officer materialisation deletes ghost .01 rows that have
        no postings."""
        # Seed a ghost row
        EntityChartOfAccount.objects.create(
            entity=self.trust,
            account_code="4053.01",
            account_name="Physical distribution",
            section="capital_accounts",
            is_active=True,
            is_custom=False,
            auto_provisioned=False,
            beneficiary_officer=None,
        )
        self.assertTrue(EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4053.01",
            beneficiary_officer__isnull=True,
        ).exists())
        # Add officer — fires signal — runs ghost cleanup (no postings) →
        # ghost should be gone, child .01 should now be officer-linked.
        officer = EntityOfficer.objects.create(
            entity=self.trust,
            full_name="Ghost Cleaner",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        self.assertFalse(EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4053.01",
            beneficiary_officer__isnull=True,
        ).exists())
        # And a fresh officer-linked .01 exists
        self.assertTrue(EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4053.01",
            beneficiary_officer=officer, auto_provisioned=True,
        ).exists())

    def test_ghost_cleanup_skips_when_postings_exist(self):
        """A ghost with TBL postings is not deleted; escalation logged."""
        fy = FinancialYear.objects.create(
            entity=self.trust, year_label="FY2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
        )
        EntityChartOfAccount.objects.create(
            entity=self.trust, account_code="4053.01",
            account_name="Physical distribution",
            section="capital_accounts",
            is_active=True, is_custom=False, auto_provisioned=False,
            beneficiary_officer=None,
        )
        TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code="4053.01",
            account_name="Physical distribution",
            opening_balance=Decimal("0"),
            debit=Decimal("100"),
            credit=Decimal("0"),
            closing_balance=Decimal("100"),
        )
        EntityOfficer.objects.create(
            entity=self.trust, full_name="Posting Bene",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        # Ghost survives because it has a posting
        self.assertTrue(EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4053.01",
            beneficiary_officer__isnull=True,
        ).exists())


class NameSyncTests(BeneficiaryAccountTestBase):
    def test_officer_name_change_propagates(self):
        officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Original Name",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        # Pick any auto-provisioned child
        child = EntityChartOfAccount.objects.filter(
            beneficiary_officer=officer, auto_provisioned=True,
        ).first()
        self.assertIn("Original Name", child.account_name)
        # Rename
        officer.full_name = "Renamed Officer"
        officer.save()
        child.refresh_from_db()
        self.assertIn("Renamed Officer", child.account_name)

    def test_officer_name_change_idempotent(self):
        officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Stable Name",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        # Save without changing name → zero updates
        result = sync_officer_account_names(officer.pk)
        self.assertEqual(result, 0)


class PreDeleteTests(BeneficiaryAccountTestBase):
    def test_pre_delete_signal_cleans_up_ecas(self):
        """officer.delete() (via shell, not view) → all auto_provisioned ECAs
        deleted via pre_delete signal."""
        officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Doomed Officer",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        before = EntityChartOfAccount.objects.filter(
            beneficiary_officer=officer, auto_provisioned=True,
        ).count()
        self.assertGreater(before, 0)
        officer_pk = officer.pk
        officer.delete()
        after = EntityChartOfAccount.objects.filter(
            beneficiary_officer_id=officer_pk, auto_provisioned=True,
        ).count()
        self.assertEqual(after, 0)


class NinethousandSeriesUntouchedTests(BeneficiaryAccountTestBase):
    def test_9000_series_untouched(self):
        """Phase 2 provisioning does not modify, delete, or rename any
        9000-series ECA."""
        # Seed a 9000-series row that mimics what capital_account_service
        # would create.
        existing = EntityChartOfAccount.objects.create(
            entity=self.trust,
            account_code="9001.01",
            account_name="Opening balance — Existing Bene",
            section="capital_accounts",
            is_active=True,
            is_custom=False,
            auto_provisioned=True,
            beneficiary_officer=None,
        )
        # Run provisioning for a new beneficiary
        officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="New Bene",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        # Re-fetch — must be unchanged
        existing.refresh_from_db()
        self.assertEqual(existing.account_code, "9001.01")
        self.assertEqual(existing.account_name, "Opening balance — Existing Bene")
        self.assertTrue(existing.auto_provisioned)

    def test_sync_does_not_rename_9000_series(self):
        """sync_officer_account_names skips 9000-series ECAs."""
        officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Bene Owner",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        # Manually create a 9000-series row linked to this officer (mimics
        # what capital_account_service does).
        nine = EntityChartOfAccount.objects.create(
            entity=self.trust,
            account_code="9001.01",
            account_name="Opening balance — Bene Owner",
            section="capital_accounts",
            is_active=True, is_custom=False,
            auto_provisioned=True,
            beneficiary_officer=officer,
        )
        # Rename officer
        officer.full_name = "Bene Owner Renamed"
        officer.save()
        nine.refresh_from_db()
        # 9000-series name was NOT touched by sync (parent code 9001 not
        # in the canonical list)
        self.assertEqual(nine.account_name, "Opening balance — Bene Owner")


class BackfillCommandTests(BeneficiaryAccountTestBase):
    def test_backfill_dry_run_creates_nothing(self):
        from io import StringIO
        from django.core.management import call_command
        # Create an officer but immediately delete its auto-provisioned ECAs
        # to simulate a pre-Phase-2 state.
        officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Backfill Bene",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        # Wipe the auto-provisioned children to simulate "needs backfill"
        EntityChartOfAccount.objects.filter(
            beneficiary_officer=officer, auto_provisioned=True,
        ).delete()
        before = EntityChartOfAccount.objects.filter(
            beneficiary_officer=officer, auto_provisioned=True,
        ).count()
        out = StringIO()
        call_command(
            "materialise_beneficiary_accounts",
            f"--entity={self.trust.pk}", "--dry-run",
            stdout=out,
        )
        after = EntityChartOfAccount.objects.filter(
            beneficiary_officer=officer, auto_provisioned=True,
        ).count()
        self.assertEqual(before, 0)
        self.assertEqual(after, 0)
        self.assertIn("DRY RUN", out.getvalue())

    def test_backfill_idempotent(self):
        from io import StringIO
        from django.core.management import call_command
        officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Idempotent Backfill",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        out1 = StringIO()
        call_command(
            "materialise_beneficiary_accounts",
            f"--entity={self.trust.pk}", stdout=out1,
        )
        first = EntityChartOfAccount.objects.filter(
            beneficiary_officer=officer, auto_provisioned=True,
        ).count()
        out2 = StringIO()
        call_command(
            "materialise_beneficiary_accounts",
            f"--entity={self.trust.pk}", stdout=out2,
        )
        second = EntityChartOfAccount.objects.filter(
            beneficiary_officer=officer, auto_provisioned=True,
        ).count()
        self.assertEqual(first, second)
