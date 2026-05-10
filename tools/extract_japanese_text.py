from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path


WORD_SIZE = 4
TAIL_LENGTH = 8
MIN_MATCH_LENGTH = 3

LITERAL_RUN_LENGTH_TABLE = [4, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0]
MATCH_LOOKUP_TABLE = [
    (0xFF, 2, 0, 0, 1),
    (0xFFFF, 2, 0, 0, 2),
    (0xFFFF, 6, 15, 2, 2),
    (0xFFFFFF, 8, 31, 3, 3),
    (0xFF, 2, 0, 0, 1),
    (0xFFFF, 2, 0, 0, 2),
    (0xFFFF, 6, 15, 2, 2),
    (0xFFFFFFFF, 11, 255, 3, 4),
]

JAPANESE_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]"
)
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MdbEntry:
    archive: Path
    name: str
    ext: str
    offset: int
    size: int
    compressed_size: int

    @property
    def logical_name(self) -> str:
        return f"{self.name}.{self.ext}" if self.ext else self.name


@dataclass(frozen=True)
class TextValue:
    table: str
    row_number: int
    rowid: int | None
    column: str
    text: str


def fast_read(source: bytes | bytearray, offset: int, size: int) -> int:
    if size in (3, 4):
        return int.from_bytes(source[offset : offset + 4], "little")
    if size == 2:
        return int.from_bytes(source[offset : offset + 2], "little")
    if size == 1:
        return source[offset]
    return 0


def fast_write(destination: bytearray, offset: int, word: int, size: int) -> None:
    if size in (3, 4):
        destination[offset : offset + 4] = word.to_bytes(4, "little")
    elif size == 2:
        destination[offset : offset + 2] = word.to_bytes(2, "little")
    elif size == 1:
        destination[offset] = word & 0xFF


def decode_doboz_header(source: bytes) -> tuple[bool, int, int, int]:
    if not source:
        raise ValueError("empty doboz block")

    attributes = source[0]
    version = attributes & 7
    size_coded_size = ((attributes >> 3) & 7) + 1
    header_size = 1 + 2 * size_coded_size

    if version != 0:
        raise ValueError(f"unsupported doboz version: {version}")
    if size_coded_size not in (1, 2, 4, 8):
        raise ValueError(f"invalid doboz size field width: {size_coded_size}")
    if len(source) < header_size:
        raise ValueError("truncated doboz header")

    is_stored = (attributes & 128) != 0
    uncompressed_size = int.from_bytes(source[1 : 1 + size_coded_size], "little")
    compressed_size = int.from_bytes(
        source[1 + size_coded_size : 1 + 2 * size_coded_size], "little"
    )
    return is_stored, uncompressed_size, compressed_size, header_size


def decompress_doboz(source: bytes) -> bytes:
    is_stored, uncompressed_size, compressed_size, input_offset = decode_doboz_header(
        source
    )

    if len(source) < compressed_size:
        raise ValueError(
            f"truncated doboz block: {len(source)} bytes < {compressed_size} bytes"
        )
    if is_stored:
        return source[input_offset : input_offset + uncompressed_size]

    output = bytearray(uncompressed_size)
    output_offset = 0
    input_end = compressed_size
    output_end = uncompressed_size
    output_tail = output_end - TAIL_LENGTH if uncompressed_size > TAIL_LENGTH else 0
    control_word = 1

    while True:
        if input_offset + 2 * WORD_SIZE > input_end:
            raise ValueError("corrupted doboz block")

        if control_word == 1:
            control_word = fast_read(source, input_offset, WORD_SIZE)
            input_offset += WORD_SIZE

        if (control_word & 1) == 0:
            if output_offset < output_tail:
                fast_write(
                    output,
                    output_offset,
                    fast_read(source, input_offset, WORD_SIZE),
                    WORD_SIZE,
                )
                run_length = LITERAL_RUN_LENGTH_TABLE[control_word & 0xF]
                input_offset += run_length
                output_offset += run_length
                control_word >>= run_length
                continue

            while output_offset < output_end:
                if input_offset + WORD_SIZE + 1 > input_end:
                    raise ValueError("corrupted doboz tail")
                if control_word == 1:
                    control_word = fast_read(source, input_offset, WORD_SIZE)
                    input_offset += WORD_SIZE

                output[output_offset] = source[input_offset]
                output_offset += 1
                input_offset += 1
                control_word >>= 1
            return bytes(output)

        word = fast_read(source, input_offset, WORD_SIZE)
        table_index = word & 7
        mask, offset_shift, length_mask, length_shift, match_size = MATCH_LOOKUP_TABLE[
            table_index
        ]
        match_offset = (word & mask) >> offset_shift
        match_length = ((word >> length_shift) & length_mask) + MIN_MATCH_LENGTH
        input_offset += match_size

        match_source = output_offset - match_offset
        if match_source < 0 or output_offset + match_length > output_tail:
            raise ValueError("corrupted doboz match")

        index = 0
        if match_offset < WORD_SIZE:
            while index < 3:
                fast_write(
                    output,
                    output_offset + index,
                    fast_read(output, match_source + index, 1),
                    1,
                )
                index += 1
            match_source -= 2 + (match_offset & 1)

        while True:
            fast_write(
                output,
                output_offset + index,
                fast_read(output, match_source + index, WORD_SIZE),
                WORD_SIZE,
            )
            index += WORD_SIZE
            if index >= match_length:
                break

        output_offset += match_length
        control_word >>= 1


def parse_mdb1_entries(archive: Path) -> list[MdbEntry]:
    data = archive.read_bytes()
    if data[:4] != b"MDB1":
        return []

    entry_count, name_count = struct.unpack_from("<HH", data, 4)
    data_count = struct.unpack_from("<I", data, 8)[0]
    data_base = struct.unpack_from("<I", data, 12)[0]

    offset = 20
    file_entries = []
    for _ in range(entry_count):
        file_entries.append(struct.unpack_from("<HHHH", data, offset))
        offset += 8

    names = []
    for _ in range(name_count):
        ext = data[offset : offset + 4].decode("ascii", "replace").rstrip(" \0")
        name = (
            data[offset + 4 : offset + 64]
            .split(b"\0", 1)[0]
            .decode("ascii", "replace")
        )
        names.append((name, ext))
        offset += 64

    data_entries = []
    for _ in range(data_count):
        relative_offset, size, compressed_size = struct.unpack_from("<III", data, offset)
        data_entries.append((data_base + relative_offset, size, compressed_size))
        offset += 12

    entries = []
    for index, file_entry in enumerate(file_entries):
        data_index = file_entry[1]
        if data_index == 0xFFFF:
            continue
        if index >= len(names) or data_index >= len(data_entries):
            continue

        name, ext = names[index]
        data_offset, size, compressed_size = data_entries[data_index]
        entries.append(
            MdbEntry(
                archive=archive,
                name=name,
                ext=ext,
                offset=data_offset,
                size=size,
                compressed_size=compressed_size,
            )
        )
    return entries


def extract_entry(entry: MdbEntry) -> bytes:
    with entry.archive.open("rb") as handle:
        handle.seek(entry.offset)
        payload = handle.read(entry.compressed_size)

    if entry.size == entry.compressed_size:
        return payload
    return decompress_doboz(payload)


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def safe_component(value: str, fallback: str) -> str:
    value = value or fallback
    for char in '<>:"/\\|?*':
        value = value.replace(char, "_")
    value = value.strip(" .")
    if not value:
        value = fallback
    return value[:180]


def display_entry_name(entry: MdbEntry) -> str:
    if entry.name:
        return entry.logical_name
    if entry.ext:
        return f"_unnamed.{entry.ext}"
    return "_unnamed"


def has_japanese(value: str) -> bool:
    return bool(JAPANESE_RE.search(value))


def flatten_text(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value).strip()


def iter_sqlite_text_rows(db_bytes: bytes) -> tuple[int, list[TextValue]]:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite") as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(db_bytes)

    rows: list[TextValue] = []
    scanned_tables = 0
    try:
        connection = sqlite3.connect(str(temp_path))
        try:
            connection.execute("PRAGMA query_only = ON")
            tables = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()

            for (table_name,) in tables:
                scanned_tables += 1
                has_rowid = True
                try:
                    cursor = connection.execute(
                        f"SELECT _rowid_, * FROM {quote_identifier(table_name)}"
                    )
                    columns = [description[0] for description in cursor.description][1:]
                except sqlite3.DatabaseError:
                    has_rowid = False
                    cursor = connection.execute(
                        f"SELECT * FROM {quote_identifier(table_name)}"
                    )
                    columns = [description[0] for description in cursor.description]

                for row_number, row in enumerate(cursor, start=1):
                    if has_rowid:
                        rowid = row[0]
                        values = row[1:]
                    else:
                        rowid = None
                        values = row

                    for column, value in zip(columns, values):
                        if isinstance(value, str) and has_japanese(value):
                            text = flatten_text(value)
                            if text:
                                rows.append(
                                    TextValue(
                                        table=table_name,
                                        row_number=row_number,
                                        rowid=rowid if isinstance(rowid, int) else None,
                                        column=column,
                                        text=text,
                                    )
                                )
        finally:
            connection.close()
    finally:
        temp_path.unlink(missing_ok=True)

    return scanned_tables, rows


def write_outputs(
    output_dir: Path,
    all_rows: list[tuple[str, str, str, int, int | None, str, str]],
    summary_rows: list[tuple[str, str, int, int]],
    split_rows: dict[tuple[str, str, str], list[TextValue]],
    manifest_rows: list[tuple[str, str, str, str]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    tsv_path = output_dir / "japanese_text.tsv"
    with tsv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "archive",
                "entry",
                "table",
                "row_number",
                "rowid",
                "column",
                "original_text",
                "translated_text",
            ]
        )
        for row in all_rows:
            writer.writerow([*row, ""])

    unique_path = output_dir / "japanese_text_unique.txt"
    seen = set()
    with unique_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in all_rows:
            text = row[-1]
            if text in seen:
                continue
            seen.add(text)
            handle.write(text + "\n")

    summary_path = output_dir / "summary.tsv"
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["archive", "entry", "tables_scanned", "japanese_values"])
        writer.writerows(summary_rows)

    split_root = output_dir / "by_original_file"
    for (archive_name, entry_name, table_name), rows in split_rows.items():
        archive_dir = split_root / safe_component(archive_name, "archive")
        entry_dir = archive_dir / safe_component(entry_name, "entry")
        entry_dir.mkdir(parents=True, exist_ok=True)
        table_path = entry_dir / f"{safe_component(table_name, 'table')}.tsv"
        with table_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(
                ["row_number", "rowid", "column", "original_text", "translated_text"]
            )
            for row in rows:
                writer.writerow(
                    [row.row_number, row.rowid or "", row.column, row.text, ""]
                )

    manifest_path = split_root / "manifest.tsv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["archive", "entry", "table", "split_tsv"])
        writer.writerows(manifest_rows)


def write_database_dump(output_dir: Path, entry: MdbEntry, db_bytes: bytes) -> Path:
    db_root = output_dir / "databases"
    archive_dir = db_root / safe_component(entry.archive.name, "archive")
    archive_dir.mkdir(parents=True, exist_ok=True)
    db_path = archive_dir / safe_component(display_entry_name(entry), "entry.db")
    db_path.write_bytes(db_bytes)
    return db_path


def run(archives: list[Path], output_dir: Path) -> None:
    all_rows: list[tuple[str, str, str, int, int | None, str, str]] = []
    summary_rows: list[tuple[str, str, int, int]] = []
    split_rows: dict[tuple[str, str, str], list[TextValue]] = {}
    manifest_rows: list[tuple[str, str, str, str]] = []

    for archive in archives:
        db_entries = [entry for entry in parse_mdb1_entries(archive) if entry.ext == "db"]
        if not db_entries:
            continue

        print(f"{archive.name}: {len(db_entries)} db entr{'y' if len(db_entries) == 1 else 'ies'}")
        for entry in db_entries:
            entry_name = display_entry_name(entry)
            print(f"  extracting {entry_name} ({entry.size} bytes)")
            db_bytes = extract_entry(entry)
            db_path = write_database_dump(output_dir, entry, db_bytes)
            print(f"  wrote database copy: {db_path}")
            tables_scanned, text_rows = iter_sqlite_text_rows(db_bytes)
            print(f"  scanned {tables_scanned} tables, found {len(text_rows)} Japanese values")

            tables_with_text = set()
            for text_row in text_rows:
                all_rows.append(
                    (
                        archive.name,
                        entry_name,
                        text_row.table,
                        text_row.row_number,
                        text_row.rowid,
                        text_row.column,
                        text_row.text,
                    )
                )
                key = (archive.name, entry_name, text_row.table)
                split_rows.setdefault(key, []).append(text_row)
                tables_with_text.add(text_row.table)

            for table_name in sorted(tables_with_text):
                split_path = (
                    Path("by_original_file")
                    / safe_component(archive.name, "archive")
                    / safe_component(entry_name, "entry")
                    / f"{safe_component(table_name, 'table')}.tsv"
                )
                manifest_rows.append(
                    (archive.name, entry_name, table_name, split_path.as_posix())
                )

            summary_rows.append(
                (archive.name, entry_name, tables_scanned, len(text_rows))
            )

    write_outputs(output_dir, all_rows, summary_rows, split_rows, manifest_rows)
    print(f"wrote {len(all_rows)} rows to {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Japanese text from SQLite databases embedded in MDB1/MVGL archives."
    )
    parser.add_argument(
        "archives",
        nargs="*",
        type=Path,
        help="MVGL archives to scan. Defaults to all *.mvgl files in the current directory.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("extracted_japanese_text"),
        help="Directory for TSV/TXT output.",
    )
    args = parser.parse_args()

    archives = args.archives or sorted(Path(".").glob("*.mvgl"))
    run(archives, args.output_dir)


if __name__ == "__main__":
    main()
