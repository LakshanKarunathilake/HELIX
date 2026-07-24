import type { Candidate, DocumentMentions, GraphData, KBConfig, LLMRerank, ParsedFile, Progress, RowData, SessionConfig } from '@/types'

const BASE = '/api'

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, options)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? body.error ?? `HTTP ${res.status}`)
  }
  return res.json()
}

// ── sessions ────────────────────────────────────────────────────────────────

export const getSessions = () =>
  req<{ sessions: string[] }>('/sessions').then(r => r.sessions)

export const parseFile = (file: File): Promise<ParsedFile> => {
  const fd = new FormData()
  fd.append('file', file)
  return req('/sessions/parse-file', { method: 'POST', body: fd })
}


export const createSession = (body: {
  session_name: string
  annotator: string
  tmp_path: string
  original_filename: string
  delimiter: string
  label_column: string
  info_columns: string[]
  kbs: KBConfig[]
  context_folder?: string | null
  doc_id_col?: string | null
  span_col?: string | null
  auto_select_secs?: number
  score_tie_threshold?: number
  entity_type_col?: string | null
  enable_llm_rerank?: boolean
  llm_provider?: string
  llm_base_url?: string | null
  llm_model?: string
  llm_top_n?: number
  llm_prompt?: string | null
  llm_api_key?: string | null
  enable_graph?: boolean
}) =>
  req<{ session_name: string }>('/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const getSession = (name: string) =>
  req<SessionConfig>(`/sessions/${name}`)

export const updateSession = (name: string, cfg: SessionConfig) =>
  req<{ ok: boolean }>(`/sessions/${name}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })

export const deleteSession = (name: string) =>
  req<{ ok: boolean }>(`/sessions/${name}`, { method: 'DELETE' })

export const autoAnnotate = (name: string) =>
  req<{ annotated: number; skipped: number; total: number }>(
    `/sessions/${name}/auto-annotate`,
    { method: 'POST' },
  )

// ── progress ─────────────────────────────────────────────────────────────────

export const getProgress = (name: string) =>
  req<Progress>(`/sessions/${name}/progress`)

// ── rows ──────────────────────────────────────────────────────────────────────

export const getRow = (name: string, idx: number) =>
  req<RowData>(`/sessions/${name}/rows/${idx}`)

export const saveRow = (
  name: string,
  idx: number,
  annotations: Record<string, string>,
  llm_used?: string | null,
) =>
  req<{ ok: boolean }>(`/sessions/${name}/rows/${idx}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ annotations, llm_used }),
  })

export const saveRowsBatch = (
  name: string,
  updates: { idx: number; annotations: Record<string, string>; llm_used?: string }[],
) =>
  req<{ ok: boolean; written: number }>(`/sessions/${name}/rows/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
  })

// ── candidates ────────────────────────────────────────────────────────────────

export const getCandidates = (
  name: string,
  label: string,
  kb_name: string,
  enable_dense: boolean,
  enable_lexical: boolean,
  context?: string | null,
) =>
  req<{ dense_candidates: Candidate[]; lexical_candidates: Candidate[]; llm: LLMRerank | null }>(
    `/sessions/${name}/candidates`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, kb_name, enable_dense, enable_lexical, context }),
    },
  ).then(r => ({ dense: r.dense_candidates, lexical: r.lexical_candidates, llm: r.llm }))

// ── LLM ───────────────────────────────────────────────────────────────────────

export const getDefaultPrompt = () =>
  req<{ prompt: string }>('/llm/default-prompt').then(r => r.prompt)

// ── graph (Neo4j) ─────────────────────────────────────────────────────────────

export const getCandidateGraph = (name: string, ids: string[]) =>
  req<GraphData>(`/sessions/${name}/graph/candidates`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })

export const getDocumentGraph = (name: string, cuis: string[]) =>
  req<GraphData>(`/sessions/${name}/graph/document`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cuis }),
  })

export const getDocumentMentions = (name: string, idx: number) =>
  req<DocumentMentions>(`/sessions/${name}/documents/mentions?idx=${idx}`)

export const listDocuments = (name: string) =>
  req<{ documents: { doc_id: string; first_idx: number; mentions: number }[] }>(
    `/sessions/${name}/documents`,
  ).then(r => r.documents)

export const optimizeAllDocuments = (name: string, skipAnnotated = true) =>
  req<{
    enabled: boolean
    documents?: number
    mentions_written?: number
    edges_before?: number
    edges_after?: number
    error?: string
  }>(`/sessions/${name}/graph/optimize-all?skip_annotated=${skipAnnotated}`, { method: 'POST' })

export const optimizeDocument = (name: string, idx: number, kb_name: string) =>
  req<{
    selections: Record<string, string>
    edges_before: number
    edges_after: number
    changed: number
    enabled: boolean
    error?: string
  }>(`/sessions/${name}/graph/optimize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ idx, kb_name }),
  })

// ── KB test ───────────────────────────────────────────────────────────────────

export const testKB = (kb_config: KBConfig, label: string) =>
  req<{ ok: boolean; candidates?: Candidate[]; error?: string }>('/kb/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kb_config, label }),
  })

// ── export & per-step snapshots ────────────────────────────────────────────────

export const exportCSV = (name: string, stage?: string) => {
  const q = stage ? `?stage=${encodeURIComponent(stage)}` : ''
  window.open(`${BASE}/sessions/${name}/export${q}`, '_blank')
}

// ── performance metrics ────────────────────────────────────────────────────────

export const getMetricsStatus = () => req<{ enabled: boolean }>('/metrics/status')

export const toggleMetrics = (enabled: boolean) =>
  req<{ enabled: boolean }>('/metrics/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })

export const getMetricsInsights = (name: string) =>
  req<import('@/types').MetricsInsights>(`/sessions/${name}/metrics/insights`)

export const resetMetrics = (name: string) =>
  req<{ ok: boolean }>(`/sessions/${name}/metrics/reset`, { method: 'POST' })

export const getGraphStats = (name: string) =>
  req<import('@/types').GraphStats>(`/sessions/${name}/graph/stats`)

export const resetGraphStats = (name: string) =>
  req<{ ok: boolean }>(`/sessions/${name}/graph/stats/reset`, { method: 'POST' })

export const exportMetrics = (name: string) =>
  window.open(`${BASE}/sessions/${name}/metrics/export`, '_blank')

export const snapshotStage = (name: string, stage: string) =>
  req<{ ok: boolean; stage: string }>(`/sessions/${name}/snapshot?stage=${encodeURIComponent(stage)}`, {
    method: 'POST',
  })

export const listSnapshots = (name: string) =>
  req<{ stages: string[] }>(`/sessions/${name}/snapshots`).then(r => r.stages)
