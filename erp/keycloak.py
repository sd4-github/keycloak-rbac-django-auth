"""Keycloak <-> Django auth bridge.

Authentication is handled by ``mozilla_django_oidc`` (see the auth backend
in this package); this module owns the *authorization* half: mirroring
Keycloak's access control matrix into Django's ``Group``/``Permission``
tables so the same rows that drive ``has_perm`` in DRF also power every
scoped queryset.
"""
from __future__ import annotations

import logging
from typing import Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)


def _get_or_create_keycloak_group(name: str, source: str = "") -> Group:
    """Get-or-create a Django group flagged as a Keycloak-mapped role."""
    from .models import GroupProfile

    group, created = Group.objects.get_or_create(name=name)
    GroupProfile.objects.get_or_create(
        group=group,
        defaults={
            "kind": GroupProfile.Kind.KEYCLOAK_ROLE,
            "keycloak_source": source or name,
        },
    )
    return group


def group_names_from_claims(claims: dict) -> set[str]:
    """Extract the subject's Keycloak principals from decoded OIDC claims.

    Union of:
      * realm roles   -> ``claims["realm_access"]["roles"]``
      * client roles  -> ``claims["resource_access"][<client id>]["roles"]``
      * groups        -> ``claims["groups"]`` (requires ``groups`` in the
        client's token claim mapping; Keycloak emits them by default).

    Every principal is mirrored into a Django ``auth.Group`` of the same
    name, so permissions configured against Keycloak roles in the ACM
    import are enforced automatically.
    """
    names: set[str] = set()

    realm_access = claims.get("realm_access") or {}
    names.update(realm_access.get("roles", []))

    client_id = settings.KEYCLOAK_CLIENT_ID
    resource_access = claims.get("resource_access") or {}
    client_roles = resource_access.get(client_id, {}).get("roles", [])
    names.update(client_roles)

    names.update(claims.get("groups", []))

    # Keycloak's default "offline_access"/"uma_authorization" bookkeeping
    # roles grant nothing in Django; drop them to avoid phantom groups.
    return {n for n in names if n not in {"offline_access", "uma_authorization"}}


def sync_user_from_claims(user, claims: dict) -> set[str]:
    """Set the user's Django groups to exactly the Keycloak principals
    named in their token. The groups are created on first sight.

    Returns the set of principal names that were mirrored.
    """
    names = group_names_from_claims(claims)
    groups = [_get_or_create_keycloak_group(n) for n in sorted(names)]
    user.groups.set(groups)

    # Drop the per-instance permission caches: the token is newer than
    # whatever this process may have cached from a previous login.
    for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        user.__dict__.pop(attr, None)

    logger.info("synced %s principals to user %s", len(groups), user.username)
    return names


class KeycloakAdminClient:
    """Thin wrapper over ``python-keycloak``'s admin REST client.

    Used by the ``sync_keycloak`` management command to (re)build the set
    of Django groups from every realm role, client role and group that
    actually exists in Keycloak - so the data model never drifts from the
    authority.
    """

    def __init__(self):
        from keycloak import KeycloakAdmin

        self.client = KeycloakAdmin(
            server_url=settings.KEYCLOAK_URL,
            username=settings.KEYCLOAK_ADMIN_USERNAME,
            password=settings.KEYCLOAK_ADMIN_PASSWORD,
            realm_name=settings.KEYCLOAK_REALM,
            verify=settings.KEYCLOAK_VERIFY_TLS,
            user_realm_name=settings.KEYCLOAK_ADMIN_REALM,
            client_id="admin-cli",
        )

    def principals(self) -> set[str]:
        """Every realm role, client role and group name in the realm."""
        names: set[str] = set()

        for role in self.client.get_realm_roles():
            names.add(role["name"])
        for client in self.client.get_clients():
            for role in self.client.get_client_roles(client["id"]):
                names.add(role["name"])
        for group in self.client.get_groups():
            names.add(group["name"])

        return {n for n in names if n not in {"offline_access", "uma_authorization"}}

    def sync_all_roles(self) -> list[Group]:
        """Create a Django group for every Keycloak principal in the realm.

        Idempotent: existing groups are reused and only re-flagged as
        Keycloak-mapped. Call after the ACM import so grant changes in
        Keycloak propagate to Django data.
        """
        groups = [_get_or_create_keycloak_group(n) for n in sorted(self.principals())]
        logger.info("mirrored %d Keycloak principals into auth groups", len(groups))
        return groups

    def sync_user(self, username: str) -> None:
        """Mirror one Keycloak user's principals into their Django groups.

        Uses the admin API, so it also works for users who have never
        logged in via OIDC (admin provisioning path).
        """
        User = get_user_model()
        user = User.objects.get(username=username)

        kc_user = next(
            u for u in self.client.get_users(query={"username": username})
            if u["username"] == username
        )

        claims = {
            "realm_access": {"roles": self.client.get_realm_roles_of_user(kc_user["id"])},
            "groups": [g["name"] for g in self.client.get_user_groups(kc_user["id"])],
        }
        claims["realm_access"]["roles"] = [
            r["name"] for r in claims["realm_access"]["roles"]
        ]

        resource_access: dict = {}
        for client in self.client.get_clients():
            client_id = client.get("clientId")
            if client_id in {settings.KEYCLOAK_CLIENT_ID, settings.KEYCLOAK_ADMIN_CLIENT_ID}:
                roles = self.client.get_client_role_mappings(kc_user["id"], client["id"])
                resource_access[client_id] = {
                    "roles": [r["name"] for r in roles] if roles else []
                }
        claims["resource_access"] = resource_access

        sync_user_from_claims(user, claims)


def ensure_groups_for_principals(names: Iterable[str]) -> None:
    """Public helper: mirror arbitrary principal names into Django groups."""
    for name in names:
        _get_or_create_keycloak_group(name)