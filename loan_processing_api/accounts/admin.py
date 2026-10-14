from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class LamfUserAdmin(UserAdmin):
    list_display = ("username", "role", "pan_masked", "referred_via", "is_staff", "date_joined")
    list_filter = ("role", "is_staff")
    readonly_fields = ("pan_masked",)
    fieldsets = UserAdmin.fieldsets + (("Lending", {"fields": ("role", "phone", "pan_masked", "referred_via")}),)
