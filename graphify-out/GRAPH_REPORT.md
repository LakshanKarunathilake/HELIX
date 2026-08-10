# Graph Report - .  (2026-07-25)

## Corpus Check
- 42 files · ~100,981 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 731 nodes · 1227 edges · 58 communities (48 shown, 10 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 97 edges (avg confidence: 0.68)
- Token cost: 335,373 input · 0 output

## Community Hubs (Navigation)
- KB Client & Config
- Docs & Deployment Configs
- Web Review & Graph UI
- Neo4j Graph Client
- Web Frontend Dependencies
- SAPBERT Dense Search
- Web API Client
- API Session Endpoints
- LLM Re-ranking
- Neo4j Loader
- TypeScript Config
- API Request Models
- KB Server
- UMLS Graph Builder
- Annotate Screenshot Figure
- Graph Panel Screenshot Figure
- Candidate Retrieval Fusion
- Metrics Modal & Types
- Lexical Search
- Auto-Annotate & Configure UI
- Evaluation Scoring
- UMLS Graph Docs
- MeSH Graph Builder
- Graph Optimize Endpoints
- Candidate Card UI
- Session Config Model
- Graph Stats Modal
- BC5CDR Hybrid Eval
- Oxidation Subgraph Figure
- Skin (Human) Subgraph Figure
- BC5CDR Converter
- BC5CDR Auto-Annotate Eval
- BC5CDR Graph Eval
- Oxidation-Reduction Figure
- Skin Subgraph Figure
- Run Launcher Script
- BC5CDR Session Builder
- Doc Review Screenshot Figure
- Mock KB Server
- File Upload Parsing
- MedMentions Eval
- Countdown Timer
- ELTool Package Init
- BC5CDR Eval Runner
- KB Package Init
- API KBConfig Import
- Candidate Stub
- Graph Client Any Type
- Graph Client Error
- UMLS DataFrame Type

## God Nodes (most connected - your core abstractions)
1. `Neo4jGraphClient` - 32 edges
2. `req()` - 27 edges
3. `_load_cfg()` - 24 edges
4. `KBConfig` - 24 edges
5. `SessionConfig` - 20 edges
6. `SAPBERTIndexSearcher` - 18 edges
7. `GraphError` - 18 edges
8. `compilerOptions` - 17 edges
9. `LLMReranker` - 16 edges
10. `main()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `HELIX Favicon (gradient helix/ladder icon)` --conceptually_related_to--> `HELIX (Hybrid Entity Linking Tool)`  [INFERRED]
  web/public/favicon.svg → README.md
- `API Server Dependencies (FastAPI, no ML)` --semantically_similar_to--> `KB Server Dependencies (Flask + heavy ML)`  [INFERRED] [semantically similar]
  requirements-api.txt → requirements-kb.txt
- `Mention-length Breakdown (short-mention rescue)` --conceptually_related_to--> `Query-length Search Routing (lexical vs FAISS)`  [INFERRED]
  eval/README.md → kb/SEARCH_SETUP.md
- `Reciprocal Rank Fusion (RRF) of Lexical Components` --conceptually_related_to--> `Hybrid Retrieval`  [INFERRED]
  kb/SEARCH_SETUP.md → README.md
- `CreateSessionRequest` --uses--> `SessionConfig`  [INFERRED]
  api/main.py → eltool/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **HELIX Service Architecture (UI, API, KB, Graph)** — readme_web_ui, readme_api_server, readme_kb_server, readme_neo4j_graph [EXTRACTED 1.00]
- **Hybrid Retrieval via Reciprocal Rank Fusion** — readme_dense_retrieval_sapbert_faiss, readme_lexical_retrieval_bm25_ngram, readme_reciprocal_rank_fusion, readme_hybrid_retrieval [EXTRACTED 1.00]
- **UMLS to Neo4j Graph Build Pipeline** — graph_readme_mrrel, graph_readme_mrconso, graph_readme_umls_neo4j_builder, graph_readme_concept_node, graph_readme_relates_to_edge [EXTRACTED 1.00]
- **HELIX Four-Service Architecture** — readme_web_ui, readme_api_server, readme_kb_server, readme_neo4j_graph [EXTRACTED 1.00]
- **Hybrid Retrieval Fusion Pipeline** — kb_search_setup_lexical_index, kb_search_setup_faiss_dense_index, kb_search_setup_rrf_fusion, readme_hybrid_retrieval [EXTRACTED 0.90]
- **Evaluation Ablation Configurations (lexical/dense/hybrid/hybrid+LLM)** — eval_readme_harness, eval_readme_any_match_scoring, eval_readme_mention_length_breakdown, eval_eval_config_example_config [EXTRACTED 0.85]

## Communities (58 total, 10 thin omitted)

### Community 0 - "KB Client & Config"
Cohesion: 0.07
Nodes (31): Candidate, _dotted_get(), KBClient, Any, KBConfig, _render_template(), Metrics, _pctl() (+23 more)

### Community 1 - "Docs & Deployment Configs"
Cohesion: 0.06
Nodes (50): Host-mounted FAISS Index (not baked into images), Docker Compose Four-Service Stack, CPU-only Inference in Containers, In-Docker KB Endpoint URL Caveat, Conda Environment (Python 3.11), BC5CDR Eval Config (MeSH-id gold), Evaluation Config (UMLS gold), Multi-gold Any-match Scoring (+42 more)

### Community 2 - "Web Review & Graph UI"
Cohesion: 0.06
Nodes (30): App(), DocumentReview(), KBCands, Props, FloatingGraphPanel(), loadSize(), Props, buildSvg() (+22 more)

### Community 3 - "Neo4j Graph Client"
Cohesion: 0.08
Nodes (29): Any, _config_search_paths(), GraphResult, GraphSchema, _ident(), load_neo4j_settings(), Neo4jGraphClient, Path (+21 more)

### Community 4 - "Web Frontend Dependencies"
Cohesion: 0.06
Nodes (35): autoprefixer, lucide-react, postcss, react, react-dom, react-router-dom, tailwindcss, @tanstack/react-query (+27 more)

### Community 5 - "SAPBERT Dense Search"
Cohesion: 0.10
Nodes (20): batch_search_mode(), debug_mode(), get_entity_mode(), main(), parse_arguments(), Resolve final adapter settings based on:           1) CLI overrides (highest pri, Load SAPBERT model + tokenizer (and adapter if requested), Load FAISS index + metadata + config (+12 more)

### Community 6 - "Web API Client"
Cohesion: 0.13
Nodes (27): autoAnnotate(), createSession(), deleteSession(), getCandidateGraph(), getCandidates(), getDefaultPrompt(), getDocumentGraph(), getDocumentMentions() (+19 more)

### Community 7 - "API Session Endpoints"
Cohesion: 0.10
Nodes (24): document_mentions(), export_csv(), get_progress(), get_row(), get_session(), graph_stats(), graph_stats_reset(), list_documents() (+16 more)

### Community 8 - "LLM Re-ranking"
Cohesion: 0.17
Nodes (13): _candidate_dict(), LLMReranker, LLMRerankError, Any, Path, RuntimeError, LLM re-ranking (RAG disambiguation) for HELIX.  Hybrid retrieval (dense + lexica, Claude-backed re-ranker over a retrieved candidate set. (+5 more)

### Community 9 - "Neo4j Loader"
Cohesion: 0.15
Nodes (24): admin_needed_msg(), base_name(), connect(), count_rows(), create_database(), current_user_roles(), database_exists(), find_id_column() (+16 more)

### Community 10 - "TypeScript Config"
Cohesion: 0.09
Nodes (22): DOM, DOM.Iterable, ES2020, src, compilerOptions, allowImportingTsExtensions, baseUrl, isolatedModules (+14 more)

### Community 11 - "API Request Models"
Cohesion: 0.13
Nodes (18): AnnotateRequest, BatchAnnotateRequest, BatchUpdate, create_session(), CreateSessionRequest, graph_document(), GraphDocumentRequest, metrics_toggle() (+10 more)

### Community 12 - "KB Server"
Cohesion: 0.15
Nodes (18): _cached_search(), _dense_fn(), _lex_fn(), _load_yaml(), main(), _parse_args(), Path, query() (+10 more)

### Community 13 - "UMLS Graph Builder"
Cohesion: 0.21
Nodes (12): DataFrame, _find_config(), main(), _neo4j_conn(), Path, Connection creds from neo4j.yaml (single source), for the CREATE/START step., load_build_config(), main() (+4 more)

### Community 14 - "Annotate Screenshot Figure"
Cohesion: 0.13
Nodes (18): Entity Annotation Workflow, Ranked KB Candidate List, Candidate Similarity Score, Document Context Passage View, Entity Mention Panel (superoxide dismutase), Graph Panel Toggle, HELIX Entity Linking Annotation Tool, Highlighted Entity Span in Text (+10 more)

### Community 15 - "Graph Panel Screenshot Figure"
Cohesion: 0.14
Nodes (18): associated_morphology_of / has_associated_morphology, Auto-match (maximise connections), Candidate Resolution Panel (b-cell lymphoma, score), Show Candidate Suggestions on Graph Toggle, has_cell_origin / is_normal_cell_origin_of_disease, Document Graph Visualization, Document Review View, finding_site_of / has_finding_site (+10 more)

### Community 16 - "Candidate Retrieval Fusion"
Cohesion: 0.15
Nodes (17): auto_annotate_session(), CandidateRequest, _derived_url(), _fuse_candidates(), get_candidates(), _get_reranker(), _pick_best(), Append /dense or /lexical to the configured endpoint URL. (+9 more)

### Community 17 - "Metrics Modal & Types"
Cohesion: 0.12
Nodes (13): getGraphStats(), getMetricsInsights(), OP_LABEL, Props, DocProgress, DocumentMentions, GraphEdge, GraphNode (+5 more)

### Community 18 - "Lexical Search"
Cohesion: 0.19
Nodes (6): LexicalSearcher, Return all character n-grams for configured sizes., Return ranked list of entity_ids from BM25., Return ranked list of entity_ids from character n-gram overlap., Search for entities matching query using BM25 + n-gram fusion.         Returns a, BM25 + character n-gram search over entity aliases.

### Community 19 - "Auto-Annotate & Configure UI"
Cohesion: 0.18
Nodes (8): AutoAnnotateModal(), OP_LABEL, pickBest(), Props, Status, DEFAULT_KB, ParsedFile, SessionConfig

### Community 20 - "Evaluation Scoring"
Cohesion: 0.29
Nodes (11): _derived(), fuse(), _idset(), length_bucket(), main(), _pct(), _pred_ids(), print_report() (+3 more)

### Community 21 - "UMLS Graph Docs"
Cohesion: 0.27
Nodes (11): Graph Build Configuration, Concept Node (:Concept {cui, name}), load_neo4j.py (online Bolt loader), MeSH-only Graph (build_mesh_graph.py), UMLS MRCONSO.RRF (concept names), UMLS MRREL.RRF (relationships), neo4j-admin offline bulk import, RELATES_TO Edge (+3 more)

### Community 22 - "MeSH Graph Builder"
Cohesion: 0.40
Nodes (9): extract_mesh_edges(), load_mesh_concepts(), main(), Path, Scan MRCONSO once → (mesh_cuis, cui->name, cui->mesh_code).      A CUI is a MeSH, Scan MRREL once → deduped edges (start=cui2, rel, end=cui1) with both endpoints, rel_histogram(), resolve_paths() (+1 more)

### Community 23 - "Graph Optimize Endpoints"
Cohesion: 0.22
Nodes (9): _get_graph_client(), graph_candidates(), graph_optimize(), graph_optimize_all(), GraphCandidateRequest, GraphOptimizeRequest, Individual level — how one mention's candidates connect to each other + neighbor, Auto-match: pick candidates across the document to maximise graph connections. (+1 more)

### Community 24 - "Candidate Card UI"
Cohesion: 0.28
Nodes (5): CandidateCard(), Props, scoreColor(), SEMANTIC_GROUP_LABELS, Candidate

### Community 25 - "Session Config Model"
Cohesion: 0.29
Nodes (3): BaseModel, Path, SessionConfig

### Community 26 - "Graph Stats Modal"
Cohesion: 0.29
Nodes (4): fmt(), GraphStatsModal(), Props, GraphStats

### Community 27 - "BC5CDR Hybrid Eval"
Cohesion: 0.57
Nodes (6): bucket(), eval_split(), fuse_max(), fuse_rrf(), hit(), ids()

### Community 28 - "Oxidation Subgraph Figure"
Cohesion: 0.47
Nodes (6): Oxidation Subgraph Figure, HELIX Graph-Aware Entity Linking, Peripheral Hub Concept Nodes, Oxidation, Oxidation-Reduction (Redox) Neighborhood, UMLS/MeSH Neighbor Concepts

### Community 29 - "Skin (Human) Subgraph Figure"
Cohesion: 0.47
Nodes (6): Contrasting Figure: sub_skin.png, Subgraph Figure: Skin, Human Neighborhood, Peripheral Hub Nodes, Concentric Ring of Neighbor Concepts, Skin, Human, UMLS/MeSH Concept Neighborhood

### Community 30 - "BC5CDR Converter"
Cohesion: 0.70
Nodes (4): convert_split(), main(), _norm_cui(), Path

### Community 31 - "BC5CDR Auto-Annotate Eval"
Cohesion: 0.70
Nodes (4): flush(), pick_best(), step1_retrieval(), step2_graph()

### Community 32 - "BC5CDR Graph Eval"
Cohesion: 0.70
Nodes (4): acc(), _ids(), Path, run()

### Community 33 - "Oxidation-Reduction Figure"
Cohesion: 0.60
Nodes (5): Biological Process, Oxidation-Reduction Subgraph Figure, Gene Product, Oxidation-Reduction, UMLS Concept Neighborhood

### Community 34 - "Skin Subgraph Figure"
Cohesion: 0.40
Nodes (5): Subgraph Figure: Skin Neighborhood, Finding Site Neighbor Concept, Location Neighbor Concept, Skin, Specimen Source Topography Neighbor Concept

### Community 35 - "Run Launcher Script"
Cohesion: 0.50
Nodes (3): KMP_DUPLICATE_LIB_OK, run.sh script, start()

### Community 36 - "BC5CDR Session Builder"
Cohesion: 0.67
Nodes (3): build(), norm_cui(), Path

### Community 37 - "Doc Review Screenshot Figure"
Cohesion: 0.50
Nodes (4): Blank Placeholder Image (No Rendered UI), Document Review Screen, Entity Linking Annotation, HELIX Document Review Screenshot

### Community 38 - "Mock KB Server"
Cohesion: 0.67
Nodes (3): _fake_candidates(), query(), Tiny mock KB REST server for local testing of ELTool.  Run:     python scripts/m

### Community 39 - "File Upload Parsing"
Cohesion: 0.67
Nodes (3): parse_file(), Upload a CSV/TSV, return columns + preview. File stored in /tmp., UploadFile

## Ambiguous Edges - Review These
- `HELIX Document Review Screenshot` → `Blank Placeholder Image (No Rendered UI)`  [AMBIGUOUS]
  paper/figures/doc_review.png · relation: references

## Knowledge Gaps
- **86 isolated node(s):** `run_bc5cdr.sh script`, `KMP_DUPLICATE_LIB_OK`, `name`, `version`, `type` (+81 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `HELIX Document Review Screenshot` and `Blank Placeholder Image (No Rendered UI)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `load_neo4j_settings()` connect `Neo4j Graph Client` to `Neo4j Loader`, `Graph Optimize Endpoints`, `UMLS Graph Builder`, `API Session Endpoints`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Why does `Neo4jGraphClient` connect `Neo4j Graph Client` to `Candidate Retrieval Fusion`, `API Request Models`, `Graph Optimize Endpoints`, `API Session Endpoints`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Why does `KBConfig` connect `API Request Models` to `KB Client & Config`, `API Session Endpoints`, `Candidate Retrieval Fusion`, `Evaluation Scoring`, `Graph Optimize Endpoints`, `Session Config Model`?**
  _High betweenness centrality (0.014) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `Neo4jGraphClient` (e.g. with `AnnotateRequest` and `BatchAnnotateRequest`) actually correct?**
  _`Neo4jGraphClient` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `KBConfig` (e.g. with `AnnotateRequest` and `BatchAnnotateRequest`) actually correct?**
  _`KBConfig` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `SessionConfig` (e.g. with `AnnotateRequest` and `BatchAnnotateRequest`) actually correct?**
  _`SessionConfig` has 10 INFERRED edges - model-reasoned connections that need verification._