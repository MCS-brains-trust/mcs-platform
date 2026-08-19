"""SSO callback.

Wraps the library's callback purely to stamp two session flags:

  auth_via_entra   read by Require2FAMiddleware to skip SH's own TOTP gate,
                   because MFA is enforced centrally in Entra.
  2fa_verified     the flag the middleware's second check reads. Set for the
                   same reason, and kept explicit so a future reader sees that
                   an Entra session IS a two-factor session, not an exempted one.
"""
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView

ENTRA_SESSION_KEY = "auth_via_entra"


class EntraCallbackView(OIDCAuthenticationCallbackView):
    def login_success(self):
        response = super().login_success()
        self.request.session[ENTRA_SESSION_KEY] = True
        self.request.session["2fa_verified"] = True
        return response
