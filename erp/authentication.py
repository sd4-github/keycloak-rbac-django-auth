"""DRF authentication for Bearer tokens minted by Keycloak.

Every request authenticates the access token via the Keycloak OIDC backend,
which (as a side effect of ``get_or_create_user``) re-syncs the subject's
roles/groups from Keycloak - so permission changes are live on the very next
request, with no cache flush and no restart.
"""
from mozilla_django_oidc.contrib.drf import OIDCAuthentication


class KeycloakBearerAuthentication(OIDCAuthentication):
    """Bearer-token auth against Keycloak with per-request group sync."""

    www_authenticate_realm = "keycloak-rbac"