"""Import a Keycloak ACM matrix (JSON) into Django auth as data.

Usage::

    python manage.py import_acm matrix.json

The file mirrors Keycloak client authorization:

.. code-block:: json

    {
      "client": "erp-api",
      "roles": {
        "erp-admin":        {"invoice": ["view", "add", "change", "delete"]},
        "pune-coordinator": {"invoice": ["view", "change"]}
      }
    }

Each ``role x resource x action`` cell becomes an ``AcmEntry`` row and a
matching ``auth_group_permissions`` row. Re-importing an updated matrix is
an update (entries for each group are replaced), so grants change on the
next request with no deploy.
"""
from django.core.management.base import BaseCommand

from erp import acm


class Command(BaseCommand):
    help = "Import a Keycloak ACM matrix (JSON) into Django auth groups/permissions"

    def add_arguments(self, parser):
        parser.add_argument("matrix_file", help="Path to the ACM matrix JSON")
        parser.add_argument(
            "--no-replace",
            action="store_false",
            dest="replace",
            help="Accumulate cells instead of replacing each subject's matrix",
        )

    def handle(self, *args, **opts):
        count = acm.import_matrix_file(opts["matrix_file"], replace_subjects=opts["replace"])
        self.stdout.write(self.style.SUCCESS(f"imported {count} ACM cells"))