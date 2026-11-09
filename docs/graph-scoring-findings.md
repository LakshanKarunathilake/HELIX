# Graph scoring: what actually helps (2026-11-09)

Offline study of the graph step, run on the type-scoped / abbreviation-expanded
candidate pools (`eval/run_hint_analysis.py`) with UMLS 2025AA MRREL as the graph
(SIB dropped). Everything is training-free: a mention gives text, span and type only.

Reproduce (needs the cached pools in `eval/out/typescope/`, no servers):

```bash
export UMLS_META=".../2025AA/META" SEMGROUPS=".../SemGroups.txt"
python eval/run_graph_scoring.py score --dataset medmentions --split dev   # grid + isolation
python eval/run_graph_scoring.py cross --split dev                         # BC5CDR chem <-> disease
```

## 1. An earlier negative result was a bug

`relation_fn(kind="any")` in `run_hint_analysis.py` returned True for **unrelated** pairs
(an empty relation set passed `kinds == "any" or ...`). So every "graph any",
"deg-norm" and "gold-anchor ceiling" number from that script was wrong. The RO/hier
rules were correct. Fixed, with a regression test. Corrected numbers on dev, base pool:

| | before fix | after fix |
|---|---|---|
| MedMentions, graph any near-tie 0.02 | 56.37 (−246) | **58.96 (+814, p≈1e-75)** |
| Disease, same rule | 73.51 (−14) | 74.17 (+14, p=0.08) |
| Gold anchors prefer gold over the top-1 (share of fixable mentions, MedMentions) | 1.7% | 26.7% |

"Raw link counts hurt everywhere, even with gold anchors" is **not true**. They hurt
only when nothing limits which candidates the graph may move (no gate).

## 2. Headline: the rule now in `optimize_selection`

**Rule:**
- An exact dense tie at the top is always re-ranked by the graph.
- Otherwise the graph acts only when the top-1 score is below 0.90, and only among
  candidates within 0.02 of it.
- Graph support = number of distinct anchor concepts linked. Anchors are the other
  mentions' top-1s; mentions with the same text are excluded.
- A candidate needs strictly more support than the top-1 to win.

Baseline below is the abbreviation-expanded pool. Fixed / broken counts are against
that baseline; p-values are exact McNemar.

| Full index | dev | test |
|---|---|---|
| MedMentions | 60.54 → **62.52** (+1351 / −541) | 59.79 → **61.86** (+1254 / −420) |
| BC5CDR disease | 77.46 → **78.03** (+32 / −8, p=2e-4) | 78.75 → 78.98 (+20 / −10, p=0.10) |
| BC5CDR chemical | 91.04 → 90.99 (+0 / −3) | 88.44 → 88.50 (+3 / −0) |

Running the real `Neo4jGraphClient.optimize_selection`, with MRREL standing in for
Neo4j, gives on dev: MedMentions 62.88, disease 77.91, chemical 90.99. The tool code
does what was measured.

**Overlap with the names tie-break.** On MedMentions, the existing "abbrev + ties: more
KB names" rule (63.86 dev / 63.17 test) solves mostly the same problem:
- Graph alone is weaker than names alone.
- Graph on top of names adds only +0.21 on dev (p=0.004) and +0.10 on test (p=0.12).
- BC5CDR has no exact ties, so the names rule does nothing there and the graph gain stands.

The names tie-break is not in the tool yet; it needs KB name counts per candidate.

## 3. What each suggestion turned out to be worth

| Suggestion | Result | Adopted? |
|---|---|---|
| Tighter gate (switch margin) | **Decisive.** δ=0.10, the old default, loses on every dataset and split (MedMentions −906, disease −241, chemical −163 net). δ=0.02 or less is safe. | yes: default 0.02 |
| Confidence pin must not block exact ties | **Decisive on MedMentions.** All 2,345 graph changes at δ=0 are exact ties; 2,132 have a top score ≥ 0.90. The old pin took +849 down to −10. | yes |
| Trusted anchors only | Slightly worse than all predicted anchors (MedMentions dev +742 vs +817) | no |
| Human-accepted anchors (gold of earlier mentions) | Worse than predicted anchors (+501 vs +817). Even full gold anchors (+660) don't beat them. | no special treatment |
| Same-text mentions not anchoring each other | Part of every positive configuration above | yes |
| Hub weighting (Adamic-Adar / degree) | Slightly better against plain abbrev on MedMentions dev (+858 vs +817), but worse on top of names on test (−51 vs +15 net at δ=0.01) | no |
| Drop structural relations (AQ/QB) | No effect: those links never occur between candidates | no |
| Drop hierarchy (PAR/CHD/RB/RN) or RO-only | Loses about a third of the gain (MedMentions +817 → +560 / +538) | no |
| Cross-track anchors (chemical ↔ disease) | 47% of documents have ≥1 linked gold chem–dis pair, mostly may_treat, contraindicated_with, may_prevent. But only 8% of gold pairs are linked. Re-ranking is inconsistent (disease dev −7 to −20, test +12 to +41, depending on δ). | no |
| Isolated top-1 as an error flag | Isolated picks are much less accurate (MedMentions 57 vs 77, disease 70 vs 87 Acc@1), but the dense score explains most of that. Penalising isolated picks lifts error-ranking AUROC only a little: MedMentions 0.741 → 0.752 (test 0.766 → 0.773); disease 0.835 → 0.848 (0.857 with cross-track anchors; test 0.837 → 0.840 / 0.852). | UI hint only (already shown) |

## 4. Not verified

- **Live Neo4j graphs.** Neo4j wasn't running. The tool's graphs differ from MRREL:
  - The MeSH database keeps `sibling` edges. These were never in the measured graph, so
    `rel_exclude: [sibling]` in that session's graph config matches the measured setting.
  - Whether the MeSH-only builder kept MED-RT relations (may_treat etc.) wasn't checked.
- **Existing sessions.** Saved session configs keep `graph_switch_margin: 0.10`. Only
  new sessions get 0.02; the paper's Table 3 sessions are unchanged. To apply the new
  margin to a session, edit its config.
- **The old optimiser on the same data.** The table in §2 compares against retrieval.
  The old optimiser is approximated by the δ=0.10 / pin-blocked rows, not rerun as is.

## Files

- `eval/run_graph_scoring.py`: grid (relations × weighting × anchors × δ), isolation
  analysis, cross-track analysis; results in `eval/out/typescope/*.graphscore.json`.
- `eval/run_hint_analysis.py`: `prepare()` factored out (outputs byte-identical); `relation_fn` bug fixed.
- `eltool/graph_client.py`: `optimize_selection` now implements the rule in §2; `labels=` parameter added.
- `api/main.py`, `eltool/config.py`: pass mention labels; default `graph_switch_margin` 0.10 → 0.02.
- Tests: `tests/test_graph_scoring.py`, `tests/test_graph_optimize.py`, a regression test in `tests/test_hint_analysis.py`.
