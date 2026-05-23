from __future__ import annotations

import argparse
import shutil
import struct
import zlib
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo


DEFAULT_APK = Path("apk") / "com_dena_a12021245_v2.0.1.apk"
DEFAULT_REPLACEMENTS_DIR = Path("packed_translated") / "mvgl"
DEFAULT_OUTPUT = Path("patched_apk") / "com_dena_a12021245_v2.0.1_cn_unsigned.apk"

OFFLINE_CHECKSUM_ENTRY = "assets/offlinechecksumcache"
ASSET_PREFIX = "assets/"
APK_ASSET_REPLACEMENT_EXCLUDES = {
    # The APK also contains a small DBGK100 bootstrap asset, but the larger
    # localized DBGK100 file belongs to the external runtime data directory.
    "DBGK100.android.mvgl",
}


def copy_info(src: ZipInfo) -> ZipInfo:
    dst = ZipInfo(src.filename, src.date_time)
    dst.comment = src.comment
    dst.extra = src.extra
    dst.flag_bits = src.flag_bits
    dst.internal_attr = src.internal_attr
    dst.external_attr = src.external_attr
    dst.create_system = src.create_system
    dst.compress_type = src.compress_type
    return dst


def detect_newline(data: bytes) -> str:
    lf_count = data.count(b"\n")
    crlf_count = data.count(b"\r\n")
    return "\r\n" if lf_count and lf_count == crlf_count else "\n"


def update_offline_checksum(data: bytes, crc_by_file: dict[str, str]) -> bytes:
    text = data.decode("utf-8")
    newline = detect_newline(data)
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
    return (newline.join(out) + newline).encode("utf-8")


def load_offline_checksum_names(data: bytes) -> set[str]:
    names: set[str] = set()
    text = data.decode("utf-8")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            names.add(parts[0])
    return names


def collect_replacements(
    apk: Path, replacements_dir: Path
) -> tuple[dict[str, bytes], dict[str, str], list[str]]:
    if not replacements_dir.is_dir():
        raise FileNotFoundError(replacements_dir)

    with ZipFile(apk, "r") as archive:
        archive_names = set(archive.namelist())
        checksum_names: set[str] = set()
        if OFFLINE_CHECKSUM_ENTRY in archive_names:
            checksum_names = load_offline_checksum_names(
                archive.read(OFFLINE_CHECKSUM_ENTRY)
            )

    replacement_data: dict[str, bytes] = {}
    crc_by_file: dict[str, str] = {}
    skipped: list[str] = []
    for path in sorted(replacements_dir.iterdir()):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        archive_name = f"{ASSET_PREFIX}{path.name}"
        matched = False
        if (
            archive_name in archive_names
            and path.name not in APK_ASSET_REPLACEMENT_EXCLUDES
        ):
            replacement_data[archive_name] = payload
            matched = True
        if path.name in checksum_names:
            crc_by_file[path.name] = f"{zlib.crc32(payload) & 0xFFFFFFFF:08x}"
            matched = True
        if not matched:
            skipped.append(path.name)

    if not replacement_data and not crc_by_file:
        raise RuntimeError(
            f"No files in {replacements_dir} matched APK assets or "
            "assets/offlinechecksumcache entries."
        )
    return replacement_data, crc_by_file, skipped


def find_eocd(data: bytes) -> int:
    min_pos = max(0, len(data) - 0xFFFF - 22)
    pos = data.rfind(b"PK\x05\x06", min_pos)
    if pos < 0:
        raise RuntimeError("Could not find ZIP end of central directory.")
    return pos


def patch_flag_bits(apk: Path, flag_bits_by_name: dict[str, int]) -> None:
    data = bytearray(apk.read_bytes())

    with ZipFile(apk, "r") as archive:
        for info in archive.infolist():
            flag_bits = flag_bits_by_name.get(info.filename)
            if flag_bits is None:
                continue
            if data[info.header_offset : info.header_offset + 4] != b"PK\x03\x04":
                raise RuntimeError(f"Bad local file header for {info.filename}")
            struct.pack_into("<H", data, info.header_offset + 6, flag_bits)

    eocd = find_eocd(data)
    central_size = struct.unpack_from("<I", data, eocd + 12)[0]
    central_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    pos = central_offset
    end = central_offset + central_size
    while pos < end:
        if data[pos : pos + 4] != b"PK\x01\x02":
            raise RuntimeError(f"Bad central directory header at offset {pos}")
        name_len = struct.unpack_from("<H", data, pos + 28)[0]
        extra_len = struct.unpack_from("<H", data, pos + 30)[0]
        comment_len = struct.unpack_from("<H", data, pos + 32)[0]
        name = data[pos + 46 : pos + 46 + name_len].decode("utf-8")
        flag_bits = flag_bits_by_name.get(name)
        if flag_bits is not None:
            struct.pack_into("<H", data, pos + 8, flag_bits)
        pos += 46 + name_len + extra_len + comment_len
    if pos != end:
        raise RuntimeError("Central directory parse ended at an unexpected offset.")

    apk.write_bytes(data)


def is_signature_entry(name: str) -> bool:
    upper = name.upper()
    return upper.startswith("META-INF/") and upper.endswith(
        (".RSA", ".DSA", ".EC", ".SF", ".MF")
    )


def patch_apk(
    apk: Path,
    replacements_dir: Path,
    output: Path,
    update_checksum: bool = True,
) -> None:
    replacement_data, crc_by_file, skipped = collect_replacements(apk, replacements_dir)

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(output.suffix + ".tmp")
    if temp_output.exists():
        temp_output.unlink()

    replaced = set()
    skipped_signatures = 0
    flag_bits_by_name: dict[str, int] = {}
    with ZipFile(apk, "r") as zin, ZipFile(temp_output, "w", allowZip64=True) as zout:
        for info in zin.infolist():
            if is_signature_entry(info.filename):
                skipped_signatures += 1
                continue

            flag_bits_by_name[info.filename] = info.flag_bits
            out_info = copy_info(info)
            if info.filename in replacement_data:
                out_info.compress_type = ZIP_STORED
                zout.writestr(out_info, replacement_data[info.filename])
                replaced.add(info.filename)
                continue
            if update_checksum and crc_by_file and info.filename == OFFLINE_CHECKSUM_ENTRY:
                checksum_data = update_offline_checksum(zin.read(info), crc_by_file)
                zout.writestr(out_info, checksum_data)
                continue

            with zin.open(info, "r") as src, zout.open(out_info, "w") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)

    patch_flag_bits(temp_output, flag_bits_by_name)
    temp_output.replace(output)
    print(f"wrote: {output}")
    print(f"removed signature entries: {skipped_signatures}")
    for name in sorted(replaced):
        print(f"replaced\t{name}")
    for name in sorted(crc_by_file):
        print(f"checksum\t{name}\t{crc_by_file[name]}")
    for name in skipped:
        print(f"skipped\t{name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", type=Path, default=DEFAULT_APK)
    parser.add_argument("--replacements-dir", type=Path, default=DEFAULT_REPLACEMENTS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--update-offline-checksum",
        dest="update_offline_checksum",
        action="store_true",
        default=True,
        help="Update assets/offlinechecksumcache entries. This is the default.",
    )
    parser.add_argument(
        "--no-update-offline-checksum",
        dest="update_offline_checksum",
        action="store_false",
        help="Skip updating assets/offlinechecksumcache.",
    )
    args = parser.parse_args()
    patch_apk(
        args.apk,
        args.replacements_dir,
        args.output,
        update_checksum=args.update_offline_checksum,
    )


if __name__ == "__main__":
    main()
