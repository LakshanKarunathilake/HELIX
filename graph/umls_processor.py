#!/usr/bin/env python3
"""
UMLS → Neo4j processor for HELIX.

Streams UMLS MRREL.RRF (relationships) and MRCONSO.RRF (concepts) into two
Neo4j bulk-import CSVs. Same strategy as the original MedicalGraphComparison
script — one ``RELATES_TO`` edge per (cui2, rel, cui1), hyphens cleaned, self-
references dropped — with one addition: every node carries the **English
concept name** (MRCONSO ``STR`` where ``LAT == ENG``) as a ``name`` property,
falling back to the CUI when no English name exists.

The RRF datasets are NOT part of this repo — point ``graph_config.yaml`` at your
licensed local UMLS files.

Output (Neo4j ``database import`` format):
  <prefix>_nodes.csv       concept:ID, name, cui, :LABEL
  <prefix>_relations.csv   :START_ID, rel, :END_ID, :TYPE
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class UMLSProcessor:
    def __init__(self, mrrel_path: str, mrconso_path: str | None, output_dir: str = "."):
        self.mrrel_path = Path(mrrel_path)
        self.mrconso_path = Path(mrconso_path) if mrconso_path else None
        self.output_dir = Path(output_dir)
        if not self.mrrel_path.exists():
            raise FileNotFoundError(f"MRREL file not found: {mrrel_path}")
        if self.mrconso_path and not self.mrconso_path.exists():
            raise FileNotFoundError(f"MRCONSO file not found: {mrconso_path}")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── relationships (MRREL) ────────────────────────────────────────────────
    def process_relationships(self, sample_size: int | None = None) -> pd.DataFrame:
        logger.info("Processing relationships from %s", self.mrrel_path)
        relationships: set[tuple[str, str, str]] = set()
        with open(self.mrrel_path, "r", encoding="utf-8") as fh:
            for line_num, line in enumerate(fh, 1):
                try:
                    f = line.rstrip("\n").split("|")
                    if len(f) < 8:
                        continue
                    cui1, cui2, rel = f[0].strip(), f[4].strip(), f[7].strip()
                    if not rel or cui1 == cui2:
                        continue
                    rel = rel.replace("-", "_")
                    relationships.add((cui2, rel, cui1))   # same direction as original
                    if line_num % 1_000_000 == 0:
                        logger.info("  %s lines, %s unique relationships", f"{line_num:,}", f"{len(relationships):,}")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("skip MRREL line %d: %s", line_num, exc)
        logger.info("Found %s unique relationships", f"{len(relationships):,}")
        df = pd.DataFrame(list(relationships), columns=[":START_ID", "rel", ":END_ID"])
        df[":TYPE"] = "RELATES_TO"
        if sample_size and len(df) > sample_size:
            logger.info("Sampling %s relationships from %s", f"{sample_size:,}", f"{len(df):,}")
            df = df.sample(n=sample_size, random_state=42)
        return df

    # ── concept names (MRCONSO) ──────────────────────────────────────────────
    def load_names(self, language: str, keep_cuis: set[str] | None) -> dict[str, str]:
        """CUI -> English name. Restricted to `keep_cuis` when provided (saves memory)."""
        if not self.mrconso_path:
            return {}
        logger.info("Reading %s names from %s", language, self.mrconso_path)
        names: dict[str, str] = {}
        with open(self.mrconso_path, "r", encoding="utf-8") as fh:
            for line_num, line in enumerate(fh, 1):
                try:
                    f = line.rstrip("\n").split("|")
                    if len(f) < 15 or f[1].strip() != language:
                        continue
                    cui = f[0].strip()
                    if keep_cuis is not None and cui not in keep_cuis:
                        continue
                    if cui in names:            # keep first English name seen
                        continue
                    name = f[14].strip().replace("\n", " ").replace("\r", " ")
                    if name:
                        names[cui] = name
                    if line_num % 1_000_000 == 0:
                        logger.info("  %s lines, %s names", f"{line_num:,}", f"{len(names):,}")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("skip MRCONSO line %d: %s", line_num, exc)
        logger.info("Found %s English concept names", f"{len(names):,}")
        return names

    # ── nodes = relationship CUIs, with English name attached ────────────────
    def build_nodes(self, relationships_df: pd.DataFrame, language: str, use_concepts: bool) -> pd.DataFrame:
        rel_cuis = set(relationships_df[":START_ID"]).union(relationships_df[":END_ID"])
        logger.info("Building %s nodes from relationship CUIs", f"{len(rel_cuis):,}")
        names = self.load_names(language, keep_cuis=rel_cuis) if use_concepts else {}
        named = sum(1 for c in rel_cuis if c in names)
        logger.info("  %s/%s nodes have an English name (rest fall back to the CUI)",
                    f"{named:,}", f"{len(rel_cuis):,}")
        rows = [(c, names.get(c, c), c) for c in rel_cuis]   # concept:ID, name, cui
        nodes = pd.DataFrame(rows, columns=["concept:ID", "name", "cui"])
        nodes[":LABEL"] = "Concept"
        return nodes

    # ── save + stats ─────────────────────────────────────────────────────────
    def save(self, rels: pd.DataFrame, nodes: pd.DataFrame, prefix: str) -> tuple[Path, Path]:
        rels_file = self.output_dir / f"{prefix}_relations.csv"
        nodes_file = self.output_dir / f"{prefix}_nodes.csv"
        logger.info("Saving %s relationships → %s", f"{len(rels):,}", rels_file)
        rels.to_csv(rels_file, index=False)
        logger.info("Saving %s nodes → %s", f"{len(nodes):,}", nodes_file)
        nodes.to_csv(nodes_file, index=False)
        top = rels["rel"].value_counts()
        logger.info("Top relationship types: %s", ", ".join(f"{k}({v:,})" for k, v in top.head(8).items()))
        logger.info("Total unique relationship types: %s", f"{len(top):,}")
        return nodes_file, rels_file

    def process_all(self, sample_size: int | None, language: str, use_concepts: bool,
                    prefix: str) -> tuple[Path, Path]:
        rels = self.process_relationships(sample_size)
        nodes = self.build_nodes(rels, language, use_concepts)
        return self.save(rels, nodes, prefix)


# ── config loading ────────────────────────────────────────────────────────────

def load_build_config(path: str | Path) -> dict:
    import yaml
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    if "mrrel" not in cfg:
        raise ValueError(f"{path}: 'mrrel' is required")
    return cfg


def run_from_config(cfg: dict) -> tuple[Path, Path]:
    proc = UMLSProcessor(
        mrrel_path=cfg["mrrel"],
        mrconso_path=cfg.get("mrconso"),
        output_dir=cfg.get("output_dir", "graph/out/umls"),
    )
    return proc.process_all(
        sample_size=cfg.get("sample"),
        language=cfg.get("language", "ENG"),
        use_concepts=bool(cfg.get("mrconso")),
        prefix=cfg.get("prefix", "umls"),
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Process UMLS RRF files into Neo4j import CSVs (with English names).")
    p.add_argument("--config", help="YAML config (graph/graph_config.yaml). Overrides the flags below.")
    p.add_argument("--mrrel", help="Path to MRREL.RRF")
    p.add_argument("--mrconso", help="Path to MRCONSO.RRF (omit to skip names)")
    p.add_argument("--output", "-o", default="graph/out/umls", help="Output directory")
    p.add_argument("--sample", "-s", type=int, help="Sample N relationships (testing)")
    p.add_argument("--prefix", default="umls", help="Output file prefix")
    p.add_argument("--language", default="ENG", help="Concept-name language (MRCONSO LAT)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.config:
        cfg = load_build_config(args.config)
    else:
        if not args.mrrel:
            p.error("either --config or --mrrel is required")
        cfg = {"mrrel": args.mrrel, "mrconso": args.mrconso, "output_dir": args.output,
               "sample": args.sample, "prefix": args.prefix, "language": args.language}

    try:
        nodes_file, rels_file = run_from_config(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.error("Processing failed: %s", exc)
        sys.exit(1)

    prefix = cfg.get("prefix", "umls")
    db = (cfg.get("neo4j") or {}).get("database", prefix)
    print("\n" + "=" * 60)
    print("Generated:")
    print(f"  {nodes_file}")
    print(f"  {rels_file}")
    print("\nNeo4j bulk import (run offline / on a stopped target database):")
    print(f"  bin/neo4j-admin database import full {db} \\")
    print(f"    --nodes=import/{nodes_file.name} \\")
    print(f"    --relationships=import/{rels_file.name} \\")
    print("    --overwrite-destination --skip-bad-relationships --verbose")
    print("\nOr automate everything with:  python graph/build_graph.py")


if __name__ == "__main__":
    main()
