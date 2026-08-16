"""Keycloak OIDC authentication backend.

Mozilla's ``OIDCAuthenticationBackend`` handles the standard code flow and
token validation against Keycloak. We only add the authorization half: every
successful login mirrors the subject's Keycloak principals (realm roles,
client roles and groups) into Django ``auth.Group`` membership, so the ACM
imported from Keycloak is enforced for that user from the very next request.
"""
from __future__ import annotations

from django.conf import settings
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .keycloak import sync_user_from_claims


class KeycloakOIDCBackend(OIDCAuthenticationBackend):
    """OIDC backend that syncs Keycloak roles/groups into Django groups."""

    def get_or_create_user(self, access_token, id_token, payload):
        user = super().get_or_create_user(access_token, id_token, payload)
        if user is not None:
            # payload is the verified id_token claims; the userinfo response
            # (carrying the authoritative roles/groups claims) is fetched by
            # get_userinfo inside the parent call. Merge groups from both.
            user_info = self.get_userinfo(access_token, id_token, payload)
            claims = {**user_info, **payload}
            sync_user_from_claims(user, claims)
        return user

    def filter_users_by_claims(self, claims):
        """Match Keycloak ``preferred_username`` or ``email`` to a Django user."""
        email = claims.get("email")
        username = claims.get("preferred_username") or claims.get("sub")

        users = self.UserModel.objects.none()
        if email:
            users = users | self.UserModel.objects.filter(email__iexact=email)
        if username:
            users = users | self.UserModel.objects.filter(username__exact=username)
        return users.distinct()

    def verify_claims(self, claims):
        """Restrict logins to users carrying at least one granted principal.

        This is the Keycloak-side gate: a token from Keycloak proves
        identity, but access is only created once the ACM has been imported
        and at least one role/group maps into Django.
        """
        verified = super().verify_claims(claims)
        if not verified:
            return False
        if getattr(settings, "KEYCLOAK_REQUIRE_PRINCIPAL", True):
            from .keycloak import group_names_from_claims

            if not group_names_from_claims(claims):
                return False
        return True