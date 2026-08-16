from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AcmViewSet, InvoiceViewSet, OrgUnitViewSet

router = DefaultRouter()
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("org-units", OrgUnitViewSet, basename="orgunit")
router.register("acm", AcmViewSet, basename="acm")

app_name = "erp"

urlpatterns = [
    path("", include(router.urls)),
    # OIDC endpoints (login / callback / logout) served by mozilla_django_oidc.
    path("oidc/", include("mozilla_django_oidc.urls")),
]