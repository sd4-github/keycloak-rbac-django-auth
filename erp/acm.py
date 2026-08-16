"""Access Control Matrix engine.

The ACM is ``subject x object x action``. Keycloak's authorization services
express it for a client as roles (subjects), resources (objects) and scopes
(actions). This module imports such a matrix as Django data: an ``AcmEntry``
per allowed cell, materialised into ``auth_group_permissions`` - the same
tables ``has_perm`` and the DRF permission classes read per request.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType

from .models import AcmEntry, GroupProfile

logger = logging.getLogger(__name__)


class AcmImportError(Exception):
    pass


def _revoke_entries(group: Group) -> None:
    """Remove the auth_group_permissions rows materialised by the group's
    current ACM entries, so a narrower re-import actually revokes."""
    from django.contrib.auth.models import Permission

    for entry in group.acm_entries.select_related("object_type"):
        try:
            perm = Permission.objects.get(
                content_type=entry.object_type,
                codename=entry.permission_codename(),
            )
        except Permission.DoesNotExist:
            continue
        if entry.allow:
            group.permissions.remove(perm)


def resource_to_content_type(resource: str) -> ContentType:
    """Map a Keycloak resource name (e.g. ``invoice``) to its Django model.

    Resolution is explicit and unambiguous: ``ACM_RESOURCE_MODELS`` in
    settings maps resource -> ``app_label.model``, so a typo can never
    silently widen a grant to a same-named model in another app.
    """
    app_model = settings.ACM_RESOURCE_MODELS.get(resource)
    if not app_model:
        raise AcmImportError(f"resource '{resource}' is not in ACM_RESOURCE_MODELS")
    app_label, _, model_name = app_model.partition(".")
    try:
        return ContentType.objects.get_by_natural_key(app_label, model_name)
    except ContentType.DoesNotExist as exc:
        raise AcmImportError(
            f"resource '{resource}' maps to {app_model}, but that model has no "
            "ContentType (did you run migrations?)"
        ) from exc


def import_matrix(
    data: dict,
    *,
    subject_kind: str = GroupProfile.Kind.KEYCLOAK_ROLE,
    replace_subjects: bool = True,
) -> int:
    """Import a Keycloak ACM matrix as Django data.

    Expected shape (mirrors Keycloak client authorization):

    .. code-block:: json

        {
          "client": "erp-api",
          "roles": {
            "erp-admin": {
              "grants": ["erp.view_all_invoices", "erp.view_acm"],
              "invoice": ["view", "add", "change", "delete"],
              "orgunit": ["view"]
            },
            "pune-coordinator": {"invoice": ["view", "change"]}
          }
        }

    For every ``role -> resource -> actions`` cell this:
      1. gets-or-creates the Django ``Group`` named after the role
      2. writes an ``AcmEntry`` row (the matrix, kept as data)
      3. materialises the matching row in ``auth_group_permissions``

    ``grants`` lists raw Django permission codenames (``app_label.codename``)
    applied to the role directly - the org-wide bypasses like
    ``erp.view_all_invoices`` that are not a single model/action cell.

    ``replace_subjects`` removes existing grants and ACM entries for each
    group first, so re-importing a revised matrix is an update, not an
    accumulation.

    Returns the number of matrix cells imported.
    """
    from django.contrib.auth.models import Permission

    roles: dict = data.get("roles") or {}
    count = 0

    for role_name, resources in roles.items():
        group, _ = Group.objects.get_or_create(name=role_name)
        GroupProfile.objects.get_or_create(
            group=group,
            defaults={"kind": subject_kind, "keycloak_source": f"role:{role_name}"},
        )

        if replace_subjects:
            # Undo the old matrix before importing the new one, so a
            # revised (narrower) grant actually stops being enforced.
            _revoke_entries(group)
            AcmEntry.objects.filter(subject=group).delete()

        grants = resources.get("grants") or []
        for codename in grants:
            if "." not in codename:
                raise AcmImportError(
                    f"role '{role_name}': grant '{codename}' must be app_label.codename"
                )
            app_label, _, name = codename.partition(".")
            perm = Permission.objects.filter(
                content_type__app_label=app_label, codename=name
            ).first()
            if perm is None:
                raise AcmImportError(
                    f"role '{role_name}': grant '{codename}' does not exist "
                    "(did you run migrations?)"
                )
            group.permissions.add(perm)

        for resource, actions in (resources or {}).items():
            if resource == "grants":
                continue
            ct = resource_to_content_type(resource)
            for action in actions:
                if action not in AcmEntry.Action.values:
                    raise AcmImportError(
                        f"role '{role_name}': unknown action '{action}' "
                        f"(expected one of {AcmEntry.Action.values})"
                    )
                AcmEntry.objects.update_or_create(
                    subject=group,
                    object_type=ct,
                    object_id=None,
                    action=action,
                    defaults={"allow": True, "keycloak_scope": action},
                )
                count += 1

    logger.info("imported %d ACM cells across %d subjects", count, len(roles))
    return count


def import_matrix_file(path: str | Path, **kwargs) -> int:
    """Import an ACM matrix from a JSON file on disk."""
    data = json.loads(Path(path).read_text())
    if "roles" not in data:
        raise AcmImportError("matrix file must contain a 'roles' object")
    return import_matrix(data, **kwargs)


def matrix_for_group(group: Group) -> list[dict]:
    """Dump one subject's rows of the matrix (for audits / UIs)."""
    return [
        {
            "subject": group.name,
            "resource": entry.object_type.app_label + "." + entry.object_type.model,
            "action": entry.action,
            "allow": entry.allow,
        }
        for entry in group.acm_entries.all()
    ]


def full_matrix() -> list[dict]:
    return [
        {
            "subject": e.subject.name,
            "resource": f"{e.object_type.app_label}.{e.object_type.model}",
            "action": e.action,
            "allow": e.allow,
        }
        for e in AcmEntry.objects.select_related("subject", "object_type")
    ]