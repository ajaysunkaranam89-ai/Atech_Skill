from django.contrib import admin

from .models import AuditLog, SiteSettings


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor", "action", "object_type", "object_id", "object_repr"]
    list_filter = ["action", "object_type"]
    search_fields = ["object_repr", "actor__email"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(SiteSettings)
