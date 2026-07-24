# HELIX evaluation

Scores the automatic retrieval pipeline against an **already-annotated gold
dataset** — no human study needed. Gold is multi-valued: a mention may have
several correct CUIs joined with `|`, and a prediction counts correct
(**any-match**) when the top-1 CUI is in the gold set.

## What it reports
Per configuration — **lexical**, **dense**, **hybrid**, **hybrid+LLM** — on the
same gold set:

- **Acc@1** — top-1 prediction is in the gold set
- **Recall@k** — the gold set overlaps the top-k candidates

with breakdowns by **mention length** (`≤4 / 5-8 / >8` chars — the short-mention
story where lexical rescues dense) and by **entity type**.

## Run

1. Start the KB server (the same one HELIX queries).
2. Point the config at your gold file:
   ```bash
   cp eval/eval_config.example.yaml eval/eval_config.yaml
   # edit gold_file, columns (text / gold / entity_type), and kb.endpoint_url
   ```
3. Run:
   ```bash
   python eval/score.py --config eval/eval_config.yaml
   ```
   `hybrid_llm` needs `ANTHROPIC_API_KEY` in the environment.

## Gold file format
Any CSV/TSV with a mention column and a gold-CUI column. Multiple correct CUIs go
in one cell, pipe-joined:

| entity_text | entity_type | cui |
|---|---|---|
| aortic wall | ANATOMY | C0507851 |
| DM | DISO | C0011849\|C0011860 |

Map the column names in `eval_config.yaml → columns`.

## BC5CDR-chemical (train / dev / test)

BC5CDR ships in the BioSyn/SapBERT layout (`processed_<split>/*.concept`, gold is
MeSH ids, composite mentions already pipe-joined). Convert and evaluate all splits:

```bash
# 1. one-time: build a SAPBERT FAISS index from the BC5CDR chemical dictionary
#    (train_dictionary.txt) and point the KB server at it, so predicted ids are
#    the same MeSH ids as the gold.
# 2. configure:
cp eval/eval_config.bc5cdr.example.yaml eval/eval_config.bc5cdr.yaml   # set kb.endpoint_url
# 3. convert + score every split:
./eval/run_bc5cdr.sh /path/to/bc5cdr-chemical            # or: ... dev
```

`convert_bc5cdr.py` writes `eval/out/bc5cdr-chemical.<split>.gold.tsv`; the runner
scores each split and writes `eval/out/bc5cdr-chemical.<split>.results.json`. Use
`--split dev` for a quick single-split run. `--raw` uses the un-normalised
mentions instead of the processed (lower-cased) ones.

> The gold CUIs are **MeSH** (`D…/C…`). The KB index must therefore be built from
> the BC5CDR dictionary — evaluating against a UMLS index will not match.

## Notes
- The pipeline is queried per config from the running KB server (`/dense`,
  `/lexical` are derived from `kb.endpoint_url`); no session is needed.
- For a quick sanity run, point `gold_file` at a small slice first.
- Output JSON (counts per config + breakdowns) is written to `output:`.
