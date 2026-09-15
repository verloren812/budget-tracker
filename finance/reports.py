"""Aggregations behind the reports page.

Everything is computed in the database with annotate/aggregate instead of
Python loops: on a couple of thousand transactions the difference is already
noticeable.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth

from .models import ZERO, Transaction

# Palette for the pie chart, used by categories that have no colour of their own.
FALLBACK_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
]


@dataclass
class Period:
    start: date
    end: date

    @property
    def months(self) -> int:
        """How many months the period spans, at least one."""
        return max(
            (self.end.year - self.start.year) * 12 + self.end.month - self.start.month + 1, 1
        )


def default_period(today: date | None = None) -> Period:
    """By default the last 12 months, the current one included."""
    today = today or date.today()
    # Counting in "absolute months" avoids special cases around the year boundary.
    absolute = today.year * 12 + (today.month - 1) - 11
    start = date(absolute // 12, absolute % 12 + 1, 1)
    return Period(start=start, end=today)


def base_queryset(user, period: Period):
    return Transaction.objects.for_user(user).filter(
        date__gte=period.start, date__lte=period.end
    )


def totals(user, period: Period) -> dict:
    agg = base_queryset(user, period).aggregate(
        income=Sum("amount", filter=Q(kind=Transaction.Kind.INCOME), default=ZERO),
        expense=Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE), default=ZERO),
        count=Count("id"),
    )
    months = period.months
    agg["result"] = agg["income"] - agg["expense"]
    agg["avg_expense_per_month"] = (agg["expense"] / months).quantize(Decimal("0.01"))
    agg["avg_income_per_month"] = (agg["income"] / months).quantize(Decimal("0.01"))
    agg["months"] = months
    return agg


def by_category(user, period: Period, limit: int = 10) -> list[dict]:
    """Expenses per category, largest first. The long tail is folded into "Other"."""
    rows = list(
        base_queryset(user, period)
        .expenses()
        .values("category__name", "category__color")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    for row in rows:
        if row["category__name"] is None:
            row["category__name"] = "Uncategorized"
            row["category__color"] = "#adb5bd"

    head, tail = rows[:limit], rows[limit:]
    if tail:
        head.append(
            {
                "category__name": "Other",
                "category__color": "#adb5bd",
                "total": sum((r["total"] for r in tail), ZERO),
                "count": sum(r["count"] for r in tail),
            }
        )

    grand_total = sum((r["total"] for r in head), ZERO)
    for index, row in enumerate(head):
        row["color"] = row["category__color"] or FALLBACK_COLORS[index % len(FALLBACK_COLORS)]
        row["share"] = (
            (row["total"] / grand_total * 100).quantize(Decimal("0.1")) if grand_total else ZERO
        )
    return head


def by_month(user, period: Period) -> list[dict]:
    """Income and expenses per month, for the bar chart."""
    rows = (
        base_queryset(user, period)
        .annotate(month=TruncMonth("date"))
        .values("month")
        .annotate(
            income=Sum("amount", filter=Q(kind=Transaction.Kind.INCOME), default=ZERO),
            expense=Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE), default=ZERO),
        )
        .order_by("month")
    )
    return [
        {
            "month": row["month"],
            "income": row["income"],
            "expense": row["expense"],
            "result": row["income"] - row["expense"],
        }
        for row in rows
    ]


def top_counterparties(user, period: Period, limit: int = 10) -> list[dict]:
    return list(
        base_queryset(user, period)
        .expenses()
        .exclude(counterparty="")
        .values("counterparty")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")[:limit]
    )
