"""inspect.py — a self-contained white-box view of a memory store.

The class advertises "white-box debugging: every intermediate artifact is
visible." mneme's inspector shows the same layered artifacts AND the three
things their dashboards cannot: each memory's provenance (the turn it came
from), its live drift verdict (MATCH / DRIFT / UNVERIFIABLE), and the
hash-chained audit log of every forget/update. One offline HTML file, no
server, no network — the whole memory, and why you can trust each piece.

Pure builder: `build_inspect` reads the store into a JSON-able snapshot;
`render_inspect_html` renders it. Deterministic.
"""
from __future__ import annotations

import html
import json

from .drift import check_memory


def build_inspect(memory) -> dict:
    """Snapshot the store: L0 turns, L1-L3 memories with provenance + drift
    verdict, and the audit log. Reads through the AgentMemory facade's store."""
    store = memory.store
    turns = [{"id": t["id"], "session": t["session"], "role": t["role"],
              "text": t["text"]} for t in store.turns()]
    layers = {}
    for layer in ("L1", "L2", "L3"):
        rows = []
        for r in store.memories(layer=layer):
            v = check_memory(store, r["id"])
            rows.append({"id": r["id"], "text": r["text"],
                         "source_ids": json.loads(r["source_ids"]),
                         "extractor": r["extractor"], "criterion": r["criterion"],
                         "verdict": v.verdict, "reason": v.reason})
        layers[layer] = rows
    audit = memory.audit()
    return {"schema": "mneme.inspect/1",
            "turns": turns, "layers": layers,
            "audit": {"entries": audit["entries"], "chain_intact": audit["chain_intact"],
                      "log": audit["log"]},
            "counts": {"turns": len(turns),
                       "L1": len(layers["L1"]), "L2": len(layers["L2"]), "L3": len(layers["L3"])}}


_CSS = """
:root{--bg:#f4f3ef;--ink:#0b0c0e;--muted:#565a62;--hair:rgba(11,12,14,.14);
--iris:#4636e8;--ok:#1f6b45;--warn:#8a5a12;--bad:#b23b3b;--mono:ui-monospace,Consolas,monospace;--body:Arial,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body);padding:1.4rem;line-height:1.5}
h1{font:600 1.2rem var(--body);margin:0 0 .2rem}
.sub{color:var(--muted);font-size:.85rem;max-width:74ch}
.tier{border:1px solid var(--hair);border-radius:10px;padding:.7rem 1rem;margin:.8rem 0;background:#fbfaf7}
.tier h2{font:600 .78rem var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:.1rem 0 .5rem}
.mem{border-top:1px solid var(--hair);padding:.5rem 0;display:grid;grid-template-columns:1fr auto;gap:.6rem;align-items:start}
.mem .t{font-size:.9rem}
.mem .p{font:.7rem var(--mono);color:var(--muted);margin-top:.2rem}
.chip{font:600 .66rem var(--mono);padding:.2em .55em;border-radius:999px;border:1px solid;white-space:nowrap}
.chip.MATCH{color:var(--ok);border-color:var(--ok)}
.chip.DRIFT{color:var(--bad);border-color:var(--bad)}
.chip.UNVERIFIABLE{color:var(--warn);border-color:var(--warn)}
.turn{font-size:.85rem;padding:.25rem 0;border-top:1px solid var(--hair)}
.turn .r{font:.7rem var(--mono);color:var(--iris)}
.audit .row{font:.74rem var(--mono);padding:.3rem 0;border-top:1px solid var(--hair)}
.audit .op{font-weight:600}.audit .op.forget{color:var(--bad)}.audit .op.update{color:var(--warn)}
.intact{font:600 .72rem var(--mono);padding:.2em .6em;border-radius:6px;border:1px solid var(--ok);color:var(--ok)}
.empty{color:var(--muted);font-style:italic;font-size:.85rem}
"""


def render_inspect_html(snap: dict) -> str:
    c = snap["counts"]
    def esc(s): return html.escape(str(s))
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mneme memory inspector</title><style>{_CSS}</style></head><body>
<h1>mneme memory inspector</h1>
<div class="sub">White-box view of the memory: {c['turns']} turns, {c['L1']} atoms,
{c['L2']} scenarios, {c['L3']} persona. Every memory shows the turn it came from
and its live drift verdict; the audit log below records every forget/update.
Offline, self-contained.</div>"""]

    # L1-L3 with provenance + drift
    names = {"L1": "atoms (L1)", "L2": "scenarios (L2)", "L3": "persona (L3)"}
    for layer in ("L1", "L2", "L3"):
        rows = snap["layers"][layer]
        parts.append(f'<div class="tier"><h2>{names[layer]}</h2>')
        if not rows:
            parts.append('<div class="empty">none</div>')
        for m in rows:
            parts.append(
                f'<div class="mem"><div><div class="t">{esc(m["text"])}</div>'
                f'<div class="p">from {esc(", ".join(m["source_ids"]))} · '
                f'{esc(m["extractor"])} · {esc(m["criterion"])}</div></div>'
                f'<span class="chip {esc(m["verdict"])}" title="{esc(m["reason"])}">{esc(m["verdict"])}</span></div>')
        parts.append('</div>')

    # L0 turns
    parts.append('<div class="tier"><h2>turns (L0)</h2>')
    for t in snap["turns"]:
        parts.append(f'<div class="turn"><span class="r">{esc(t["role"])}</span> {esc(t["text"])}</div>')
    if not snap["turns"]:
        parts.append('<div class="empty">none</div>')
    parts.append('</div>')

    # audit log
    a = snap["audit"]
    intact = '<span class="intact">chain intact ✓</span>' if a["chain_intact"] \
        else '<span class="chip DRIFT">chain broken</span>'
    parts.append(f'<div class="tier audit"><h2>audit log — {a["entries"]} entries {intact}</h2>')
    if not a["log"]:
        parts.append('<div class="empty">no forget/update recorded</div>')
    for e in a["log"]:
        parts.append(
            f'<div class="row"><span class="op {esc(e["op"])}">{esc(e["op"])}</span> '
            f'{esc(e["memory_id"])} ({esc(e["layer"])}) · {esc(e["reason"] or "no reason given")} '
            f'· {esc(e["before_sha"][:12])}…→{esc((e["after_sha"] or "gone")[:12])}</div>')
    parts.append('</div></body></html>')
    return "".join(parts)
