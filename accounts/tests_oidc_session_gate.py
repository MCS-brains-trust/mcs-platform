"""An Entra session must not be bounced into SH's own TOTP gate.

Require2FAMiddleware enforces two things on every non-exempt path: TOTP
configured, and TOTP performed this session. Entra users satisfy neither by
construction — they have no totp_secret — so the middleware needs an explicit
branch, and the callback needs to stamp the flag that branch reads.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from accounts.oidc_views import ENTRA_SESSION_KEY


class EntraSessionGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="louise", email="louise@mcands.com.au", password="x" * 14,
        )
        self.assertFalse(self.user.has_2fa)  # no TOTP: the whole point

    def _sign_in_via_entra(self):
        self.client.force_login(self.user)
        session = self.client.session
        session[ENTRA_SESSION_KEY] = True
        session["2fa_verified"] = True
        session.save()

    def test_entra_session_reaches_the_app_without_totp(self):
        self._sign_in_via_entra()
        response = self.client.get(reverse("core:entity_list"), secure=True)
        self.assertEqual(response.status_code, 200)

    def test_password_session_without_totp_is_still_forced_into_setup(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:entity_list"), secure=True)
        self.assertRedirects(
            response, reverse("accounts:setup_2fa"), fetch_redirect_response=False,
        )

    def test_oidc_paths_are_exempt_from_the_gate(self):
        from config.middleware import Require2FAMiddleware
        middleware = Require2FAMiddleware(lambda request: None)
        self.assertTrue(middleware._is_exempt("/accounts/oidc/authenticate/"))
        self.assertTrue(middleware._is_exempt("/accounts/oidc/callback/"))

    def test_login_page_offers_microsoft_sign_in_alongside_the_password_form(self):
        response = self.client.get(reverse("accounts:login"), secure=True)
        self.assertContains(response, reverse("oidc_authentication_init"))
        self.assertContains(response, "Sign in with Microsoft")
        # Dual-run: the password form is still there.
        self.assertContains(response, 'name="password"')
