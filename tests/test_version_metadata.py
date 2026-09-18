from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mneme import __version__


def test_package_version_matches_pyproject_and_changelog_top_entry():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = pyproject["project"]["version"]

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    top_entry = re.search(r"^##\s+([0-9]+\.[0-9]+\.[0-9]+)\b", changelog, re.MULTILINE)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    public_release = re.search(r"^### Released v([0-9]+\.[0-9]+\.[0-9]+) wheel$", readme, re.MULTILINE)

    assert __version__ == package_version
    assert top_entry is not None
    assert top_entry.group(1) == package_version
    assert public_release is not None
    assert package_version != public_release.group(1)
