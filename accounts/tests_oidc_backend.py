# accounts/tests_oidc_backend.py
"""Entra claims -> existing accounts.User row.

Behaviours carry the cutover:
  1. email claim matches an existing row and stamps azure_object_id (first sign-in)
  2. a row already linked is found by oid, even if the email claim changed
  3. no matching row -> refused, and NO user is created
  4. a row linked to a DIFFERENT oid -> refused loudly, never silently relinked
  5. Entra users without a `mail` attribute present the UPN as
     preferred_username instead of email; that must still match
  6. verify_claims must still pass for a UPN-only claim set: OIDC_RP_SCOPES
     includes "email", and the base class's verify_claims checks for the literal
     "email" key, which a UPN-only presentation never carries. Without an
     override those staff would be refused before filter_users_by_claims ever
     runs.
  7. azure_object_id is unique at the DB level: two existing unlinked users
     cannot both be saved with the same oid (the cutover's actual linking
     codepath is .save(), not create_user).
"""
from unittest.mock import patch

from django.core.exceptions import SuspiciousOperation
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User
from accounts.oidc_backend import EntraLinkOnlyBackend

OID_A = "aaaaaaaa-1111-2222-3333-444444444444"
OID_B = "bbbbbbbb-1111-2222-3333-444444444444"


class EntraLinkOnlyBackendTests(TestCase):
    def setUp(self):
        self.backend = EntraLinkOnlyBackend()
        self.user = User.objects.create_user(
            username="angelo", email="angelo@mcands.com.au", password="x" * 14,
        )

    def test_first_sign_in_links_by_email_and_stamps_oid(self):
        claims = {"email": "Angelo@mcands.com.au", "oid": OID_A}
        found = list(self.backend.filter_users_by_claims(claims))
        self.assertEqual(found, [self.user])
        linked = self.backend.update_user(found[0], claims)
        linked.refresh_from_db()
        self.assertEqual(linked.azure_object_id, OID_A)

    def test_linked_user_is_found_by_oid_when_email_changed(self):
        self.user.azure_object_id = OID_A
        self.user.save(update_fields=["azure_object_id"])
        claims = {"email": "angelo.covelli@mcands.com.au", "oid": OID_A}
        self.assertEqual(list(self.backend.filter_users_by_claims(claims)), [self.user])

    def test_unknown_email_is_refused_and_creates_nobody(self):
        before = User.objects.count()
        claims = {"email": "stranger@example.com", "oid": OID_B}
        self.assertEqual(list(self.backend.filter_users_by_claims(claims)), [])
        self.assertEqual(User.objects.count(), before)

    def test_relinking_a_row_to_a_second_entra_identity_is_refused(self):
        self.user.azure_object_id = OID_A
        self.user.save(update_fields=["azure_object_id"])
        with self.assertRaises(SuspiciousOperation):
            self.backend.update_user(self.user, {"email": "angelo@mcands.com.au", "oid": OID_B})
        self.user.refresh_from_db()
        self.assertEqual(self.user.azure_object_id, OID_A)

    def test_upn_only_claims_still_match(self):
        claims = {"preferred_username": "angelo@mcands.com.au", "oid": OID_A}
        self.assertEqual(list(self.backend.filter_users_by_claims(claims)), [self.user])

    def test_inactive_user_is_not_matched(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        claims = {"email": "angelo@mcands.com.au", "oid": OID_A}
        self.assertEqual(list(self.backend.filter_users_by_claims(claims)), [])

    def test_userinfo_merges_the_oid_claim_from_the_verified_id_token(self):
        # Graph's /oidc/userinfo does not return `oid`; the id_token does.
        #
        # mozilla-django-oidc 4.0.1's OIDCAuthenticationBackend.get_userinfo has
        # no `get_userinfo_from_endpoint` hook (auth.py:270): it calls
        # `requests.get` directly. Patching a `get_userinfo_from_endpoint`
        # attribute here would create an unused instance attribute and let the
        # real call dial out to Microsoft Graph. Instead patch the `requests.get`
        # name the library actually calls, with a fake response whose `.json()`
        # returns the userinfo payload and whose `.raise_for_status()` is a
        # no-op. The behaviour asserted is unchanged: `oid` is merged in from
        # the already-signature-verified id_token payload.
        backend = EntraLinkOnlyBackend()

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"email": "angelo@mcands.com.au"}

        with patch("mozilla_django_oidc.auth.requests.get", return_value=FakeResponse()):
            claims = backend.get_userinfo(
                "access", "id", {"oid": OID_A, "preferred_username": "angelo@mcands.com.au"}
            )
        self.assertEqual(claims["oid"], OID_A)
        self.assertEqual(claims["email"], "angelo@mcands.com.au")

    def test_verify_claims_passes_for_upn_only_claim_set(self):
        # OIDC_RP_SCOPES includes "email", so the base class's verify_claims
        # (auth.py:84) requires the literal "email" key and runs BEFORE
        # filter_users_by_claims. Staff whose Entra directory object has no
        # `mail` attribute present only `preferred_username`; without this
        # override they would be refused here and the UPN fallback in
        # filter_users_by_claims would never be reached.
        claims = {"preferred_username": "angelo@mcands.com.au", "oid": OID_A}
        self.assertTrue(self.backend.verify_claims(claims))

    def test_verify_claims_fails_with_no_email_or_upn(self):
        self.assertFalse(self.backend.verify_claims({"oid": OID_A}))

    def test_two_unlinked_users_cannot_share_an_azure_object_id_via_save(self):
        # The cutover's actual linking codepath is User.save(), not
        # create_user(); the DB-level uniqueness constraint (Task 1) must hold
        # against that codepath, not just at the ORM/create_user layer.
        other = User.objects.create_user(
            username="ross", email="ross@mcands.com.au", password="x" * 14,
        )
        self.user.azure_object_id = OID_A
        self.user.save(update_fields=["azure_object_id"])

        other.azure_object_id = OID_A
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                other.save(update_fields=["azure_object_id"])
