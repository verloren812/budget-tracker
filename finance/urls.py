from django.urls import path

from . import views

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),

    path("reports/", views.ReportView.as_view(), name="reports"),

    path("import/", views.ImportUploadView.as_view(), name="import_upload"),
    path("import/mapping/", views.ImportMappingView.as_view(), name="import_mapping"),
    path("import/<int:pk>/", views.ImportBatchDetailView.as_view(), name="import_batch_detail"),

    path("rules/", views.CategoryRuleListView.as_view(), name="rule_list"),
    path("rules/new/", views.CategoryRuleCreateView.as_view(), name="rule_create"),
    path("rules/<int:pk>/edit/", views.CategoryRuleUpdateView.as_view(), name="rule_update"),
    path("rules/<int:pk>/delete/", views.CategoryRuleDeleteView.as_view(), name="rule_delete"),
    path("rules/apply/", views.ApplyRulesView.as_view(), name="rules_apply"),

    path("transactions/", views.TransactionListView.as_view(), name="transaction_list"),
    path("transactions/export/", views.TransactionExportView.as_view(), name="transaction_export"),
    path("transactions/new/", views.TransactionCreateView.as_view(), name="transaction_create"),
    path("transactions/<int:pk>/", views.TransactionDetailView.as_view(), name="transaction_detail"),
    path("transactions/<int:pk>/edit/", views.TransactionUpdateView.as_view(), name="transaction_update"),
    path("transactions/<int:pk>/delete/", views.TransactionDeleteView.as_view(), name="transaction_delete"),

    path("accounts/", views.AccountListView.as_view(), name="account_list"),
    path("accounts/new/", views.AccountCreateView.as_view(), name="account_create"),
    path("accounts/<int:pk>/", views.AccountDetailView.as_view(), name="account_detail"),
    path("accounts/<int:pk>/edit/", views.AccountUpdateView.as_view(), name="account_update"),
    path("accounts/<int:pk>/delete/", views.AccountDeleteView.as_view(), name="account_delete"),

    path("categories/", views.CategoryListView.as_view(), name="category_list"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_update"),
    path("categories/<int:pk>/delete/", views.CategoryDeleteView.as_view(), name="category_delete"),

    path("recurring/", views.RecurringListView.as_view(), name="recurring_list"),
    path("recurring/scan/", views.RecurringScanView.as_view(), name="recurring_scan"),
    path("recurring/<int:pk>/decision/", views.RecurringDecisionView.as_view(), name="recurring_decision"),

    path("budgets/", views.BudgetListView.as_view(), name="budget_list"),
    path("budgets/copy/", views.BudgetCopyView.as_view(), name="budget_copy"),
    path("budgets/new/", views.BudgetCreateView.as_view(), name="budget_create"),
    path("budgets/<int:pk>/edit/", views.BudgetUpdateView.as_view(), name="budget_update"),
    path("budgets/<int:pk>/delete/", views.BudgetDeleteView.as_view(), name="budget_delete"),
]
