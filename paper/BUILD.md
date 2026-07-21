# HELIX paper — build & fill-in guide

Springer **LNCS** format (`llncs.cls`, `splncs04.bst`) — the format IJCKG uses.

## Compile
```bash
cd paper
pdflatex main
bibtex   main
pdflatex main
pdflatex main
```
Produces `main.pdf`. Needs a normal TeX Live / MacTeX install with `tikz`,
`pgfplots`, `booktabs`, `listings`, `hyperref` (all standard).

## Structure (≈15 pages once figures + results are in)
1. Introduction — motivation, three gaps, contributions
2. Related Work — back ends, LLM rerank, graph/collective, annotation tools (+ Table 1)
3. System Overview — architecture (Fig. 1), hybrid retrieval, LLM rerank, graph, workflow
4. Implementation & Reproducibility
5. Evaluation — data/metrics, ablation (Table 2), length breakdown (Fig. 4),
   per-config (Fig. 5), LLM trade-off (Table 3), graph-assisted case study
6. Discussion & Limitations
7. Conclusion

## What YOU must fill in (search `TODO` in `main.tex` — 17 markers)
- **Authors / affiliations / ORCIDs** (title block) and the repository URL footnote.
- **Screenshots**: Fig. 2 (annotation screen) and Fig. 3 (document-review screen).
  Replace the `\fbox{...}` placeholders with `\includegraphics`.
- **Dataset facts**: name/version, `N` mentions, `k` for Recall@k.
- **Results** — replace every `\result{...}` placeholder (24 of them, underlined
  blue in the draft) with real numbers:
  - Table 2 (main ablation) and Fig. 5 — from `python eval/score.py`.
  - Fig. 4 (by mention length) — from the `by_len` breakdown of the same run.
  - Table 3 (LLM model trade-off) — from a sweep over reranker models.
  - Sec. 5.6 case study — acceptance/override rate + isolated-pick reduction from
    your annotation provenance log, plus one worked document example.
- **Headline result** sentence in the Conclusion, once numbers are final.
- **Acknowledgements** / competing interests.

## Bibliography (`references.bib`)
23 entries. **18 are marked `TODO verify`** — mostly author lists and arXiv IDs
for the most recent papers (BeLink, LongBEL, BioELX, LLM4BioEL, REAL, AnNER,
collective-disambiguation, KG-disambiguation, and the BioNNE-L hybrid re-ranking
system, which may be your own prior work — set the authors accordingly).
Confirm each against the source before camera-ready.

## Notes
- Charts (Figs. 4–5) use **pgfplots with illustrative placeholder data** and are
  clearly labelled as such; swap in the real numbers.
- The architecture diagram (Fig. 1) and the two charts are vector TikZ/pgfplots —
  no external image files needed. Only the two screenshots need image files.
- If a reviewer track requires **double-blind**, anonymise the author block and
  the repository footnote.
