from django.contrib import admin
from django.contrib.admin import ModelAdmin
from django.contrib.auth.admin import GroupAdmin
from django.contrib.auth.models import Group
from django.contrib.contenttypes.admin import GenericStackedInline
from django.contrib.contenttypes.models import ContentType

from .models import AcmEntry, GroupProfile, Invoice, OrgUnit


class GroupProfileInline(admin.StackedInline):
    model = GroupProfile
    can_delete = False


class AcmEntryInline(admin.TabularInline):
    model = AcmEntry
    extra = 0


class KeycloakGroupAdmin(GroupAdmin):
    """auth.Group with its profile + ACM matrix editable from one screen.

    This is the "permissions as data" surface: an administrator creates a
    position, ticks its permissions, and the running app honours it on the
    next request - no code change, no deploy.
    """

    inlines = [GroupProfileInline, AcmEntryInline]
    list_display = ("name", "kind", "scope")
    list_select_related = ("profile", "profile__scope")

    @admin.display(description="kind")
    def kind(self, obj):
        return getattr(obj.profile, "kind", "-")

    @admin.display(description="scope")
    def scope(self, obj):
        return getattr(obj.profile, "scope", "-")


admin.site.unregister(Group)
admin.site.register(Group, KeycloakGroupAdmin)


@admin.register(OrgUnit)
class OrgUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "parent")
    search_fields = ("name",)


@admin.register(AcmEntry)
class AcmEntryAdmin(admin.ModelAdmin):
    list_display = ("subject", "object_type", "object_id", "action", "allow")
    list_filter = ("action", "allow", "object_type")
    autocomplete_fields = ["subject"]
    search_fields = ("subject__name", "object_type__model")


class ContentTypeAdmin(ModelAdmin):
    list_display = ("app_label", "model", "id")
    search_fields = ("app_label", "model")


admin.site.register(ContentType, ContentTypeAdmin)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "org_unit", "amount", "status", "created_by")
    list_filter = ("status", "org_unit")
    search_fields = ("number",)
    list_select_related = ("org_unit", "created_by")