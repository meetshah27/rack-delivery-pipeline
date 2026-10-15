"""Build the deployment artifacts into ./build:

- lambda_code.zip: orchestration/ and simulator/ (the handlers import both)
- lambda_layer.zip: numpy, pyarrow, pyyaml for the Lambda python3.12 runtime (Amazon Linux
  2023, glibc 2.34: manylinux_2_28 wheels), at the versions in
  uv.lock (boto3 comes with the Lambda runtime)
- dbt_project.zip: what CodeBuild needs to run dbt: buildspec, uv project, dbt/, ops/

    python -m ops.package
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
LAYER_PACKAGES = ("numpy", "pyarrow", "pyyaml")
LAMBDA_UNZIPPED_LIMIT = 250 * 1024 * 1024
SKIP_PARTS = {"__pycache__", "tests", "target", "dbt_packages", "logs", ".user.yml"}


def _zip(target: Path, files: list[tuple[Path, str]]) -> None:
    """Deterministic zip: sorted entries, fixed timestamps."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def _tree(directory: Path, prefix: str = "") -> list[tuple[Path, str]]:
    """Files under directory, without build output, caches or hidden directories."""

    def wanted(path: Path) -> bool:
        parts = path.relative_to(ROOT).parts
        hidden_dir = any(part.startswith(".") for part in parts[:-1])
        return path.is_file() and not hidden_dir and not SKIP_PARTS & set(parts)

    return [
        (path, prefix + path.relative_to(directory.parent).as_posix())
        for path in directory.rglob("*")
        if wanted(path) and path.suffix != ".pyc"
    ]


def build_code() -> Path:
    target = BUILD / "lambda_code.zip"
    _zip(target, _tree(ROOT / "orchestration") + _tree(ROOT / "simulator"))
    return target


def _locked_requirements() -> list[str]:
    exported = subprocess.run(
        ["uv", "export", "--no-dev", "--no-hashes", "--format", "requirements-txt"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pins = [line.split(";")[0].strip() for line in exported.splitlines() if "==" in line]
    return [pin for pin in pins if pin.split("==")[0].lower() in LAYER_PACKAGES]


def build_layer() -> Path:
    target = BUILD / "lambda_layer.zip"
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "python"
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--quiet",
                "--target",
                str(site),
                "--python-platform",
                "x86_64-manylinux_2_28",
                "--python-version",
                "3.12",
                "--only-binary",
                ":all:",
                *_locked_requirements(),
            ],
            cwd=ROOT,
            check=True,
        )
        files = [
            (p, p.relative_to(Path(tmp)).as_posix())
            for p in site.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        ]
        unzipped = sum(p.stat().st_size for p, _ in files)
        if unzipped > LAMBDA_UNZIPPED_LIMIT:
            raise SystemExit(f"layer is {unzipped / 2**20:.0f} MiB unzipped, over Lambda's 250 MiB")
        _zip(target, files)
    return target


def build_dbt_project() -> Path:
    target = BUILD / "dbt_project.zip"
    top = [
        ROOT / name for name in ("buildspec.yml", "pyproject.toml", "uv.lock", ".python-version")
    ]
    _zip(target, [(p, p.name) for p in top] + _tree(ROOT / "dbt") + _tree(ROOT / "ops"))
    return target


def main() -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    for build in (build_code, build_layer, build_dbt_project):
        path = build()
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size / 2**20:.1f} MiB")


if __name__ == "__main__":
    main()
