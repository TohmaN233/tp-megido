from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sqlite3
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from extract_japanese_text import decompress_doboz
from split_translated_texts import (
    DEFAULT_BILINGUAL,
    DEFAULT_CHINESE_UNIQUE,
    DEFAULT_FORTRANS_ROOT,
    split_translations,
)


DEFAULT_DB_SOURCE = Path("extracted_japanese_text") / "databases"
DEFAULT_OUTPUT = Path("packed_translated")
WHITESPACE_RE = re.compile(r"\s+")
CJK_SPACE_RE = re.compile(
    r"(?<=[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff60])\s+"
    r"(?=[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff60])"
)
CONTROL_TAG_RE = re.compile(r"\{[^{}\s]+\}")
CONTROL_TAG_SPLIT_RE = re.compile(r"(\{[^{}\s]+\})")
INLINE_ATOM_RE = re.compile(r"[A-Za-z0-9]+(?:[._+/%-][A-Za-z0-9]+)*%?")
TRAILING_PAREN_RE = re.compile(r"([（(][^（）()]+[）)])$")
TEXT_PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:]")
NO_LINE_START = set("，。！？；：、）】」』》〉）…….!?;:,%)]}")
STORY_TEXT_TABLE = "gk_episode_text"
STORY_TEXT_COLUMN = "text"
STORY_TEXT_MAX_LINES = 3
STORY_TEXT_MIN_WIDTH = 33
STORY_TEXT_MAX_WIDTH = 33
@dataclass
class ArchiveFile:
    index: int
    name: str
    ext: str
    data_index: int

    @property
    def logical_name(self) -> str:
        if self.name:
            return f"{self.name}.{self.ext}" if self.ext else self.name
        if self.ext:
            return f"_unnamed.{self.ext}"
        return "_unnamed"


@dataclass
class Mdb1Archive:
    entry_count: int
    name_count: int
    data_count: int
    data_base: int
    data_entries_offset: int
    files: list[ArchiveFile]
    data_entries: list[tuple[int, int, int]]


@dataclass
class PackStats:
    split_rows: int = 0
    split_matched: int = 0
    split_missing: int = 0
    databases: int = 0
    update_rows: int = 0
    skipped_blank: int = 0
    skipped_no_rowid: int = 0
    skipped_missing_db: int = 0
    archives: int = 0


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def flatten_text(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value).strip()


def normalize_translation_text(value: str) -> str:
    value = flatten_text(value)
    while True:
        normalized = CJK_SPACE_RE.sub("", value)
        if normalized == value:
            return normalized
        value = normalized


def display_width(text: str) -> int:
    width = 0
    for char in CONTROL_TAG_RE.sub("", text):
        if char == "\n":
            continue
        width += 1 if ord(char) < 128 else 2
    return width


def iter_wrap_tokens(text: str):
    for part in CONTROL_TAG_SPLIT_RE.split(text):
        if not part:
            continue
        if CONTROL_TAG_RE.fullmatch(part):
            yield part, 0, True
        else:
            pos = 0
            for match in INLINE_ATOM_RE.finditer(part):
                for char in part[pos : match.start()]:
                    yield char, 1 if ord(char) < 128 else 2, False
                atom = match.group(0)
                yield atom, sum(1 if ord(char) < 128 else 2 for char in atom), False
                pos = match.end()
            for char in part[pos:]:
                yield char, 1 if ord(char) < 128 else 2, False


def wrap_text(text: str, max_width: int) -> str:
    text = normalize_translation_text(text)
    if not text:
        return text

    lines: list[str] = []
    current: list[str] = []
    current_width = 0

    for token, token_width, is_control_tag in iter_wrap_tokens(text):
        if is_control_tag:
            current.append(token)
            continue

        if token.isspace():
            if current and current[-1] != " ":
                current.append(" ")
                current_width += 1
            continue

        if current and current_width + token_width > max_width:
            if token in NO_LINE_START:
                current.append(token)
                current_width += token_width
                continue
            line = "".join(current).strip()
            if line:
                lines.append(line)
            current = [token]
            current_width = token_width
        else:
            current.append(token)
            current_width += token_width

    line = "".join(current).strip()
    if line:
        lines.append(line)
    return "\n".join(lines)


def rebalance_short_tail(text: str, wrapped: str, max_width: int) -> str:
    lines = [line for line in wrapped.splitlines() if line.strip()]
    if len(lines) <= 1 or display_width(lines[-1]) > 2:
        return wrapped

    line_count = len(lines)
    total_width = display_width(text)
    min_width = max(2, math.ceil(total_width / line_count))
    for width in range(min_width, max_width):
        candidate = wrap_text(text, width)
        candidate_lines = [line for line in candidate.splitlines() if line.strip()]
        if len(candidate_lines) != line_count:
            continue
        if max(display_width(line) for line in candidate_lines) > max_width:
            continue
        if display_width(candidate_lines[-1]) > 2:
            return candidate
    return wrapped


def wrap_to_line_count(text: str, line_count: int, max_width: int) -> str | None:
    total_width = display_width(text)
    if line_count <= 1 or total_width <= 0:
        return None

    min_width = max(2, math.ceil(total_width / line_count))
    for width in range(min_width, max_width + 1):
        candidate = wrap_text(text, width)
        candidate_lines = [line for line in candidate.splitlines() if line.strip()]
        if len(candidate_lines) != line_count:
            continue
        if max(display_width(line) for line in candidate_lines) <= max_width:
            return rebalance_short_tail(text, candidate, max_width)
    return None


def format_for_original_layout(
    original: str,
    translated: str,
    table_name: str = "",
    column_name: str = "",
    section_max_width: int | None = None,
) -> str:
    if "\n" not in original or "\n" in translated:
        return translated

    original_lines = [line for line in original.splitlines() if line.strip()]
    if not original_lines:
        return translated

    line_count = len(original_lines)
    if line_count == 2 and len(flatten_text(original)) <= 12:
        match = TRAILING_PAREN_RE.search(translated)
        if match and match.start() > 0:
            return f"{translated[: match.start()].strip()}\n{match.group(1)}"

    original_max_width = max(display_width(line) for line in original_lines)
    global_safe_width = section_max_width - 2 if section_max_width else 80
    max_width = max(2, min(80, original_max_width, global_safe_width))

    if not TEXT_PUNCT_RE.search(translated):
        balanced = wrap_to_line_count(translated, line_count, max_width)
        if balanced:
            return balanced

    wrapped = wrap_text(translated, max_width)
    return rebalance_short_tail(translated, wrapped, max_width)


def count_display_lines(text: str) -> int:
    if not text:
        return 0
    return len([line for line in text.splitlines() if line.strip()])


def format_story_text(translated: str) -> str:
    translated = normalize_translation_text(translated)
    if not translated:
        return translated

    for max_width in range(STORY_TEXT_MIN_WIDTH, STORY_TEXT_MAX_WIDTH + 1, 2):
        wrapped = wrap_text(translated, max_width)
        if count_display_lines(wrapped) <= STORY_TEXT_MAX_LINES:
            return wrapped

    return wrap_text(translated, STORY_TEXT_MAX_WIDTH)


def open_tsv(path: Path, mode: str):
    return path.open(mode, encoding="utf-8-sig" if "r" in mode else "utf-8", newline="")


def parse_mdb1(data: bytes) -> Mdb1Archive:
    if data[:4] != b"MDB1":
        raise ValueError("not an MDB1 archive")

    entry_count, name_count = struct.unpack_from("<HH", data, 4)
    data_count = struct.unpack_from("<I", data, 8)[0]
    data_base = struct.unpack_from("<I", data, 12)[0]

    offset = 20
    file_entries: list[tuple[int, int, int, int]] = []
    for _ in range(entry_count):
        file_entries.append(struct.unpack_from("<HHHH", data, offset))
        offset += 8

    names: list[tuple[str, str]] = []
    for _ in range(name_count):
        ext = data[offset : offset + 4].decode("ascii", "replace").rstrip(" \0")
        name = (
            data[offset + 4 : offset + 64]
            .split(b"\0", 1)[0]
            .decode("ascii", "replace")
        )
        names.append((name, ext))
        offset += 64

    data_entries_offset = offset
    data_entries: list[tuple[int, int, int]] = []
    for _ in range(data_count):
        data_entries.append(struct.unpack_from("<III", data, offset))
        offset += 12

    files: list[ArchiveFile] = []
    for index, file_entry in enumerate(file_entries):
        data_index = file_entry[1]
        if data_index == 0xFFFF or index >= len(names):
            continue
        name, ext = names[index]
        files.append(ArchiveFile(index=index, name=name, ext=ext, data_index=data_index))

    return Mdb1Archive(
        entry_count=entry_count,
        name_count=name_count,
        data_count=data_count,
        data_base=data_base,
        data_entries_offset=data_entries_offset,
        files=files,
        data_entries=data_entries,
    )


def encode_doboz_stored(payload: bytes) -> bytes:
    size = len(payload)
    size_coded_size = 4 if size <= 0xFFFFFFFF else 8
    header_size = 1 + 2 * size_coded_size
    compressed_size = header_size + size
    attributes = 0x80 | ((size_coded_size - 1) << 3)
    return (
        bytes([attributes])
        + size.to_bytes(size_coded_size, "little")
        + compressed_size.to_bytes(size_coded_size, "little")
        + payload
    )


def read_manifest(fortrans_root: Path) -> list[dict[str, str]]:
    manifest_path = fortrans_root / "by_original_file" / "manifest.tsv"
    with open_tsv(manifest_path, "r") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_story_overrides(path: Path | None) -> dict[int, str]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(path)

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("story overrides JSON must be an object keyed by rowid")
        return {int(rowid): str(text) for rowid, text in data.items()}

    overrides: dict[int, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            rowid = row.get("rowid") or row.get("id")
            text = row.get("text") or row.get("translated_text") or row.get("cn")
            if rowid and text:
                overrides[int(rowid)] = text
    return overrides


def update_database(
    source_db: Path,
    target_db: Path,
    table_tsvs: list[tuple[str, Path]],
    story_size_tag: int | None = None,
    story_overrides: dict[int, str] | None = None,
) -> tuple[int, int, int]:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_db, target_db)
    story_overrides = story_overrides or {}

    updated = 0
    skipped_blank = 0
    skipped_no_rowid = 0
    connection = sqlite3.connect(str(target_db))
    section_width_cache: dict[tuple[str, str], int | None] = {}

    def get_section_max_width(table_name: str, column: str) -> int | None:
        key = (table_name, column)
        if key in section_width_cache:
            return section_width_cache[key]
        max_width = 0
        try:
            rows = connection.execute(
                f"SELECT {quote_identifier(column)} "
                f"FROM {quote_identifier(table_name)} "
                f"WHERE {quote_identifier(column)} LIKE '%' || char(10) || '%'"
            )
            for (value,) in rows:
                if not isinstance(value, str):
                    continue
                for line in value.splitlines():
                    if line.strip():
                        max_width = max(max_width, display_width(line))
        except sqlite3.Error:
            max_width = 0
        section_width_cache[key] = max_width or None
        return section_width_cache[key]

    try:
        for table_name, tsv_path in table_tsvs:
            with open_tsv(tsv_path, "r") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                for row in reader:
                    translated = row.get("translated_text") or ""
                    rowid = row.get("rowid") or ""
                    row_number = row.get("row_number") or ""
                    column = row.get("column") or ""
                    if not translated:
                        skipped_blank += 1
                        continue
                    current = None
                    if not rowid:
                        if not row_number.isdigit():
                            skipped_no_rowid += 1
                            continue
                        candidate_rowid = int(row_number) - 1
                        original = row.get("original_text") or ""
                        current = connection.execute(
                            f"SELECT {quote_identifier(column)} "
                            f"FROM {quote_identifier(table_name)} WHERE rowid = ?",
                            (candidate_rowid,),
                        ).fetchone()
                        if not current or flatten_text(str(current[0])) != original:
                            skipped_no_rowid += 1
                            continue
                        rowid = str(candidate_rowid)
                    if current is None:
                        current = connection.execute(
                            f"SELECT {quote_identifier(column)} "
                            f"FROM {quote_identifier(table_name)} WHERE rowid = ?",
                            (int(rowid),),
                        ).fetchone()
                    rowid_int = int(rowid)
                    if table_name == STORY_TEXT_TABLE and column == STORY_TEXT_COLUMN:
                        translated = story_overrides.get(rowid_int, translated)
                        translated = format_story_text(translated)
                    elif current and isinstance(current[0], str):
                        translated = format_for_original_layout(
                            current[0],
                            translated,
                            table_name,
                            column,
                            get_section_max_width(table_name, column),
                        )
                    if (
                        story_size_tag
                        and table_name == STORY_TEXT_TABLE
                        and column == STORY_TEXT_COLUMN
                    ):
                        translated = f"<size={story_size_tag}>{translated}</size>"
                    connection.execute(
                        f"UPDATE {quote_identifier(table_name)} "
                        f"SET {quote_identifier(column)} = ? WHERE rowid = ?",
                        (translated, rowid_int),
                    )
                    updated += 1
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"sqlite integrity check failed for {target_db}: {integrity}")
    finally:
        connection.close()

    return updated, skipped_blank, skipped_no_rowid


def rebuild_mdb1_archive(
    source_archive: Path,
    output_archive: Path,
    replacements_by_entry: dict[str, bytes],
    align: int = 4,
) -> int:
    data = source_archive.read_bytes()
    archive = parse_mdb1(data)
    header = bytearray(data[: archive.data_base])

    replacements_by_data: dict[int, bytes] = {}
    for file in archive.files:
        replacement = replacements_by_entry.get(file.logical_name)
        if replacement is not None:
            replacements_by_data[file.data_index] = replacement

    output_payload = bytearray()
    new_entries: list[tuple[int, int, int]] = []
    for data_index, (relative_offset, size, compressed_size) in enumerate(
        archive.data_entries
    ):
        if align > 1:
            padding = (-len(output_payload)) % align
            if padding:
                output_payload.extend(b"\0" * padding)

        new_relative_offset = len(output_payload)
        replacement = replacements_by_data.get(data_index)
        if replacement is None:
            start = archive.data_base + relative_offset
            payload = data[start : start + compressed_size]
            new_size = size
            new_compressed_size = compressed_size
        else:
            payload = encode_doboz_stored(replacement)
            new_size = len(payload)
            new_size = len(replacement)
            new_compressed_size = len(payload)

        output_payload.extend(payload)
        new_entries.append((new_relative_offset, new_size, new_compressed_size))

    for index, entry in enumerate(new_entries):
        struct.pack_into("<III", header, archive.data_entries_offset + index * 12, *entry)

    output_archive.parent.mkdir(parents=True, exist_ok=True)
    output_archive.write_bytes(bytes(header) + bytes(output_payload))
    return len(replacements_by_data)


def verify_replacements(output_archive: Path, replacement_entries: set[str]) -> None:
    data = output_archive.read_bytes()
    archive = parse_mdb1(data)
    found = 0
    for file in archive.files:
        if file.logical_name not in replacement_entries:
            continue
        rel, size, compressed_size = archive.data_entries[file.data_index]
        payload = data[archive.data_base + rel : archive.data_base + rel + compressed_size]
        if size != compressed_size:
            payload = decompress_doboz(payload)
        if not payload.startswith(b"SQLite format 3\0"):
            raise RuntimeError(f"replacement is not a sqlite database: {file.logical_name}")
        found += 1
    if found != len(replacement_entries):
        raise RuntimeError(
            f"archive verification found {found}/{len(replacement_entries)} replacements in {output_archive}"
        )


def pack_translated(
    fortrans_root: Path = DEFAULT_FORTRANS_ROOT,
    bilingual_path: Path = DEFAULT_BILINGUAL,
    chinese_unique_path: Path | None = DEFAULT_CHINESE_UNIQUE,
    db_source: Path = DEFAULT_DB_SOURCE,
    output_root: Path = DEFAULT_OUTPUT,
    story_size_tag: int | None = None,
    story_overrides_path: Path | None = None,
) -> PackStats:
    story_overrides = load_story_overrides(story_overrides_path)
    split_stats = split_translations(
        fortrans_root=fortrans_root,
        bilingual_path=bilingual_path,
        chinese_unique_path=chinese_unique_path,
    )
    stats = PackStats(
        split_rows=split_stats.rows,
        split_matched=split_stats.matched,
        split_missing=split_stats.missing,
    )

    filled_root = fortrans_root / "translated_filled_tsv"
    manifest_rows = read_manifest(fortrans_root)
    grouped: dict[tuple[str, str], list[tuple[str, Path]]] = {}
    for row in manifest_rows:
        archive_name = row["archive"]
        entry_name = row["entry"]
        table_name = row["table"]
        tsv_path = filled_root / Path(row["split_tsv"])
        grouped.setdefault((archive_name, entry_name), []).append((table_name, tsv_path))

    db_work_root = output_root / "databases"
    archive_replacements: dict[str, dict[str, bytes]] = {}
    db_report: list[dict[str, str | int]] = []

    for (archive_name, entry_name), table_tsvs in sorted(grouped.items()):
        source_db = db_source / archive_name / entry_name
        target_db = db_work_root / archive_name / entry_name
        if not source_db.exists():
            stats.skipped_missing_db += 1
            db_report.append(
                {
                    "archive": archive_name,
                    "entry": entry_name,
                    "status": "missing_source_db",
                    "updated": 0,
                }
            )
            continue

        updated, skipped_blank, skipped_no_rowid = update_database(
            source_db,
            target_db,
            table_tsvs,
            story_size_tag=story_size_tag,
            story_overrides=story_overrides,
        )
        archive_replacements.setdefault(archive_name, {})[entry_name] = target_db.read_bytes()
        stats.databases += 1
        stats.update_rows += updated
        stats.skipped_blank += skipped_blank
        stats.skipped_no_rowid += skipped_no_rowid
        db_report.append(
            {
                "archive": archive_name,
                "entry": entry_name,
                "status": "updated",
                "updated": updated,
                "skipped_blank": skipped_blank,
                "skipped_no_rowid": skipped_no_rowid,
            }
        )

    archive_root = output_root / "mvgl"
    archive_report: list[dict[str, str | int]] = []
    for archive_name, replacements in sorted(archive_replacements.items()):
        source_archive = Path(archive_name)
        if not source_archive.exists():
            raise FileNotFoundError(f"missing source archive: {source_archive}")
        output_archive = archive_root / archive_name
        replacement_count = rebuild_mdb1_archive(source_archive, output_archive, replacements)
        verify_replacements(output_archive, set(replacements))
        stats.archives += 1
        archive_report.append(
            {
                "archive": archive_name,
                "status": "rebuilt",
                "replacement_entries": replacement_count,
                "output": str(output_archive),
            }
        )

    output_root.mkdir(parents=True, exist_ok=True)
    report = {
        "stats": asdict(stats),
        "databases": db_report,
        "archives": archive_report,
    }
    (output_root / "pack_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-click pipeline: split translated text, update extracted SQLite DBs, and rebuild MDB1/MVGL archives."
    )
    parser.add_argument("--fortrans-root", type=Path, default=DEFAULT_FORTRANS_ROOT)
    parser.add_argument("--bilingual", type=Path, default=DEFAULT_BILINGUAL)
    parser.add_argument(
        "--chinese-unique",
        type=Path,
        default=DEFAULT_CHINESE_UNIQUE,
        help="Line-aligned Chinese unique text. Defaults to 已翻译完成/chinese_text_unique.txt.",
    )
    parser.add_argument("--db-source", type=Path, default=DEFAULT_DB_SOURCE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--story-size-tag",
        type=int,
        help="Optional Unity rich-text size tag for gk_episode_text.text, for example 36. Use only for testing because unsupported rich text will display the tags.",
    )
    parser.add_argument(
        "--story-overrides",
        type=Path,
        help="Optional JSON or TSV rowid->text overrides for story lines that need manual shortening.",
    )
    args = parser.parse_args()

    stats = pack_translated(
        fortrans_root=args.fortrans_root,
        bilingual_path=args.bilingual,
        chinese_unique_path=args.chinese_unique,
        db_source=args.db_source,
        output_root=args.output_root,
        story_size_tag=args.story_size_tag,
        story_overrides_path=args.story_overrides,
    )
    print(f"split rows: {stats.split_rows}")
    print(f"split matched: {stats.split_matched}")
    print(f"split missing: {stats.split_missing}")
    print(f"databases updated: {stats.databases}")
    print(f"sqlite rows updated: {stats.update_rows}")
    print(f"archives rebuilt: {stats.archives}")
    print(f"output: {args.output_root}")


if __name__ == "__main__":
    main()
