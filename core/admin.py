from django.contrib import admin

from .models import AuditEvent, IdempotencyKey


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "entity", "entity_id", "action", "actor")
    list_filter = ("entity", "action")
    search_fields = ("entity_id",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # append-only

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "scope", "key", "response_code")
    list_filter = ("scope",)
