"""Sanity-check the built distribution (run by CI and by the release workflow).

Usage: python3 .github/scripts/check_dist.py dist [expected-version]

Standard library only.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

EXPECTED_METADATA_VERSION = "2.4"
REQUIRED_IN_WHEEL = (
    "payhub/__init__.py",
    "payhub/py.typed",
    "payhub/client.py",
    "payhub/resources.py",
    "payhub/webhook.py",
)
REQUIRED_IN_SDIST = (
    "src/payhub/py.typed",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "pyproject.toml",
)


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def main() -> None:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    expected = sys.argv[2] if len(sys.argv) > 2 else None

    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        found = sorted(p.name for p in dist.iterdir())
        fail(f"expected exactly one wheel and one sdist in {dist}, found {found}")

    wheel, sdist = wheels[0], sdists[0]

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")

    for required in REQUIRED_IN_WHEEL:
        if required not in names:
            fail(f"{required} is missing from the wheel")
    stray = [n for n in names if not (n.startswith("payhub/") or ".dist-info/" in n)]
    if stray:
        fail(f"unexpected files in the wheel: {stray}")

    header = metadata.split("\n\n", 1)[0]
    fields = [
        line.split(": ", 1)
        for line in header.splitlines()
        if ": " in line and not line.startswith(" ")
    ]
    version = next(value for key, value in fields if key == "Version")
    metadata_version = next(value for key, value in fields if key == "Metadata-Version")

    if metadata_version != EXPECTED_METADATA_VERSION:
        # Pinned in pyproject.toml (core-metadata-version). A newer value must
        # be a deliberate change, validated by a release dry run.
        fail(
            f"unexpected core metadata version {metadata_version} "
            f"(expected {EXPECTED_METADATA_VERSION})"
        )
    if any(key == "Requires-Dist" and "extra ==" not in value for key, value in fields):
        fail("the wheel declares runtime dependencies")
    if not any(key == "Requires-Python" and value == ">=3.9" for key, value in fields):
        fail("Requires-Python is not >=3.9")
    if expected is not None and version != expected:
        fail(f"built version {version} does not match the expected {expected}")
    if wheel.name != f"barkapay_payhub-{version}-py3-none-any.whl":
        fail(f"unexpected wheel file name {wheel.name} for version {version}")
    if sdist.name != f"barkapay_payhub-{version}.tar.gz":
        fail(f"unexpected sdist file name {sdist.name} for version {version}")

    with tarfile.open(sdist) as archive:
        sdist_names = archive.getnames()
    root = f"barkapay_payhub-{version}/"
    for required in REQUIRED_IN_SDIST:
        if root + required not in sdist_names:
            fail(f"{required} is missing from the sdist")

    print(
        f"OK: {wheel.name} and {sdist.name} (version {version}, "
        f"metadata {metadata_version}, py.typed present, no runtime dependency)"
    )


if __name__ == "__main__":
    main()
