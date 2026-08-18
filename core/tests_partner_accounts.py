"""Per-partner 4xxx account materialisation on partnership entities.

The trust side of this has worked for a while: add a beneficiary, get their
own child accounts under each parent code, rename the beneficiary and the
accounts follow. Partnerships were excluded by two gates -- role not in
DISTRIBUTION_ROLES, and entity_type != "trust" -- so instead they inherited
whatever names the seed template happened to carry. In production that meant a
real partnership's chart listing "Drawings - Angela", "Drawings - Chris" and
"Drawings - Oskah": three people from whichever firm the template was built
from, and none of them partners in the entity looking at them.

These tests pin both directions. A partnership must get partner accounts, and
must never get the trust-only codes.
"""
from datetime import date, timedelta

from django.test import TestCase, override_settings

from core.models import (
    Client, Entity, EntityChartOfAccount, EntityOfficer, ChartOfAccount,
    FinancialYear, TrialBalanceLine,
)
from core.beneficiary_account_service import (
    BENEFICIARY_PARENT_CODES,
    PARTNER_PARENT_CODES,
    PARTNER_SLOT_CODES_TO_REMOVE,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class PartnerAccountTestBase(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Partnership Test Client")
        self.partnership = Entity.objects.create(
            entity_name="Vaughan & Vriend",
            entity_type="partnership",
            client=self.client_obj,
        )
        # Parent codes only, no .NN children — the shape the seed template
        # should ship once the personal names are out of it.
        for entry in PARTNER_PARENT_CODES:
            ChartOfAccount.objects.update_or_create(
                entity_type="partnership",
                account_code=entry["code"],
                defaults={
                    "account_name": entry["name"],
                    "section": entry["section"],
                    "is_active": True,
                },
            )
            EntityChartOfAccount.objects.update_or_create(
                entity=self.partnership,
                account_code=entry["code"],
                defaults={
                    "account_name": entry["name"],
                    "section": entry["section"],
                    "is_active": True,
                    "is_custom": False,
                    "auto_provisioned": False,
                },
            )

    def _add_partner(self, name, **kwargs):
        return EntityOfficer.objects.create(
            entity=self.partnership,
            full_name=name,
            role=EntityOfficer.OfficerRole.PARTNER,
            **kwargs,
        )

    def _children_of(self, officer):
        return EntityChartOfAccount.objects.filter(
            entity=self.partnership,
            beneficiary_officer=officer,
            auto_provisioned=True,
        )


class PartnerProvisioningTests(PartnerAccountTestBase):
    def test_adding_a_partner_creates_one_account_per_parent_code(self):
        partner = self._add_partner("Daniel Vriend")

        self.assertEqual(self._children_of(partner).count(), len(PARTNER_PARENT_CODES))

    def test_each_account_is_named_for_the_partner(self):
        partner = self._add_partner("Daniel Vriend")

        names = set(self._children_of(partner).values_list("account_name", flat=True))
        self.assertIn("Drawings — Daniel Vriend", names)
        self.assertIn("Capital contribution — Daniel Vriend", names)
        self.assertIn("Share of profit — Daniel Vriend", names)

    def test_a_second_partner_gets_their_own_accounts_without_disturbing_the_first(self):
        first = self._add_partner("Daniel Vriend")
        first_codes = set(self._children_of(first).values_list("account_code", flat=True))

        second = self._add_partner("Douglas Vaughan")
        second_codes = set(self._children_of(second).values_list("account_code", flat=True))

        self.assertEqual(len(second_codes), len(PARTNER_PARENT_CODES))
        self.assertFalse(first_codes & second_codes)
        self.assertIn("4054.01", first_codes)
        self.assertIn("4054.02", second_codes)
        # The first partner's names are untouched by the second arriving.
        self.assertEqual(
            self._children_of(first).filter(account_code="4054.01").first().account_name,
            "Drawings — Daniel Vriend",
        )

    def test_the_accounts_inherit_their_parents_financial_statement_mapping(self):
        """Drawings and capital contribution map to different balance sheet
        lines. A child that lost its parent's mapping would fall off the
        financial statements silently."""
        parent = EntityChartOfAccount.objects.get(
            entity=self.partnership, account_code="4054")
        parent.section = "capital_accounts"
        parent.save(update_fields=["section"])

        partner = self._add_partner("Daniel Vriend")
        child = self._children_of(partner).get(account_code="4054.01")

        self.assertEqual(child.section, parent.section)

    def test_renaming_a_partner_renames_their_accounts(self):
        partner = self._add_partner("Danial Vreind")  # typo, as first entered

        partner.full_name = "Daniel Vriend"
        partner.save()

        names = set(self._children_of(partner).values_list("account_name", flat=True))
        self.assertIn("Drawings — Daniel Vriend", names)
        self.assertNotIn("Drawings — Danial Vreind", names)

    def test_a_ceased_partner_keeps_their_accounts_but_flagged(self):
        """The accounts still hold history, so they cannot simply vanish."""
        partner = self._add_partner(
            "Departing Partner", date_ceased=date.today() - timedelta(days=1))

        children = self._children_of(partner)
        self.assertEqual(children.count(), len(PARTNER_PARENT_CODES))
        self.assertTrue(all(c.is_ceased for c in children))

    def test_provisioning_twice_creates_nothing_the_second_time(self):
        partner = self._add_partner("Daniel Vriend")
        before = self._children_of(partner).count()

        partner.save()

        self.assertEqual(self._children_of(partner).count(), before)


class LegacySlotCleanupTests(PartnerAccountTestBase):
    """The template's hard-coded partner slots have to go, or the entity ends
    up with both the real partners' accounts and the stranger's."""

    def _add_legacy_slots(self):
        for code, name in [("4007", "Capital Contribution - Chris"),
                           ("4007.01", "Capital Contribution - Chris"),
                           ("4054.03", "Drawings - Oskah")]:
            EntityChartOfAccount.objects.create(
                entity=self.partnership, account_code=code, account_name=name,
                section="capital_accounts", is_active=True, is_custom=False,
                auto_provisioned=False,
            )

    def test_the_inherited_slots_are_removed_when_the_first_partner_is_added(self):
        self._add_legacy_slots()

        self._add_partner("Daniel Vriend")

        # Only the INHERITED rows go. The partner's own 4054.01 etc. share
        # those codes and must survive, which auto_provisioned distinguishes.
        remaining = EntityChartOfAccount.objects.filter(
            entity=self.partnership,
            account_code__in=PARTNER_SLOT_CODES_TO_REMOVE,
            auto_provisioned=False,
        )
        self.assertEqual(remaining.count(), 0)

    def test_a_slot_that_has_been_posted_to_is_never_removed(self):
        """Deleting an account that carries a balance would take the balance
        with it. It stays, and the escalation is logged for a human."""
        self._add_legacy_slots()
        fy = FinancialYear.objects.create(
            entity=self.partnership, start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
        )
        TrialBalanceLine.objects.create(
            financial_year=fy, account_code="4007",
            account_name="Capital Contribution - Chris",
            debit=0, credit=5000,
        )

        self._add_partner("Daniel Vriend")

        self.assertTrue(
            EntityChartOfAccount.objects.filter(
                entity=self.partnership, account_code="4007").exists()
        )


class EntityTypeIsolationTests(PartnerAccountTestBase):
    """The entity-type gate is the only thing keeping the two schemes apart."""

    def test_a_partnership_never_gets_the_trust_only_codes(self):
        partner = self._add_partner("Daniel Vriend")

        codes = {c.account_code.split(".")[0]
                 for c in self._children_of(partner)}
        trust_only = {e["code"] for e in BENEFICIARY_PARENT_CODES} - {
            e["code"] for e in PARTNER_PARENT_CODES}
        self.assertFalse(codes & trust_only)

    def test_a_director_on_a_company_gets_no_partner_accounts(self):
        company = Entity.objects.create(
            entity_name="Not A Partnership Pty Ltd",
            entity_type="company",
            client=self.client_obj,
        )
        officer = EntityOfficer.objects.create(
            entity=company, full_name="A Director",
            role=EntityOfficer.OfficerRole.DIRECTOR,
        )

        self.assertEqual(
            EntityChartOfAccount.objects.filter(
                beneficiary_officer=officer, auto_provisioned=True).count(),
            0,
        )

    def test_a_partner_role_on_a_company_gets_nothing(self):
        """The role alone must not be enough — the entity type decides."""
        company = Entity.objects.create(
            entity_name="Company With A Partner Pty Ltd",
            entity_type="company",
            client=self.client_obj,
        )
        officer = EntityOfficer.objects.create(
            entity=company, full_name="Mislabelled Partner",
            role=EntityOfficer.OfficerRole.PARTNER,
        )

        self.assertEqual(
            EntityChartOfAccount.objects.filter(
                beneficiary_officer=officer, auto_provisioned=True).count(),
            0,
        )
