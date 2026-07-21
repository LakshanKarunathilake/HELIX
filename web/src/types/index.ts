export interface KBConfig {
  name: string
  endpoint_url: string
  method: 'GET' | 'POST'
  top_k: number
  request_template: string
  response_path: string
  id_field: string
  label_field: string
  score_field: string
  headers: string
  enable_dense: boolean
  enable_lexical: boolean
}

export interface SessionConfig {
  session_name: string
  annotator: string
  input_path: string
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
  llm_api_key_set?: boolean   // read-only flag from GET (key itself is masked)
  enable_graph?: boolean
}

export interface LLMRerank {
  entity_id: string
  rationale: string
  confidence: number
  ranking: string[]
  model: string
  cached?: boolean
  error?: string
}

export interface GraphNode {
  id: string
  name: string
  is_candidate: boolean
  degree?: number
  isolated?: boolean
}

export interface GraphEdge {
  source: string
  target: string
  rel: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  enabled: boolean
  error?: string
}

export interface DocMention {
  idx: number
  label: string
  span: string
  selected: Record<string, string>   // kb name -> selected id
}

export interface DocumentMentions {
  doc_id: string
  kb_names: string[]
  mentions: DocMention[]
}

export interface Candidate {
  id: string
  label: string
  score: number | null
  matched_alias: string
  aliases: string[]
  semantic_groups: string
  semantic_types: string
  definition: string
}

export interface DocProgress {
  doc_id: string
  total: number          // pre-known number of entities in the document
  done: number
  position: number | null
  row_indices: number[]
}

export interface RowData {
  idx: number
  total: number
  label: string
  label_column: string
  info: Record<string, string>
  annotations: Record<string, string>
  context_text?: string | null
  entity_span?: string | null
  doc_progress?: DocProgress | null
}


export interface Progress {
  total: number
  done: number
  resume_idx: number
}

export interface ParsedFile {
  filename: string
  delimiter: string
  columns: string[]
  preview: Record<string, string>[]
  total_rows: number
  tmp_path: string
}

export const DEFAULT_KB: KBConfig = {
  name: '',
  endpoint_url: 'http://127.0.0.1:8765/query',
  method: 'POST',
  top_k: 10,
  request_template: '{"label": "{label}", "top_k": {top_k}}',
  response_path: 'results',
  id_field: 'id',
  label_field: 'label',
  score_field: 'score',
  headers: '{}',
  enable_dense: true,
  enable_lexical: true,
}
