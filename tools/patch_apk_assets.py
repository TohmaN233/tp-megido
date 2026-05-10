from __future__ import annotations

import argparse
import shutil
import zlib
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo


DEFAULT_APK = Path("apk") / "com_dena_a12021245_v2.0.1.apk"
DEFAULT_REPLACEMENTS_DIR = Path("packed_translated") / "mvgl"
DEFAULT_OUTPUT = Path("patched_apk") / "com_dena_a12021245_v2.0.1_cn_unsigned.apk"

REPLACEMENTS = {
    "assets/GKDB_offline.android.mvgl": "GKDB_offline.android.mvgl",
    "assets/GKDB_offline_episode.android.mvgl": "GKDB_offline_episode.android.mvgl",
}
OFFLINE_CHECKSUM_ENTRY = "assets/offlinechecksumcache"


def copy_info(src: ZipInfo) -> ZipInfo:
    dst = ZipInfo(src.filename, src.date_time)
    dst.comment = src.comment
    dst.extra = src.extra
    dst.internal_attr = src.internal_attr
    dst.external_attr = src.external_attr
    dst.create_system = src.create_system
    dst.compress_type = src.compress_type
    return dst


def update_offline_checksum(data: bytes, crc_by_file: dict[str, str]) -> bytes:
    text = data.decode("utf-8")
    out: list[str] = []
    changed = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in crc_by_file:
            out.append(f"{parts[0]} {crc_by_file[parts[0]]}")
            changed += 1
        else:
            out.append(line)
    if changed == 0:
        raise RuntimeError("No checksum lines were updated in assets/offlinechecksumcache.")
    return ("\n".join(out) + "\n").encode("utf-8")


def is_signature_entry(name: str) -> bool:
    upper = name.upper()
    return upper.startswith("META-INF/") and upper.endswith(
        (".RSA", ".DSA", ".EC", ".SF", ".MF")
    )


def patch_apk(apk: Path, replacements_dir: Path, output: Path) -> None:
    replacement_data: dict[str, bytes] = {}
    crc_by_file: dict[str, str] = {}

    for archive_name, file_name in REPLACEMENTS.items():
        payload = (replacements_dir / file_name).read_bytes()
        replacement_data[archive_name] = payload
        crc_by_file[file_name] = f"{zlib.crc32(payload) & 0xFFFFFFFF:08x}"

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(output.suffix + ".tmp")
    if temp_output.exists():
        temp_output.unlink()

    replaced = set()
    skipped_signatures = 0
    with ZipFile(apk, "r") as zin, ZipFile(temp_output, "w", allowZip64=True) as zout:
        for info in zin.infolist():
            if is_signature_entry(info.filename):
                skipped_signatures += 1
                continue

            out_info = copy_info(info)
            if info.filename in replacement_data:
                out_info.compress_type = ZIP_STORED
                zout.writestr(out_info, replacement_data[info.filename])
                replaced.add(info.filename)
                continue
            if info.filename == OFFLINE_CHECKSUM_ENTRY:
                checksum_data = update_offline_checksum(zin.read(info), crc_by_file)
                zout.writestr(out_info, checksum_data)
                continue

            with zin.open(info, "r") as src, zout.open(out_info, "w") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)

    missing = set(REPLACEMENTS) - replaced
    if missing:
        temp_output.unlink(missing_ok=True)
        raise RuntimeError(f"Missing APK entries: {sorted(missing)}")

    temp_output.replace(output)
    print(f"wrote: {output}")
    print(f"removed signature entries: {skipped_signatures}")
    for name in sorted(crc_by_file):
        print(f"{name}\t{crc_by_file[name]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", type=Path, default=DEFAULT_APK)
    parser.add_argument("--replacements-dir", type=Path, default=DEFAULT_REPLACEMENTS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    patch_apk(args.apk, args.replacements_dir, args.output)


if __name__ == "__main__":
    main()
