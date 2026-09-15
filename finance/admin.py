from django.contrib import admin

from .models import (
    Account,
    Budget,
    Category,
    CategoryRule,
    ImportBatch,
    RecurringPayment,
    Transaction,
)


class OwnedAdminMixin:
    """In the admin a user only sees their own objects, superusers see everything."""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(user=request.user)

    def save_model(self, request, obj, form, change):
        if not change and not obj.user_id:
            obj.user = request.user
        super().save_model(request, obj, form, change)


@admin.register(Account)
class AccountAdmin(OwnedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "user", "kind", "initial_balance", "balance", "is_archived")
    list_filter = ("kind", "is_archived")
    search_fields = ("name",)

    @admin.display(description="current balance")
    def balance(self, obj):
        return obj.balance


@admin.register(Category)
class CategoryAdmin(OwnedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "kind", "parent", "user")
    list_filter = ("kind",)
    search_fields = ("name",)


@admin.register(Transaction)
class TransactionAdmin(OwnedAdminMixin, admin.ModelAdmin):
    list_display = ("date", "kind", "amount", "account", "category", "counterparty", "source")
    list_filter = ("kind", "source", "account", "date")
    search_fields = ("counterparty", "note")
    date_hierarchy = "date"
    autocomplete_fields = ("account", "category", "transfer_to")
    list_select_related = ("account", "category")


@admin.register(Budget)
class BudgetAdmin(OwnedAdminMixin, admin.ModelAdmin):
    list_display = ("category", "month", "limit", "spent", "progress")
    list_filter = ("month",)

    @admin.display(description="spent")
    def spent(self, obj):
        return obj.spent

    @admin.display(description="% of limit")
    def progress(self, obj):
        return f"{obj.progress}%"


@admin.register(ImportBatch)
class ImportBatchAdmin(OwnedAdminMixin, admin.ModelAdmin):
    list_display = ("file_name", "account", "uploaded_at", "rows_total", "rows_imported", "rows_skipped")
    readonly_fields = ("uploaded_at",)


@admin.register(CategoryRule)
class CategoryRuleAdmin(OwnedAdminMixin, admin.ModelAdmin):
    list_display = ("pattern", "match_field", "match_type", "category", "priority", "is_active")
    list_filter = ("match_field", "match_type", "is_active")
    search_fields = ("pattern",)


@admin.register(RecurringPayment)
class RecurringPaymentAdmin(OwnedAdminMixin, admin.ModelAdmin):
    list_display = ("counterparty", "avg_amount", "day_of_month", "occurrences", "is_confirmed")
    list_filter = ("is_confirmed", "is_dismissed")
    search_fields = ("counterparty",)
