"""Forms.

Every account or category choice is filtered by owner: otherwise a tampered id
in a POST request could attach a transaction to somebody else's account.
"""

from django import forms

from .models import Account, Budget, Category, CategoryRule, Transaction

BOOTSTRAP_INPUT = "form-control"
BOOTSTRAP_SELECT = "form-select"


class BootstrapMixin:
    """Add Bootstrap classes to every field so templates stay free of them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", BOOTSTRAP_SELECT)
            elif isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            else:
                widget.attrs.setdefault("class", BOOTSTRAP_INPUT)


class OwnedFormMixin(BootstrapMixin):
    """A form that knows its user and narrows the choice fields accordingly."""

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.user_id and self.user is not None:
            obj.user = self.user
        if commit:
            obj.save()
        return obj


class TransactionForm(OwnedFormMixin, forms.ModelForm):
    class Meta:
        model = Transaction
        fields = (
            "kind", "date", "amount", "account", "transfer_to",
            "category", "counterparty", "note",
        )
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.Textarea(attrs={"rows": 2}),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
        }
        labels = {"transfer_to": "Destination account (for transfers)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = ["%Y-%m-%d", "%d.%m.%Y"]
        if self.user is not None:
            accounts = Account.objects.filter(user=self.user, is_archived=False)
            self.fields["account"].queryset = accounts
            self.fields["transfer_to"].queryset = accounts
            self.fields["category"].queryset = Category.objects.filter(user=self.user)
        self.fields["transfer_to"].required = False
        self.fields["category"].required = False

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        account = cleaned.get("account")
        transfer_to = cleaned.get("transfer_to")
        category = cleaned.get("category")

        if kind == Transaction.Kind.TRANSFER:
            if not transfer_to:
                self.add_error("transfer_to", "A transfer needs a destination account.")
            elif account and transfer_to == account:
                self.add_error("transfer_to", "Source and destination accounts are the same.")
            if category:
                self.add_error("category", "A transfer cannot have a category.")
        else:
            if transfer_to:
                self.add_error("transfer_to", "A destination account is only used for transfers.")
            if category and category.kind != kind:
                self.add_error(
                    "category",
                    f"Category “{category}” belongs to a different transaction type.",
                )
        return cleaned


class AccountForm(OwnedFormMixin, forms.ModelForm):
    class Meta:
        model = Account
        fields = ("name", "kind", "currency", "initial_balance", "is_archived")
        widgets = {"initial_balance": forms.NumberInput(attrs={"step": "0.01"})}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Account.objects.filter(user=self.user, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("An account with this name already exists.")
        return name


class CategoryForm(OwnedFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "kind", "parent", "color")
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.user is not None:
            qs = Category.objects.filter(user=self.user, parent__isnull=True)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            self.fields["parent"].queryset = qs
        self.fields["parent"].required = False

    def clean(self):
        cleaned = super().clean()
        parent = cleaned.get("parent")
        kind = cleaned.get("kind")
        if parent and kind and parent.kind != kind:
            self.add_error("parent", "The parent category has a different type.")
        return cleaned


class BudgetForm(OwnedFormMixin, forms.ModelForm):
    class Meta:
        model = Budget
        fields = ("category", "month", "limit")
        widgets = {
            "month": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "limit": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["month"].input_formats = ["%Y-%m-%d", "%d.%m.%Y"]
        if self.user is not None:
            self.fields["category"].queryset = Category.objects.filter(
                user=self.user, kind=Category.Kind.EXPENSE
            )

    def clean_month(self):
        # A budget is always pinned to the first day of the month.
        return self.cleaned_data["month"].replace(day=1)

    def clean(self):
        cleaned = super().clean()
        category, month = cleaned.get("category"), cleaned.get("month")
        if category and month:
            qs = Budget.objects.filter(user=self.user, category=category, month=month)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("category", "A budget for this category and month already exists.")
        return cleaned


class CategoryRuleForm(OwnedFormMixin, forms.ModelForm):
    class Meta:
        model = CategoryRule
        fields = ("match_field", "match_type", "pattern", "category", "priority", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.user is not None:
            self.fields["category"].queryset = Category.objects.filter(user=self.user)


class ImportUploadForm(BootstrapMixin, forms.Form):
    """Step 1: which file is uploaded and into which account."""

    MAX_SIZE = 5 * 1024 * 1024

    account = forms.ModelChoiceField(
        label="Account", queryset=Account.objects.none(), empty_label=None
    )
    file = forms.FileField(
        label="Statement file (CSV)", widget=forms.FileInput(attrs={"accept": ".csv,.txt"})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["account"].queryset = Account.objects.filter(
                user=user, is_archived=False
            )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded.size > self.MAX_SIZE:
            raise forms.ValidationError("The file is larger than 5 MB, which is too much for a statement.")
        if not uploaded.name.lower().endswith((".csv", ".txt")):
            raise forms.ValidationError("A CSV file is required. Export the statement in that format.")
        return uploaded


class ImportMappingForm(BootstrapMixin, forms.Form):
    """Step 2: which column of the file holds which field."""

    SIGN_CHOICES = (
        ("signed", "Minus is an expense, plus is income"),
        ("expense", "Every row is an expense"),
        ("income", "Every row is income"),
    )

    date = forms.TypedChoiceField(label="Date", coerce=int)
    amount = forms.TypedChoiceField(label="Amount", coerce=int)
    counterparty = forms.TypedChoiceField(label="Counterparty", coerce=int, required=False)
    note = forms.TypedChoiceField(label="Payment reference", coerce=int, required=False)
    sign_mode = forms.ChoiceField(label="How to read the amount", choices=SIGN_CHOICES)

    def __init__(self, *args, columns=None, **kwargs):
        super().__init__(*args, **kwargs)
        columns = columns or []
        required_choices = [(str(index), name) for index, name in columns]
        optional_choices = [("", "- no such column -")] + required_choices
        self.fields["date"].choices = required_choices
        self.fields["amount"].choices = required_choices
        self.fields["counterparty"].choices = optional_choices
        self.fields["note"].choices = optional_choices

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("date") == cleaned.get("amount"):
            self.add_error("amount", "Date and amount cannot be the same column.")
        return cleaned


class ReportPeriodForm(BootstrapMixin, forms.Form):
    """Period for the reports page. Empty fields mean the default period."""

    date_from = forms.DateField(
        label="From", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        label="To", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "The end of the period is before its start.")
        return cleaned


class TransactionFilterForm(BootstrapMixin, forms.Form):
    """Filters for the transaction list. Every field is optional."""

    q = forms.CharField(
        label="Search", required=False,
        widget=forms.TextInput(attrs={"placeholder": "counterparty or note"}),
    )
    kind = forms.ChoiceField(
        label="Type", required=False,
        choices=[("", "All")] + list(Transaction.Kind.choices),
    )
    account = forms.ModelChoiceField(
        label="Account", required=False, queryset=Account.objects.none(), empty_label="All",
    )
    category = forms.ModelChoiceField(
        label="Category", required=False, queryset=Category.objects.none(), empty_label="All",
    )
    date_from = forms.DateField(
        label="From", required=False, widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="To", required=False, widget=forms.DateInput(attrs={"type": "date"}),
    )
    only_uncategorized = forms.BooleanField(label="Uncategorized only", required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["account"].queryset = Account.objects.filter(user=user)
            self.fields["category"].queryset = Category.objects.filter(user=user)

    def filter(self, queryset):
        """Apply the filled-in filters to the transaction queryset."""
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data.get("q"):
            from django.db.models import Q

            queryset = queryset.filter(
                Q(counterparty__icontains=data["q"]) | Q(note__icontains=data["q"])
            )
        if data.get("kind"):
            queryset = queryset.filter(kind=data["kind"])
        if data.get("account"):
            queryset = queryset.filter(account=data["account"])
        if data.get("category"):
            queryset = queryset.filter(category=data["category"])
        if data.get("date_from"):
            queryset = queryset.filter(date__gte=data["date_from"])
        if data.get("date_to"):
            queryset = queryset.filter(date__lte=data["date_to"])
        if data.get("only_uncategorized"):
            queryset = queryset.filter(category__isnull=True).exclude(
                kind=Transaction.Kind.TRANSFER
            )
        return queryset
