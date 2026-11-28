# HELIX hosted demo: design

Status: approved by the owner, with invite-only access. 2026-11-27.
Diagram: [`../../architecture/helix-tool-kit.html`](../../architecture/helix-tool-kit.html).

## Intent

Let evaluators try the HELIX tool in their browser. The owner hosts it as Docker containers on
a Linux server (later a DGX). Evaluators see the tool working on a small demo index and demo
graph, without any full index, full graph, UMLS data or study annotations.

Decided by the owner:

* Expose the tool, not the full index. Which index to use stays a separate choice.
* Release 1 uses a **small demo index**.
* Release 1 **includes the graph** (a Neo4j container).
* The tool is **hosted** by the owner. Linux for now; DGX is part of the plan.
* The documentation must show which parts are Docker containers.

An earlier draft planned a downloadable kit. The owner then chose hosting, so there is no
image registry, download zip or per-computer support in this design. The owner's answer that
downloads could be public no longer applies; publishing images can be a later piece of work.

## Non-goals

* Reproducing the paper's or the pilot study's accuracy numbers. A different index and graph
  give different candidates and hints.
* UMLS, the full indexes, the UMLS-derived graphs, study sessions or their annotations on the
  public server.
* GPU use, and anything DGX-specific, in release 1.
* Open access without sign-in.

## Approaches

1. **Add `kb` and `neo4j` containers to the existing hosted deployment (chosen).** One command,
   one server, accounts and HTTPS already designed. Needs an allow-list entry so the API may
   call the two internal containers.
2. Run the search server and Neo4j as a separate kit reached over public HTTPS and encrypted
   Bolt. No allow-list, but two more public endpoints and certificates to run.
3. Downloadable containers that evaluators run themselves. Rejected by the owner's choice to
   host.

## What is a Docker container

| Part | What it is | Image | Reachable from the internet |
|---|---|---|---|
| `caddy` | **Docker container**, HTTPS front door | public image | yes, ports 80 and 443 |
| `web` | **Docker container**, web app | built on the server | only through `caddy` |
| `api` | **Docker container**, FastAPI | built on the server | only through `web` |
| `kb` | **Docker container**, search server | built on the server | no |
| `neo4j` | **Docker container**, Neo4j Community | public image | no |
| sessions volume | Docker volume used by `api` (sessions, answers, accounts) | | no |
| demo index | folder on the server, mounted read-only into `kb` | | no |
| graph volume | Docker volume used by `neo4j` | | no |
| model cache | Docker volume used by `kb` | | no |
| Google sign-in (Firebase), LLM API | outside services; the LLM is optional | | |

## Access

The existing hosted accounts apply (`HELIX_AUTH=1`, `HELIX_HOSTED=1`): the owner invites each
evaluator as an annotator or reviewer on the demo session. Each annotator has a private layer,
so evaluators do not see each other's answers. Only admins create or configure sessions, so
evaluators cannot point the server at other addresses.

The server needs what the hosted README already asks for: a hostname, ports 80 and 443 open to
the internet, and a Firebase project.

## Demo data

Both come from NLM's MeSH 2026 descriptor file, downloaded from NLM, never extracted from UMLS
files. Scope: descriptors in the Diseases branch (tree numbers starting `C`).

**Index** (`scripts/build_demo_index.py`):

* Writes `<prefix>.faiss`, `<prefix>_metadata.pkl`, `<prefix>_config.json`.
* One entry per name or entry term: `entity_id` (descriptor UI), `primary_alias` (preferred
  name), `all_aliases`, `processed_text` (the embedded term), `definition` (scope note), empty
  `semantic_groups` and `semantic_types`.
* Flat (exact) FAISS index.
* Model: public `cambridgeltl/SapBERT-from-PubMedBERT-fulltext`, no adapter, pinned to one
  revision recorded in the index config. Check its licence first.
* Size is estimated at a few hundred MB (the owner's disease-only index folder is about
  300 MB). To be measured.

**Graph** (new builder; the existing `graph/build_mesh_graph.py` reads licensed UMLS files and
must not be used here):

* One node per descriptor, keyed by the same descriptor UI as the index, with its name.
* Edges: parent and child from tree numbers, and MeSH "see related" links, using the labels
  and node and relationship names HELIX already expects.
* This graph is much sparser than the UMLS graph, so graph hints will appear less often.

**Demo session** (a seeding script run on the server):

* About ten short texts written for this demo, 40 to 60 disease mentions, including
  abbreviations and ambiguous names. No saved answers.
* Files inside the session's own folder, as hosted mode requires.
* KB address `http://kb:8765/query`; graph connection `bolt://neo4j:7687` with the password
  from `.env`; LLM re-ranking off.

NLM's terms: show "Courtesy of the U.S. National Library of Medicine", name the MeSH version,
say the data may not be the most current, and do not imply NLM endorsement. Show this where
evaluators can see it. Sources:
<https://www.nlm.nih.gov/databases/download/terms_and_conditions_mesh.html> and
<https://www.nlm.nih.gov/databases/download/terms_and_conditions.html>.

## Changes needed

Items 1 to 4 were found by reading the code; none has been reproduced in a container.

1. `kb/search_sapbert_index.py` calls `model.adapter_summary()` even without an adapter. A
   plain model has no such method, so the demo index should fail at the first search.
2. The search server cannot be told which model revision to load. Add that.
3. The model loads on the first search, so `/health` reports ready too early. Warm it up at
   start.
4. `kb/config.yaml` holds paths under the owner's home folder and its adapter path overrides
   the index settings. The container needs a neutral config.
5. The server asks the index for `k` entries and then drops repeats of the same concept. Fetch
   more and trim to `k`, only for indexes whose config asks for it; the demo index does.
6. Lock dependency versions, pin base images, and install CPU-only `torch`.
7. In `deploy/hosted/docker-compose.yml`: add `kb` and `neo4j` with no published ports, their
   volumes, and `HELIX_URL_ALLOWLIST` entries `kb:8765` and `neo4j:7687`.
8. The index builder, graph builder and session seeding script, runnable in containers so the
   server needs only Docker.
9. Update the hosted README, including a larger server size once memory is measured.

## Done when

* On an x86 Linux machine with Docker, the documented commands bring up all five containers.
* An invited evaluator signs in, opens the demo session, sees candidates and graph
  information, saves answers; a reviewer can decide and export.
* The search server and Neo4j cannot be reached from outside the server.
* No UMLS file or UMLS-derived data is on the server.
* Memory use is measured and written in the README.
* Builders and server changes have automated tests.

## Later: DGX

Check whether the machine is x86 or ARM, add GPU `torch` and the NVIDIA container runtime, and
confirm it can have a public hostname. Nothing in release 1 should block this.

## Calls made (correct if wrong)

* Invite-only access, as the hosted deployment already works.
* x86 Linux first.
* Images are built on the server from the repository; none are published.
* Evaluators annotate sessions the owner sets up; they do not create their own.
* The work happens on its own branch from `main` after pull request 7 is merged, since accounts
  live there.
* Downloading the MeSH file and anything done on the server need the owner's go-ahead.

## Left to the builder

Script layout, how the graph is loaded, test fixtures, README wording.
