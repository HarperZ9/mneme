# Navigation recall

Navigation recall walks a topic outline of your memories one level at a time and ranks only the memories it reaches. The receipt records the path: every branch it scored, the score, and whether it followed that branch. You can see why a memory was or was not considered, and you can re-run the walk to get the same path.

Flat recall still ranks every memory and stays the default. Navigation recall is opt-in.

## Bar, set before the run

This bar was committed before the navigator existed, against the fixed corpus in `tests/fixtures/navrecall_corpus.json` (120 synthetic memories in 12 topics, 50 queries, one answering memory per query).

- **B1, recall.** Navigation recall@5 is at least flat keyword recall@5 minus 0.04. That allows at most 2 of 50 queries lost.
- **B2, memories examined.** The mean number of memories the navigator reaches per query is at most 60, half the corpus.
- **Control.** The same walk over an outline with memories dealt to random leaves must miss B1. If a random outline also passes, the outline is doing no work and the result does not count.
- **Ship rule.** The mode ships opt-in whatever the result. The numbers below are reported either way, and flat recall stays the default.

## Results

Pending the run.

## Limits

The corpus is synthetic and written by the same author as the navigator, so it does not show how the walk behaves on real memory stores. A pass shows the walk can hold recall while reading less on short, topic-clustered facts. It does not show a speed gain, since the outline build reads every memory once.
