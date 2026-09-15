from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse

from .models import Account, Budget, Category, CategoryRule, RecurringPayment, Transaction

User = get_user_model()


class BalanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("nazar", password="pass12345")
        self.card = Account.objects.create(
            user=self.user, name="Card", initial_balance=Decimal("100.00")
        )
        self.cash = Account.objects.create(
            user=self.user, name="Cash", initial_balance=Decimal("0.00")
        )

    def test_balance_counts_income_and_expense(self):
        Transaction.objects.create(
            user=self.user, account=self.card, kind=Transaction.Kind.INCOME,
            amount=Decimal("50.00"), date=date(2026, 1, 10),
        )
        Transaction.objects.create(
            user=self.user, account=self.card, kind=Transaction.Kind.EXPENSE,
            amount=Decimal("20.50"), date=date(2026, 1, 11),
        )
        self.assertEqual(self.card.balance, Decimal("129.50"))

    def test_transfer_moves_money_between_accounts(self):
        Transaction.objects.create(
            user=self.user, account=self.card, transfer_to=self.cash,
            kind=Transaction.Kind.TRANSFER, amount=Decimal("40.00"), date=date(2026, 1, 12),
        )
        self.assertEqual(self.card.balance, Decimal("60.00"))
        self.assertEqual(self.cash.balance, Decimal("40.00"))

    def test_amounts_are_decimal_not_float(self):
        Transaction.objects.create(
            user=self.user, account=self.card, kind=Transaction.Kind.EXPENSE,
            amount=Decimal("0.10"), date=date(2026, 1, 13),
        )
        Transaction.objects.create(
            user=self.user, account=self.card, kind=Transaction.Kind.EXPENSE,
            amount=Decimal("0.20"), date=date(2026, 1, 13),
        )
        self.assertEqual(self.card.balance, Decimal("99.70"))


class DedupTests(TestCase):
    def test_same_import_hash_cannot_repeat(self):
        user = User.objects.create_user("u1", password="pass12345")
        account = Account.objects.create(user=user, name="Card")
        kwargs = dict(
            user=user, account=account, kind=Transaction.Kind.EXPENSE,
            amount=Decimal("9.99"), date=date(2026, 2, 1),
            source=Transaction.Source.IMPORT, dedup_hash="abc123",
        )
        Transaction.objects.create(**kwargs)
        with self.assertRaises(IntegrityError):
            Transaction.objects.create(**kwargs)


class BudgetTests(TestCase):
    def test_spent_and_progress(self):
        user = User.objects.create_user("u2", password="pass12345")
        account = Account.objects.create(user=user, name="Card")
        category = Category.objects.create(user=user, name="Groceries")
        budget = Budget.objects.create(
            user=user, category=category, month=date(2026, 3, 1), limit=Decimal("200.00")
        )
        Transaction.objects.create(
            user=user, account=account, category=category,
            kind=Transaction.Kind.EXPENSE, amount=Decimal("50.00"), date=date(2026, 3, 5),
        )
        self.assertEqual(budget.spent, Decimal("50.00"))
        self.assertEqual(budget.progress, 25)


class AccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pass12345")
        self.stranger = User.objects.create_user("stranger", password="pass12345")
        self.account = Account.objects.create(user=self.owner, name="Card")

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_stranger_cannot_open_foreign_account(self):
        self.client.login(username="stranger", password="pass12345")
        response = self.client.get(reverse("account_detail", args=[self.account.pk]))
        self.assertEqual(response.status_code, 404)

    def test_owner_can_open_own_account(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(reverse("account_detail", args=[self.account.pk]))
        self.assertEqual(response.status_code, 200)


class TransactionFormTests(TestCase):
    """Transaction form validation, the most rule-heavy part of the project."""

    def setUp(self):
        self.user = User.objects.create_user("former", password="pass12345")
        self.other = User.objects.create_user("other", password="pass12345")
        self.card = Account.objects.create(user=self.user, name="Card")
        self.cash = Account.objects.create(user=self.user, name="Cash")
        self.foreign = Account.objects.create(user=self.other, name="Someone else's account")
        self.food = Category.objects.create(
            user=self.user, name="Groceries", kind=Category.Kind.EXPENSE
        )
        self.salary = Category.objects.create(
            user=self.user, name="Salary", kind=Category.Kind.INCOME
        )

    def form(self, **overrides):
        from .forms import TransactionForm

        data = {
            "kind": Transaction.Kind.EXPENSE,
            "date": "2026-05-01",
            "amount": "12.50",
            "account": self.card.pk,
            "category": self.food.pk,
            "counterparty": "REWE",
            "note": "",
        }
        data.update(overrides)
        return TransactionForm(data=data, user=self.user)

    def test_valid_expense(self):
        self.assertTrue(self.form().is_valid())

    def test_category_kind_must_match(self):
        form = self.form(category=self.salary.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)

    def test_transfer_requires_target_account(self):
        form = self.form(kind=Transaction.Kind.TRANSFER, category="")
        self.assertFalse(form.is_valid())
        self.assertIn("transfer_to", form.errors)

    def test_transfer_to_same_account_rejected(self):
        form = self.form(
            kind=Transaction.Kind.TRANSFER, category="", transfer_to=self.card.pk
        )
        self.assertFalse(form.is_valid())

    def test_valid_transfer(self):
        form = self.form(
            kind=Transaction.Kind.TRANSFER, category="", transfer_to=self.cash.pk
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_foreign_account_not_allowed(self):
        """Posting another user's account id must not be accepted."""
        form = self.form(account=self.foreign.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("account", form.errors)


class TransactionListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lister", password="pass12345")
        self.card = Account.objects.create(user=self.user, name="Card")
        self.food = Category.objects.create(user=self.user, name="Groceries")
        for day, amount, party in ((1, "10.00", "REWE"), (2, "20.00", "ALDI"), (3, "30.00", "REWE")):
            Transaction.objects.create(
                user=self.user, account=self.card, category=self.food,
                kind=Transaction.Kind.EXPENSE, amount=Decimal(amount),
                date=date(2026, 4, day), counterparty=party,
            )
        Transaction.objects.create(
            user=self.user, account=self.card, kind=Transaction.Kind.INCOME,
            amount=Decimal("500.00"), date=date(2026, 4, 5), counterparty="Arbeitgeber",
        )
        self.client.login(username="lister", password="pass12345")

    def test_list_shows_all(self):
        response = self.client.get(reverse("transaction_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["found"], 4)

    def test_filter_by_text(self):
        response = self.client.get(reverse("transaction_list"), {"q": "REWE"})
        self.assertEqual(response.context["found"], 2)

    def test_filter_by_kind(self):
        response = self.client.get(reverse("transaction_list"), {"kind": "income"})
        self.assertEqual(response.context["found"], 1)

    def test_filter_by_period(self):
        response = self.client.get(
            reverse("transaction_list"), {"date_from": "2026-04-02", "date_to": "2026-04-03"}
        )
        self.assertEqual(response.context["found"], 2)

    def test_totals_count_filtered_set(self):
        response = self.client.get(reverse("transaction_list"), {"kind": "expense"})
        self.assertEqual(response.context["total_expense"], Decimal("60.00"))


class CrudAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner2", password="pass12345")
        self.stranger = User.objects.create_user("stranger2", password="pass12345")
        self.account = Account.objects.create(user=self.owner, name="Card")
        self.tx = Transaction.objects.create(
            user=self.owner, account=self.account, kind=Transaction.Kind.EXPENSE,
            amount=Decimal("5.00"), date=date(2026, 4, 1),
        )

    def test_stranger_cannot_edit_foreign_transaction(self):
        self.client.login(username="stranger2", password="pass12345")
        response = self.client.get(reverse("transaction_update", args=[self.tx.pk]))
        self.assertEqual(response.status_code, 404)

    def test_owner_can_create_transaction(self):
        self.client.login(username="owner2", password="pass12345")
        response = self.client.post(
            reverse("transaction_create"),
            {
                "kind": "expense", "date": "2026-04-10", "amount": "7.25",
                "account": self.account.pk, "category": "", "counterparty": "Kiosk", "note": "",
            },
        )
        self.assertRedirects(response, reverse("transaction_list"))
        self.assertEqual(Transaction.objects.filter(user=self.owner).count(), 2)

    def test_account_with_transactions_is_protected(self):
        self.client.login(username="owner2", password="pass12345")
        response = self.client.post(reverse("account_delete", args=[self.account.pk]), follow=True)
        self.assertTrue(Account.objects.filter(pk=self.account.pk).exists())
        self.assertContains(response, "cannot be deleted")


class ReportTests(TestCase):
    """Report aggregations: totals, shares and the per-month breakdown."""

    def setUp(self):
        from . import reports

        self.reports = reports
        self.user = User.objects.create_user("reporter", password="pass12345")
        self.card = Account.objects.create(user=self.user, name="Card")
        self.food = Category.objects.create(user=self.user, name="Groceries")
        self.fun = Category.objects.create(user=self.user, name="Entertainment")

        data = [
            (date(2026, 1, 10), "100.00", self.food, "REWE"),
            (date(2026, 1, 20), "50.00", self.fun, "Kino"),
            (date(2026, 2, 10), "150.00", self.food, "REWE"),
            (date(2026, 3, 10), "200.00", None, ""),
        ]
        for day, amount, category, party in data:
            Transaction.objects.create(
                user=self.user, account=self.card, category=category,
                kind=Transaction.Kind.EXPENSE, amount=Decimal(amount),
                date=day, counterparty=party,
            )
        Transaction.objects.create(
            user=self.user, account=self.card, kind=Transaction.Kind.INCOME,
            amount=Decimal("1000.00"), date=date(2026, 1, 1), counterparty="Arbeitgeber",
        )
        self.period = self.reports.Period(start=date(2026, 1, 1), end=date(2026, 3, 31))

    def test_period_counts_months(self):
        self.assertEqual(self.period.months, 3)

    def test_totals(self):
        totals = self.reports.totals(self.user, self.period)
        self.assertEqual(totals["expense"], Decimal("500.00"))
        self.assertEqual(totals["income"], Decimal("1000.00"))
        self.assertEqual(totals["result"], Decimal("500.00"))
        self.assertEqual(totals["avg_expense_per_month"], Decimal("166.67"))

    def test_by_category_sorted_with_shares(self):
        rows = self.reports.by_category(self.user, self.period)
        self.assertEqual(rows[0]["category__name"], "Groceries")
        self.assertEqual(rows[0]["total"], Decimal("250.00"))
        self.assertEqual(rows[0]["share"], Decimal("50.0"))

    def test_uncategorized_is_labelled(self):
        names = [row["category__name"] for row in self.reports.by_category(self.user, self.period)]
        self.assertIn("Uncategorized", names)

    def test_by_month(self):
        rows = self.reports.by_month(self.user, self.period)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["expense"], Decimal("150.00"))
        self.assertEqual(rows[0]["income"], Decimal("1000.00"))
        self.assertEqual(rows[0]["result"], Decimal("850.00"))

    def test_top_counterparties(self):
        rows = self.reports.top_counterparties(self.user, self.period)
        self.assertEqual(rows[0]["counterparty"], "REWE")
        self.assertEqual(rows[0]["total"], Decimal("250.00"))
        self.assertEqual(rows[0]["count"], 2)

    def test_default_period_is_twelve_months(self):
        period = self.reports.default_period(date(2026, 3, 15))
        self.assertEqual(period.start, date(2025, 4, 1))
        self.assertEqual(period.months, 12)

    def test_report_page_opens(self):
        self.client.login(username="reporter", password="pass12345")
        response = self.client.get(reverse("reports"), {"date_from": "2026-01-01", "date_to": "2026-03-31"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["totals"]["expense"], Decimal("500.00"))

    def test_other_user_sees_nothing(self):
        User.objects.create_user("outsider", password="pass12345")
        self.client.login(username="outsider", password="pass12345")
        response = self.client.get(reverse("reports"))
        self.assertEqual(response.context["totals"]["expense"], Decimal("0.00"))


class ExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("exporter", password="pass12345")
        self.card = Account.objects.create(user=self.user, name="Card")
        self.food = Category.objects.create(user=self.user, name="Groceries")
        Transaction.objects.create(
            user=self.user, account=self.card, category=self.food,
            kind=Transaction.Kind.EXPENSE, amount=Decimal("12.34"),
            date=date(2026, 4, 1), counterparty="REWE",
        )
        Transaction.objects.create(
            user=self.user, account=self.card, kind=Transaction.Kind.INCOME,
            amount=Decimal("1000.00"), date=date(2026, 4, 2), counterparty="Arbeitgeber",
        )
        self.client.login(username="exporter", password="pass12345")

    def get_csv(self, params=None):
        response = self.client.get(reverse("transaction_export"), params or {})
        return response, response.content.decode("utf-8-sig")

    def test_csv_headers_and_rows(self):
        response, text = self.get_csv()
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        lines = [line for line in text.splitlines() if line]
        self.assertEqual(len(lines), 3)  # header + two transactions
        self.assertTrue(lines[0].startswith("Date;Type;Amount"))

    def test_decimal_comma_for_excel(self):
        _, text = self.get_csv()
        self.assertIn("12,34", text)

    def test_export_respects_filters(self):
        _, text = self.get_csv({"kind": "income"})
        lines = [line for line in text.splitlines() if line]
        self.assertEqual(len(lines), 2)
        self.assertIn("Arbeitgeber", text)
        self.assertNotIn("REWE", text)

    def test_export_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("transaction_export"))
        self.assertEqual(response.status_code, 302)


class ParserTests(TestCase):
    """Parsing of the formats that usually break a statement import."""

    def setUp(self):
        from . import importer

        self.importer = importer

    def test_parse_amount_german(self):
        self.assertEqual(self.importer.parse_amount("1.234,56"), Decimal("1234.56"))
        self.assertEqual(self.importer.parse_amount("-12,50 €"), Decimal("-12.50"))
        self.assertEqual(self.importer.parse_amount("0,99"), Decimal("0.99"))

    def test_parse_amount_international(self):
        self.assertEqual(self.importer.parse_amount("1,234.56"), Decimal("1234.56"))
        self.assertEqual(self.importer.parse_amount("+1 200.00"), Decimal("1200.00"))
        self.assertEqual(self.importer.parse_amount("12.34"), Decimal("12.34"))

    def test_thousands_without_decimals(self):
        self.assertEqual(self.importer.parse_amount("1.234"), Decimal("1234.00"))

    def test_broken_amount_raises(self):
        with self.assertRaises(self.importer.ImportError_):
            self.importer.parse_amount("not a number")

    def test_parse_dates(self):
        self.assertEqual(self.importer.parse_date("31.12.2026"), date(2026, 12, 31))
        self.assertEqual(self.importer.parse_date("2026-12-31"), date(2026, 12, 31))
        self.assertEqual(self.importer.parse_date("31/12/2026"), date(2026, 12, 31))

    def test_broken_date_raises(self):
        with self.assertRaises(self.importer.ImportError_):
            self.importer.parse_date("32.13.2026")

    def test_detects_cp1252(self):
        raw = "Datum;Betrag;Empfänger\r\n01.04.2026;-9,99;Müller".encode("cp1252")
        parsed = self.importer.parse_file(raw)
        self.assertEqual(parsed.encoding, "cp1252")
        self.assertEqual(parsed.delimiter, ";")
        self.assertEqual(parsed.rows[0][2], "Müller")

    def test_detects_utf8_with_bom_and_comma(self):
        raw = "date,amount,payee\r\n2026-04-01,-9.99,Shop".encode("utf-8-sig")
        parsed = self.importer.parse_file(raw)
        self.assertEqual(parsed.encoding, "utf-8-sig")
        self.assertEqual(parsed.delimiter, ",")
        self.assertEqual(parsed.header[0], "date")

    def test_guess_columns_german_header(self):
        header = ["Buchungstag", "Verwendungszweck", "Empfänger", "Betrag"]
        guessed = self.importer.guess_columns(header)
        self.assertEqual(guessed["date"], 0)
        self.assertEqual(guessed["note"], 1)
        self.assertEqual(guessed["counterparty"], 2)
        self.assertEqual(guessed["amount"], 3)

    def test_empty_file_raises(self):
        with self.assertRaises(self.importer.ImportError_):
            self.importer.parse_file(b"")

    def test_header_only_raises(self):
        with self.assertRaises(self.importer.ImportError_):
            self.importer.parse_file("Datum;Betrag\r\n".encode("utf-8"))


STATEMENT = (
    "Buchungstag;Empfänger;Verwendungszweck;Betrag\r\n"
    "01.04.2026;REWE Markt;Einkauf;-45,20\r\n"
    "02.04.2026;Arbeitgeber GmbH;Gehalt;2.200,00\r\n"
    "03.04.2026;Kiosk;Snack;-3,50\r\n"
    "03.04.2026;Kiosk;Snack;-3,50\r\n"
    "04.04.2026;Netflix;Abo;-12,99\r\n"
)


class ImportRunTests(TestCase):
    def setUp(self):
        from . import importer

        self.importer = importer
        self.user = User.objects.create_user("importer", password="pass12345")
        self.account = Account.objects.create(user=self.user, name="Card")
        self.food = Category.objects.create(
            user=self.user, name="Groceries", kind=Category.Kind.EXPENSE
        )
        self.salary = Category.objects.create(
            user=self.user, name="Salary", kind=Category.Kind.INCOME
        )
        self.parsed = importer.parse_file(STATEMENT.encode("cp1252"))
        self.mapping = importer.Mapping(date=0, counterparty=1, note=2, amount=3)

    def run_import(self):
        return self.importer.run_import(
            self.user, self.account, self.parsed, self.mapping, "statement.csv"
        )

    def test_imports_all_rows(self):
        batch, _ = self.run_import()
        self.assertEqual(batch.rows_total, 5)
        self.assertEqual(batch.rows_imported, 5)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 5)

    def test_sign_defines_kind(self):
        self.run_import()
        income = Transaction.objects.get(user=self.user, kind=Transaction.Kind.INCOME)
        self.assertEqual(income.amount, Decimal("2200.00"))
        self.assertEqual(income.counterparty, "Arbeitgeber GmbH")
        self.assertEqual(
            Transaction.objects.filter(user=self.user, kind=Transaction.Kind.EXPENSE).count(), 4
        )

    def test_amounts_are_positive_in_db(self):
        self.run_import()
        self.assertFalse(Transaction.objects.filter(user=self.user, amount__lt=0).exists())

    def test_identical_rows_in_one_file_are_kept(self):
        """Two identical snacks on the same day are two transactions, not a duplicate."""
        self.run_import()
        self.assertEqual(
            Transaction.objects.filter(user=self.user, counterparty="Kiosk").count(), 2
        )

    def test_repeated_import_skips_everything(self):
        self.run_import()
        batch, _ = self.run_import()
        self.assertEqual(batch.rows_imported, 0)
        self.assertEqual(batch.rows_skipped, 5)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 5)

    def test_rules_set_category(self):
        CategoryRule.objects.create(
            user=self.user, match_field=CategoryRule.Field.COUNTERPARTY,
            match_type=CategoryRule.MatchType.CONTAINS, pattern="REWE",
            category=self.food, priority=10,
        )
        self.run_import()
        tx = Transaction.objects.get(user=self.user, counterparty="REWE Markt")
        self.assertEqual(tx.category, self.food)

    def test_rule_of_wrong_kind_is_ignored(self):
        """A rule pointing at an income category must not stick to an expense."""
        CategoryRule.objects.create(
            user=self.user, match_field=CategoryRule.Field.COUNTERPARTY,
            match_type=CategoryRule.MatchType.CONTAINS, pattern="REWE",
            category=self.salary, priority=10,
        )
        self.run_import()
        tx = Transaction.objects.get(user=self.user, counterparty="REWE Markt")
        self.assertIsNone(tx.category)

    def test_priority_decides_between_rules(self):
        other = Category.objects.create(user=self.user, name="Other", kind=Category.Kind.EXPENSE)
        CategoryRule.objects.create(
            user=self.user, pattern="REWE", category=other, priority=50
        )
        CategoryRule.objects.create(
            user=self.user, pattern="REWE", category=self.food, priority=10
        )
        self.run_import()
        tx = Transaction.objects.get(user=self.user, counterparty="REWE Markt")
        self.assertEqual(tx.category, self.food)

    def test_broken_rows_are_skipped_not_fatal(self):
        broken = STATEMENT + "32.13.2026;Shop;;-1,00\r\n"
        parsed = self.importer.parse_file(broken.encode("cp1252"))
        batch, rows = self.importer.run_import(
            self.user, self.account, parsed, self.mapping, "broken.csv"
        )
        self.assertEqual(batch.rows_imported, 5)
        self.assertEqual(sum(1 for row in rows if not row.is_valid), 1)

    def test_expense_only_mode(self):
        mapping = self.importer.Mapping(date=0, counterparty=1, note=2, amount=3, sign_mode="expense")
        self.importer.run_import(self.user, self.account, self.parsed, mapping, "s.csv")
        self.assertEqual(
            Transaction.objects.filter(user=self.user, kind=Transaction.Kind.INCOME).count(), 0
        )


class ImportViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("wizard", password="pass12345")
        self.account = Account.objects.create(user=self.user, name="Card")
        self.client.login(username="wizard", password="pass12345")

    def upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return self.client.post(
            reverse("import_upload"),
            {
                "account": self.account.pk,
                "file": SimpleUploadedFile("statement.csv", STATEMENT.encode("cp1252"), "text/csv"),
            },
        )

    def test_mapping_requires_uploaded_file(self):
        response = self.client.get(reverse("import_mapping"))
        self.assertRedirects(response, reverse("import_upload"))

    def test_full_wizard(self):
        self.assertRedirects(self.upload(), reverse("import_mapping"))

        preview = self.client.get(reverse("import_mapping"))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "will be imported")

        response = self.client.post(
            reverse("import_mapping"),
            {"date": "0", "counterparty": "1", "note": "2", "amount": "3",
             "sign_mode": "signed", "commit": "1"},
            follow=True,
        )
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 5)
        self.assertContains(response, "Imported transactions: 5")

    def test_non_csv_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            reverse("import_upload"),
            {"account": self.account.pk,
             "file": SimpleUploadedFile("photo.png", b"\x89PNG\r\n", "image/png")},
        )
        self.assertContains(response, "A CSV file is required")

    def test_apply_rules_to_existing(self):
        food = Category.objects.create(user=self.user, name="Groceries")
        Transaction.objects.create(
            user=self.user, account=self.account, kind=Transaction.Kind.EXPENSE,
            amount=Decimal("10.00"), date=date(2026, 4, 1), counterparty="REWE Markt",
        )
        CategoryRule.objects.create(user=self.user, pattern="REWE", category=food)
        self.client.post(reverse("rules_apply"))
        self.assertEqual(Transaction.objects.get(user=self.user).category, food)


class RecurringDetectionTests(TestCase):
    """Recurring payment detection: what counts as a subscription and what does not."""

    def setUp(self):
        from . import recurring

        self.recurring = recurring
        self.user = User.objects.create_user("subscriber", password="pass12345")
        self.account = Account.objects.create(
            user=self.user, name="Card", initial_balance=Decimal("1000.00")
        )
        self.today = date(2026, 6, 15)

    def add(self, when, amount, counterparty, kind=Transaction.Kind.EXPENSE):
        return Transaction.objects.create(
            user=self.user, account=self.account, kind=kind,
            amount=Decimal(amount), date=when, counterparty=counterparty,
        )

    def test_monthly_subscription_is_found(self):
        for month in (3, 4, 5, 6):
            self.add(date(2026, month, 5), "12.99", "Netflix")
        found = self.recurring.detect(self.user, self.today)
        self.assertEqual(len(found), 1)
        candidate = found[0]
        self.assertEqual(candidate.counterparty, "Netflix")
        self.assertEqual(candidate.avg_amount, Decimal("12.99"))
        self.assertEqual(candidate.day_of_month, 5)
        self.assertEqual(candidate.period_days, 30)
        self.assertEqual(candidate.occurrences, 4)

    def test_two_occurrences_are_not_enough(self):
        self.add(date(2026, 5, 5), "12.99", "Spotify")
        self.add(date(2026, 6, 5), "12.99", "Spotify")
        self.assertEqual(self.recurring.detect(self.user, self.today), [])

    def test_random_purchases_are_not_recurring(self):
        for day, amount in ((3, "12.00"), (11, "47.00"), (12, "5.00"), (28, "31.00")):
            self.add(date(2026, 6, day), amount, "REWE")
        self.assertEqual(self.recurring.detect(self.user, self.today), [])

    def test_amount_jumping_too_much_is_rejected(self):
        for month, amount in ((3, "10.00"), (4, "90.00"), (5, "15.00"), (6, "70.00")):
            self.add(date(2026, month, 7), amount, "Stromanbieter")
        self.assertEqual(self.recurring.detect(self.user, self.today), [])

    def test_salary_is_found_as_income(self):
        for month in (3, 4, 5, 6):
            self.add(date(2026, month, 1), "2200.00", "Arbeitgeber GmbH", Transaction.Kind.INCOME)
        found = self.recurring.detect(self.user, self.today)
        self.assertEqual(found[0].kind, Transaction.Kind.INCOME)

    def test_sync_creates_and_updates(self):
        for month in (3, 4, 5, 6):
            self.add(date(2026, month, 5), "12.99", "Netflix")
        first = self.recurring.sync(self.user, self.today)
        self.assertEqual((first["created"], first["updated"]), (1, 0))
        second = self.recurring.sync(self.user, self.today)
        self.assertEqual((second["created"], second["updated"]), (0, 1))
        self.assertEqual(RecurringPayment.objects.filter(user=self.user).count(), 1)

    def test_dismissed_payment_is_not_resurrected(self):
        for month in (3, 4, 5, 6):
            self.add(date(2026, month, 5), "12.99", "Netflix")
        self.recurring.sync(self.user, self.today)
        payment = RecurringPayment.objects.get(user=self.user)
        payment.is_dismissed = True
        payment.avg_amount = Decimal("1.00")
        payment.save()

        self.recurring.sync(self.user, self.today)
        payment.refresh_from_db()
        self.assertTrue(payment.is_dismissed)
        self.assertEqual(payment.avg_amount, Decimal("1.00"))


class ForecastTests(TestCase):
    def setUp(self):
        from . import recurring

        self.recurring = recurring
        self.user = User.objects.create_user("planner", password="pass12345")
        self.account = Account.objects.create(
            user=self.user, name="Card", initial_balance=Decimal("1000.00")
        )
        self.today = date(2026, 6, 10)

    def payment(self, counterparty, amount, day, kind=Transaction.Kind.EXPENSE, **extra):
        return RecurringPayment.objects.create(
            user=self.user, counterparty=counterparty, kind=kind,
            avg_amount=Decimal(amount), day_of_month=day, period_days=30,
            occurrences=4, last_seen=date(2026, 5, day), **extra,
        )

    def test_future_expense_lowers_forecast(self):
        self.payment("Vermieter", "750.00", 25)
        result = self.recurring.forecast(self.user, self.today)
        self.assertEqual(result["balance_now"], Decimal("1000.00"))
        self.assertEqual(result["expected_expense"], Decimal("750.00"))
        self.assertEqual(result["forecast"], Decimal("250.00"))
        self.assertEqual(len(result["upcoming"]), 1)

    def test_future_income_raises_forecast(self):
        self.payment("Arbeitgeber", "2200.00", 28, kind=Transaction.Kind.INCOME)
        result = self.recurring.forecast(self.user, self.today)
        self.assertEqual(result["forecast"], Decimal("3200.00"))

    def test_payment_already_passed_this_month_is_not_counted_twice(self):
        """Netflix was already charged on the 5th, it must not be subtracted again."""
        self.payment("Netflix", "12.99", 5)
        Transaction.objects.create(
            user=self.user, account=self.account, kind=Transaction.Kind.EXPENSE,
            amount=Decimal("12.99"), date=date(2026, 6, 5), counterparty="Netflix",
        )
        result = self.recurring.forecast(self.user, self.today)
        self.assertEqual(result["expected_expense"], Decimal("0.00"))

    def test_dismissed_payment_is_ignored(self):
        self.payment("Vermieter", "750.00", 25, is_dismissed=True)
        result = self.recurring.forecast(self.user, self.today)
        self.assertEqual(result["expected_expense"], Decimal("0.00"))

    def test_scan_and_decision_views(self):
        for month in (3, 4, 5, 6):
            Transaction.objects.create(
                user=self.user, account=self.account, kind=Transaction.Kind.EXPENSE,
                amount=Decimal("12.99"), date=date(2026, month, 5), counterparty="Netflix",
            )
        self.client.login(username="planner", password="pass12345")
        self.client.post(reverse("recurring_scan"))
        payment = RecurringPayment.objects.get(user=self.user, counterparty="Netflix")

        self.client.post(reverse("recurring_decision", args=[payment.pk]), {"decision": "confirm"})
        payment.refresh_from_db()
        self.assertTrue(payment.is_confirmed)

        self.client.post(reverse("recurring_decision", args=[payment.pk]), {"decision": "dismiss"})
        payment.refresh_from_db()
        self.assertTrue(payment.is_dismissed)

    def test_stranger_cannot_decide_on_foreign_payment(self):
        payment = self.payment("Vermieter", "750.00", 25)
        User.objects.create_user("nosy", password="pass12345")
        self.client.login(username="nosy", password="pass12345")
        response = self.client.post(
            reverse("recurring_decision", args=[payment.pk]), {"decision": "dismiss"}
        )
        self.assertEqual(response.status_code, 404)


class BudgetCopyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("copier", password="pass12345")
        self.category = Category.objects.create(user=self.user, name="Groceries")
        self.this_month = date.today().replace(day=1)
        Budget.objects.create(
            user=self.user, category=self.category, month=self.this_month, limit=Decimal("300.00")
        )
        self.client.login(username="copier", password="pass12345")

    def next_month(self):
        if self.this_month.month == 12:
            return date(self.this_month.year + 1, 1, 1)
        return date(self.this_month.year, self.this_month.month + 1, 1)

    def test_copy_creates_next_month_budgets(self):
        self.client.post(reverse("budget_copy"))
        budget = Budget.objects.get(user=self.user, month=self.next_month())
        self.assertEqual(budget.limit, Decimal("300.00"))

    def test_copy_twice_does_not_duplicate(self):
        self.client.post(reverse("budget_copy"))
        self.client.post(reverse("budget_copy"))
        self.assertEqual(Budget.objects.filter(user=self.user, month=self.next_month()).count(), 1)
