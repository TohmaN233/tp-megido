from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path


DEFAULT_FORTRANS_ROOT = Path("Fortransjp")
DEFAULT_BILINGUAL = Path("已翻译完成") / "text_bilingual.txt"
DEFAULT_CHINESE_UNIQUE = Path("已翻译完成") / "chinese_text_unique.txt"


@dataclass
class SplitStats:
    bilingual_pairs: int = 0
    duplicate_conflicts: int = 0
    split_files: int = 0
    rows: int = 0
    matched: int = 0
    missing: int = 0


def read_bilingual_map(path: Path) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) % 2:
        raise ValueError(f"bilingual file has an odd line count: {path}")

    translations: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    for index in range(0, len(lines), 2):
        zh = lines[index].strip()
        jp = lines[index + 1].strip()
        if not jp:
            continue
        old = translations.get(jp)
        if old is not None and old != zh:
            conflicts.append((jp, old, zh))
            continue
        translations[jp] = zh
    return translations, conflicts


def read_aligned_unique_map(
    japanese_unique_path: Path, chinese_unique_path: Path
) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    japanese_lines = japanese_unique_path.read_text(encoding="utf-8-sig").splitlines()
    chinese_lines = chinese_unique_path.read_text(encoding="utf-8-sig").splitlines()
    if len(japanese_lines) != len(chinese_lines):
        raise ValueError(
            "unique file line counts do not match: "
            f"{japanese_unique_path} has {len(japanese_lines)}, "
            f"{chinese_unique_path} has {len(chinese_lines)}"
        )

    translations: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    for jp, zh in zip(japanese_lines, chinese_lines):
        jp = jp.strip()
        zh = zh.strip()
        if not jp:
            continue
        old = translations.get(jp)
        if old is not None and old != zh:
            conflicts.append((jp, old, zh))
            continue
        translations[jp] = zh
    return translations, conflicts


def open_tsv(path: Path, mode: str):
    return path.open(mode, encoding="utf-8-sig" if "r" in mode else "utf-8", newline="")


def split_translations(
    fortrans_root: Path = DEFAULT_FORTRANS_ROOT,
    bilingual_path: Path = DEFAULT_BILINGUAL,
    chinese_unique_path: Path | None = DEFAULT_CHINESE_UNIQUE,
    txt_root: Path | None = None,
    filled_root: Path | None = None,
) -> SplitStats:
    txt_root = txt_root or fortrans_root / "translated_split_txt"
    filled_root = filled_root or fortrans_root / "translated_filled_tsv"
    manifest_path = fortrans_root / "by_original_file" / "manifest.tsv"

    japanese_unique_path = fortrans_root / "japanese_text_unique.txt"
    if chinese_unique_path and chinese_unique_path.exists():
        if not japanese_unique_path.exists():
            raise FileNotFoundError(f"missing Japanese unique file: {japanese_unique_path}")
        translation_map, conflicts = read_aligned_unique_map(
            japanese_unique_path, chinese_unique_path
        )
    elif not bilingual_path.exists():
        raise FileNotFoundError(f"missing bilingual file: {bilingual_path}")
    else:
        translation_map, conflicts = read_bilingual_map(bilingual_path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing manifest: {manifest_path}")

    stats = SplitStats(
        bilingual_pairs=len(translation_map),
        duplicate_conflicts=len(conflicts),
    )

    txt_root.mkdir(parents=True, exist_ok=True)
    filled_root.mkdir(parents=True, exist_ok=True)
    missing_path = txt_root / "missing_translations.tsv"
    summary_path = txt_root / "summary.tsv"
    conflict_path = txt_root / "duplicate_conflicts.tsv"

    with open_tsv(manifest_path, "r") as manifest_handle:
        manifest_rows = list(csv.DictReader(manifest_handle, delimiter="\t"))

    missing_rows: list[list[str]] = []
    summary_rows: list[list[str | int]] = []

    for manifest_row in manifest_rows:
        rel_tsv = Path(manifest_row["split_tsv"])
        source_tsv = fortrans_root / rel_tsv
        out_txt = txt_root / rel_tsv.with_suffix(".txt")
        out_tsv = filled_root / rel_tsv
        out_txt.parent.mkdir(parents=True, exist_ok=True)
        out_tsv.parent.mkdir(parents=True, exist_ok=True)

        file_rows = 0
        file_matched = 0
        file_missing = 0

        with open_tsv(source_tsv, "r") as source_handle, open_tsv(
            out_tsv, "w"
        ) as tsv_handle, out_txt.open(
            "w", encoding="utf-8", newline="\n"
        ) as txt_handle:
            reader = csv.DictReader(source_handle, delimiter="\t")
            fieldnames = reader.fieldnames or []
            if "translated_text" not in fieldnames:
                fieldnames.append("translated_text")
            writer = csv.DictWriter(
                tsv_handle,
                delimiter="\t",
                lineterminator="\n",
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()

            for row in reader:
                original = (row.get("original_text") or "").strip()
                translated = translation_map.get(original, "")
                row["translated_text"] = translated
                writer.writerow(row)
                txt_handle.write(translated + "\n")

                file_rows += 1
                if translated:
                    file_matched += 1
                else:
                    file_missing += 1
                    missing_rows.append(
                        [
                            manifest_row["archive"],
                            manifest_row["entry"],
                            manifest_row["table"],
                            row.get("row_number", ""),
                            row.get("rowid", ""),
                            row.get("column", ""),
                            original,
                        ]
                    )

        stats.split_files += 1
        stats.rows += file_rows
        stats.matched += file_matched
        stats.missing += file_missing
        summary_rows.append(
            [
                manifest_row["archive"],
                manifest_row["entry"],
                manifest_row["table"],
                rel_tsv.with_suffix(".txt").as_posix(),
                file_rows,
                file_matched,
                file_missing,
            ]
        )

    with open_tsv(missing_path, "w") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ["archive", "entry", "table", "row_number", "rowid", "column", "original_text"]
        )
        writer.writerows(missing_rows)

    with open_tsv(summary_path, "w") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "archive",
                "entry",
                "table",
                "split_txt",
                "rows",
                "matched",
                "missing",
            ]
        )
        writer.writerows(summary_rows)

    with open_tsv(conflict_path, "w") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["japanese", "first_chinese", "conflicting_chinese"])
        writer.writerows(conflicts)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split a Chinese/Japanese bilingual unique text file back into line-aligned per-table TXT files."
    )
    parser.add_argument("--fortrans-root", type=Path, default=DEFAULT_FORTRANS_ROOT)
    parser.add_argument("--bilingual", type=Path, default=DEFAULT_BILINGUAL)
    parser.add_argument(
        "--chinese-unique",
        type=Path,
        default=DEFAULT_CHINESE_UNIQUE,
        help="Line-aligned Chinese unique text. Defaults to 已翻译完成/chinese_text_unique.txt. If present, this is used instead of the bilingual file.",
    )
    parser.add_argument("--txt-root", type=Path)
    parser.add_argument("--filled-root", type=Path)
    args = parser.parse_args()

    stats = split_translations(
        fortrans_root=args.fortrans_root,
        bilingual_path=args.bilingual,
        chinese_unique_path=args.chinese_unique,
        txt_root=args.txt_root,
        filled_root=args.filled_root,
    )
    print(f"translation pairs: {stats.bilingual_pairs}")
    print(f"split files: {stats.split_files}")
    print(f"rows: {stats.rows}")
    print(f"matched: {stats.matched}")
    print(f"missing: {stats.missing}")
    print(f"duplicate conflicts: {stats.duplicate_conflicts}")


if __name__ == "__main__":
    main()
