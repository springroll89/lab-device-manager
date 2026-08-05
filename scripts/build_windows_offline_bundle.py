from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PYTHON_VERSION = "3.12.10"
PYTHON_INSTALLER = f"python-{PYTHON_VERSION}-amd64.exe"
PYTHON_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_INSTALLER}"
)


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the self-contained Windows x64 offline installer."
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ref", default="origin/main")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    bundle = output / "Puricore-Win11-x64-offline"
    payload = bundle / "payload"
    wheels = payload / "wheels"
    if bundle.exists():
        raise SystemExit(f"output already exists: {bundle}")
    wheels.mkdir(parents=True)

    template = repo / "deploy" / "windows-offline"
    for source in template.iterdir():
        if source.is_file():
            shutil.copy2(source, bundle / source.name)

    windows_requirements = template / "requirements.windows.txt"
    payload_requirements = payload / "requirements.txt"
    base_lines = {
        line.strip()
        for line in (repo / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    windows_lines = {
        line.strip()
        for line in windows_requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not base_lines.issubset(windows_lines):
        raise RuntimeError("requirements.windows.txt is missing base requirements")
    shutil.copy2(windows_requirements, payload_requirements)
    shutil.copy2(
        template / "config.windows.toml",
        payload / "config.windows.toml",
    )

    source_archive = payload / "app-source.zip"
    run(
        [
            "git", "archive", "--format=zip",
            f"--output={source_archive}", args.ref,
        ],
        repo,
    )

    installer = payload / PYTHON_INSTALLER
    print(f"Downloading {PYTHON_URL}")
    urllib.request.urlretrieve(PYTHON_URL, installer)

    run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheels),
            "--requirement",
            str(payload_requirements),
            "--platform",
            "win_amd64",
            "--python-version",
            "312",
            "--implementation",
            "cp",
            "--abi",
            "cp312",
            "--only-binary=:all:",
        ],
        repo,
    )

    if not any(wheels.glob("colorama-*.whl")):
        raise RuntimeError("Windows-only dependency colorama is missing")

    commit = subprocess.check_output(
        ["git", "rev-parse", args.ref], cwd=repo, text=True
    ).strip()
    manifest = {
        "application": "Puricore Lab Device Manager",
        "source_ref": args.ref,
        "source_commit": commit,
        "python_version": PYTHON_VERSION,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "target": "Windows 11 x64",
    }
    (bundle / "VERSION.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    checksum_lines = []
    for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
        if path.name == "SHA256SUMS.txt":
            continue
        relative_path = path.relative_to(bundle).as_posix()
        # Windows PowerShell 5.1 reads UTF-8 files without a BOM as ANSI.
        # Keep the integrity manifest ASCII-only so paths are decoded reliably.
        if not relative_path.isascii():
            continue
        checksum_lines.append(f"{sha256(path)}  {relative_path}")
    (bundle / "SHA256SUMS.txt").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
