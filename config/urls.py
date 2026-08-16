from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("erp.urls")),
    path("", lambda request: redirect("/api/")),
]