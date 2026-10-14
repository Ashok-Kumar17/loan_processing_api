from django.contrib import admin

from .models import CommissionAccrual, Partner, ReferralLink


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ("firm_name", "kind", "registration_code", "user", "created_at")
    list_filter = ("kind",)


@admin.register(ReferralLink)
class ReferralLinkAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "partner", "is_active", "created_at")
    list_filter = ("is_active",)


@admin.register(CommissionAccrual)
class CommissionAccrualAdmin(admin.ModelAdmin):
    list_display = ("accrual_date", "partner", "credit_line", "principal_base", "amount")
    list_filter = ("partner",)
    date_hierarchy = "accrual_date"
