# Graph-path audit (2026-11-09)

Started after a bug in `run_hint_analysis.relation_fn` turned out to count unrelated
concept pairs as linked. The question: does any other code path decide "are these two
concepts linked?", or count links, incorrectly? And which reported numbers depended on it?

**Method**
- Every path was traced on all branches.
- An independent reviewer audited the same code without being told my conclusions.
  Each of its claims was then re-verified here before being accepted.
- The blast radius was measured by diffing each output before and after its fix.
  Live Neo4j was not running, so Neo4j behaviour was reproduced offline from MRREL and
  the built graph CSVs.

**Branch scope.** The graph scoring code in `eltool/graph_client.py` is identical on
`main` and every feature branch; they differ only in the hosted connection code. The
buggy evaluation scripts exist only on `feature/type-scoped-baseline` (from `90c2141`,
2 Nov) and `feature/graph-scoring`.

## Findings

| # | Where | What was wrong | Verdict | Status |
|---|---|---|---|---|
| A | `eval/run_hint_analysis.py` `relation_fn` | Kind `"any"` returned True for pairs with **no** relation | Confirmed | **Fixed** (80cf0d1) + test |
| B | `run_hint_analysis.relation_fn`, `run_graph_scoring.Rel.codes` | A concept counted as linked to **itself**: a composite id and its own part (`D056846|277600` vs `D056846`), or two ids sharing a CUI | Confirmed (bc5cdr-dis dataset index: 302 of 373 composite/part pairs) | **Fixed** + tests |
| C | Tool, Neo4j MeSH graph (`id_prop: mesh`) | Composite / OMIM ids (`601367|D002544`) never match a node, which holds one MeSH code. They get zero support, never anchor, and always show as "isolated" | Confirmed (18% of disease top-1s; 0 of 402,563 nodes hold a `|`) | Open (needs a decision) |
| D | `eval/run_medmentions_annotate.py` | Passed bare ids without scores, so every candidate tied at 0.0 and the gate never applied (ungated edge maximisation) | Confirmed | **Fixed**: scores now passed |
| E | Tool: `/graph/optimize` + `DocumentReview.autoMatch` | Auto-match passes no `fixed`, so the optimiser starts from retrieval top-1. The UI then **saves over the annotator's manual corrections** (undo exists, but the overwrite is already saved) | Confirmed | Open (needs a decision) |
| F | Tool: score gate | Lexical scores are RRF (≤0.033) while dense scores are cosine (~1), and fusion takes the max. In lexical-only sessions every candidate is within the margin and none reaches the pin, so the gate is a no-op | Confirmed | Open |
| G | Graph builders vs the offline study | The UMLS graph keeps only rows with a RELA label (incl. `sib_in_*`). The MeSH graph keeps `sibling`, AQ and QB. The study used every REL except SIB. | Confirmed | Measured below |

Minor, non-blocking:
- The scripts keep NIL gold (`-1`) in their denominators. Both arms are affected equally,
  so the deltas hold.
- `isolation()` counts empty pools as isolated (23 of 4,228).
- The neighbour `LIMIT` in `candidate_graph` applies to all candidates together, not per
  candidate (display only).
- An id with no Neo4j node is drawn as "isolated", the same as a real node with no links
  (UI ambiguity).

Checked and OK:
- `rel_filter` Cypher: a pure AND chain, applied on all three edge queries.
- Edge matching is undirected and de-duplicated; self-edges are dropped.
- MRREL and MRCONSO column indexes are correct in every reader and builder.
- `optimize_selection` excludes the candidate itself and same-text mentions.
- McNemar test, AUROC direction and tie handling are correct.
- No gold leakage outside rules explicitly labelled `gold` / `gold-prev` / ceiling.

## What each bug contaminated

**A: hint-analysis graph rules (all splits, both indexes).** Diffing the 18 output files
before and after the fix:
- Only the 6 "graph any" rules, the 2 separation ceilings and 2 per-mention TSV columns changed.
- Every other rule (dense, type-scoped, abbreviation, lexical, KB-names ties, graph RO,
  graph hier) and `hints_summary.tsv` are byte-identical.
- The wrong conclusion ("graph counts hurt everywhere, even with gold anchors") was never
  committed to the repo, README or paper. It lived in session notes only, now corrected.
- Corrected result (gated rule, MedMentions full): dev 56.37 → 58.96, test 56.13 → 58.59.

**B: BC5CDR dataset-index runs only.** Full-index runs have no composite ids; all
full-index outputs and every headline number are unchanged.
- Ungated disease graph rules were inflated by 3.5–5.4 Acc@1 points (dev "graph any"
  58.40 → 54.26).
- Gated rules moved by at most 0.14.

**C: tool and paper (BC5CDR-disease).** Re-running the paper's own `gate()` on the disease
dataset index, with composites invisible (as in Neo4j) vs resolved:
- Retrieval: 95.79 in both.
- Pin 0.90: 95.79 vs 95.67.
- Ungated: 58.02 vs 57.10.

So the paper's "ungated collapses / gate is protective" conclusion holds. The defect is in
the tool's behaviour and display: one disease pick in five can never gain or give graph
support and is always flagged "isolated".

**D: the MedMentions graph snapshot.** Any graph Acc@1 produced by this script measured
ungated maximisation, not the gate. The paper's Section 5 numbers come from
`run_threshold_sweep.py`, which passes scores and is not affected.

**E: no study data affected.** None of the six `study2-*` sessions has a graph-optimise
call logged, and the C3 (graph-assisted) sessions have not been annotated yet. This must
be fixed **before** C3 is run.

**G: does the measured gain survive on graphs built the tool's way?** See the next section.

## Gain on tool-built graphs

**UMLS 2025AA ships no SIB rows at all**, so the study's "SIB dropped" filter never removed
anything. The tool's Neo4j graphs were built from an older MRREL
(`MedicalGraphComparison/Semantic Structure/UMLS/`), which does contain sibling rows. The
new rule was re-run (real `optimize_selection`, abbrev pool, full index) on graphs built
each way:

| | study graph (2025AA, all REL) | tool-style graph (old MRREL) |
|---|---|---|
| MedMentions dev, `umls_processor` convention (RELA-only, incl. `sib_*`) | 60.54 → 62.93 | 60.54 → **62.36** (+1163 / −419); minus `sib_*`: 62.29 |
| MedMentions test, same convention but from 2025AA | 59.79 → 62.12 | 59.79 → 61.65 |
| BC5CDR disease dev, `build_mesh_graph` convention (incl. sibling/AQ/QB) | 77.46 → 77.91 (p=0.007) | 77.46 → **77.44** (+15 / −16, p=1) |
| BC5CDR disease test, same | 78.75 → 79.07 (p=0.02) | 78.75 → 78.82 (p=0.72) |
| BC5CDR chemical dev, same | 91.04 → 90.99 | 91.04 → 90.99 |

- **MedMentions:** the gain survives the tool's UMLS graph convention, keeping about 80%
  of it. Siblings don't matter there.
- **BC5CDR disease:** the gain **disappears** when sibling/qualifier edges are present.
  They are 61% of the edges among disease candidates (51,558 of 84,353). Excluding them
  is required.

**Action for the MeSH graph connection:**
`rel_exclude: [sibling, allowed_qualifier, can_be_qualified_by]`.

## Open decisions

1. **E: auto-match overwriting the annotator.** Fix before the C3 study sessions. Options:
   - pass the document's saved annotations as `fixed`, so they anchor but never move; or
   - only write back mentions the annotator hasn't touched (needs provenance: auto-selected
     vs human-confirmed).
2. **C: composite ids.** Match a candidate to the graph through any of its id parts:
   split on `|` for the query, and map the matched node back to the composite id.
3. **F: score gate on non-dense scores.** Disable the graph gate (or normalise scores) when
   a session's candidates aren't cosine-scaled, e.g. lexical-only.
4. **Paper narrative.** The paper's graph numbers are not affected by A–D, but its
   conclusion ("gate is safe but not corrective") reflects the old gate. With the
   exact-tie rule and a 0.02 margin the graph is corrective on MedMentions (+1.8 to +2.3
   Acc@1). On BC5CDR it is near zero, and only without sibling edges.

## Reproduce

- `eval/run_graph_scoring.py` and `eval/run_hint_analysis.py`.
- The probe scripts used for C, E and G are in the session scratchpad and are not committed.
- The before/after output snapshots were diffed with the commands in this session.
