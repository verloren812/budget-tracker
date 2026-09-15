# Budget Tracker

A personal finance web app built with Django: accounts, income and expense tracking,
monthly budgets, reports with charts, bank statement import with duplicate detection,
and automatic discovery of recurring payments with an end-of-month balance forecast.

**Stack:** Python 3.12, Django 5, PostgreSQL (SQLite for local runs), Bootstrap 5, Chart.js, Docker Compose.

## Features

- **Accounts and transactions** — income, expenses and transfers between your own accounts.
  Account balance is never stored as a field: it is derived from transactions, so it cannot
  drift out of sync.
- **Categories and budgets** — monthly spending limits per category with progress bars,
  copied to the next month with one click.
- **Transaction list** — filters by period, account, category, type and free text, with
  pagination. Totals are calculated over the whole filtered set, not the current page.
- **Reports** — income and expense per period, average monthly spending, category breakdown
  with shares, month-over-month dynamics, top counterparties. Charts rendered with Chart.js.
- **CSV export** — exports exactly the current filtered selection, using `;` as the separator
  and a decimal comma, so the file opens in a German Excel without the import wizard.
- **Bank statement import** — a three step wizard: upload, column mapping with preview, commit.
  Encoding, delimiter and column meaning are detected automatically.
- **Categorization rules** — user defined rules applied during import and on demand to
  uncategorized transactions.
- **Recurring payments** — subscriptions, rent and salary are found by analysing history;
  confirmed ones feed the end-of-month balance forecast.

## Quick start

```bash
python -m venv venv
venv\Scripts\activate           # Windows
source venv/bin/activate        # Linux / macOS
pip install -r requirements.txt

python manage.py migrate
python manage.py demo_data      # 12 months of demo data, login demo / demo12345
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/ for the app and http://127.0.0.1:8000/admin/ for the admin site.

SQLite is used by default. To switch to PostgreSQL, copy `.env.example` to `.env`
and fill in the `DB_*` variables.

## Run with Docker

```bash
docker compose up --build
docker compose exec web python manage.py demo_data
```

This starts the app together with PostgreSQL; migrations run on container start.

## Tests

```bash
python manage.py test
```

75 tests covering balance calculation, transfers, decimal arithmetic, import deduplication,
budgets, access control, transaction form validation, filters, report aggregations,
CSV export, the statement parser and the full import scenario.

## Project layout

```
config/     settings and root urls
accounts/   custom user model, sign up
finance/    accounts, categories, transactions, budgets, import, recurring payments
  importer.py    statement parsing and deduplication
  recurring.py   recurring payment detection and balance forecast
  reports.py     aggregations for the reports page
templates/  Bootstrap 5 templates
docs/       sample bank statement for testing the import
```

## Statement import

`docs/sample_statement.csv` is a sample file in German bank format: cp1252 encoding,
`;` separator, dates as `31.12.2026`, amounts as `-45,20`.

How it works:

1. The file is decoded by trying encodings in order (`utf-8-sig`, `utf-8`, `cp1252`, `latin-1`);
   the delimiter is detected with `csv.Sniffer`, falling back to character frequency.
2. Columns are guessed from the header row, which recognises both German and English
   names (`Buchungstag`, `Verwendungszweck`, `Empfänger`, `Betrag`, `date`, `amount`).
3. The preview shows what will be imported, what already exists in the database, and which
   rows failed to parse — before anything is written.
4. On commit, categorization rules are applied. A rule pointing at an income category can
   never attach itself to an expense.

Amounts are parsed independently of locale: `1.234,56` and `1,234.56` both mean the same
number, decided by whichever separator comes last, while `1.234` is recognised as thousands.

## Recurring payments and forecast

A counterparty is treated as recurring when all three conditions hold:

- it appears at least **three** times within the last 12 months;
- the median interval between transactions falls into a weekly (6–8 days), monthly (26–35)
  or quarterly (85–95) window;
- amounts stay within **±25 %** of the median.

All thresholds are constants at the top of `finance/recurring.py`. Detected payments can be
confirmed or dismissed; dismissed ones do not come back on the next scan.

Forecast = current balance + expected recurring income − expected recurring expenses.
Only monthly payments that have not occurred yet this month are counted: if Netflix was
already charged on the 5th, it is not subtracted a second time.

## Design decisions

- **Money is always `Decimal`**, never `float` — otherwise cents drift.
- **Account balance is derived, not stored** — it is computed from transactions, which makes
  it impossible to desynchronise.
- **Amounts are always positive**; direction is defined by the `kind` field
  (income / expense / transfer).
- **Import deduplication** relies on a unique index over `(user, dedup_hash)`, so re-importing
  the same file creates no duplicates.
- **Occurrence number inside the dedup hash** — two identical purchases on the same day are
  two real transactions, not a duplicate, so identical rows within a file are numbered
  `0`, `1`, `2`… Re-importing the same file reproduces that numbering and both rows are
  correctly recognised as duplicates.
- **Access control is applied at queryset level** (`filter(user=request.user)`) rather than
  checked after fetching an object, so foreign records return 404 instead of leaking.

## Possible next steps

1. Screenshots in the README and an "about" page
2. Multi-currency support (currently EUR only, deliberately)
3. Creating categorization rules directly from the list of uncategorized transactions

## License

MIT — see [LICENSE](LICENSE).
