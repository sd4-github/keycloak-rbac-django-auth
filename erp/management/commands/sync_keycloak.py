"""Sync Keycloak principals into Django groups (admin API path).

Usage::

    python manage.py sync_keycloak              # mirror every KC principal
    python manage.py sync_keycloak --user priya # one user's group membership

Requires a service account with realm-management rights
(``KEYCLOAK_ADMIN_USERNAME`` / ``KEYCLOAK_ADMIN_PASSWORD``). The OIDC login
path syncs per-user groups automatically; this command is for bootstrapping
and for users who have never logged in.
"""
from django.core.management.base import BaseCommand

from erp.keycloak import KeycloakAdminClient


class Command(BaseCommand):
    help = "Mirror Keycloak realm roles / client roles / groups into Django groups"

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Sync a single user's groups only")

    def handle(self, *args, **opts):
        client = KeycloakAdminClient()
        if opts["user"]:
            client.sync_user(opts["user"])
            self.stdout.write(self.style.SUCCESS(f"synced user {opts['user']}"))
        else:
            groups = client.sync_all_roles()
            self.stdout.write(
                self.style.SUCCESS(f"mirrored {len(groups)} Keycloak principals")
            )