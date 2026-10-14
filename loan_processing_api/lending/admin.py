"""Admin is view-only for money and state: every change has to go through the API
(services.py), so lending rules, row locks and the audit trail always apply."""

from django.contrib import admin

from .models import CreditLine, Holding, LedgerEntry, Pledge


class ReadOnlyAdminMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PledgeInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = Pledge
    extra = 0
    fields = ("holding", "units", "nav_at_pledge", "ltv", "eligible_value", "status", "lien_reference", "released_at")


class LedgerInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = LedgerEntry
    extra = 0
    fields = (
        "value_date",
        "entry_type",
        "amount",
        "principal_component",
        "interest_component",
        "balance_after",
        "reference",
    )


@admin.register(CreditLine)
class CreditLineAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "borrower",
        "partner",
        "status",
        "sanctioned_limit",
        "outstanding_principal",
        "interest_due",
        "bureau_score",
        "created_at",
    )
    list_filter = ("status", "partner")
    search_fields = ("borrower__username",)
    inlines = [PledgeInline, LedgerInline]


@admin.register(Holding)
class HoldingAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("borrower", "rta", "scheme_name", "category", "units", "pledged_units", "nav")
    list_filter = ("rta", "category")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "credit_line", "entry_type", "amount", "balance_after", "value_date", "reference")
    list_filter = ("entry_type",)
