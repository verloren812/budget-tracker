"""CSV bank statement parsing.

This lives in its own module because real statements are messy:

* encoding - German banks export cp1252 or UTF-8 with BOM, not plain UTF-8;
* delimiter - ";" in German banks, "," in international services;
* date format - 31.12.2026, 2026-12-31, 31/12/2026;
* amount format - "1.234,56" and "1,234.56" mean the same number;
* uploading the same file twice must not create duplicates.
"""

import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
DELIMITERS = (";", ",", "\t", "|")
DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y", "%m/%d/%Y")

# Hints used to guess which column is which, for German and English headers.
COLUMN_HINTS = {
    "date": ("buchungstag", "valuta", "datum", "date", "wertstellung"),
    "amount": ("betrag", "amount", "umsatz", "wert"),
    "counterparty": (
        "auftraggeber", "empf", "beguenstigter", "zahlungspflichtiger",
        "name", "payee", "counterparty",
    ),
    "note": (
        "verwendungszweck", "buchungstext", "description", "note", "reference",
    ),
}


class ImportError_(Exception):
    """A parsing error that is safe to show to the user."""


@dataclass
class ParsedFile:
    header: list[str]
    rows: list[list[str]]
    delimiter: str
    encoding: str

    @property
    def columns(self) -> list[tuple[int, str]]:
        """Columns for the select widgets: (index, label)."""
        return [(index, name or f"Column {index + 1}") for index, name in enumerate(self.header)]


@dataclass
class PreviewRow:
    number: int
    date: date | None
    amount: Decimal | None
    kind: str
    counterparty: str
    note: str
    category = None  # Category instance picked by a rule
    category_name: str = ""
    hash: str = ""
    is_duplicate: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


# ------------------------------------------------------------------ reading the file


def decode(raw: bytes) -> tuple[str, str]:
    """Return (text, encoding name). Tries every encoding instead of guessing from the first bytes."""
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ImportError_("Could not detect the file encoding.")


def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:5])
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        # Fallback: take the character that appears most often in the first line.
        first_line = sample.splitlines()[0] if sample else ""
        counts = {d: first_line.count(d) for d in DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] else ";"


def parse_file(raw: bytes) -> ParsedFile:
    text, encoding = decode(raw)
    if not text.strip():
        raise ImportError_("The file is empty.")

    delimiter = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ImportError_("The file contains no data rows.")

    header, data = rows[0], rows[1:]
    if not data:
        raise ImportError_("The file only contains a header, there are no transaction rows.")

    width = max(len(row) for row in rows)
    header = [cell.strip() for cell in header] + [""] * (width - len(header))
    data = [row + [""] * (width - len(row)) for row in data]
    return ParsedFile(header=header, rows=data, delimiter=delimiter, encoding=encoding)


def guess_columns(header: list[str]) -> dict[str, int | None]:
    """Try to map the file columns onto the required fields by their header names."""
    guessed: dict[str, int | None] = {key: None for key in COLUMN_HINTS}
    for index, name in enumerate(header):
        lowered = name.strip().lower()
        if not lowered:
            continue
        for key, hints in COLUMN_HINTS.items():
            if guessed[key] is None and any(hint in lowered for hint in hints):
                guessed[key] = index
    return guessed


# ------------------------------------------------------------------ parsing values


def parse_date(value: str) -> date:
    value = value.strip()
    if not value:
        raise ImportError_("empty date")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ImportError_(f"could not read the date “{value}”")


def parse_amount(value: str) -> Decimal:
    """Convert "1.234,56", "1,234.56", "-12,50 EUR" and "+1 200.00" into a Decimal."""
    raw = value.strip()
    if not raw:
        raise ImportError_("empty amount")

    cleaned = re.sub(r"[^\d,.\-+]", "", raw.replace("\xa0", ""))
    if not cleaned or cleaned in {"-", "+"}:
        raise ImportError_(f"could not read the amount “{value}”")

    has_comma, has_dot = "," in cleaned, "." in cleaned
    if has_comma and has_dot:
        # The decimal separator is whichever of the two comes last.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif has_comma:
        cleaned = cleaned.replace(",", ".")
    elif has_dot:
        # "1.234" is a thousands separator, "12.34" is a decimal part.
        if re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})+", cleaned):
            cleaned = cleaned.replace(".", "")

    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ImportError_(f"could not read the amount “{value}”")


# ------------------------------------------------------------------ deduplication


def dedup_base(account_id: int, when: date, amount: Decimal, counterparty: str, note: str) -> str:
    payload = f"{account_id}|{when:%Y-%m-%d}|{amount}|{counterparty.strip().lower()}|{note.strip().lower()[:120]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:48]


def dedup_hash(base: str, occurrence: int) -> str:
    """Append an occurrence number to the hash.

    Two identical purchases on the same day are two real transactions, not a
    duplicate, so identical rows inside one file are numbered 0, 1, 2, ...
    Re-uploading the same file reproduces the same numbering, and both rows
    are then recognised as duplicates.
    """
    return f"{base}:{occurrence}"


# ------------------------------------------------------------------ preview and write


@dataclass
class Mapping:
    """Which column of the file holds which field."""

    date: int
    amount: int
    counterparty: int | None = None
    note: int | None = None
    sign_mode: str = "signed"  # signed | expense | income

    @classmethod
    def from_dict(cls, data: dict) -> "Mapping":
        return cls(
            date=int(data["date"]),
            amount=int(data["amount"]),
            counterparty=None if data.get("counterparty") in (None, "") else int(data["counterparty"]),
            note=None if data.get("note") in (None, "") else int(data["note"]),
            sign_mode=data.get("sign_mode", "signed"),
        )

    def as_dict(self) -> dict:
        return asdict(self)


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def build_preview(user, account, parsed: ParsedFile, mapping: Mapping, rules=None, limit=None):
    """Build the rows shown in the preview and later written to the database.

    Saves nothing: it validates the parsing, derives the transaction type,
    applies the category rules and flags duplicates.
    """
    from .models import Transaction  # local import keeps the parser free of model imports

    rules = list(rules if rules is not None else [])
    rows: list[PreviewRow] = []
    seen: dict[str, int] = {}

    source_rows = parsed.rows if limit is None else parsed.rows[:limit]
    for number, raw_row in enumerate(source_rows, start=1):
        preview = PreviewRow(
            number=number, date=None, amount=None, kind="",
            counterparty=_cell(raw_row, mapping.counterparty),
            note=_cell(raw_row, mapping.note),
        )

        try:
            preview.date = parse_date(_cell(raw_row, mapping.date))
        except ImportError_ as exc:
            preview.errors.append(str(exc))

        try:
            value = parse_amount(_cell(raw_row, mapping.amount))
        except ImportError_ as exc:
            preview.errors.append(str(exc))
            value = None

        if value is not None:
            if mapping.sign_mode == "signed":
                preview.kind = (
                    Transaction.Kind.INCOME if value > 0 else Transaction.Kind.EXPENSE
                )
            elif mapping.sign_mode == "income":
                preview.kind = Transaction.Kind.INCOME
            else:
                preview.kind = Transaction.Kind.EXPENSE
            preview.amount = abs(value)
            if preview.amount == 0:
                preview.errors.append("zero amount")

        if preview.is_valid:
            base = dedup_base(
                account.pk, preview.date, preview.amount, preview.counterparty, preview.note
            )
            occurrence = seen.get(base, 0)
            seen[base] = occurrence + 1
            preview.hash = dedup_hash(base, occurrence)
            preview.is_duplicate = Transaction.objects.filter(
                user=user, dedup_hash=preview.hash
            ).exists()

            category = match_category(rules, preview.counterparty, preview.note)
            # A rule only applies when the category type matches the transaction
            # type, so a "Salary" category never sticks to an expense.
            if category is not None and category.kind == preview.kind:
                preview.category = category
                preview.category_name = str(category)

        rows.append(preview)
    return rows


def match_category(rules, counterparty: str, note: str):
    """The first matching rule, rules being ordered by priority."""
    for rule in rules:
        if rule.is_active and rule.matches(counterparty, note):
            return rule.category
    return None


def run_import(user, account, parsed: ParsedFile, mapping: Mapping, file_name: str):
    """Write the transactions to the database. Returns the created ImportBatch."""
    from django.db import transaction as db_transaction

    from .models import CategoryRule, ImportBatch, Transaction

    rules = list(
        CategoryRule.objects.filter(user=user, is_active=True).select_related("category")
    )
    rows = build_preview(user, account, parsed, mapping, rules=rules)

    with db_transaction.atomic():
        batch = ImportBatch.objects.create(
            user=user, account=account, file_name=file_name, rows_total=len(rows)
        )
        to_create = []
        for row in rows:
            if not row.is_valid or row.is_duplicate:
                continue
            to_create.append(
                Transaction(
                    user=user,
                    account=account,
                    category=row.category,
                    kind=row.kind,
                    amount=row.amount,
                    date=row.date,
                    counterparty=row.counterparty[:255],
                    note=row.note,
                    source=Transaction.Source.IMPORT,
                    import_batch=batch,
                    dedup_hash=row.hash,
                )
            )
        Transaction.objects.bulk_create(to_create)

        batch.rows_imported = len(to_create)
        batch.rows_skipped = sum(1 for row in rows if row.is_duplicate)
        batch.save(update_fields=["rows_imported", "rows_skipped"])

    return batch, rows
