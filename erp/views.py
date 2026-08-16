"""Views - Layer 2 (row scoping in SQL) lives here, per viewset.

Neither layer contains a role name. The code only knows permissions
(``view_all_invoices``) and a scope key (``org_unit_id``). Which Keycloak
principals hold which of those is data, editable via the ACM.
"""
from django.contrib.auth.models import Group
from rest_framework import filters, viewsets

from . import acm
from .models import AcmEntry, GroupProfile, Invoice, OrgUnit
from .permissions import AcmAuditPermission, DepartmentScopedModelPermissions
from .serializers import InvoiceSerializer, OrgUnitSerializer


class InvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = InvoiceSerializer
    # Layer 1 - the model-level gate, plus the per-object backstop.
    permission_classes = [DepartmentScopedModelPermissions]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["amount", "created_at", "number"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = Invoice.objects.select_related("org_unit")
        user = self.request.user

        # Org-wide roles carry a group with this permission - see the ACM.
        if user.has_perm("erp.view_all_invoices"):
            return qs

        # Layer 2 - row scoping in SQL. You passed the gate, but you only
        # see the invoices of the OrgUnits your department groups scope to.
        # The ids come from the user's groups; the filter runs on an index.
        dept_ids = user.groups.filter(
            profile__kind=GroupProfile.Kind.DEPARTMENT
        ).values_list("profile__scope_id", flat=True)
        return qs.filter(org_unit_id__in=list(dept_ids))

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class OrgUnitViewSet(viewsets.ModelViewSet):
    queryset = OrgUnit.objects.all()
    serializer_class = OrgUnitSerializer
    permission_classes = [DepartmentScopedModelPermissions]


class AcmViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only window onto the Access Control Matrix.

    Exposes the *django* mirror of the Keycloak ACM: every
    ``subject x resource x action`` cell, plus the materialised Django
    permission each cell granted. Useful for audits and admin UIs.
    """

    queryset = (
        AcmEntry.objects.select_related("subject", "object_type")
        .all()
        .prefetch_related("subject__permissions")
    )
    serializer_class = None  # custom response in list()
    permission_classes = [AcmAuditPermission]

    def list(self, request, *args, **kwargs):
        from rest_framework.response import Response

        return Response({"acm": acm.full_matrix()})

    def retrieve(self, request, *args, **kwargs):
        group = Group.objects.get(pk=kwargs["pk"])
        return Response({"subject": group.name, "entries": acm.matrix_for_group(group)})