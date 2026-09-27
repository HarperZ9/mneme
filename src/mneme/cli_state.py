"""cli_state.py: `mneme status` and `mneme doctor`, where the memory lives.

Both print one JSON report (state_report.py) and open the database read-only,
so neither creates, migrates nor changes it. `status` exits 0 whenever it can
describe the state. `doctor` also re-derives the audit chain and exits 1 when
the report carries a warning.
"""
from __future__ import annotations

import json

from .state_report import describe


def add_parsers(sub) -> None:
    st = sub.add_parser(
        "status", help="show where the memory database is, its snapshot directory and counts",
        description="Report the database's absolute path, whether it is the default "
                    "location, the replay snapshot directory, files and row counts. "
                    "Read-only; exit 0.")
    st.set_defaults(func=cmd_status)
    dr = sub.add_parser(
        "doctor", help="check the database's location, schema history and audit chain",
        description="Everything status reports, plus the audit chain. Read-only; exit 1 "
                    "when a warning needs attention.")
    dr.set_defaults(func=cmd_doctor)


def cmd_status(args) -> int:
    print(json.dumps(describe(args.state, check="status"), indent=2))
    return 0


def cmd_doctor(args) -> int:
    report = describe(args.state, check="doctor")
    print(json.dumps(report, indent=2))
    return 1 if report["warnings"] else 0
