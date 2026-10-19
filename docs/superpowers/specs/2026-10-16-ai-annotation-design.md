# Local LLM on the DGX Spark + AI in the annotation flow — design & runbook

Date: 2026-10-16 · Owner: Lakshan Karunathilake · Journal deadline (New Generation Computing special issue): **31 Dec 2026**

Implementation plan: [`docs/superpowers/plans/2026-10-16-ai-annotation-flows.md`](../plans/2026-10-16-ai-annotation-flows.md)

## 1. Goal

Run a large open-weight LLM on the lab's **DGX Spark**, reach it securely from the laptop through
university IT, and put it through the **same annotation protocol as the human annotators** in
three ways:

| Mode | Question it answers | Where it runs |
|---|---|---|
| **A. AI as annotator** | How does a local LLM compare with humans on the same mentions, under the same information (C1/C2/C3)? Does it report the same hints, and is its confidence calibrated? | Offline batch, before/independent of humans |
| **B. AI as assistant (C4)** | Does showing the LLM's pick + rationale help annotators, or do they over-trust it (automation bias)? | New counterbalanced study arm |
| **C. AI pre-annotates, human reviews** | How much human time is saved if confident LLM answers are auto-accepted and only the rest is reviewed — and what does that cost in accuracy? | Separate document blocks after the main study |

Non-goals: fine-tuning, multi-GPU clusters, exposing the model to anyone outside the project.

## 2. What HELIX already has

* `eltool/llm_reranker.py` already talks to any **OpenAI-compatible server** (`llm_provider: "openai"`
  + `llm_base_url`) and forces a structured answer with a named tool call whose `entity_id` is an
  enum of the retrieved ids. A vLLM server on the DGX therefore plugs in with configuration only.
* Results are cached on disk (`~/.cache/helix_llm_rerank`), keyed by model, base URL, prompt, mention,
  context and candidate ids. **Gap:** the key does not include the output schema, so a second
  mode with a different schema would collide. The plan adds a cache namespace.
* Per-decision provenance (`_method`, `_elapsed_ms`, …) and feedback (`_hint`, `_confidence`,
  `_panels`, reflection) already exist and are opt-in per session — the AI writes into the same
  columns so `eval/analyze_study.py` can compare like with like.

## 3. Hardware: what the DGX Spark can run

DGX Spark (GB10 Grace Blackwell): **128 GB unified LPDDR5x memory shared by CPU and GPU,
~273 GB/s bandwidth**, 20-core **Arm (aarch64)** CPU, DGX OS (Ubuntu-based), 4 TB NVMe.
Two consequences:

1. **Arm, not x86** — use NVIDIA's NGC containers (built for aarch64 + Blackwell) instead of
   `pip install vllm`, which may not have wheels for this platform.
2. **Memory bandwidth, not compute, limits generation speed.** Tokens/s ≈ bandwidth ÷ bytes of
   *active* weights read per token. Mixture-of-experts (MoE) models with few active parameters are
   far faster than dense models of similar quality.

Candidate models for the bake-off (check the current version of each on Hugging Face before
downloading; numbers are rough estimates to be **measured** in step 6.3):

| Model | Type | Memory (quantised) | Expected speed | Why consider it |
|---|---|---|---|---|
| `openai/gpt-oss-120b` | MoE, ~5B active, MXFP4 | ~65 GB | fast (tens of tok/s) | Strong reasoning, NVIDIA's showcase model for Spark |
| Qwen3 MoE (e.g. 30B-A3B / Next-80B-A3B) | MoE, ~3B active | 20–50 GB | fast | Strong multilingual + tool calling |
| `meta-llama/Llama-3.3-70B-Instruct` | dense, FP8/NVFP4 | 35–70 GB | slow (~3–8 tok/s) | Widely used baseline, easy to cite |
| MedGemma 27B (text) | dense, BF16/FP8 | 27–55 GB | medium | Medical domain model |

One answer is ~100–200 output tokens, so even a slow dense model takes ~20–40 s per mention:
720 calls (240 mentions × 3 conditions) is an overnight run. Leave ~15–20 GB of the 128 GB for the
OS and KV cache (`--gpu-memory-utilization 0.75–0.8`), and run **one model at a time**.

## 4. Serving the model on the DGX

**Choice: vLLM in NVIDIA's NGC container**, OpenAI-compatible API, bound to **localhost only**,
protected by an API key. vLLM enforces named tool calls with guided decoding, so the `entity_id`
enum is guaranteed — this is what keeps the model from inventing ids. (Ollama is fine for quick
manual tries but is weaker at forced tool calls; do not use it for study runs.)

```bash
# on the DGX (once): HF token for gated models, and a key HELIX will send
export HF_TOKEN=hf_...            # Hugging Face, for gated models (Llama, Gemma)
export HELIX_LLM_KEY=$(openssl rand -hex 24); echo "$HELIX_LLM_KEY" > ~/.helix_llm_key

# serve (replace <tag> with the current tag at catalog.ngc.nvidia.com → "vLLM")
docker run -d --name helix-llm --restart unless-stopped \
  --gpus all --ipc=host \
  -p 127.0.0.1:8000:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -e HF_TOKEN \
  nvcr.io/nvidia/vllm:<tag> \
  vllm serve openai/gpt-oss-120b \
    --api-key "$HELIX_LLM_KEY" \
    --max-model-len 16384 \
    --gpu-memory-utilization 0.8

docker logs -f helix-llm                       # wait for "Application startup complete"
curl -s -H "Authorization: Bearer $HELIX_LLM_KEY" http://127.0.0.1:8000/v1/models
```

`-p 127.0.0.1:8000:8000` is deliberate: the model is **never reachable from the network**, only
through SSH. `--restart unless-stopped` brings it back after a reboot.

## 5. Connecting from the laptop through university IT

The laptop reaches the DGX over **SSH via the university gateway (jump host)** and forwards a
local port to the DGX's localhost-only model server. No new firewall holes beyond SSH.

```
laptop ──ssh──▶ gateway.<univ>.ac.jp ──ssh──▶ DGX (127.0.0.1:8000 vLLM)
  └─ 127.0.0.1:18000 ═════════════ tunnel ══════════════▶ ┘
```

**5.1 Ask IT first (send this list):**
1. Which way in is allowed: an SSH gateway/bastion host, or the university VPN then direct SSH?
2. A fixed IP or DHCP reservation and a hostname for the DGX.
3. SSH (port 22) reachable on the DGX from that gateway/VPN — **no other inbound ports needed**.
4. Confirmation that running a service bound to `127.0.0.1` on the DGX, reached via SSH port
   forwarding, is within policy.
5. Whether they require key-based login only (recommended anyway) or MFA on the gateway.

Do **not** set up Tailscale, Cloudflare Tunnel, ngrok or similar without IT's written approval —
they bypass the university perimeter and are commonly against policy.

**5.2 Laptop setup (`~/.ssh/config`):**

```sshconfig
Host uni-gw
  HostName gateway.<univ>.ac.jp
  User <university-id>
  IdentityFile ~/.ssh/id_ed25519

Host dgx
  HostName <dgx-hostname-or-ip>        # as seen from the gateway
  User <dgx-user>
  ProxyJump uni-gw                     # drop this line if you use the VPN instead
  IdentityFile ~/.ssh/id_ed25519
  LocalForward 18000 127.0.0.1:8000    # laptop :18000 → DGX vLLM
  ServerAliveInterval 30
  ServerAliveCountMax 3
  ExitOnForwardFailure yes
```

```bash
ssh-keygen -t ed25519                     # once, if you have no key
ssh-copy-id uni-gw && ssh-copy-id dgx     # once
ssh -N dgx                                # open the tunnel (leave running)
# or keep it alive across sleep/wifi changes:
brew install autossh && autossh -M 0 -N dgx

# test from the laptop
curl -s -H "Authorization: Bearer $(cat ~/.helix_llm_key)" http://127.0.0.1:18000/v1/models
```

Local port **18000**, not 8000: the HELIX API already uses 8000 on the laptop. NVIDIA's *Sync*
desktop app automates the same SSH + port forwarding and is worth trying first; if it cannot go
through the gateway, the plain SSH config above is the fallback.

**5.3 Point HELIX at it** (session config, or Configure page → LLM section):

```json
"llm_provider": "openai",
"llm_base_url": "http://127.0.0.1:18000/v1",
"llm_model": "openai/gpt-oss-120b",
"llm_api_key": "<contents of ~/.helix_llm_key>"
```

## 6. Design of the three AI modes

### 6.1 Shared rules

* **The AI never sees gold.** Gold stays in `eval/out/study_gold.csv`; no AI script reads it.
* **Same information as the human in that condition.** The AI's prompt is built from the same
  candidate list the UI shows (fetched through the running HELIX API), the same document context,
  and for C2/C3 the same suggested entry (dense rank-1). C3 adds the graph links as text.
* **Same output protocol as humans:** one entity (or `NIL`), one main hint, a confidence level
  (sure / fairly sure / unsure) plus a numeric confidence, a one-sentence rationale, and a
  reflection at the end of each session. The hint list is the human list **minus "Looked it up
  outside the tool"** (the model has no tools); "Own background knowledge" means the model's
  pre-trained knowledge.
* **Deterministic and recorded:** temperature 0, every answer cached, and the model name,
  quantisation, container tag and prompt version noted in the run log for the paper.
* **AI rows are labelled:** `annotator = "llm:<model>"`, `_method = "llm"`.
* **Humans never wait for the model.** Everything shown to humans (modes B and C) is computed in
  advance; human sessions read the cache only (`llm_cache_only`) and fall back to the plain
  candidate list on a miss, so a dropped tunnel cannot stall or slow a session.

### 6.2 Mode A — AI as annotator

For each study session `study2-k-cN` the runner creates `ai-<model>-study2-k-cN` with the same
rows, condition and columns, and fills it in. Compared per condition on the same mentions:
Acc@1 (human vs AI vs retrieval top-1), agreement, where each is right and the other wrong, hint
agreement (Cohen's κ), and accuracy per confidence level for both (calibration).

Caveat to state in the paper: the model's "hint" is a self-report produced alongside its answer,
not a trace of its computation; it is comparable to the human self-report, not a ground truth.

### 6.3 Model bake-off (before any study run)

A **dev set** of ~200 MedMentions mentions from documents not used by any study block or the
debug session, with gold. Each candidate model annotates it in C2; pick the main model by Acc@1,
then speed, then rate of schema failures. Optionally run one frontier API model (e.g. Claude) on
the same dev set as a reference point — MedMentions is public PubMed text, so sending it out is
acceptable, but the main results use the local model.

### 6.4 Mode B — AI as assistant (condition C4)

C4 = C2 plus the LLM's pick, rationale and confidence shown in the existing purple banner, with
the LLM's pick pre-selected and the usual countdown. Recorded per row: `_llm_id` and
`_llm_confidence` (what the human was shown). Analysis: accuracy when the AI was right vs wrong,
**over-reliance** (AI wrong, human followed) and **under-reliance** (AI right, human overrode).
C4 sessions add one hint, **"The AI suggestion and its explanation"**.

Counterbalancing with four conditions: **ABCD-DCBA**, 8 blocks. Keeping 240 mentions means
8 × 30; keeping 40 per block means 320 mentions (+33% annotator time).

### 6.5 Mode C — AI pre-annotates, human reviews

Run on separate, disjoint document blocks after the main study. The LLM annotates every row (C2
view). Routing per row:

* **auto** — the LLM said *sure*, its pick equals retrieval rank-1 and is not NIL → accepted
  without human review;
* **spotcheck** — a fixed random 10 % of auto-eligible rows go to the human **without** showing the
  AI's answer, to estimate the quality of the auto-accepted set honestly;
* **review** — everything else goes to the human **with** the AI's pick shown.

The annotator works only through the queue (spotcheck + review rows). Reported: share
auto-accepted, accuracy of auto-accepted rows (gold) and spot-check agreement, human minutes
spent, and estimated minutes saved (auto rows × median human time per row). The routing rule is
fixed on the dev set, never tuned on study blocks.

## 7. Decisions for Lakshan (defaults used in the plan until changed)

| # | Decision | Default in plan |
|---|---|---|
| D1 | C4 block size: 8 × 30 (same total) or 8 × 40 (+33% time) | 8 × 30 |
| D2 | Add the 10th hint "AI suggestion" for C4 only | Yes, C4 only |
| D3 | Run a frontier API model as a reference on the dev set | Yes, dev set only |
| D4 | Spot-check rate in mode C | 10 % |
| D5 | Main local model | Decided by the bake-off (6.3) |

## 8. Paperwork that must change

* **Ethics protocol:** C4 and mode C show AI output to annotators; describe it, say it runs
  locally, and that no annotator data goes to the model. (Also still pending: the feedback data
  from Sec. 8 of the Annotation Guide.)
* **Annotation Guide:** add C4 and the review queue to the process, the 10th hint, and the new
  time estimates; update the counterbalancing table.

## 9. Timeline (weeks from Mon 19 Oct 2026)

| Week | Infrastructure | Code (plan tasks) | Study |
|---|---|---|---|
| 1 · 19 Oct | Send IT request (day 1); DGX OS update; vLLM container; local smoke test | Tasks 1–3 | — |
| 2 · 26 Oct | Tunnel working end-to-end from laptop | Tasks 4–6 | Bake-off on dev set; choose model |
| 3 · 2 Nov | — | Tasks 7–9 | Mode A runs on all study blocks; ethics amendment submitted |
| 4 · 9 Nov | — | Fixes from practice round | Annotator training + practice round |
| 5–8 · 16 Nov–11 Dec | Keep tunnel/model stable; no model changes | — | Human annotation (C1–C4), then mode C blocks |
| 9–10 · 14–24 Dec | — | Analysis | Results, write-up, submit by 31 Dec |

## 10. Risks

| Risk | Mitigation |
|---|---|
| IT approval is slow | Request on day 1; mode A and the bake-off can run **on the DGX itself** (HELIX API + KB copied there) without the tunnel |
| vLLM image doesn't support a chosen model on GB10 | Bake-off measures it; fall back to the next model on the list |
| Model returns invalid output | Guided decoding prevents it; any failure is logged in `_llm_error`, the row is retried on the next run, and the run continues |
| Tunnel drops during a human session | Human sessions read the cache only; runs are resumable |
| Changing the model mid-study | Freeze model + container tag + prompt version before week 5; cache keys include them |
