"""Seed demo data for local development (no Keycloak required).

Creates a couple of OrgUnits and invoices so the row-scoping behaviour is
visible immediately. Idempotent.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from erp.models import Invoice, OrgUnit

User = get_user_model()


class Command(BaseCommand):
    help = "Create demo OrgUnits and invoices for local development"

    def handle(self, *args, **opts):
        pune, _ = OrgUnit.objects.get_or_create(name="Pune")
        mumbai, _ = OrgUnit.objects.get_or_create(name="Mumbai")
        admin, _ = User.objects.get_or_create(
            username="admin@erp.test", defaults={"is_staff": True, "is_superuser": True}
        )
        admin.set_password("admin")
        admin.save()

        for i, unit in enumerate((pune, mumbai), start=1):
            Invoice.objects.get_or_create(
                number=f"INV-00{i}",
                defaults={
                    "org_unit": unit,
                    "amount": f"{i}000.00",
                    "status": Invoice.Status.DRAFT,
                    "created_by": admin,
                },
            )
        self.stdout.write(
            self.style.SUCCESS("created org units, admin user (admin@erp.test / admin), invoices")
        )