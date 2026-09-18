from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mneme import __version__
from verify_release_metadata import ReleaseMetadataError, verify_metadata


def _write_project(
    root: Path,
    *,
    package_version: str = "0.4.1",
    runtime_version: str = "0.4.1",
    changelog_heading: str = "0.4.1 (2026-09-17)",
    readme_release: str = "0.4.1",
    readme_url_version: str | None = None,
) -> None:
    readme_url_version = readme_release if readme_url_version is None else readme_url_version
    (root / "pyproject.toml").write_text(
        textwrap.dedent(f"""
            [project]
            name = "mneme-memory"
            version = "{package_version}"
        """).lstrip(),
        encoding="utf-8",
    )
    package = root / "src" / "mneme"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f'__version__ = "{runtime_version}"\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {changelog_heading}\n\n- Release entry.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        textwrap.dedent(f"""
            # mneme

            ### Released v{readme_release} wheel

            ```bash
            python -m pip install "https://github.com/HarperZ9/mneme/releases/download/v{readme_url_version}/mneme_memory-{readme_url_version}-py3-none-any.whl"
            ```
        """).lstrip(),
        encoding="utf-8",
    )


def test_current_release_candidate_metadata_is_allowed_but_not_publishable():
    assert __version__ == "0.4.1"

    verify_metadata(ROOT)

    with pytest.raises(ReleaseMetadataError, match="unreleased"):
        verify_metadata(ROOT, publication_tag="v0.4.1")


def test_publication_guard_accepts_final_aligned_release_metadata(tmp_path):
    _write_project(tmp_path)

    verify_metadata(tmp_path, publication_tag="v0.4.1")


def test_publication_guard_rejects_metadata_mismatch(tmp_path):
    _write_project(tmp_path, runtime_version="0.4.0")

    with pytest.raises(ReleaseMetadataError, match="__version__"):
        verify_metadata(tmp_path, publication_tag="v0.4.1")


def test_publication_guard_rejects_stale_readme_asset_url(tmp_path):
    _write_project(tmp_path, readme_url_version="0.4.0")

    with pytest.raises(ReleaseMetadataError, match="README"):
        verify_metadata(tmp_path, publication_tag="v0.4.1")
