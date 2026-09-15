import csv
import uuid
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, ProtectedError, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from . import importer, recurring, reports
from .forms import (
    AccountForm,
    BudgetForm,
    CategoryForm,
    CategoryRuleForm,
    ImportMappingForm,
    ImportUploadForm,
    ReportPeriodForm,
    TransactionFilterForm,
    TransactionForm,
)
from .models import (
    ZERO,
    Account,
    Budget,
    Category,
    CategoryRule,
    ImportBatch,
    RecurringPayment,
    Transaction,
)


class OwnedMixin(LoginRequiredMixin):
    """Every queryset is filtered by owner: another user's object is unreachable even by URL."""

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


class OwnedFormMixin(OwnedMixin):
    """Pass the user into the form and show a message once it is saved."""

    success_message = "Saved."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, self.success_message)
        return response


class ProtectedDeleteMixin:
    """Refuse to delete an object that transactions still reference, and explain why."""

    protected_message = "Cannot delete: transactions still reference this object."

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, self.protected_message)
            return redirect(self.get_success_url())


# ---------------------------------------------------------------- dashboard


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "finance/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        today = date.today()

        accounts = list(Account.objects.filter(user=user, is_archived=False))
        totals = (
            Transaction.objects.for_user(user)
            .in_month(today.year, today.month)
            .aggregate(
                income=Coalesce(Sum("amount", filter=Q(kind=Transaction.Kind.INCOME)), ZERO),
                expense=Coalesce(Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE)), ZERO),
            )
        )

        ctx.update(
            accounts=accounts,
            total_balance=sum((a.balance for a in accounts), ZERO),
            month_income=totals["income"],
            month_expense=totals["expense"],
            month_result=totals["income"] - totals["expense"],
            last_transactions=Transaction.objects.for_user(user).with_related()[:10],
            budgets=Budget.objects.filter(
                user=user, month__year=today.year, month__month=today.month
            ).select_related("category"),
            forecast=recurring.forecast(user, today),
            today=today,
        )
        return ctx


# ---------------------------------------------------------------- transactions


class TransactionListView(OwnedMixin, ListView):
    model = Transaction
    template_name = "finance/transaction_list.html"
    context_object_name = "transactions"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().with_related()
        self.filter_form = TransactionFilterForm(self.request.GET or None, user=self.request.user)
        return self.filter_form.filter(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Totals are computed over the filtered queryset, not over the current page.
        totals = self.object_list.aggregate(
            income=Coalesce(Sum("amount", filter=Q(kind=Transaction.Kind.INCOME)), ZERO),
            expense=Coalesce(Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE)), ZERO),
        )
        ctx.update(
            filter_form=self.filter_form,
            total_income=totals["income"],
            total_expense=totals["expense"],
            total_result=totals["income"] - totals["expense"],
            found=self.object_list.count(),
        )
        return ctx


class TransactionDetailView(OwnedMixin, DetailView):
    model = Transaction
    template_name = "finance/transaction_detail.html"
    context_object_name = "transaction"

    def get_queryset(self):
        return super().get_queryset().with_related()


class TransactionCreateView(OwnedFormMixin, CreateView):
    model = Transaction
    form_class = TransactionForm
    template_name = "finance/transaction_form.html"
    success_url = reverse_lazy("transaction_list")
    success_message = "Transaction added."

    def get_queryset(self):
        return Transaction.objects.all()

    def get_initial(self):
        return {"date": date.today(), "kind": self.request.GET.get("kind", Transaction.Kind.EXPENSE)}


class TransactionUpdateView(OwnedFormMixin, UpdateView):
    model = Transaction
    form_class = TransactionForm
    template_name = "finance/transaction_form.html"
    success_url = reverse_lazy("transaction_list")
    success_message = "Transaction updated."


class TransactionDeleteView(OwnedMixin, DeleteView):
    model = Transaction
    template_name = "finance/confirm_delete.html"
    success_url = reverse_lazy("transaction_list")

    def form_valid(self, form):
        messages.success(self.request, "Transaction deleted.")
        return super().form_valid(form)


class TransactionExportView(LoginRequiredMixin, ListView):
    """Export the filtered transaction list to CSV.

    A ";" delimiter and a decimal comma make the file open directly in Excel
    with a German locale, without the import wizard. The BOM tells Excel the
    file is UTF-8.
    """

    model = Transaction

    def get_queryset(self):
        qs = Transaction.objects.for_user(self.request.user).with_related()
        self.filter_form = TransactionFilterForm(self.request.GET or None, user=self.request.user)
        return self.filter_form.filter(qs)

    def render_to_response(self, context, **kwargs):
        # The charset stays utf-8: the BOM is written by hand one line below,
        # otherwise Django would prepend it to every written chunk.
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="transactions-{date.today():%Y-%m-%d}.csv"'
        )
        response.write("﻿")  # BOM for Excel

        writer = csv.writer(response, delimiter=";", lineterminator="\r\n")
        writer.writerow(
            ["Date", "Type", "Amount", "Currency", "Account", "Destination account",
             "Category", "Counterparty", "Note", "Source"]
        )
        for tx in self.object_list:
            writer.writerow([
                tx.date.strftime("%d.%m.%Y"),
                tx.get_kind_display(),
                f"{tx.amount:.2f}".replace(".", ","),
                tx.account.currency,
                tx.account.name,
                tx.transfer_to.name if tx.transfer_to else "",
                tx.category.name if tx.category else "",
                tx.counterparty,
                tx.note.replace("\n", " "),
                tx.get_source_display(),
            ])
        return response


# ---------------------------------------------------------------- statement import


IMPORT_SESSION_KEY = "import_state"
PREVIEW_LIMIT = 15


class ImportUploadView(LoginRequiredMixin, FormView):
    """Step 1: file upload. The file goes to a temporary folder, the session only keeps its path."""

    template_name = "finance/import_upload.html"
    form_class = ImportUploadForm
    success_url = reverse_lazy("import_mapping")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["batches"] = ImportBatch.objects.filter(user=self.request.user).select_related(
            "account"
        )[:10]
        return ctx

    def form_valid(self, form):
        uploaded = form.cleaned_data["file"]
        raw = uploaded.read()

        try:
            importer.parse_file(raw)  # fail fast instead of carrying a broken file to step 2
        except importer.ImportError_ as exc:
            form.add_error("file", str(exc))
            return self.form_invalid(form)

        target_dir = Path(settings.MEDIA_ROOT) / "imports"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{uuid.uuid4().hex}.csv"
        path.write_bytes(raw)

        self.request.session[IMPORT_SESSION_KEY] = {
            "path": str(path),
            "name": uploaded.name,
            "account": form.cleaned_data["account"].pk,
        }
        return super().form_valid(form)


class ImportMappingView(LoginRequiredMixin, TemplateView):
    """Steps 2 and 3: column mapping with a preview, then the actual write."""

    template_name = "finance/import_mapping.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        state = request.session.get(IMPORT_SESSION_KEY)
        if not state:
            messages.error(request, "Upload a statement file first.")
            return redirect("import_upload")

        self.state = state
        self.account = get_object_or_404(Account, pk=state["account"], user=request.user)
        try:
            self.parsed = importer.parse_file(Path(state["path"]).read_bytes())
        except (OSError, importer.ImportError_) as exc:
            request.session.pop(IMPORT_SESSION_KEY, None)
            messages.error(request, f"The file could not be read: {exc}")
            return redirect("import_upload")
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, data=None):
        if data is None:
            guessed = importer.guess_columns(self.parsed.header)
            initial = {key: value for key, value in guessed.items() if value is not None}
            initial.setdefault("date", 0)
            initial.setdefault("amount", 1)
            initial["sign_mode"] = "signed"
            return ImportMappingForm(columns=self.parsed.columns, initial=initial)
        return ImportMappingForm(data, columns=self.parsed.columns)

    def get(self, request, *args, **kwargs):
        form = self.get_form()
        preview = None
        if form.initial.get("date") is not None and form.initial.get("amount") is not None:
            preview = self.build_preview(importer.Mapping.from_dict(form.initial))
        return self.render_to_response(self.get_context_data(form=form, preview=preview))

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, preview=None))

        mapping = importer.Mapping.from_dict(form.cleaned_data)

        if "commit" not in request.POST:
            preview = self.build_preview(mapping)
            return self.render_to_response(self.get_context_data(form=form, preview=preview))

        batch, rows = importer.run_import(
            request.user, self.account, self.parsed, mapping, self.state["name"]
        )
        Path(self.state["path"]).unlink(missing_ok=True)
        request.session.pop(IMPORT_SESSION_KEY, None)

        broken = sum(1 for row in rows if not row.is_valid)
        messages.success(
            request,
            f"Imported transactions: {batch.rows_imported}. "
            f"Skipped duplicates: {batch.rows_skipped}. "
            f"Rows with errors: {broken}.",
        )
        return redirect("import_batch_detail", pk=batch.pk)

    def build_preview(self, mapping):
        rules = CategoryRule.objects.filter(
            user=self.request.user, is_active=True
        ).select_related("category")
        return importer.build_preview(
            self.request.user, self.account, self.parsed, mapping,
            rules=rules, limit=PREVIEW_LIMIT,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            account=self.account,
            file_name=self.state["name"],
            parsed=self.parsed,
            rows_total=len(self.parsed.rows),
            preview_limit=PREVIEW_LIMIT,
        )
        return ctx


class ImportBatchDetailView(OwnedMixin, DetailView):
    model = ImportBatch
    template_name = "finance/import_batch_detail.html"
    context_object_name = "batch"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["transactions"] = self.object.transactions.select_related("category", "account")[:100]
        return ctx


# ---------------------------------------------------------------- category rules


class CategoryRuleListView(OwnedMixin, ListView):
    model = CategoryRule
    template_name = "finance/rule_list.html"
    context_object_name = "rules"

    def get_queryset(self):
        return super().get_queryset().select_related("category")


class CategoryRuleCreateView(OwnedFormMixin, CreateView):
    model = CategoryRule
    form_class = CategoryRuleForm
    template_name = "finance/rule_form.html"
    success_url = reverse_lazy("rule_list")
    success_message = "Rule created."

    def get_queryset(self):
        return CategoryRule.objects.all()


class CategoryRuleUpdateView(OwnedFormMixin, UpdateView):
    model = CategoryRule
    form_class = CategoryRuleForm
    template_name = "finance/rule_form.html"
    success_url = reverse_lazy("rule_list")
    success_message = "Rule updated."


class CategoryRuleDeleteView(OwnedMixin, DeleteView):
    model = CategoryRule
    template_name = "finance/confirm_delete.html"
    success_url = reverse_lazy("rule_list")


class ApplyRulesView(LoginRequiredMixin, View):
    """Run the rules over every transaction that has no category yet."""

    def post(self, request, *args, **kwargs):
        rules = list(
            CategoryRule.objects.filter(user=request.user, is_active=True).select_related("category")
        )
        updated = 0
        pending = Transaction.objects.for_user(request.user).filter(category__isnull=True)
        for tx in pending.exclude(kind=Transaction.Kind.TRANSFER):
            category = importer.match_category(rules, tx.counterparty, tx.note)
            if category is not None and category.kind == tx.kind:
                tx.category = category
                tx.save(update_fields=["category"])
                updated += 1
        messages.success(request, f"A category was assigned to {updated} transactions.")
        return redirect(request.POST.get("next") or "rule_list")


# ---------------------------------------------------------------- recurring payments


class RecurringListView(OwnedMixin, ListView):
    model = RecurringPayment
    template_name = "finance/recurring_list.html"
    context_object_name = "payments"

    def get_queryset(self):
        return super().get_queryset().select_related("category")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["forecast"] = recurring.forecast(self.request.user)
        return ctx


class RecurringScanView(LoginRequiredMixin, View):
    """Scan the transaction history for recurring payments."""

    def post(self, request, *args, **kwargs):
        result = recurring.sync(request.user)
        messages.success(
            request,
            f"Recurring payments found: {result['found']} "
            f"(new: {result['created']}, updated: {result['updated']}).",
        )
        return redirect("recurring_list")


class RecurringDecisionView(OwnedMixin, View):
    """Confirm or dismiss a detected payment."""

    def post(self, request, pk, *args, **kwargs):
        payment = get_object_or_404(RecurringPayment, pk=pk, user=request.user)
        decision = request.POST.get("decision")
        if decision == "confirm":
            payment.is_confirmed, payment.is_dismissed = True, False
            messages.success(request, f"“{payment.counterparty}” confirmed.")
        else:
            payment.is_confirmed, payment.is_dismissed = False, True
            messages.success(request, f"“{payment.counterparty}” is no longer treated as recurring.")
        payment.save(update_fields=["is_confirmed", "is_dismissed"])
        return redirect("recurring_list")

    def get_queryset(self):
        return RecurringPayment.objects.filter(user=self.request.user)


class BudgetCopyView(LoginRequiredMixin, View):
    """Copy this month's budgets to the next month, leaving existing ones untouched."""

    def post(self, request, *args, **kwargs):
        today = date.today()
        this_month = today.replace(day=1)
        next_month = (
            date(this_month.year + 1, 1, 1)
            if this_month.month == 12
            else date(this_month.year, this_month.month + 1, 1)
        )

        existing = set(
            Budget.objects.filter(user=request.user, month=next_month).values_list(
                "category_id", flat=True
            )
        )
        new_budgets = [
            Budget(
                user=request.user, category=budget.category, month=next_month, limit=budget.limit
            )
            for budget in Budget.objects.filter(user=request.user, month=this_month)
            if budget.category_id not in existing
        ]
        Budget.objects.bulk_create(new_budgets)

        if new_budgets:
            messages.success(
                request, f"Budgets copied to {next_month:%m.%Y}: {len(new_budgets)}."
            )
        else:
            messages.info(request, "Nothing to copy: next month already has its budgets.")
        return redirect("budget_list")


# ---------------------------------------------------------------- reports


class ReportView(LoginRequiredMixin, TemplateView):
    template_name = "finance/reports.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        form = ReportPeriodForm(self.request.GET or None)
        period = reports.default_period()
        if form.is_valid():
            period = reports.Period(
                start=form.cleaned_data.get("date_from") or period.start,
                end=form.cleaned_data.get("date_to") or period.end,
            )

        categories = reports.by_category(user, period)
        months = reports.by_month(user, period)

        ctx.update(
            period_form=form,
            period=period,
            totals=reports.totals(user, period),
            categories=categories,
            months=months,
            counterparties=reports.top_counterparties(user, period),
            # Chart.js data reaches the template through json_script.
            chart_categories={
                "labels": [row["category__name"] for row in categories],
                "values": [float(row["total"]) for row in categories],
                "colors": [row["color"] for row in categories],
            },
            chart_months={
                "labels": [row["month"].strftime("%m.%Y") for row in months],
                "income": [float(row["income"]) for row in months],
                "expense": [float(row["expense"]) for row in months],
            },
        )
        return ctx


# ---------------------------------------------------------------- accounts


class AccountListView(OwnedMixin, ListView):
    model = Account
    template_name = "finance/account_list.html"
    context_object_name = "accounts"


class AccountDetailView(OwnedMixin, DetailView):
    model = Account
    template_name = "finance/account_detail.html"
    context_object_name = "account"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["transactions"] = (
            Transaction.objects.for_user(self.request.user)
            .filter(Q(account=self.object) | Q(transfer_to=self.object))
            .with_related()[:50]
        )
        return ctx


class AccountCreateView(OwnedFormMixin, CreateView):
    model = Account
    form_class = AccountForm
    template_name = "finance/account_form.html"
    success_url = reverse_lazy("account_list")
    success_message = "Account created."

    def get_queryset(self):
        return Account.objects.all()


class AccountUpdateView(OwnedFormMixin, UpdateView):
    model = Account
    form_class = AccountForm
    template_name = "finance/account_form.html"
    success_url = reverse_lazy("account_list")
    success_message = "Account updated."


class AccountDeleteView(ProtectedDeleteMixin, OwnedMixin, DeleteView):
    model = Account
    template_name = "finance/confirm_delete.html"
    success_url = reverse_lazy("account_list")
    protected_message = "This account has transactions and cannot be deleted. Archive it instead."


# ---------------------------------------------------------------- categories


class CategoryListView(OwnedMixin, ListView):
    model = Category
    template_name = "finance/category_list.html"
    context_object_name = "categories"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("parent")
            .annotate(tx_count=Count("transactions"))
        )


class CategoryCreateView(OwnedFormMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = "finance/category_form.html"
    success_url = reverse_lazy("category_list")
    success_message = "Category created."

    def get_queryset(self):
        return Category.objects.all()


class CategoryUpdateView(OwnedFormMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "finance/category_form.html"
    success_url = reverse_lazy("category_list")
    success_message = "Category updated."


class CategoryDeleteView(OwnedMixin, DeleteView):
    model = Category
    template_name = "finance/confirm_delete.html"
    success_url = reverse_lazy("category_list")

    def form_valid(self, form):
        # Transactions use SET_NULL for category, so they simply become uncategorized.
        messages.success(self.request, "Category deleted, its transactions are now uncategorized.")
        return super().form_valid(form)


# ---------------------------------------------------------------- budgets


class BudgetListView(OwnedMixin, ListView):
    model = Budget
    template_name = "finance/budget_list.html"
    context_object_name = "budgets"

    def get_queryset(self):
        return super().get_queryset().select_related("category")


class BudgetCreateView(OwnedFormMixin, CreateView):
    model = Budget
    form_class = BudgetForm
    template_name = "finance/budget_form.html"
    success_url = reverse_lazy("budget_list")
    success_message = "Budget created."

    def get_queryset(self):
        return Budget.objects.all()

    def get_initial(self):
        return {"month": date.today().replace(day=1)}


class BudgetUpdateView(OwnedFormMixin, UpdateView):
    model = Budget
    form_class = BudgetForm
    template_name = "finance/budget_form.html"
    success_url = reverse_lazy("budget_list")
    success_message = "Budget updated."


class BudgetDeleteView(OwnedMixin, DeleteView):
    model = Budget
    template_name = "finance/confirm_delete.html"
    success_url = reverse_lazy("budget_list")
