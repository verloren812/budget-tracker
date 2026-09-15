"""Fill the database with demo data: python manage.py demo_data

Useful to see the dashboard, the reports and the recurring payment detector
with realistic data right after a fresh install.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from finance.models import Account, Budget, Category, Transaction

User = get_user_model()

EXPENSE_CATEGORIES = [
    ("Groceries", ["REWE", "EDEKA", "ALDI", "LIDL"], 15, 90),
    ("Cafes and restaurants", ["Cafe Central", "Pizzeria Roma"], 8, 45),
    ("Transport", ["Deutsche Bahn", "Tankstelle"], 10, 70),
    ("Housing", ["Vermieter Mueller"], 750, 750),
    ("Phone and internet", ["Vodafone", "Telekom"], 20, 40),
    ("Entertainment", ["Netflix", "Spotify", "Kino"], 10, 25),
    ("Health", ["Apotheke", "dm"], 8, 35),
]
INCOME_CATEGORIES = [
    ("Salary", ["Arbeitgeber GmbH"], 2200, 2200),
    ("Freelance", ["Kunde"], 150, 600),
]

# Random everyday spending is drawn from these categories only.
RANDOM_CATEGORIES = [
    row for row in EXPENSE_CATEGORIES
    if row[0] not in {"Housing", "Entertainment", "Phone and internet"}
]


class Command(BaseCommand):
    help = "Create a demo user with accounts, categories and 12 months of transactions"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="demo")
        parser.add_argument("--password", default="demo12345")
        parser.add_argument("--months", type=int, default=12)

    @db_transaction.atomic
    def handle(self, *args, **options):
        username = options["username"]
        user, created = User.objects.get_or_create(
            username=username, defaults={"email": f"{username}@example.com"}
        )
        user.set_password(options["password"])
        user.save()

        if not created:
            Transaction.objects.filter(user=user).delete()
            Budget.objects.filter(user=user).delete()
            Category.objects.filter(user=user).delete()
            Account.objects.filter(user=user).delete()

        card = Account.objects.create(
            user=user, name="Sparkasse card", kind=Account.Kind.CARD,
            initial_balance=Decimal("1200.00"),
        )
        cash = Account.objects.create(
            user=user, name="Cash", kind=Account.Kind.CASH,
            initial_balance=Decimal("80.00"),
        )

        categories = {}
        for name, _, _, _ in EXPENSE_CATEGORIES:
            categories[name] = Category.objects.create(
                user=user, name=name, kind=Category.Kind.EXPENSE
            )
        for name, _, _, _ in INCOME_CATEGORIES:
            categories[name] = Category.objects.create(
                user=user, name=name, kind=Category.Kind.INCOME
            )

        today = date.today()
        start = today - timedelta(days=30 * options["months"])
        created_count = 0

        day = start
        while day <= today:
            # Salary on the first day of the month
            if day.day == 1:
                Transaction.objects.create(
                    user=user, account=card, category=categories["Salary"],
                    kind=Transaction.Kind.INCOME, amount=Decimal("2200.00"),
                    date=day, counterparty="Arbeitgeber GmbH",
                )
                Transaction.objects.create(
                    user=user, account=card, category=categories["Housing"],
                    kind=Transaction.Kind.EXPENSE, amount=Decimal("750.00"),
                    date=day, counterparty="Vermieter Mueller", note="Miete",
                )
                created_count += 2
            # Fixed-amount subscriptions: the recurring payment detector should find these
            if day.day == 5:
                for name, amount in (("Netflix", "12.99"), ("Spotify", "10.99")):
                    Transaction.objects.create(
                        user=user, account=card, category=categories["Entertainment"],
                        kind=Transaction.Kind.EXPENSE, amount=Decimal(amount),
                        date=day, counterparty=name,
                    )
                    created_count += 1
            if day.day == 20:
                Transaction.objects.create(
                    user=user, account=card, category=categories["Phone and internet"],
                    kind=Transaction.Kind.EXPENSE, amount=Decimal("29.99"),
                    date=day, counterparty="Vodafone",
                )
                created_count += 1
            # Random everyday spending.
            # Subscription and rent categories are excluded on purpose: random
            # amounts for the same counterparty would break the detector.
            for _ in range(random.randint(0, 2)):
                name, shops, low, high = random.choice(RANDOM_CATEGORIES)
                Transaction.objects.create(
                    user=user,
                    account=random.choice([card, card, cash]),
                    category=categories[name],
                    kind=Transaction.Kind.EXPENSE,
                    amount=Decimal(random.randint(low * 100, high * 100)) / 100,
                    date=day,
                    counterparty=random.choice(shops),
                )
                created_count += 1
            day += timedelta(days=1)

        month_start = today.replace(day=1)
        for name, limit in (("Groceries", "350.00"), ("Entertainment", "60.00"), ("Transport", "120.00")):
            Budget.objects.create(
                user=user, category=categories[name], month=month_start, limit=Decimal(limit)
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: user {username} / {options['password']}, "
                f"transactions created: {created_count}"
            )
        )
