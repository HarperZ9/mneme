"""Falsifiers for the README's account of the erase receipt.

The receipt has a `status` and a list of `findings`, and they are different
vocabularies. The README once listed status values as findings. It now keeps
a table from each finding code to the status it gives, and this test pins that
table to the code, so a new finding cannot ship undocumented and a documented
mapping cannot drift from `erase_receipt._STATUS_OF`.
"""
from __future__ import annotations

import re
from pathlib import Path

from mneme.erase_receipt import ORDER, _STATUS_OF

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)


def test_every_finding_code_is_documented_with_its_status():
    documented = dict(ROW.findall(README))
    expected = {code: ORDER[rank] for code, rank in _STATUS_OF.items()}
    expected["post_commit_failure"] = "erased_unverified"

    assert documented == expected


def test_the_readme_says_what_erased_means_for_a_short_text():
    section = README.split("## Accountable forgetting", 1)[1].split("\n## ", 1)[0]

    assert "under 16 bytes" in section
    assert "structural" in section
