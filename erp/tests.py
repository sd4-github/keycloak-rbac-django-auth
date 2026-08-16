"""End-to-end tests for the Keycloak RBAC/ACM <-> Django auth mapping.

Covers the two layers of enforcement and the ACM import path without
needing a live Keycloak: the OIDC backend is swapped for ``ModelBackend``
and groups are provisioned directly through the same helpers the Keycloak
sync uses.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from . import acm
from .keycloak import sync_user_from_claims
from .models import AcmEntry, GroupProfile, Invoice, OrgUnit

User = get_user_model()

MATRIX = {
    "client": "erp-api",
    "roles": {
        "erp-admin": {
            "grants": ["erp.view_all_invoices", "erp.view_acm"],
            "invoice": ["view", "add", "change", "delete"],
            "orgunit": ["view"],
        },
        "pune-coordinator": {
            "invoice": ["view", "change"],
        },
    },
}


class AcmImportTests(TestCase):
    def test_import_materialises_group_permissions(self):
        acm.import_matrix(MATRIX)

        admin_group = Group.objects.get(name="erp-admin")
        self.assertEqual(admin_group.profile.kind, GroupProfile.Kind.KEYCLOAK_ROLE)
        self.assertTrue(admin_group.permissions.filter(codename="view_invoice").exists())
        self.assertTrue(admin_group.permissions.filter(codename="delete_invoice").exists())

        # unqualified: coordinator gets no orgunit access at all
        coord = Group.objects.get(name="pune-coordinator")
        self.assertFalse(coord.permissions.filter(codename="view_orgunit").exists())

    def test_reimport_replaces_matrix(self):
        acm.import_matrix(MATRIX)
        revised = {
            "roles": {
                "erp-admin": {"invoice": ["view"]},
            }
        }
        acm.import_matrix(revised)
        admin_group = Group.objects.get(name="erp-admin")
        self.assertTrue(admin_group.permissions.filter(codename="view_invoice").exists())
        self.assertFalse(admin_group.permissions.filter(codename="delete_invoice").exists())

    def test_unknown_action_rejected(self):
        with self.assertRaises(acm.AcmImportError):
            acm.import_matrix({"roles": {"x": {"invoice": ["view", "shred"]}}})


class ScopeAndEnforcementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pune = OrgUnit.objects.create(name="Pune")
        cls.mumbai = OrgUnit.objects.create(name="Mumbai")

        cls.invoice_pune = Invoice.objects.create(
            number="INV-001", org_unit=cls.pune, amount="1000.00", status=Invoice.Status.DRAFT
        )
        cls.invoice_mumbai = Invoice.objects.create(
            number="INV-002", org_unit=cls.mumbai, amount="2000.00", status=Invoice.Status.DRAFT
        )

        # Layer-1 and Layer-2 both come from the ACM import.
        acm.import_matrix(MATRIX)

        cls.admin = User.objects.create_user("admin@erp.test", password="pw")
        cls.coord = User.objects.create_user("priya@erp.test", password="pw")

        # Simulate Keycloak claims: admin has the org-wide role, the
        # coordinator has the role + a department group scoped to Pune.
        sync_user_from_claims(cls.admin, {"realm_access": {"roles": ["erp-admin"]}})

        dept = Group.objects.create(name="Pune department")
        GroupProfile.objects.create(group=dept, kind=GroupProfile.Kind.DEPARTMENT, scope=cls.pune)
        sync_user_from_claims(cls.coord, {"realm_access": {"roles": ["pune-coordinator"]}})
        cls.coord.groups.add(dept)
        cls.coord.__dict__.pop("_perm_cache", None)

        cls.admin_client = APIClient()
        cls.admin_client.force_authenticate(cls.admin)
        cls.coord_client = APIClient()
        cls.coord_client.force_authenticate(cls.coord)

    def test_admin_sees_all_rows(self):
        resp = self.admin_client.get(reverse("erp:invoice-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 2)

    def test_coordinator_is_row_scoped_to_pune(self):
        resp = self.coord_client.get(reverse("erp:invoice-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["number"], "INV-001")

    def test_coordinator_cannot_reach_foreign_row(self):
        resp = self.coord_client.get(
            reverse("erp:invoice-detail", kwargs={"pk": self.invoice_mumbai.pk})
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_coordinator_cannot_delete(self):
        resp = self.coord_client.delete(
            reverse("erp:invoice-detail", kwargs={"pk": self.invoice_pune.pk})
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_with_no_role_is_denied(self):
        stranger = User.objects.create_user("stranger@erp.test", password="pw")
        client = APIClient()
        client.force_authenticate(stranger)
        resp = client.get(reverse("erp:invoice-list"))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_has_perm_walks_group_join(self):
        self.assertTrue(self.coord.has_perm("erp.view_invoice"))
        self.assertTrue(self.coord.has_perm("erp.change_invoice"))
        self.assertFalse(self.coord.has_perm("erp.delete_invoice"))

    def test_acm_audit_endpoint(self):
        resp = self.admin_client.get(reverse("erp:acm-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        cells = {(e["subject"], e["resource"], e["action"]) for e in resp.data["acm"]}
        self.assertIn(("erp-admin", "erp.invoice", "delete"), cells)


class AcmEntryModelTests(TestCase):
    def test_deny_entry_removes_permission(self):
        group = Group.objects.create(name="sensitive")
        invoice_ct = ContentType.objects.get_for_model(Invoice)
        AcmEntry.objects.create(
            subject=group, object_type=invoice_ct, action=AcmEntry.Action.VIEW, allow=True
        )
        self.assertTrue(group.permissions.filter(codename="view_invoice").exists())
        AcmEntry.objects.create(
            subject=group, object_type=invoice_ct, action=AcmEntry.Action.VIEW, allow=False
        )
        self.assertFalse(group.permissions.filter(codename="view_invoice").exists())