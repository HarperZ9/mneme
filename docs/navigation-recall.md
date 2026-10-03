# Navigation recall

Navigation recall walks a topic outline of your memories one level at a time and ranks only the memories it reaches. The receipt records the path: every branch it scored, the score, and whether it followed that branch. You can see why a memory was or was not considered, and you can re-run the walk to get the same path.

Flat recall still ranks every memory and stays the default. Navigation recall is opt-in.

```python
from mneme import AgentMemory

mem = AgentMemory("memory.db")
receipt = mem.navigate("when does my passport expire", top_k=5)
receipt["reached"]   # memories the walk ranked
receipt["path"]      # every node visited, every child scored, followed or not
```

Re-run the bench with `python -m mneme.navbench`. Check a receipt with `mneme.navigate.verify_navigation(receipt, rows)`, which replays the walk from the rows and refuses a receipt whose path, reach or hits differ.

## How the walk works

The outline splits memories by shared terms: the term most memories share becomes a branch, those memories leave the pool, and the next term is picked from the rest, up to six branches per node. A node with 12 memories or fewer is a leaf. At each node the walk scores every branch against the query with BM25 and follows each branch scoring above zero and at least half the best branch's score. Memories in the leaves it reaches are ranked with the same BM25 flat recall uses.

## Bar, set before the run

This bar was committed before the navigator existed, against the fixed corpus in `tests/fixtures/navrecall_corpus.json` (120 synthetic memories in 12 topics, 50 queries, one answering memory per query).

- **B1, recall.** Navigation recall@5 is at least flat keyword recall@5 minus 0.04. That allows at most 2 of 50 queries lost.
- **B2, memories examined.** The mean number of memories the navigator reaches per query is at most 60, half the corpus.
- **Control.** The same walk over an outline with memories dealt to random leaves must miss B1. If a random outline also passes, the outline is doing no work and the result does not count.
- **Ship rule.** The mode ships opt-in whatever the result. The numbers below are reported either way, and flat recall stays the default.

## Results

Run on 2026-10-03, seed 20261003, top 5.

| Arm | recall@5 | 95% Wilson interval | memories ranked per query |
|---|---|---|---|
| Flat keyword recall | 0.86 (43 of 50) | 0.74 to 0.93 | 120 |
| Navigation recall | 0.84 (42 of 50) | 0.72 to 0.92 | 41.9 |
| Random outline, seed 20261003 | 0.76 (38 of 50) | 0.63 to 0.86 | 43.5 |

- **B1 passes.** Navigation loses one query flat recall answers ("what music plays while working") and gains none.
- **B2 passes.** The walk ranks 35% of the corpus on average.
- **The pre-stated control misses B1**, so by the stated rule the result counts.

**The topic structure is not shown to be doing the work.** After the pre-stated run I repeated the random-outline control over 20 seeds (seeds 0 to 19). Mean recall@5 was 0.848, range 0.78 to 0.86, and most seeds pass B1. The seed named in the bar was one of the unlucky ones. The likely cause: each branch is scored on the full text of its memories, so the branch that holds a rare query term wins wherever that memory sits. A walk over any partition of this size finds it. The reduction in memories ranked is real; the claim that a topic outline earns it is not supported.

An exploratory variant, run after the bar and not part of it, scored each branch on its 8 or 16 most shared terms in place of its full text, which is closer to reading an outline. Recall@5 fell to 0.62 and 0.64. Reading summaries alone loses too much on this corpus.

## Limits

The corpus is synthetic and written by the same author as the navigator, so it does not show how the walk behaves on real memory stores. A pass shows the walk can hold recall while ranking fewer memories on short, topic-clustered facts. It does not show a speed gain or a saving in text read: the outline build reads every memory once, and branch scoring reads each branch's full text.
