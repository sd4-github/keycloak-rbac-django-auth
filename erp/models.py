from django.contrib.auth.models import Group
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class OrgUnit(models.Model):
    """A node in the org chart - a centre, a region, a wing.

    Rows that belong to the org (invoices, bookings, assets) carry an
    org_unit FK pointing here. This is the row-scope key used by the
    Layer-2 queryset filter (``WHERE org_unit_id IN (...)...``).
    """

    name = models.CharField(max_length=120, unique=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class GroupProfile(models.Model):
    """Thin metadata sidecar on Django's ``auth.Group``.

    A group is either a *department* (scopes rows to an OrgUnit), a
    *position* (a named bundle of permissions) or a *keycloak role*
    (synced from a Keycloak role/group). What the group can *do* lives
    in ``auth_group_permissions``; this table only records the group's
    kind, its row scope and its Keycloak source for traceability.
    """

    class Kind(models.TextChoices):
        DEPARTMENT = "department", "Department"
        POSITION = "position", "Position"
        KEYCLOAK_ROLE = "keycloak_role", "Keycloak role"

    group = models.OneToOneField(
        Group,
        on_delete=models.CASCADE,
        related_name="profile",
        primary_key=True,
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)

    # For a department group: the OrgUnit whose rows it scopes to.
    # Null for positions and Keycloak roles.
    scope = models.ForeignKey(
        OrgUnit,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="department_groups",
    )

    # Keycloak traceability: the realm/client role or Keycloak group name
    # this Django group was created from (informational only).
    keycloak_source = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "erp_group_profile"
        constraints = [
            # A department must name its scope; a position or KC role must not.
            models.CheckConstraint(
                name="department_requires_scope",
                check=(
                    models.Q(kind="department", scope__isnull=False)
                    | models.Q(kind="position", scope__isnull=True)
                    | models.Q(kind="keycloak_role", scope__isnull=True)
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.group.name}"


class AcmEntry(models.Model):
    """One cell of the Access Control Matrix.

    The ACM is the ``subject x object x action`` matrix that Keycloak's
    authorization services express for its clients. We mirror it in Django
    as data: each row says "group *subject* may *action* on objects of type
    *object_type*". Materialising a row writes (or removes) the matching
    ``auth_group_permissions`` row, so the exact same lookup that powers
    ``user.has_perm(...)`` enforces the Keycloak ACM.
    """

    class Action(models.TextChoices):
        VIEW = "view", "View"
        ADD = "add", "Add"
        CHANGE = "change", "Change"
        DELETE = "delete", "Delete"

    # Action prefix -> Django permission codename action.
    CODENAME_ACTION = {
        Action.VIEW: "view",
        Action.ADD: "add",
        Action.CHANGE: "change",
        Action.DELETE: "delete",
    }

    # ---- subject ---------------------------------------------------
    subject = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="acm_entries",
        help_text="Django group (position or Keycloak-mapped role).",
    )

    # ---- object ----------------------------------------------------
    object_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="acm_entries",
        help_text="The resource kind: an app model (e.g. erp.invoice).",
    )
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    object = GenericForeignKey("object_type", "object_id")
    # object-level (row) entries are optional; model-level when object_id is null.

    # ---- action ----------------------------------------------------
    action = models.CharField(max_length=16, choices=Action.choices)
    allow = models.BooleanField(
        default=True,
        help_text="True grants the permission; False explicitly revokes it.",
    )

    # bookkeeping
    keycloak_scope = models.CharField(
        max_length=64, blank=True, default="",
        help_text="The Keycloak client scope this entry mirrors, if any.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "erp_acm_entry"
        unique_together = ("subject", "object_type", "object_id", "action")
        ordering = ["subject", "object_type", "action"]
        permissions = [
            ("view_acm", "Can view the access control matrix"),
        ]

    def __str__(self) -> str:
        verb = "may" if self.allow else "may NOT"
        target = self.object or self.object_type.model
        return f"{self.subject} {verb} {self.action} {target}"

    def permission_codename(self) -> str:
        prefix = self.CODENAME_ACTION[self.action]
        return f"{prefix}_{self.object_type.model}"

    # -- materialise ---------------------------------------------------
    def apply(self) -> None:
        """Write this matrix cell into ``auth_group_permissions``."""
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(
            content_type=self.object_type,
            codename=self.permission_codename(),
        )
        if self.allow:
            self.subject.permissions.add(perm)
        else:
            self.subject.permissions.remove(perm)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.apply()


class Invoice(models.Model):
    """Demo row-scoped resource. Rows belong to exactly one OrgUnit."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        PAID = "paid", "Paid"

    number = models.CharField(max_length=32, unique=True)
    org_unit = models.ForeignKey(
        OrgUnit, on_delete=models.PROTECT, related_name="invoices"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        "auth.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_invoices",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("view_all_invoices", "Can view invoices across all org units"),
        ]

    def __str__(self) -> str:
        return self.number