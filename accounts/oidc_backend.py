# accounts/oidc_backend.py
"""Microsoft Entra -> existing accounts.User, link-only.

This backend never provisions a user. The 7 SH staff already exist as
accounts.User rows carrying every foreign key that matters (primary_accountant,
reviewer, assigned entities, AuditLog, ActivityLog), so the cutover LINKS rows;
it does not migrate or recreate them. An Entra identity with no matching row is
refused, and the login page shows the SSO failure notice.
"""
import logging

from django.core.exceptions import SuspiciousOperation
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

logger = logging.getLogger(__name__)


def claim_email(claims):
    """The signing-in identity's email address.

    Entra only issues the `email` claim when the directory object has a `mail`
    attribute; otherwise the UPN arrives as `preferred_username`. All 7 staff are
    @mcands.com.au either way, so both are accepted.
    """
    raw = claims.get("email") or claims.get("preferred_username") or ""
    return raw.strip()


class EntraLinkOnlyBackend(OIDCAuthenticationBackend):
    def get_userinfo(self, access_token, id_token, payload):
        """Merge `oid` in from the id_token.

        `oid` -- Entra's immutable directory object id, and the value we store as
        azure_object_id -- is an id_token claim and is absent from Graph's
        /oidc/userinfo response. `payload` is the id_token's claims AFTER
        verify_token() has checked the signature, so merging from it adds no
        trust that the flow did not already establish.
        """
        claims = dict(super().get_userinfo(access_token, id_token, payload))
        for key in ("oid", "preferred_username"):
            if payload.get(key):
                claims.setdefault(key, payload[key])
        return claims

    def verify_claims(self, claims):
        """Accept a UPN-only presentation, not just a literal `email` claim.

        `OIDC_RP_SCOPES` includes "email", so the base class's verify_claims
        (auth.py:84) requires the literal "email" key and runs BEFORE
        filter_users_by_claims. An Entra directory object with no `mail`
        attribute never issues that key, presenting only `preferred_username`
        instead; without this override those staff would be refused here and
        the UPN fallback filter_users_by_claims documents would never be
        reached.
        """
        return bool(claim_email(claims))

    def filter_users_by_claims(self, claims):
        oid = claims.get("oid")
        if oid:
            by_oid = self.UserModel.objects.filter(azure_object_id=oid, is_active=True)
            if by_oid.exists():
                return by_oid

        email = claim_email(claims)
        if not email:
            logger.warning("entra.sso.no_email_claim oid=%s", oid)
            return self.UserModel.objects.none()

        matches = self.UserModel.objects.filter(email__iexact=email, is_active=True)
        if not matches.exists():
            # Deliberate dead end: no row means no access. Provisioning happens
            # in SH's own user admin, not as a side effect of signing in.
            logger.warning("entra.sso.no_matching_user email=%s oid=%s", email, oid)
        return matches

    def update_user(self, user, claims):
        """Stamp the Entra link on first sign-in; refuse a second identity."""
        oid = claims.get("oid")
        if not oid:
            return user
        if user.azure_object_id and user.azure_object_id != oid:
            logger.error(
                "entra.sso.oid_conflict user=%s stored=%s presented=%s",
                user.pk, user.azure_object_id, oid,
            )
            raise SuspiciousOperation(
                "This account is already linked to a different Microsoft identity."
            )
        if not user.azure_object_id:
            user.azure_object_id = oid
            user.save(update_fields=["azure_object_id"])
            logger.info("entra.sso.linked user=%s oid=%s", user.pk, oid)
        return user
