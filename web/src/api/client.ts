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

export const saveRow = (name: string, idx: number, annotations: Record<string, string>) =>
  req<{ ok: boolean }>(`/sessions/${name}/rows/${idx}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ annotations }),
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

// ── KB test ───────────────────────────────────────────────────────────────────

export const testKB = (kb_config: KBConfig, label: string) =>
  req<{ ok: boolean; candidates?: Candidate[]; error?: string }>('/kb/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kb_config, label }),
  })

// ── export ────────────────────────────────────────────────────────────────────

export const exportCSV = (name: string) => {
  window.open(`${BASE}/sessions/${name}/export`, '_blank')
}
