"""Shared test-support helpers for security-middleware-aware tests.

Two middlewares intercept every request in production (see
``config/middleware.py`` and ``config/settings.py``), and a test that
doesn't satisfy them never reaches the view it's supposedly testing:

- ``Require2FAMiddleware`` requires the authenticated session to have
  ``session["2fa_verified"] = True`` in addition to Django auth. Test users
  also need 2FA *configured* (``totp_secret`` set, ``totp_confirmed=True``)
  or the middleware bounces them to the setup-2fa page before it even looks
  at ``2fa_verified``.
- ``SECURE_SSL_REDIRECT = True`` 301s any request that isn't ``secure=True``.

Use ``TwoFAClient`` in place of ``django.test.Client`` (it defaults every
request to ``secure=True``, still overridable per call), and
``Require2FAMixin.login_as`` in place of a bare ``self.client.force_login``
(it force-logs in and marks the session 2FA-verified). Together they let a
test case log in once per class/setUp and stop thinking about either
middleware again.
"""
from django.test import Client


class TwoFAClient(Client):
    """A Django test Client that issues HTTPS requests by default.

    ``SECURE_SSL_REDIRECT`` means a plain-HTTP test request 301s before the
    view under test ever runs. Defaulting to ``secure=True`` here fixes that
    for every request made through this client; pass ``secure=False``
    explicitly on a call that means to exercise the redirect itself.
    """

    def get(self, path, *args, **kwargs):
        kwargs.setdefault("secure", True)
        return super().get(path, *args, **kwargs)

    def post(self, path, *args, **kwargs):
        kwargs.setdefault("secure", True)
        return super().post(path, *args, **kwargs)

    def put(self, path, *args, **kwargs):
        kwargs.setdefault("secure", True)
        return super().put(path, *args, **kwargs)

    def patch(self, path, *args, **kwargs):
        kwargs.setdefault("secure", True)
        return super().patch(path, *args, **kwargs)

    def delete(self, path, *args, **kwargs):
        kwargs.setdefault("secure", True)
        return super().delete(path, *args, **kwargs)

    def head(self, path, *args, **kwargs):
        kwargs.setdefault("secure", True)
        return super().head(path, *args, **kwargs)

    def options(self, path, *args, **kwargs):
        kwargs.setdefault("secure", True)
        return super().options(path, *args, **kwargs)

    def trace(self, path, *args, **kwargs):
        kwargs.setdefault("secure", True)
        return super().trace(path, *args, **kwargs)


class Require2FAMixin:
    """Give a TestCase a `login_as(user)` that satisfies Require2FAMiddleware.

    Replaces ``self.client`` with a fresh ``TwoFAClient``, force-logs in the
    given user, and marks the session as having completed TOTP this session
    (``session["2fa_verified"] = True``) — the thing
    ``accounts.views.totp_verify_view`` does on a real login and that
    ``force_login`` skips. The user still needs 2FA *configured*
    (``totp_secret`` + ``totp_confirmed=True``) or the middleware redirects
    to the setup-2fa page regardless.
    """

    def login_as(self, user):
        self.client = TwoFAClient()
        self.client.force_login(user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()
        return self.client
