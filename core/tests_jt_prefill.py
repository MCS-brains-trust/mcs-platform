"""Mapping JT's identity envelope onto EntityForm's initial values.

Every fixture here is invented. Real client identity never goes in a test file,
so these names, ABNs and addresses belong to nobody.
"""
from django.test import TestCase

from accounts.models import User
from core.jt_prefill import prefill_from_identity


def envelope(**held):
    """A JT identity envelope: every named field held, nothing else present."""
    return {name: {"status": "held", "value": value} for name, value in held.items()}


class PrefillFromIdentityTests(TestCase):
    def test_maps_legal_name_to_entity_name(self):
        out = prefill_from_identity(envelope(legalName="Wombat Holdings Pty Ltd"))
        self.assertEqual(out["entity_name"], "Wombat Holdings Pty Ltd")

    def test_strips_the_abn_to_digits_so_it_fits_the_column(self):
        out = prefill_from_identity(envelope(abn="12 345 678 901"))
        self.assertEqual(out["abn"], "12345678901")
        self.assertEqual(len(out["abn"]), 11)

    def test_maps_entity_type_label_to_the_stored_value(self):
        out = prefill_from_identity(envelope(entityType="Company"))
        self.assertEqual(out["entity_type"], "company")

    def test_entity_type_match_ignores_case_and_spacing(self):
        out = prefill_from_identity(envelope(entityType="sole trader"))
        self.assertEqual(out["entity_type"], "sole_trader")

    def test_unrecognised_entity_type_is_left_blank_never_guessed(self):
        out = prefill_from_identity(envelope(entityType="Superannuation Fund Trustee"))
        self.assertNotIn("entity_type", out)

    def test_maps_the_address_parts_onto_sh_field_names(self):
        out = prefill_from_identity(envelope(
            address="14 Example Pde", city="SOMEWHERE", region="VIC",
            postCode="3000", country="Australia",
        ))
        self.assertEqual(out["address_line_1"], "14 Example Pde")
        self.assertEqual(out["suburb"], "SOMEWHERE")
        self.assertEqual(out["state"], "VIC")
        self.assertEqual(out["postcode"], "3000")
        self.assertEqual(out["country"], "Australia")

    def test_not_held_fields_are_absent_rather_than_blank_strings(self):
        out = prefill_from_identity({
            "legalName": {"status": "held", "value": "Wombat Holdings Pty Ltd"},
            "email": {"status": "not_held"},
            "address": {"status": "not_held"},
        })
        self.assertNotIn("contact_email", out)
        self.assertNotIn("address_line_1", out)

    def test_a_restricted_tfn_is_never_prefilled(self):
        out = prefill_from_identity({"tfn": {"status": "restricted", "masked": "***-***-901"}})
        self.assertNotIn("tfn", out)

    def test_a_held_tfn_is_still_never_prefilled(self):
        """includePii is never sent, but refuse it even if a held TFN arrives."""
        out = prefill_from_identity({"tfn": {"status": "held", "value": "123456789"}})
        self.assertNotIn("tfn", out)

    def test_an_empty_envelope_prefills_nothing(self):
        self.assertEqual(prefill_from_identity({}), {})


class PrefillAccountantTests(TestCase):
    def test_matches_the_account_manager_to_a_statementhub_user(self):
        user = User.objects.create_user(
            username="pat", email="pat@example.test", password="x" * 14,
            first_name="Pat", last_name="Nguyen",
        )
        out = prefill_from_identity(envelope(accountManager="Pat Nguyen"))
        self.assertEqual(out["assigned_accountant"], user.pk)

    def test_an_unknown_account_manager_is_left_blank(self):
        out = prefill_from_identity(envelope(accountManager="Nobody Here"))
        self.assertNotIn("assigned_accountant", out)

    def test_an_inactive_user_is_not_matched(self):
        User.objects.create_user(
            username="gone", email="gone@example.test", password="x" * 14,
            first_name="Gone", last_name="Away", is_active=False,
        )
        out = prefill_from_identity(envelope(accountManager="Gone Away"))
        self.assertNotIn("assigned_accountant", out)
