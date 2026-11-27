#!/usr/bin/env python
"""Build the demo search index from NLM's MeSH descriptor file (disease branch).

Runs inside the search-server container:

    python scripts/build_demo_index.py --mesh desc2026.gz --out /data/out/mesh-diseases

Writes <out>.faiss, <out>_metadata.pkl and <out>_config.json, the files
kb/search_sapbert_index.py loads. An existing config is removed first and the
new one is written last, so a build that fails leaves nothing loadable.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eltool.mesh import Descriptor, read_descriptors  # noqa: E402

MODEL_NAME = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
MODEL_REVISION = "090663c3ae57bf35ffe4d0d468a2a88d03051a4d"  # model card licence: apache-2.0
MAX_LENGTH = 25


def entries(descriptors: Iterable[Descriptor]) -> list[dict]:
    """One metadata row per term; a row's position is its vector's position."""
    rows: list[dict] = []
    for descriptor in descriptors:
        aliases = list(descriptor.terms)
        joined = "; ".join(aliases)
        for term in aliases:
            rows.append({
                "entity_id": descriptor.ui,
                "primary_alias": descriptor.name,
                "all_aliases": aliases,
                "original_aliases": joined,
                "processed_text": term,
                "index_id": len(rows),
                "semantic_groups": "",
                "semantic_types": "",
                "definition": descriptor.definition,
            })
    return rows


def _write(path: Path, save: Callable[[Path], object]) -> None:
    """Save to ``<path>.tmp``, then rename, so ``path`` is never half written."""
    tmp = Path(f"{path}.tmp")
    try:
        save(tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def build_index(descriptors: Iterable[Descriptor],
                embed: Callable[[list[str]], np.ndarray],
                out_prefix: Path, *, mesh_version: str, batch_size: int = 64) -> dict:
    """Embed every term into a flat index at ``out_prefix``; return its config."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    config_path = Path(f"{out_prefix}_config.json")
    config_path.unlink(missing_ok=True)  # the search server needs it to load

    rows = entries(descriptors)
    if not rows:
        raise ValueError("no descriptors to index")

    index = None
    for start in range(0, len(rows), batch_size):
        texts = [row["processed_text"] for row in rows[start:start + batch_size]]
        vectors = np.array(embed(texts), dtype=np.float32)  # a copy: normalised in place
        faiss.normalize_L2(vectors)
        if index is None:
            index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)

    config = {
        "index_type": "Flat",
        "model_name": MODEL_NAME,
        "model_revision": MODEL_REVISION,
        "use_adapter": False,
        "adapter_name": None,
        "adapter_path": None,
        "embedding_dim": index.d,
        "num_entities": len(rows),
        "max_length": MAX_LENGTH,
        "normalized": True,
        "metric": "inner_product",
        "pooling": "cls",
        "distinct_entities": True,
        "mesh_version": mesh_version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _write(Path(f"{out_prefix}.faiss"), lambda tmp: faiss.write_index(index, str(tmp)))
    _write(Path(f"{out_prefix}_metadata.pkl"), lambda tmp: tmp.write_bytes(pickle.dumps(rows)))
    _write(config_path, lambda tmp: tmp.write_text(json.dumps(config, indent=2), encoding="utf-8"))
    return config


def sapbert_embedder(model_name: str, revision: str,
                     max_length: int) -> Callable[[list[str]], np.ndarray]:
    """CLS-vector embedder, tokenising as the search server does for queries."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
    model = AutoModel.from_pretrained(model_name, revision=revision).to(device).eval()

    def embed(texts: list[str]) -> np.ndarray:
        tokens = tokenizer(texts, padding="max_length", max_length=max_length,
                           truncation=True, return_tensors="pt").to(device)
        with torch.no_grad():
            return model(**tokens).last_hidden_state[:, 0, :].cpu().numpy()

    return embed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mesh", type=Path, required=True,
                        help="MeSH descriptor file (desc<year>.xml or .gz)")
    parser.add_argument("--out", type=Path, required=True,
                        help="index path prefix, e.g. /data/out/mesh-diseases")
    parser.add_argument("--mesh-version", default="2026")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args(argv)

    embed = sapbert_embedder(MODEL_NAME, MODEL_REVISION, MAX_LENGTH)
    config = build_index(read_descriptors(args.mesh), embed, args.out,
                         mesh_version=args.mesh_version, batch_size=args.batch_size)
    print(f"Indexed {config['num_entities']} terms to {args.out}")


if __name__ == "__main__":
    main()
