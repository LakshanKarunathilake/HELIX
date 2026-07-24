#!/usr/bin/env bash
#
# BC5CDR-chemical evaluation across all splits (train / dev / test).
#
#   1. Build a SAPBERT FAISS index from the BC5CDR chemical dictionary and point
#      the KB server at it (so predicted ids are MeSH, matching the gold).
#   2. Configure:  cp eval/eval_config.bc5cdr.example.yaml eval/eval_config.bc5cdr.yaml
#      (set kb.endpoint_url; hybrid_llm needs ANTHROPIC_API_KEY / an OpenAI model)
#   3. Run:        ./eval/run_bc5cdr.sh /path/to/bc5cdr-chemical
#
set -euo pipefail

DATASET="${1:?usage: eval/run_bc5cdr.sh /path/to/bc5cdr-chemical [dev|train|test|all]}"
SPLITS_ARG="${2:-all}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CONFIG="eval/eval_config.bc5cdr.yaml"
OUT="eval/out"

[ -f "$CONFIG" ] || { echo "Missing $CONFIG — copy from eval/eval_config.bc5cdr.example.yaml"; exit 1; }

echo "── Converting BC5CDR splits → gold TSVs ──"
python eval/convert_bc5cdr.py --dataset "$DATASET" --split "$SPLITS_ARG" --out "$OUT"

SPLITS=( ); [ "$SPLITS_ARG" = "all" ] && SPLITS=(train dev test) || SPLITS=("$SPLITS_ARG")
NAME="$(basename "$DATASET")"

for split in "${SPLITS[@]}"; do
  echo
  echo "══════════════ $NAME · $split ══════════════"
  python eval/score.py --config "$CONFIG" \
    --gold "$OUT/${NAME}.${split}.gold.tsv" \
    --out  "$OUT/${NAME}.${split}.results.json" \
    --label "${NAME} ${split}"
done

echo
echo "Done. Per-split reports: $OUT/${NAME}.<split>.results.json"
