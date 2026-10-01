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
            python -m pip install "https://github.com/HarperZ9/mneme/releases/download/v{readme_url_version}/flywheel_mneme-{readme_url_version}-py3-none-any.whl"
            ```
        """).lstrip(),
        encoding="utf-8",
    )


def test_current_tree_metadata_is_aligned_and_publication_respects_status():
    metadata = verify_metadata(ROOT)

    assert __version__ == metadata.package_version
    if metadata.changelog_is_unreleased:
        with pytest.raises(ReleaseMetadataError, match="still unreleased"):
            verify_metadata(ROOT, publication_tag=f"v{metadata.package_version}")
    else:
        verify_metadata(ROOT, publication_tag=f"v{metadata.package_version}")


def test_publication_guard_rejects_unreleased_candidate_fixture(tmp_path):
    _write_project(
        tmp_path,
        changelog_heading="0.4.1 (unreleased)",
        readme_release="0.4.0",
        readme_url_version="0.4.0",
    )
    with pytest.raises(ReleaseMetadataError, match="unreleased"):
        verify_metadata(tmp_path, publication_tag="v0.4.1")


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


@pytest.mark.parametrize("name", ["README.md", "USAGE.md"])
def test_publication_guard_rejects_a_doc_that_still_calls_the_version_unreleased(
        tmp_path, name):
    _write_project(tmp_path)
    doc = tmp_path / name
    prior = doc.read_text(encoding="utf-8") if doc.exists() else ""
    doc.write_text(prior + "\nFrom 0.4.1 (unreleased; install\nfrom source), forget "
                   "erases turns.\n", encoding="utf-8")

    with pytest.raises(ReleaseMetadataError, match=f"{name}.*unreleased"):
        verify_metadata(tmp_path, publication_tag="v0.4.1")


def test_an_unreleased_note_about_another_version_does_not_block_publication(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "USAGE.md").write_text("From 0.9.0 (unreleased), more.\n", encoding="utf-8")

    verify_metadata(tmp_path, publication_tag="v0.4.1")


def test_publication_guard_rejects_a_changelog_entry_that_says_not_published(tmp_path):
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace(
        "- Release entry.", "BREAKING. Not published.\n\n- Release entry."), encoding="utf-8")

    with pytest.raises(ReleaseMetadataError, match="CHANGELOG.*not published"):
        verify_metadata(tmp_path, publication_tag="v0.4.1")
