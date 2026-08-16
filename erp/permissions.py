"""DRF permission classes - Layer 1 of the two-layer enforcement.

Layer 1 gates *whether* a request reaches the view body by mapping the HTTP
method to a Django permission codename and calling ``user.has_perm``. Layer 2
(row scoping) lives in each viewset's ``get_queryset`` as a SQL ``WHERE`` on
the department scope.

Both layers read the *same* ``auth_group_permissions`` rows that the ACM
import writes, so an admin editing the Keycloak ACM changes what every
endpoint allows with zero deploys.
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission, DjangoModelPermissions, SAFE_METHODS


class DepartmentScopedModelPermissions(DjangoModelPermissions):
    """Layer-1 gate + a per-object backstop, both read from group perms.

    ``DjangoModelPermissions`` ships GET unguarded (its default map only
    guards writes) and has no object-level hook. We close both gaps:

      * reads are gated on ``view_<model>``
      * ``has_object_permission`` guarantees row isolation even if a view
        forgets to scope its queryset.
    """

    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": [],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }

    def has_object_permission(self, request, view, obj) -> bool:
        """Detail-route backstop.

        A scoped ``get_queryset`` would already 404 a foreign pk, but this
        guarantees row isolation even if a view forgets to scope. Users
        carrying an org-wide ``view_all_<model>s`` permission bypass the
        department check.
        """
        user = request.user
        meta = obj._meta

        if user.has_perm(f"{meta.app_label}.view_all_{meta.model_name}s"):
            return True

        dept_ids = self._user_department_ids(user)
        return dept_ids is None or self._object_department_id(obj) in dept_ids

    # -- helpers --------------------------------------------------------

    def _user_department_ids(self, user):
        from .models import GroupProfile

        return set(
            user.groups.filter(profile__kind=GroupProfile.Kind.DEPARTMENT)
            .values_list("profile__scope_id", flat=True)
        )

    def _object_department_id(self, obj):
        # Objects carry either ``org_unit`` or ``department``; fall back to
        # a conventional ``department_id`` for models that rename it.
        for attr in ("org_unit_id", "department_id"):
            if hasattr(obj, attr):
                return getattr(obj, attr)
        return None


class ReadOnly(DepartmentScopedModelPermissions):
    """Layer-1 gate for read-only resources (view perm only)."""

    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": [],
        "POST": [],
        "PUT": [],
        "PATCH": [],
        "DELETE": [],
    }

    def has_permission(self, request, view) -> bool:
        if request.method not in SAFE_METHODS:
            return False
        return super().has_permission(request, view)


class AcmAuditPermission(BasePermission):
    """Gate the ACM audit window on the ``erp.view_acm`` grant (org-wide).

    The matrix reveals every role's access, so only subjects whose own
    grant set includes ``view_acm`` may read it.
    """

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user and user.is_authenticated and user.has_perm("erp.view_acm")
        )