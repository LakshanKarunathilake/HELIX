#!/usr/bin/env python3
"""
Quick check that HELIX can pull candidate/document subgraphs from your Neo4j.

Configure the connection in `neo4j.yaml` (copy from `neo4j.example.yaml`), or via
NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD env vars, then:

    python scripts/test_graph.py C1310451 C0032089 C0024109
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eltool.graph_client import GraphError, Neo4jGraphClient, load_neo4j_settings


def main() -> None:
    cuis = sys.argv[1:] or ["C1310451", "C0032089"]
    settings = load_neo4j_settings()
    print(f"Config source: {settings['source'] or '(env/defaults)'}")
    client = Neo4jGraphClient()   # loads neo4j.yaml / env
    try:
        print(f"Connecting to {client.uri} (db={client.database or 'default'}) …")
        print(f"\nCandidate ids: {cuis}\n")

        print("── individual level: candidate_graph ──")
        cg = client.candidate_graph(cuis, neighbor_limit=15)
        print(f"  nodes: {len(cg.nodes)}   edges: {len(cg.edges)}")
        for e in cg.edges[:15]:
            print(f"    {e['source']} --[{e['rel']}]-- {e['target']}")

        print("\n── document level: document_graph ──")
        dg = client.document_graph(cuis)
        print(f"  nodes: {len(dg.nodes)}   edges: {len(dg.edges)}")
        for n in dg.nodes:
            flag = " (ISOLATED — reconsider)" if n.get("isolated") else ""
            print(f"    {n['id']}  degree={n.get('degree', 0)}{flag}")
    except GraphError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
