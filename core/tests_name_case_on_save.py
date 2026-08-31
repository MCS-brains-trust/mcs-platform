"""Saving a person's name in capitals normalises it.

Names reach these fields from the entity form, access_ledger_import, the
link_entities_to_xpm command and the admin, so the hook sits on save() rather
than on any one form.

Entity is the careful case: entity_name is a person's name for a sole trader
and a company or trust name otherwise, where title case would damage acronyms.
"""
from django.test import TestCase

from core.models import Client, Entity, EntityOfficer


class ClientNameCasingTests(TestCase):
    def test_all_caps_is_normalised_on_save(self):
        client = Client.objects.create(name="ELLIOTT JAQUES")
        client.refresh_from_db()
        self.assertEqual(client.name, "Elliott Jaques")

    def test_mixed_case_is_untouched(self):
        client = Client.objects.create(name="de Silva Family")
        client.refresh_from_db()
        self.assertEqual(client.name, "de Silva Family")


class EntityOfficerNameCasingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.entity = Entity.objects.create(
            entity_name="Holder Trust", entity_type="trust",
            client=Client.objects.create(name="Officer Casing Client"),
        )

    def test_all_caps_is_normalised_on_save(self):
        officer = EntityOfficer.objects.create(
            entity=self.entity, full_name="RONEN DAVIDOV", role="beneficiary"
        )
        officer.refresh_from_db()
        self.assertEqual(officer.full_name, "Ronen Davidov")

    def test_normalised_on_a_later_update_too(self):
        officer = EntityOfficer.objects.create(
            entity=self.entity, full_name="Jane Smith", role="beneficiary"
        )
        officer.full_name = "ANNE-MARIE O'BRIEN"
        officer.save()
        officer.refresh_from_db()
        self.assertEqual(officer.full_name, "Anne-Marie O'Brien")


class EntityNameCasingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Entity Casing Client")

    def test_a_sole_trader_name_is_a_person_and_is_normalised(self):
        """The Elliott Jaques case exactly."""
        entity = Entity.objects.create(
            entity_name="ELLIOTT JAQUES", trading_as="ELLIOTT JAQUES",
            entity_type="sole_trader", client=self.client_obj,
        )
        entity.refresh_from_db()
        self.assertEqual(entity.entity_name, "Elliott Jaques")
        self.assertEqual(entity.trading_as, "Elliott Jaques")

    def test_a_company_name_is_left_alone(self):
        """Title case would give "Abc Pty Ltd"."""
        entity = Entity.objects.create(
            entity_name="ABC PTY LTD", entity_type="company", client=self.client_obj,
        )
        entity.refresh_from_db()
        self.assertEqual(entity.entity_name, "ABC PTY LTD")

    def test_a_trust_name_is_left_alone(self):
        entity = Entity.objects.create(
            entity_name="DJLH PROPERTIES FAMILY TRUST", entity_type="trust",
            client=self.client_obj,
        )
        entity.refresh_from_db()
        self.assertEqual(entity.entity_name, "DJLH PROPERTIES FAMILY TRUST")

    def test_a_unit_trust_name_is_left_alone(self):
        entity = Entity.objects.create(
            entity_name="MINLI ENTERPRISE UNIT TRUST", entity_type="trust_unit",
            client=self.client_obj,
        )
        entity.refresh_from_db()
        self.assertEqual(entity.entity_name, "MINLI ENTERPRISE UNIT TRUST")

    def test_a_partnership_name_is_left_alone(self):
        entity = Entity.objects.create(
            entity_name="D.P VAUGHAN & D VRIEND", entity_type="partnership",
            client=self.client_obj,
        )
        entity.refresh_from_db()
        self.assertEqual(entity.entity_name, "D.P VAUGHAN & D VRIEND")
