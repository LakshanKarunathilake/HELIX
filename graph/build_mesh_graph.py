#!/usr/bin/env python3
"""
MeSH-only graph builder for HELIX.

Produces the same Neo4j bulk-import CSVs as ``umls_processor.py`` — same
extraction convention (one ``RELATES_TO`` edge per ``(cui2, RELA, cui1)``,
hyphens cleaned, self-references dropped, deduplicated) — but restricted to the
**MeSH** slice of UMLS:

  * a node is kept only if the concept exists in MeSH — i.e. it has at least one
    ``SAB == MSH`` atom in MRCONSO;
  * an edge is kept only when **both** endpoints are MeSH concepts (any source
    vocabulary may assert the edge — SNOMED, NCI, MeSH itself, …);
  * each node also carries its MeSH descriptor/supplementary id (MRCONSO CODE,
    e.g. ``D012711``) as a ``mesh`` property, and its preferred English MeSH
    term as ``name``.

Two streaming passes, memory bounded to the MeSH subgraph:
  1. MRCONSO  → set of MeSH CUIs + {CUI: English MeSH name} + {CUI: MeSH code}
  2. MRREL    → edges whose START and END are both in that set

Output (Neo4j ``database import`` format):
  <prefix>_nodes.csv       concept:ID, name, mesh, cui, :LABEL
  <prefix>_relations.csv   :START_ID, rel, :END_ID, :TYPE

The RRF datasets are NOT part of this repo — point ``--mrrel`` / ``--mrconso``
at your licensed local UMLS files (or use graph/graph_config.yaml).

Usage:
  python graph/build_mesh_graph.py --mrrel /path/MRREL.RRF --mrconso /path/MRCONSO.RRF
  python graph/build_mesh_graph.py --config graph/graph_config.yaml   # reuses mrrel/mrconso keys
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mesh_graph")

MESH_SAB = "MSH"                       # English MeSH source abbreviation in MRCONSO/MRREL
PREFERRED_TTY = {"MH", "NM"}           # Main Heading (descriptor) / Name of supplementary concept

# Readable labels for MRREL's coarse REL codes (used when a row has no fine-grained
# RELA label — e.g. the MeSH broader/narrower hierarchy). "XR" = explicitly not
# related, so it is dropped rather than relabeled.
COARSE_REL = {
    "RB": "broader", "RN": "narrower", "PAR": "parent", "CHD": "child",
    "SY": "synonym", "SIB": "sibling", "RO": "related", "RL": "similar",
    "RQ": "related_possibly_synonym", "AQ": "allowed_qualifier", "QB": "can_be_qualified_by",
}


def load_mesh_concepts(mrconso: Path, language: str = "ENG") -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Scan MRCONSO once → (mesh_cuis, cui->name, cui->mesh_code).

    A CUI is a MeSH concept iff it has an ``SAB == MSH`` atom. The name/code are
    taken from the concept's preferred English MeSH atom (TTY in {MH, NM}), with
    the first English MeSH string as a fallback.
    """
    logger.info("Pass 1/2 — reading MeSH concepts from %s", mrconso)
    mesh_cuis: set[str] = set()
    names: dict[str, str] = {}
    codes: dict[str, str] = {}
    pref: set[str] = set()             # CUIs whose stored name came from a preferred TTY
    with open(mrconso, "r", encoding="utf-8", errors="replace") as fh:
        for line_num, line in enumerate(fh, 1):
            f = line.rstrip("\n").split("|")
            if len(f) < 15 or f[11] != MESH_SAB:      # SAB column (0-based 11)
                continue
            cui = f[0].strip()
            mesh_cuis.add(cui)
            if f[1] != language:                       # names only from the requested language
                continue
            tty = f[12].strip()
            code = f[13].strip()
            name = f[14].strip().replace("\n", " ").replace("\r", " ")
            is_pref = tty in PREFERRED_TTY
            if cui not in names or (is_pref and cui not in pref):
                if name:
                    names[cui] = name
                if code:
                    codes[cui] = code
                if is_pref:
                    pref.add(cui)
            if line_num % 1_000_000 == 0:
                logger.info("  %s lines, %s MeSH CUIs, %s named",
                            f"{line_num:,}", f"{len(mesh_cuis):,}", f"{len(names):,}")
    logger.info("MeSH concepts: %s CUIs (%s with an English name, %s with a MeSH code)",
                f"{len(mesh_cuis):,}", f"{len(names):,}", f"{len(codes):,}")
    return mesh_cuis, names, codes


def extract_mesh_edges(mrrel: Path, mesh_cuis: set[str],
                       rela_only: bool = False) -> tuple[set[tuple[str, str, str]], int, int]:
    """Scan MRREL once → deduped edges (start=cui2, rel, end=cui1) with both endpoints in MeSH.

    Edge label is the fine-grained ``RELA`` when present; otherwise the coarse
    ``REL`` code mapped to a readable label (``broader``/``narrower``/…), so the
    MeSH hierarchy is kept. With ``rela_only=True`` the coarse edges are dropped
    instead (reproduces ``umls_processor.py``'s RELA-only convention).

    Returns (edges, coarse_kept, coarse_dropped).
    """
    mode = "RELA-only" if rela_only else "RELA + coarse hierarchy"
    logger.info("Pass 2/2 — extracting MeSH↔MeSH edges from %s  [%s]", mrrel, mode)
    edges: set[tuple[str, str, str]] = set()
    coarse_kept = coarse_dropped = 0
    with open(mrrel, "r", encoding="utf-8", errors="replace") as fh:
        for line_num, line in enumerate(fh, 1):
            f = line.rstrip("\n").split("|")
            if len(f) < 8:
                continue
            cui1, cui2 = f[0].strip(), f[4].strip()           # CUI1, CUI2
            if cui1 == cui2 or cui1 not in mesh_cuis or cui2 not in mesh_cuis:
                continue
            rela = f[7].strip()                                # fine-grained relationship
            if rela:
                label = rela.replace("-", "_")
            else:
                coarse = f[3].strip()                          # coarse REL code
                if not coarse or coarse == "XR":               # empty / "not related" → skip
                    continue
                if rela_only:
                    coarse_dropped += 1
                    continue
                label = COARSE_REL.get(coarse, coarse.lower())
                coarse_kept += 1
            edges.add((cui2, label, cui1))                     # same direction as umls_processor
            if line_num % 2_000_000 == 0:
                logger.info("  %s lines, %s MeSH edges", f"{line_num:,}", f"{len(edges):,}")
    logger.info("MeSH edges: %s unique (coarse-hierarchy edges kept: %s, dropped: %s)",
                f"{len(edges):,}", f"{coarse_kept:,}", f"{coarse_dropped:,}")
    return edges, coarse_kept, coarse_dropped


def write_csvs(out_dir: Path, prefix: str, edges: set[tuple[str, str, str]],
               names: dict[str, str], codes: dict[str, str]) -> tuple[Path, Path, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    node_cuis = {c for e in edges for c in (e[0], e[2])}     # nodes = concepts that appear in a kept edge

    nodes_file = out_dir / f"{prefix}_nodes.csv"
    rels_file = out_dir / f"{prefix}_relations.csv"

    logger.info("Writing %s nodes → %s", f"{len(node_cuis):,}", nodes_file)
    with open(nodes_file, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["concept:ID", "name", "mesh", "cui", ":LABEL"])
        for cui in node_cuis:
            w.writerow([cui, names.get(cui, cui), codes.get(cui, ""), cui, "Concept"])

    logger.info("Writing %s relationships → %s", f"{len(edges):,}", rels_file)
    with open(rels_file, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([":START_ID", "rel", ":END_ID", ":TYPE"])
        for start, rel, end in edges:
            w.writerow([start, rel, end, "RELATES_TO"])
    return nodes_file, rels_file, len(node_cuis)


def rel_histogram(edges: set[tuple[str, str, str]], top: int = 12) -> list[tuple[str, int]]:
    from collections import Counter
    c = Counter(rel for _, rel, _ in edges)
    return c.most_common(top)


def resolve_paths(args) -> tuple[Path, Path]:
    mrrel, mrconso = args.mrrel, args.mrconso
    if args.config:
        import yaml
        cfg = yaml.safe_load(Path(args.config).read_text()) or {}
        mrrel = mrrel or cfg.get("mrrel")
        mrconso = mrconso or cfg.get("mrconso")
    if not mrrel or not mrconso:
        raise SystemExit("Need both --mrrel and --mrconso (or a --config that defines them). "
                         "MRCONSO is required to know which concepts are MeSH.")
    mrrel_p, mrconso_p = Path(mrrel), Path(mrconso)
    for p in (mrrel_p, mrconso_p):
        if not p.exists():
            raise SystemExit(f"File not found: {p}")
    return mrrel_p, mrconso_p


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a MeSH-only Neo4j import graph from UMLS RRF.")
    ap.add_argument("--config", help="graph/graph_config.yaml (reuses its mrrel/mrconso paths)")
    ap.add_argument("--mrrel", help="Path to MRREL.RRF (relationships)")
    ap.add_argument("--mrconso", help="Path to MRCONSO.RRF (concept names + sources)")
    ap.add_argument("--output", "-o", default="graph/out/mesh", help="Output directory (default graph/out/mesh)")
    ap.add_argument("--prefix", default="mesh", help="Output file prefix (default 'mesh')")
    ap.add_argument("--language", default="ENG", help="Concept-name language (MRCONSO LAT)")
    ap.add_argument("--rela-only", action="store_true",
                    help="Keep only RELA-labeled edges (drops the coarse broader/narrower hierarchy); "
                         "reproduces umls_processor.py's convention.")
    args = ap.parse_args()

    mrrel, mrconso = resolve_paths(args)
    out_dir = Path(args.output)

    mesh_cuis, names, codes = load_mesh_concepts(mrconso, args.language)
    if not mesh_cuis:
        raise SystemExit("No MeSH (SAB=MSH) atoms found in MRCONSO — is this the right file?")
    edges, coarse_kept, coarse_dropped = extract_mesh_edges(mrrel, mesh_cuis, rela_only=args.rela_only)
    if not edges:
        raise SystemExit("No MeSH↔MeSH edges found — nothing to write.")
    nodes_file, rels_file, n_nodes = write_csvs(out_dir, args.prefix, edges, names, codes)

    hist = rel_histogram(edges)
    isolated = len(mesh_cuis) - n_nodes

    print("\n" + "=" * 64)
    print("MeSH-only graph")
    print("=" * 64)
    print(f"  MeSH concepts in MRCONSO : {len(mesh_cuis):,}")
    print(f"  Nodes written (in an edge): {n_nodes:,}   (isolated MeSH concepts dropped: {isolated:,})")
    print(f"  Edges written            : {len(edges):,}")
    if args.rela_only:
        print(f"  Coarse edges dropped     : {coarse_dropped:,}  (--rela-only)")
    else:
        print(f"  Coarse hierarchy edges   : {coarse_kept:,}  (broader/narrower/… kept)")
    print(f"  Nodes CSV : {nodes_file}")
    print(f"  Rels  CSV : {rels_file}")
    print("\n  Top relation types:")
    for rel, cnt in hist:
        print(f"    {rel:<24} {cnt:,}")
    print("\n  Import into a NEW 'mesh' database (leaves 'umls' untouched):")
    print(f"    bin/neo4j-admin database import full mesh \\")
    print(f"      --nodes=import/{nodes_file.name} \\")
    print(f"      --relationships=import/{rels_file.name} \\")
    print("      --overwrite-destination --skip-bad-relationships --verbose")

    # machine-readable summary
    import json
    summary = {
        "mesh_concepts_total": len(mesh_cuis),
        "nodes_written": n_nodes,
        "isolated_dropped": isolated,
        "edges_written": len(edges),
        "rela_only": args.rela_only,
        "coarse_hierarchy_edges_kept": coarse_kept,
        "coarse_edges_dropped": coarse_dropped,
        "top_relations": hist,
        "nodes_csv": str(nodes_file),
        "relations_csv": str(rels_file),
    }
    (out_dir / f"{args.prefix}_graph_summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
