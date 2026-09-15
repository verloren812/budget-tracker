"""Domain models of the personal budget tracker.

Key design decisions:
* every monetary field is a Decimal, never a float;
* a transaction amount is always positive, the sign comes from `kind`;
* an account balance is never stored in a column, it is derived from transactions.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse

ZERO = Decimal("0.00")


class Account(models.Model):
    """A money container: card, cash, savings."""

    class Kind(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        SAVINGS = "savings", "Savings"
        OTHER = "other", "Other"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounts"
    )
    name = models.CharField("name", max_length=100)
    kind = models.CharField("type", max_length=20, choices=Kind.choices, default=Kind.CARD)
    currency = models.CharField("currency", max_length=3, default="EUR")
    initial_balance = models.DecimalField(
        "initial balance", max_digits=12, decimal_places=2, default=ZERO
    )
    is_archived = models.BooleanField("archived", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "account"
        verbose_name_plural = "accounts"
        ordering = ["is_archived", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="unique_account_name_per_user"
            )
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("account_detail", args=[self.pk])

    @property
    def balance(self) -> Decimal:
        """Current balance = initial balance + everything the transactions moved."""
        agg = Transaction.objects.filter(
            Q(account=self) | Q(transfer_to=self)
        ).aggregate(
            income=Coalesce(
                Sum("amount", filter=Q(account=self, kind=Transaction.Kind.INCOME)), ZERO
            ),
            expense=Coalesce(
                Sum("amount", filter=Q(account=self, kind=Transaction.Kind.EXPENSE)), ZERO
            ),
            out=Coalesce(
                Sum("amount", filter=Q(account=self, kind=Transaction.Kind.TRANSFER)), ZERO
            ),
            inn=Coalesce(
                Sum("amount", filter=Q(transfer_to=self, kind=Transaction.Kind.TRANSFER)),
                ZERO,
            ),
        )
        total = (
            self.initial_balance
            + agg["income"]
            - agg["expense"]
            - agg["out"]
            + agg["inn"]
        )
        # SQLite returns sums with extra trailing digits, so round back to cents.
        return Decimal(total).quantize(Decimal("0.01"))


class Category(models.Model):
    """An income or expense category. Supports one level of nesting."""

    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories"
    )
    name = models.CharField("name", max_length=100)
    kind = models.CharField("type", max_length=10, choices=Kind.choices, default=Kind.EXPENSE)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="parent category",
    )
    color = models.CharField("color", max_length=7, default="#6c757d")

    class Meta:
        verbose_name = "category"
        verbose_name_plural = "categories"
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name", "kind"], name="unique_category_per_user"
            )
        ]

    def __str__(self):
        return f"{self.parent.name} → {self.name}" if self.parent else self.name


class TransactionQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(user=user)

    def expenses(self):
        return self.filter(kind=Transaction.Kind.EXPENSE)

    def incomes(self):
        return self.filter(kind=Transaction.Kind.INCOME)

    def in_month(self, year: int, month: int):
        return self.filter(date__year=year, date__month=month)

    def with_related(self):
        return self.select_related("account", "category", "transfer_to")


class Transaction(models.Model):
    """A single money movement: income, expense or a transfer between own accounts."""

    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        TRANSFER = "transfer", "Transfer"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Imported"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions"
    )
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="transactions", verbose_name="account"
    )
    transfer_to = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incoming_transfers",
        verbose_name="destination account",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name="category",
    )
    kind = models.CharField("type", max_length=10, choices=Kind.choices)
    amount = models.DecimalField(
        "amount",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    date = models.DateField("date")
    counterparty = models.CharField("counterparty", max_length=255, blank=True)
    note = models.TextField("note", blank=True)

    source = models.CharField(
        "source", max_length=10, choices=Source.choices, default=Source.MANUAL
    )
    import_batch = models.ForeignKey(
        "ImportBatch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    dedup_hash = models.CharField(max_length=64, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    objects = TransactionQuerySet.as_manager()

    class Meta:
        verbose_name = "transaction"
        verbose_name_plural = "transactions"
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["user", "-date"]),
            models.Index(fields=["user", "kind"]),
        ]
        constraints = [
            # The same statement row must never reach the database twice.
            models.UniqueConstraint(
                fields=["user", "dedup_hash"],
                condition=~Q(dedup_hash=""),
                name="unique_imported_transaction",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="transaction_amount_positive"
            ),
        ]

    def __str__(self):
        return f"{self.date} {self.get_kind_display()} {self.amount}"

    def get_absolute_url(self):
        return reverse("transaction_detail", args=[self.pk])

    @property
    def signed_amount(self) -> Decimal:
        """Amount with a sign, for display only."""
        if self.kind == self.Kind.INCOME:
            return self.amount
        return -self.amount


class Budget(models.Model):
    """A spending limit for one category in one month."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budgets"
    )
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="budgets", verbose_name="category"
    )
    month = models.DateField("month", help_text="First day of the month")
    limit = models.DecimalField(
        "limit",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    class Meta:
        verbose_name = "budget"
        verbose_name_plural = "budgets"
        ordering = ["-month", "category__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "category", "month"], name="unique_budget_per_month"
            )
        ]

    def __str__(self):
        return f"{self.category} — {self.month:%m.%Y}: {self.limit}"

    @property
    def spent(self) -> Decimal:
        return Transaction.objects.filter(
            user=self.user,
            category=self.category,
            kind=Transaction.Kind.EXPENSE,
            date__year=self.month.year,
            date__month=self.month.month,
        ).aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]

    @property
    def progress(self) -> int:
        """Share of the limit already spent, in percent, for the progress bar."""
        if not self.limit:
            return 0
        return int(min(self.spent / self.limit * 100, 999))


class ImportBatch(models.Model):
    """One upload of a bank statement."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="import_batches"
    )
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="import_batches", verbose_name="account"
    )
    file_name = models.CharField("file", max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    rows_total = models.PositiveIntegerField("rows in file", default=0)
    rows_imported = models.PositiveIntegerField("imported", default=0)
    rows_skipped = models.PositiveIntegerField("skipped as duplicates", default=0)

    class Meta:
        verbose_name = "statement import"
        verbose_name_plural = "statement imports"
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.file_name} ({self.uploaded_at:%d.%m.%Y})"


class CategoryRule(models.Model):
    """A rule that assigns a category to imported transactions automatically."""

    class Field(models.TextChoices):
        COUNTERPARTY = "counterparty", "Counterparty"
        NOTE = "note", "Payment reference"

    class MatchType(models.TextChoices):
        CONTAINS = "contains", "Contains"
        STARTSWITH = "startswith", "Starts with"
        EXACT = "exact", "Exact match"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="category_rules"
    )
    match_field = models.CharField(
        "field", max_length=20, choices=Field.choices, default=Field.COUNTERPARTY
    )
    match_type = models.CharField(
        "condition", max_length=20, choices=MatchType.choices, default=MatchType.CONTAINS
    )
    pattern = models.CharField("value", max_length=255)
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="rules", verbose_name="category"
    )
    priority = models.PositiveIntegerField(
        "priority", default=100, help_text="Lower number wins"
    )
    is_active = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "category rule"
        verbose_name_plural = "category rules"
        ordering = ["priority", "id"]

    def __str__(self):
        return f"{self.get_match_field_display()} {self.get_match_type_display().lower()} “{self.pattern}” → {self.category}"

    def matches(self, counterparty: str, note: str) -> bool:
        value = (counterparty if self.match_field == self.Field.COUNTERPARTY else note).lower()
        pattern = self.pattern.lower()
        if self.match_type == self.MatchType.CONTAINS:
            return pattern in value
        if self.match_type == self.MatchType.STARTSWITH:
            return value.startswith(pattern)
        return value == pattern


class RecurringPayment(models.Model):
    """A recurring payment detected in the transaction history."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recurring_payments"
    )
    counterparty = models.CharField("counterparty", max_length=255)
    kind = models.CharField(
        "type", max_length=10, choices=Category.Kind.choices, default=Category.Kind.EXPENSE
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="category"
    )
    avg_amount = models.DecimalField("average amount", max_digits=12, decimal_places=2)
    day_of_month = models.PositiveSmallIntegerField("day of month")
    period_days = models.PositiveSmallIntegerField("period, days", default=30)
    occurrences = models.PositiveSmallIntegerField("occurrences found", default=0)
    last_seen = models.DateField("last seen")
    is_confirmed = models.BooleanField("confirmed by user", default=False)
    is_dismissed = models.BooleanField("dismissed", default=False)

    class Meta:
        verbose_name = "recurring payment"
        verbose_name_plural = "recurring payments"
        ordering = ["-avg_amount"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "counterparty"], name="unique_recurring_per_user"
            )
        ]

    def __str__(self):
        return f"{self.counterparty} ≈ {self.avg_amount} on day {self.day_of_month} of the month"
