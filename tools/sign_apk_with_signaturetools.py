from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


CLI_CFG = """[Application]
app.name=ApkSignerCLI
app.mainjar=apksigner.jar
app.version=1.0
app.preferences.id=ApkSignerCLI_id
app.mainclass=com/android/apksigner/ApkSignerTool
app.classpath=
app.runtime=$APPDIR\\runtime
app.identifier=ApkSignerCLI_id

[JVMOptions]

[JVMUserOptions]

[ArgOptions]
"""


def find_first(paths: list[Path], label: str) -> Path:
    for path in paths:
        if path.exists():
            return path
    candidates = "\n".join(str(path) for path in paths)
    raise FileNotFoundError(f"Could not find {label}. Tried:\n{candidates}")


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("RUN", " ".join(cmd))
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(completed.stdout)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def sign_apk(
    tool_root: Path,
    input_apk: Path,
    output_apk: Path,
    keystore: Path,
    key_alias: str,
    store_password: str,
    key_password: str,
    aligned_apk: Path | None = None,
    verify: bool = True,
) -> None:
    tool_root = tool_root.resolve()
    app_dir = tool_root / "app"
    launcher = find_first(
        [
            tool_root / "SignatureTools.exe",
            tool_root / "ApkSignTools.exe",
            app_dir / "SignatureTools.exe",
            app_dir / "ApkSignTools.exe",
        ],
        "SignatureTools launcher",
    )
    zipalign = find_first([app_dir / "zipalign.exe", tool_root / "zipalign.exe"], "zipalign.exe")
    cfg = find_first(
        [app_dir / "ApkSignTools.cfg", app_dir / "SignatureTools.cfg"],
        "launcher cfg",
    )

    for path in [input_apk, keystore]:
        if not path.exists():
            raise FileNotFoundError(path)

    output_apk.parent.mkdir(parents=True, exist_ok=True)
    if aligned_apk is None:
        aligned_apk = output_apk.with_name(output_apk.stem + "_aligned_unsigned.apk")
    aligned_apk.parent.mkdir(parents=True, exist_ok=True)

    output_apk.unlink(missing_ok=True)
    aligned_apk.unlink(missing_ok=True)

    run([str(zipalign), "-f", "-v", "4", str(input_apk), str(aligned_apk)])

    original_cfg = cfg.read_text(encoding="utf-8", errors="replace")
    try:
        cfg.write_text(CLI_CFG, encoding="utf-8", newline="\n")
        run(
            [
                str(launcher),
                "sign",
                "--ks",
                str(keystore),
                "--ks-key-alias",
                key_alias,
                "--ks-pass",
                f"pass:{store_password}",
                "--key-pass",
                f"pass:{key_password}",
                "--out",
                str(output_apk),
                str(aligned_apk),
            ],
            cwd=app_dir,
        )
        if verify:
            run([str(launcher), "verify", "-v", str(output_apk)], cwd=app_dir)
    finally:
        cfg.write_text(original_cfg, encoding="utf-8", newline="\n")

    print(f"signed apk: {output_apk}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sign an APK by driving SignatureTools' embedded zipalign and apksigner launcher."
    )
    parser.add_argument("--tool-root", type=Path, required=True, help="SignatureTools directory.")
    parser.add_argument("--input-apk", type=Path, required=True, help="Unsigned APK from patch_apk_assets.py.")
    parser.add_argument("--output-apk", type=Path, required=True, help="Signed APK output path.")
    parser.add_argument("--keystore", type=Path, required=True, help="Local .jks/.keystore file.")
    parser.add_argument("--key-alias", required=True)
    parser.add_argument("--store-password", required=True)
    parser.add_argument("--key-password", required=True)
    parser.add_argument("--aligned-apk", type=Path, help="Optional intermediate aligned APK path.")
    parser.add_argument("--no-verify", action="store_true", help="Skip apksigner verify.")
    args = parser.parse_args()

    sign_apk(
        tool_root=args.tool_root,
        input_apk=args.input_apk,
        output_apk=args.output_apk,
        keystore=args.keystore,
        key_alias=args.key_alias,
        store_password=args.store_password,
        key_password=args.key_password,
        aligned_apk=args.aligned_apk,
        verify=not args.no_verify,
    )


if __name__ == "__main__":
    main()
