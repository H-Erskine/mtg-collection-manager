"""Parse and validate ManaBox collection-export CSVs.

ManaBox's export has 16 columns; only 6 map onto our owned_cards schema
(Name, Set code, Collector number, Foil, Quantity, Scryfall ID). Everything
else (Set name, Rarity, ManaBox ID, Purchase price, Misprint, Altered,
Condition, Language, Purchase price currency, Added) is read from the file
and deliberately discarded -- it is never written anywhere.
"""

import csv
import io
from dataclasses import dataclass

REQUIRED_COLUMNS = {"Name", "Set code", "Collector number", "Foil", "Quantity", "Scryfall ID"}
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_ROWS = 20_000


class ManaboxImportError(ValueError):
    """Raised when a ManaBox CSV fails validation."""


@dataclass
class ManaboxRow:
    name: str
    set_code: str
    collector_number: str
    foil: bool
    quantity: int
    scryfall_id: str


def parse_manabox_csv(
    csv_text: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> list[ManaboxRow]:
    """Parse a ManaBox CSV export into validated rows, or raise ManaboxImportError."""
    if len(csv_text.encode("utf-8")) > max_bytes:
        raise ManaboxImportError(f"CSV exceeds maximum size of {max_bytes} bytes")

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ManaboxImportError("CSV has no header row")

    missing = REQUIRED_COLUMNS - set(reader.fieldnames)
    if missing:
        raise ManaboxImportError(f"CSV missing required column(s): {', '.join(sorted(missing))}")

    rows: list[ManaboxRow] = []
    for line_num, raw in enumerate(reader, start=2):  # line 1 is the header
        if line_num - 1 > max_rows:
            raise ManaboxImportError(f"CSV exceeds maximum of {max_rows} rows")

        name = (raw.get("Name") or "").strip()
        if not name:
            raise ManaboxImportError(f"Row {line_num}: Name is required")

        quantity_raw = (raw.get("Quantity") or "").strip()
        try:
            quantity = int(quantity_raw)
        except ValueError:
            raise ManaboxImportError(f"Row {line_num}: Quantity must be an integer, got {quantity_raw!r}")
        if quantity <= 0:
            raise ManaboxImportError(f"Row {line_num}: Quantity must be positive, got {quantity}")

        foil_raw = (raw.get("Foil") or "").strip().lower()
        foil = foil_raw not in ("", "normal")

        rows.append(ManaboxRow(
            name=name,
            set_code=(raw.get("Set code") or "").strip().lower(),
            collector_number=(raw.get("Collector number") or "").strip(),
            foil=foil,
            quantity=quantity,
            scryfall_id=(raw.get("Scryfall ID") or "").strip(),
        ))

    return rows
