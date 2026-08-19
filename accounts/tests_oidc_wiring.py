# accounts/tests_oidc_wiring.py
"""Configuration-level guards on the OIDC wiring.

These are cheap and they protect two things that are easy to get wrong later:
the callback path must match the Azure app registration's redirect URI exactly,
and signature verification must never be weakened to match JT's server-side
exchange (JT skips id_token verification on purpose; SH has no reason to).
"""
from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class OidcWiringTests(TestCase):
    def test_callback_url_matches_the_azure_redirect_uri(self):
        self.assertEqual(reverse("oidc_authentication_callback"), "/accounts/oidc/callback/")

    def test_init_url_is_routed(self):
        self.assertEqual(reverse("oidc_authentication_init"), "/accounts/oidc/authenticate/")

    def test_entra_backend_is_first_and_password_backend_still_present(self):
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS[0],
            "accounts.oidc_backend.EntraLinkOnlyBackend",
        )
        self.assertIn(
            "django.contrib.auth.backends.ModelBackend",
            settings.AUTHENTICATION_BACKENDS,
        )

    def test_signature_verification_is_not_weakened(self):
        self.assertEqual(settings.OIDC_RP_SIGN_ALGO, "RS256")
        self.assertTrue(settings.OIDC_OP_JWKS_ENDPOINT)

    def test_oidc_never_creates_users(self):
        self.assertFalse(settings.OIDC_CREATE_USER)
