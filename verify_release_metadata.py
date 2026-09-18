"""Verify mneme release metadata before packaging or publication.

The default check permits an unreleased source candidate when package metadata,
runtime version, and the top changelog entry agree. Publication mode is stricter:
the tag, package metadata, runtime version, top changelog entry, and README
release wheel reference must all name the same final version.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


VERSION_RE = r"[0-9]+\.[0-9]+\.[0-9]+"


class ReleaseMetadataError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseMetadata:
    package_version: str
    runtime_version: str
    changelog_version: str
    changelog_status: str
    readme_release_version: str
    readme_asset_versions: tuple[str, ...]

    @property
    def changelog_is_unreleased(self) -> bool:
        return "unreleased" in self.changelog_status.lower()


def _read_project_version(root: Path) -> str:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        version = pyproject["project"]["version"]
    except KeyError as exc:
        raise ReleaseMetadataError("pyproject.toml missing project.version") from exc
    if not isinstance(version, str):
        raise ReleaseMetadataError("pyproject.toml project.version must be a string")
    return version


def _read_runtime_version(root: Path) -> str:
    init_path = root / "src" / "mneme" / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(node.value)
                    if not isinstance(value, str):
                        raise ReleaseMetadataError("__version__ must be a string")
                    return value
    raise ReleaseMetadataError("src/mneme/__init__.py missing __version__")


def _read_changelog_top_entry(root: Path) -> tuple[str, str]:
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^##\s+({VERSION_RE})([^\n]*)$",
        changelog,
        re.MULTILINE,
    )
    if match is None:
        raise ReleaseMetadataError("CHANGELOG.md top release entry is missing a semantic version")
    return match.group(1), match.group(2).strip()


def _read_readme_release(root: Path) -> tuple[str, tuple[str, ...]]:
    readme = (root / "README.md").read_text(encoding="utf-8")
    heading = re.search(
        rf"^### Released v({VERSION_RE}) wheel$",
        readme,
        re.MULTILINE,
    )
    if heading is None:
        raise ReleaseMetadataError("README.md missing 'Released vX.Y.Z wheel' heading")
    asset_versions = tuple(
        match.group(1)
        for match in re.finditer(
            rf"releases/download/v({VERSION_RE})/mneme_memory-\1-py3-none-any\.whl",
            readme,
        )
    )
    if not asset_versions:
        raise ReleaseMetadataError("README.md missing release wheel URL")
    return heading.group(1), asset_versions


def load_metadata(root: Path) -> ReleaseMetadata:
    readme_release, readme_assets = _read_readme_release(root)
    changelog_version, changelog_status = _read_changelog_top_entry(root)
    return ReleaseMetadata(
        package_version=_read_project_version(root),
        runtime_version=_read_runtime_version(root),
        changelog_version=changelog_version,
        changelog_status=changelog_status,
        readme_release_version=readme_release,
        readme_asset_versions=readme_assets,
    )


def _normalize_tag(tag: str) -> str:
    value = tag.removeprefix("refs/tags/").removeprefix("v")
    if not re.fullmatch(VERSION_RE, value):
        raise ReleaseMetadataError(f"publication tag must be vX.Y.Z, got {tag!r}")
    return value


def verify_metadata(root: Path, *, publication_tag: str | None = None) -> ReleaseMetadata:
    metadata = load_metadata(root)
    version = metadata.package_version

    if metadata.runtime_version != version:
        raise ReleaseMetadataError(
            f"__version__ {metadata.runtime_version} != pyproject version {version}"
        )
    if metadata.changelog_version != version:
        raise ReleaseMetadataError(
            f"CHANGELOG top entry {metadata.changelog_version} != pyproject version {version}"
        )

    if metadata.changelog_is_unreleased:
        if metadata.readme_release_version == version:
            raise ReleaseMetadataError(
                f"README claims v{version} is released while CHANGELOG marks it unreleased"
            )
    else:
        _verify_readme_final_release(metadata, version)

    if publication_tag is not None:
        tag_version = _normalize_tag(publication_tag)
        if tag_version != version:
            raise ReleaseMetadataError(f"tag {tag_version} != pyproject version {version}")
        if metadata.changelog_is_unreleased:
            raise ReleaseMetadataError(
                f"CHANGELOG top entry for {version} is still unreleased"
            )
        _verify_readme_final_release(metadata, version)

    return metadata


def _verify_readme_final_release(metadata: ReleaseMetadata, version: str) -> None:
    if metadata.readme_release_version != version:
        raise ReleaseMetadataError(
            f"README released wheel version {metadata.readme_release_version} != {version}"
        )
    stale_assets = sorted({item for item in metadata.readme_asset_versions if item != version})
    if stale_assets:
        raise ReleaseMetadataError(
            f"README release wheel URL version(s) {stale_assets} != {version}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--publication-tag")
    args = parser.parse_args(argv)

    try:
        metadata = verify_metadata(args.root, publication_tag=args.publication_tag)
    except ReleaseMetadataError as exc:
        print(f"release metadata error: {exc}", file=sys.stderr)
        return 1
    phase = "unreleased-candidate" if metadata.changelog_is_unreleased else "final-release"
    print(f"release metadata ok: version={metadata.package_version} phase={phase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
