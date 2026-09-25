"""Recurring payment detection and end-of-month balance forecast.

The idea is deliberately simple, no machine learning involved: if the same
counterparty shows up three or more times, the gaps between the transactions
are roughly equal and the amounts are close, it is a subscription, a rent
payment or a salary.

Everything that defines "roughly equal" sits in the constants below, so the
decision rules are in one readable place.
"""

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .models import ZERO, Account, RecurringPayment, Transaction

MIN_OCCURRENCES = 3          # fewer than three repeats is not a pattern yet
HISTORY_MONTHS = 12          # how far back the history is scanned
AMOUNT_TOLERANCE = Decimal("0.25")   # the amount may drift by +/-25%
PERIODS = {                  # name -> accepted interval in days
    "week": (6, 8),
    "month": (26, 35),
    "quarter": (85, 95),
}
PERIOD_DAYS = {"week": 7, "month": 30, "quarter": 91}


@dataclass
class Candidate:
    counterparty: str
    kind: str
    avg_amount: Decimal
    day_of_month: int
    period_days: int
    occurrences: int
    last_seen: date

    @property
    def is_monthly(self) -> bool:
        return self.period_days == PERIOD_DAYS["month"]


def _history_start(today: date) -> date:
    absolute = today.year * 12 + (today.month - 1) - HISTORY_MONTHS
    return date(absolute // 12, absolute % 12 + 1, 1)


def _classify_period(median_gap: float) -> int | None:
    for name, (low, high) in PERIODS.items():
        if low <= median_gap <= high:
            return PERIOD_DAYS[name]
    return None


def _amounts_are_close(amounts: list[Decimal], median: Decimal) -> bool:
    if median == 0:
        return False
    return all(abs(amount - median) / median <= AMOUNT_TOLERANCE for amount in amounts)


def detect(user, today: date | None = None) -> list[Candidate]:
    """Find recurring payment candidates. Saves nothing."""
    today = today or date.today()
    transactions = (
        Transaction.objects.for_user(user)
        .filter(date__gte=_history_start(today))
        .exclude(counterparty="")
        .exclude(kind=Transaction.Kind.TRANSFER)
        .order_by("date")
    )

    groups: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for tx in transactions:
        groups[(tx.counterparty.strip().lower(), tx.kind)].append(tx)

    candidates: list[Candidate] = []
    for (_, kind), items in groups.items():
        if len(items) < MIN_OCCURRENCES:
            continue

        dates = [tx.date for tx in items]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        if len(gaps) < MIN_OCCURRENCES - 1:
            continue

        period_days = _classify_period(statistics.median(gaps))
        if period_days is None:
            continue

        amounts = [tx.amount for tx in items]
        median_amount = Decimal(statistics.median(amounts)).quantize(Decimal("0.01"))
        if not _amounts_are_close(amounts, median_amount):
            continue

        candidates.append(
            Candidate(
                # Show the most frequent spelling of the name, not the lower-cased key.
                counterparty=Counter(tx.counterparty.strip() for tx in items).most_common(1)[0][0],
                kind=kind,
                avg_amount=median_amount,
                day_of_month=Counter(d.day for d in dates).most_common(1)[0][0],
                period_days=period_days,
                occurrences=len(items),
                last_seen=max(dates),
            )
        )

    return sorted(candidates, key=lambda c: c.avg_amount, reverse=True)


def sync(user, today: date | None = None) -> dict:
    """Persist the detected candidates. Ones the user dismissed stay dismissed.

    A payment is identified by counterparty *and* type: the same shop can be both a
    regular expense and a regular income (e.g. refunds), and those are two records.
    """
    found = detect(user, today)
    existing = {
        (payment.counterparty.strip().lower(), payment.kind): payment
        for payment in RecurringPayment.objects.filter(user=user)
    }

    created = updated = 0
    for candidate in found:
        key = (candidate.counterparty.strip().lower(), candidate.kind)
        payment = existing.get(key)
        if payment is None:
            existing[key] = RecurringPayment.objects.create(
                user=user,
                counterparty=candidate.counterparty,
                kind=candidate.kind,
                avg_amount=candidate.avg_amount,
                day_of_month=candidate.day_of_month,
                period_days=candidate.period_days,
                occurrences=candidate.occurrences,
                last_seen=candidate.last_seen,
            )
            created += 1
            continue

        if payment.is_dismissed:
            continue

        payment.avg_amount = candidate.avg_amount
        payment.day_of_month = candidate.day_of_month
        payment.period_days = candidate.period_days
        payment.occurrences = candidate.occurrences
        payment.last_seen = candidate.last_seen
        payment.save()
        updated += 1

    return {"found": len(found), "created": created, "updated": updated}


# ------------------------------------------------------------------ forecast


def _last_day_of_month(today: date) -> date:
    if today.month == 12:
        return date(today.year, 12, 31)
    return date(today.year, today.month + 1, 1) - timedelta(days=1)


def forecast(user, today: date | None = None) -> dict:
    """Balance at the end of the month: what is there now minus what is still due.

    Only monthly payments that have not happened yet this month are counted:
    if Netflix was already charged on the 5th, it must not be subtracted twice.
    """
    today = today or date.today()
    end_of_month = _last_day_of_month(today)

    balance_now = sum(
        (account.balance for account in Account.objects.filter(user=user, is_archived=False)),
        ZERO,
    )

    already_seen = set(
        Transaction.objects.for_user(user)
        .filter(date__year=today.year, date__month=today.month)
        .exclude(counterparty="")
        .values_list("counterparty", "kind")
    )
    already_seen = {(name.strip().lower(), kind) for name, kind in already_seen}

    upcoming: list[dict] = []
    expected_income = expected_expense = ZERO

    payments = RecurringPayment.objects.filter(user=user, is_dismissed=False)
    for payment in payments:
        if payment.period_days != PERIOD_DAYS["month"]:
            continue
        if payment.day_of_month <= today.day:
            continue
        if (payment.counterparty.strip().lower(), payment.kind) in already_seen:
            continue

        day = min(payment.day_of_month, end_of_month.day)
        if payment.kind == Transaction.Kind.INCOME:
            expected_income += payment.avg_amount
        else:
            expected_expense += payment.avg_amount
        upcoming.append({"payment": payment, "expected_on": date(today.year, today.month, day)})

    upcoming.sort(key=lambda item: item["expected_on"])
    return {
        "balance_now": balance_now,
        "expected_income": expected_income,
        "expected_expense": expected_expense,
        "forecast": balance_now + expected_income - expected_expense,
        "upcoming": upcoming,
        "end_of_month": end_of_month,
    }
