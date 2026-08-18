"""
Regression tests for the `next` destination being dropped by the 2FA login.

`MCSLoginView.form_valid` reads the redirect target from `request.POST["next"]`,
but neither login template rendered a hidden `next` field, so the value was
always empty and every deep link bounced through login landed on the dashboard
instead of the page the user asked for.

This was first seen losing a QuickBooks OAuth callback: the callback is behind
`login_required`, so a lapsed session sent it to the login page and the
authorisation code was never replayed.
"""

from urllib.parse import quote

import pyotp
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class LoginNextPreservedTests(TestCase):
    def setUp(self):
        self.secret = pyotp.random_base32()
        self.password = "correct-horse-battery-staple"
        self.user = get_user_model().objects.create_user(
            username="deeplink",
            email="deeplink@example.com",
            password=self.password,
            role="admin",
            totp_secret=self.secret,
            totp_confirmed=True,
        )
        self.target = "/integrations/qb/callback/?code=abc123&state=xyz"

    def test_login_page_renders_the_next_destination(self):
        response = self.client.get(
            reverse("accounts:login"), {"next": self.target}, secure=True
        )

        self.assertContains(response, 'name="next"')
        self.assertContains(response, "code=abc123")

    def test_user_lands_on_next_after_completing_two_factor(self):
        # Exactly the production request shape: the browser posts the form to
        # the action URL, which carries ?next=..., and (with the field now
        # rendered) the body carries it too. Posting only the query string
        # reproduces what the un-fixed template sent.
        self.client.post(
            reverse("accounts:login") + "?next=" + quote(self.target),
            {"username": "deeplink", "password": self.password},
            secure=True,
        )

        response = self.client.post(
            reverse("accounts:totp_verify"),
            {"totp_code": pyotp.TOTP(self.secret).now()},
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.target)

    def test_offsite_next_from_query_string_is_refused(self):
        # The GET fallback widened the input surface, so the open-redirect
        # guard must still reject a destination on another host.
        self.client.post(
            reverse("accounts:login") + "?next=" + quote("https://evil.example.com/steal"),
            {"username": "deeplink", "password": self.password},
            secure=True,
        )

        response = self.client.post(
            reverse("accounts:totp_verify"),
            {"totp_code": pyotp.TOTP(self.secret).now()},
            secure=True,
        )

        self.assertNotIn("evil.example.com", response.url)
