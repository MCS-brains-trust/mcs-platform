"""A unit trust reaches the same screens a discretionary trust does."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Entity, EntityOfficer


class UnitTrustTemplateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ut", email="ut@example.com", password="secret123",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()
        self.unit = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        EntityOfficer.objects.create(
            entity=self.unit,
            full_name="Jane Unitholder",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER,
            distribution_percentage=100,
        )

    def test_officers_page_renders_for_a_unit_trust(self):
        response = self.client.get(
            reverse("core:entity_officers", kwargs={"pk": self.unit.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        # Structural marker: the "Distribution %" table column only renders
        # for trust-like entities (entity_officers.html:47). It is unrelated
        # to the disputed "Unit Holder" vs "Beneficiary" wording (Task 10).
        self.assertContains(response, "Distribution %")

    def test_entity_detail_renders_for_a_unit_trust(self):
        response = self.client.get(
            reverse("core:entity_detail", kwargs={"pk": self.unit.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        # Badge arm added for trust_unit (Ruling C): distinct from the
        # discretionary trust's plain "info" badge.
        self.assertContains(response, "bg-info-subtle text-info-emphasis")
        # Ruling B site (entity_detail.html:92) already spelled out
        # trust_unit before this task; confirm it still renders.
        self.assertContains(response, "Trust Deed")
