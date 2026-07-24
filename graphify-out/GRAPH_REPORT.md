# Graph Report - .  (2026-07-23)

## Corpus Check
- Corpus is ~41,604 words - fits in a single context window. You may not need a graph.

## Summary
- 562 nodes · 1113 edges · 37 communities (33 shown, 4 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 96 edges (avg confidence: 0.58)
- Token cost: 76,165 input · 0 output

## Community Hubs (Navigation)
- API Session Endpoints
- Docs & Deployment Configs
- LLM Re-ranking
- Web Frontend Dependencies
- SAPBERT Dense Search
- Lexical Search & KB Server
- Web API Client
- TypeScript Config
- Neo4j Graph Client
- Auto-Annotate & Metrics UI
- UMLS Graph Builder
- API Request Models
- Performance Metrics
- Candidate Review UI
- Graph Client Config
- Annotation Page
- Candidate Retrieval Fusion
- KB Client
- Web Type Definitions
- Web App Shell
- Graph Visualization Panels
- Session Config Model
- Graph Stats Modal
- Graph Optimize Endpoints
- BC5CDR Converter
- Run Launcher Script
- Mock KB Server
- ELTool Package Init
- BC5CDR Eval Runner
- KB Package Init
- Annotation Output Concepts

## God Nodes (most connected - your core abstractions)
1. `LLMReranker` - 30 edges
2. `req()` - 27 edges
3. `_load_cfg()` - 25 edges
4. `Neo4jGraphClient` - 25 edges
5. `KBConfig` - 24 edges
6. `KBClient` - 24 edges
7. `SessionConfig` - 20 edges
8. `LLMRerankError` - 20 edges
9. `load_input()` - 19 edges
10. `SAPBERTIndexSearcher` - 19 edges

## Surprising Connections (you probably didn't know these)
- `Neo4j Docker Connection Config` --semantically_similar_to--> `Neo4j Connection Config + Graph Schema`  [INFERRED] [semantically similar]
  neo4j.docker.example.yaml → neo4j.example.yaml
- `API Server Dependencies (FastAPI, no ML)` --semantically_similar_to--> `KB Server Dependencies (Flask + heavy ML)`  [INFERRED] [semantically similar]
  requirements-api.txt → requirements-kb.txt
- `CreateSessionRequest` --uses--> `SessionConfig`  [INFERRED]
  api/main.py → eltool/config.py
- `CreateSessionRequest` --uses--> `GraphError`  [INFERRED]
  api/main.py → eltool/graph_client.py
- `CreateSessionRequest` --uses--> `Neo4jGraphClient`  [INFERRED]
  api/main.py → eltool/graph_client.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **HELIX Four-Service Architecture** — readme_web_ui, readme_api_server, readme_kb_server, readme_neo4j_graph [EXTRACTED 1.00]
- **Hybrid Retrieval Fusion Pipeline** — kb_search_setup_lexical_index, kb_search_setup_faiss_dense_index, kb_search_setup_rrf_fusion, readme_hybrid_retrieval [EXTRACTED 0.90]
- **Evaluation Ablation Configurations (lexical/dense/hybrid/hybrid+LLM)** — eval_readme_harness, eval_readme_any_match_scoring, eval_readme_mention_length_breakdown, eval_eval_config_example_config [EXTRACTED 0.85]

## Communities (37 total, 4 thin omitted)

### Community 0 - "API Session Endpoints"
Cohesion: 0.08
Nodes (59): auto_annotate_session(), create_session(), delete_session(), document_mentions(), export_csv(), get_progress(), get_row(), get_session() (+51 more)

### Community 1 - "Docs & Deployment Configs"
Cohesion: 0.06
Nodes (49): Host-mounted FAISS Index (not baked into images), Docker Compose Four-Service Stack, CPU-only Inference in Containers, In-Docker KB Endpoint URL Caveat, Conda Environment (Python 3.11), BC5CDR Eval Config (MeSH-id gold), Evaluation Config (UMLS gold), Multi-gold Any-match Scoring (+41 more)

### Community 2 - "LLM Re-ranking"
Cohesion: 0.12
Nodes (20): _get_reranker(), Candidate, _candidate_dict(), LLMReranker, Any, Path, LLM re-ranking (RAG disambiguation) for HELIX.  Hybrid retrieval (dense + lexica, Claude-backed re-ranker over a retrieved candidate set. (+12 more)

### Community 3 - "Web Frontend Dependencies"
Cohesion: 0.06
Nodes (35): autoprefixer, lucide-react, postcss, react, react-dom, react-router-dom, tailwindcss, @tanstack/react-query (+27 more)

### Community 4 - "SAPBERT Dense Search"
Cohesion: 0.10
Nodes (20): batch_search_mode(), debug_mode(), get_entity_mode(), main(), parse_arguments(), Resolve final adapter settings based on:           1) CLI overrides (highest pri, Load SAPBERT model + tokenizer (and adapter if requested), Load FAISS index + metadata + config (+12 more)

### Community 5 - "Lexical Search & KB Server"
Cohesion: 0.10
Nodes (20): LexicalSearcher, Return all character n-grams for configured sizes., Return ranked list of entity_ids from BM25., Return ranked list of entity_ids from character n-gram overlap., Search for entities matching query using BM25 + n-gram fusion.         Returns a, BM25 + character n-gram search over entity aliases., _load_yaml(), main() (+12 more)

### Community 6 - "Web API Client"
Cohesion: 0.13
Nodes (27): autoAnnotate(), createSession(), deleteSession(), getCandidateGraph(), getCandidates(), getDefaultPrompt(), getDocumentGraph(), getDocumentMentions() (+19 more)

### Community 7 - "TypeScript Config"
Cohesion: 0.09
Nodes (22): DOM, DOM.Iterable, ES2020, src, compilerOptions, allowImportingTsExtensions, baseUrl, isolatedModules (+14 more)

### Community 8 - "Neo4j Graph Client"
Cohesion: 0.16
Nodes (12): GraphError, GraphResult, Neo4jGraphClient, Any, RuntimeError, Raised when a graph query cannot be completed; callers degrade gracefully., Extra WHERE fragment (leading ' AND …') + params for relation filtering., Thin wrapper over the official neo4j driver. Lazily connects. (+4 more)

### Community 9 - "Auto-Annotate & Metrics UI"
Cohesion: 0.12
Nodes (12): AutoAnnotateModal(), OP_LABEL, pickBest(), Props, Status, MetricsModal(), OP_LABEL, Props (+4 more)

### Community 10 - "UMLS Graph Builder"
Cohesion: 0.21
Nodes (12): _find_config(), main(), _neo4j_conn(), Path, Connection creds from neo4j.yaml (single source), for the CREATE/START step., load_build_config(), main(), DataFrame (+4 more)

### Community 11 - "API Request Models"
Cohesion: 0.18
Nodes (14): AnnotateRequest, BatchAnnotateRequest, BatchUpdate, CreateSessionRequest, graph_document(), GraphDocumentRequest, metrics_toggle(), MetricsToggleRequest (+6 more)

### Community 12 - "Performance Metrics"
Cohesion: 0.18
Nodes (5): Metrics, _pctl(), Path, Record one graph-optimise call's cost/scale. NOT gated by the toggle., _rss_mb()

### Community 13 - "Candidate Review UI"
Cohesion: 0.17
Nodes (10): CandidateCard(), Props, scoreColor(), SEMANTIC_GROUP_LABELS, DocumentReview(), KBCands, Props, Candidate (+2 more)

### Community 14 - "Graph Client Config"
Cohesion: 0.21
Nodes (10): _config_search_paths(), GraphSchema, _ident(), load_neo4j_settings(), Path, Neo4j graph client for HELIX candidate/entity neighborhoods.  Two levels of grap, Validate a Cypher identifier (label / property / rel type) before interpolation., Where we look for the Neo4j config file, in priority order. (+2 more)

### Community 15 - "Annotation Page"
Cohesion: 0.22
Nodes (8): CountdownTimer(), Props, Annotate(), computeLlmUsed(), ContextCard(), highlightSpan(), parseIds(), LLMRerank

### Community 16 - "Candidate Retrieval Fusion"
Cohesion: 0.21
Nodes (12): CandidateRequest, _derived_url(), _fuse_candidates(), get_candidates(), KBConfig, Append /dense or /lexical to the configured endpoint URL., Merge dense + lexical, dedup by id (keep highest score), sorted by score desc., Dense + lexical candidates for a label, fused (mirrors get_candidates). (+4 more)

### Community 17 - "KB Client"
Cohesion: 0.23
Nodes (6): Candidate, _dotted_get(), KBClient, Any, KBConfig, _render_template()

### Community 18 - "Web Type Definitions"
Cohesion: 0.18
Nodes (10): getGraphStats(), getMetricsInsights(), DocProgress, DocumentMentions, GraphEdge, GraphNode, GraphStatDoc, MetricsOp (+2 more)

### Community 19 - "Web App Shell"
Cohesion: 0.24
Nodes (5): App(), NavBar(), queryClient, Configure(), Home()

### Community 20 - "Graph Visualization Panels"
Cohesion: 0.27
Nodes (7): FloatingGraphPanel(), loadSize(), Props, GraphPanel(), Placed, Props, GraphData

### Community 21 - "Session Config Model"
Cohesion: 0.29
Nodes (3): BaseModel, Path, SessionConfig

### Community 22 - "Graph Stats Modal"
Cohesion: 0.29
Nodes (4): fmt(), GraphStatsModal(), Props, GraphStats

### Community 23 - "Graph Optimize Endpoints"
Cohesion: 0.29
Nodes (7): _get_graph_client(), graph_candidates(), graph_optimize(), GraphCandidateRequest, GraphOptimizeRequest, Individual level — how one mention's candidates connect to each other + neighbor, Auto-match: pick candidates across the document to maximise graph connections.

### Community 24 - "BC5CDR Converter"
Cohesion: 0.70
Nodes (4): convert_split(), main(), _norm_cui(), Path

### Community 25 - "Run Launcher Script"
Cohesion: 0.50
Nodes (3): KMP_DUPLICATE_LIB_OK, run.sh script, start()

### Community 26 - "Mock KB Server"
Cohesion: 0.67
Nodes (3): _fake_candidates(), query(), Tiny mock KB REST server for local testing of ELTool.  Run:     python scripts/m

## Knowledge Gaps
- **59 isolated node(s):** `run_bc5cdr.sh script`, `KMP_DUPLICATE_LIB_OK`, `name`, `version`, `type` (+54 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `load_neo4j_settings()` connect `Graph Client Config` to `API Session Endpoints`, `Neo4j Graph Client`, `UMLS Graph Builder`, `Graph Optimize Endpoints`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **Why does `LLMReranker` connect `LLM Re-ranking` to `API Session Endpoints`, `Candidate Retrieval Fusion`, `API Request Models`, `Graph Optimize Endpoints`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Why does `Neo4jGraphClient` connect `Neo4j Graph Client` to `API Session Endpoints`, `API Request Models`, `Graph Client Config`, `Candidate Retrieval Fusion`, `Graph Optimize Endpoints`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `LLMReranker` (e.g. with `AnnotateRequest` and `BatchAnnotateRequest`) actually correct?**
  _`LLMReranker` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `Neo4jGraphClient` (e.g. with `AnnotateRequest` and `BatchAnnotateRequest`) actually correct?**
  _`Neo4jGraphClient` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `KBConfig` (e.g. with `AnnotateRequest` and `BatchAnnotateRequest`) actually correct?**
  _`KBConfig` has 12 INFERRED edges - model-reasoned connections that need verification._
- **What connects `run_bc5cdr.sh script`, `KMP_DUPLICATE_LIB_OK`, `name` to the rest of the system?**
  _59 weakly-connected nodes found - possible documentation gaps or missing edges._