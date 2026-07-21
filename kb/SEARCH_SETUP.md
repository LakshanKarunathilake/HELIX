# Search Setup Guide

ELTool's KB server uses two complementary search strategies depending on the length of the query:

| Query length | Strategy | Why |
|---|---|---|
| ≤ 8 characters | Lexical (BM25 + n-gram) | BERT embeddings have no semantic signal for abbreviations like "DM" or "HTN" |
| > 8 characters | FAISS dense search (SAPBERT) | Semantic similarity captures paraphrase and synonymy for longer phrases |

The routing is automatic — the annotation tool always calls `POST /query` and receives the best result regardless of which strategy ran.

---

## How the indexes are built

Neither index requires a separate build step. Both are constructed **at server startup** from the same `_metadata.pkl` file used by the FAISS index. No extra files are written to disk.

### FAISS index (dense)

Built offline using `create_sapbert_index.py` (in the sapbert project). Each entity alias is embedded individually and stored as a 768-dim SAPBERT vector. See your sapbert project's README for rebuild instructions.

If you want better embedding quality for long entity names, rebuild the index with a higher `max_length`:

```bash
# In your sapbert project:
python create_sapbert_index.py \
  --input_csv umls.csv \
  --output_prefix indexes/full_mesh_eng_adapter \
  --max_length 64          # default is 16; 32–64 gives better results for multi-word names
  --model_name cambridgeltl/SapBERT-from-PubMedBERT-fulltext \
  --adapter_path /path/to/adapter
```

Changing `max_length` requires a full rebuild (all embeddings change).

### Lexical index (BM25 + n-gram)

Built automatically when the server starts by reading `all_aliases` from the FAISS metadata:

**BM25 component**
- One document per entity = all aliases joined and lowercased
- Tokenised by whitespace
- Effective for short multi-word queries like "lung ca" or "chron dis"

**Character n-gram component**
- All bigrams and trigrams extracted from every alias
- Inverted index maps each n-gram to its entity_ids
- Effective for single abbreviations like "MI", "DM", "CKD"
- Score = fraction of query n-grams that appear in the entity's aliases

Results from both components are merged using Reciprocal Rank Fusion (RRF).

---

## Endpoints

### `POST /query` — auto-routing (recommended)

```bash
curl -s -X POST http://127.0.0.1:8765/query \
  -H "Content-Type: application/json" \
  -d '{"label": "DM", "top_k": 10}'
```

Short query (`len("DM") = 2 <= threshold 8`) → routed to lexical search.

```bash
curl -s -X POST http://127.0.0.1:8765/query \
  -H "Content-Type: application/json" \
  -d '{"label": "chronic kidney disease stage 3", "top_k": 10}'
```

Long query → routed to FAISS dense search.

### `POST /query/lexical` — always lexical

Forces lexical search regardless of query length. Useful when:
- Testing whether an abbreviation is indexed as an alias
- Comparing lexical vs dense results for the same query

```bash
curl -s -X POST http://127.0.0.1:8765/query/lexical \
  -H "Content-Type: application/json" \
  -d '{"label": "HTN", "top_k": 5}'
```

Both endpoints return identical JSON:

```json
{
  "results": [
    {
      "id": "D006973",
      "label": "Hypertension",
      "score": 0.008197,
      "matched_alias": "HTN",
      "aliases": ["Hypertension", "HTN", "High Blood Pressure"],
      "rank": 1
    }
  ]
}
```

### `GET /health` — check both indexes

```bash
curl -s http://127.0.0.1:8765/health
```

```json
{
  "status": "ok",
  "index": {
    "entity_count": 28000,
    "model_name": "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
    "adapter": "ON"
  },
  "lexical": {
    "entity_count": 28000,
    "ngram_count": 142500,
    "ngram_sizes": [2, 3],
    "rrf_k": 60
  }
}
```

---

## Tuning

All parameters live in `kb/config.yaml` under the `lexical:` key.

```yaml
lexical:
  threshold: 8        # raise to route more queries to lexical; set 0 to disable auto-routing
  ngram_sizes: [2, 3] # add 4 for longer abbreviations; remove 2 to reduce index size
  rrf_k: 60           # raise to flatten score differences; lower to amplify rank gaps
```

| Parameter | Effect of raising |
|---|---|
| `threshold` | More queries use lexical instead of FAISS |
| `ngram_sizes` | Larger n-grams → more precise but less recall for short aliases |
| `rrf_k` | Flatter score distribution; ranks matter less |

Changes take effect on the next server restart (index is rebuilt at startup).

---

## Improving abbreviation recall

If an abbreviation like "MI" is not returning "Myocardial Infarction", check whether "MI" is stored as an alias:

```bash
# Look up the entity directly
curl -s http://127.0.0.1:8765/entity/D009203   # MeSH ID for Myocardial Infarction
# Check the "aliases" field in the response
```

If the abbreviation is missing, add it to your source data (CSV) before rebuilding the FAISS index. The lexical index picks it up automatically on the next server start since it reads from the same metadata.

Common reasons abbreviations are missing from aliases:
- Source data (UMLS, MeSH) did not include them in the `Terms` column
- Aliases were split incorrectly at index build time (check delimiter — `;` and `,` are both handled)
- Alias normalisation lowercased the abbreviation (n-gram search is case-insensitive, so this should still work)

---

## Verification test suite

```bash
# TC1: 2-char abbreviation → lexical auto-routed, correct entity at rank 1
curl -s -X POST http://127.0.0.1:8765/query -H "Content-Type: application/json" \
  -d '{"label": "DM", "top_k": 5}'

# TC2: uppercase abbreviation via /query/lexical
curl -s -X POST http://127.0.0.1:8765/query/lexical -H "Content-Type: application/json" \
  -d '{"label": "HTN", "top_k": 5}'

# TC3: long query → FAISS, result same as before
curl -s -X POST http://127.0.0.1:8765/query -H "Content-Type: application/json" \
  -d '{"label": "chronic kidney disease stage 3", "top_k": 5}'

# TC4: 2-char abbreviation not stored as exact alias — n-gram overlap rescues it
curl -s -X POST http://127.0.0.1:8765/query/lexical -H "Content-Type: application/json" \
  -d '{"label": "MI", "top_k": 5}'

# TC5: health shows both indexes
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
```
