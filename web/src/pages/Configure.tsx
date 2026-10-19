import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload, Plus, Trash2, ChevronDown, ChevronUp,
  FlaskConical, CheckCircle2, XCircle, Loader2, ArrowRight, Zap,
} from 'lucide-react'
import * as api from '@/api/client'
import type { Candidate, KBConfig, ParsedFile, SessionConfig } from '@/types'
import { DEFAULT_KB } from '@/types'
import { CandidateCard } from '@/components/CandidateCard'
import { AutoAnnotateModal } from '@/components/AutoAnnotateModal'
import { MetricsModal } from '@/components/MetricsModal'
import { GraphStatsModal } from '@/components/GraphStatsModal'
import { Activity, Share2 } from 'lucide-react'

export function Configure() {
  const { sessionName } = useParams<{ sessionName?: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const isEdit = Boolean(sessionName)

  // ── base fields ─────────────────────────────────────────────────────────
  const [name, setName] = useState('')
  const [annotator, setAnnotator] = useState('')
  const [parsed, setParsed] = useState<ParsedFile | null>(null)
  const [labelCol, setLabelCol] = useState('')
  const [infoCols, setInfoCols] = useState<string[]>([])
  const [kbs, setKbs] = useState<KBConfig[]>([{ ...DEFAULT_KB }])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [showAutoModal, setShowAutoModal] = useState(false)
  const [showMetrics, setShowMetrics] = useState(false)
  const [showGraphStats, setShowGraphStats] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  // context folder
  const [contextFolder, setContextFolder] = useState('')
  const [docIdCol, setDocIdCol] = useState('')
  const [spanCol, setSpanCol] = useState('')
  // auto-select
  const [autoSelectSecs, setAutoSelectSecs] = useState(5)
  const [scoreTieThreshold, setScoreTieThreshold] = useState(0.02)
  const [entityTypeCol, setEntityTypeCol] = useState('')
  // LLM re-ranking
  const [enableLlmRerank, setEnableLlmRerank] = useState(false)
  const [llmProvider, setLlmProvider] = useState('anthropic')
  const [llmBaseUrl, setLlmBaseUrl] = useState('')
  const [llmModel, setLlmModel] = useState('claude-haiku-4-5')
  const [llmTopN, setLlmTopN] = useState(10)
  const [llmPrompt, setLlmPrompt] = useState('')
  const [llmApiKey, setLlmApiKey] = useState('')
  const [llmApiKeySet, setLlmApiKeySet] = useState(false)
  const [defaultPrompt, setDefaultPrompt] = useState('')
  // Neo4j graph
  const [enableGraph, setEnableGraph] = useState(false)
  const [collectFeedback, setCollectFeedback] = useState(false)
  const [debugShowGold, setDebugShowGold] = useState(false)

  useEffect(() => {
    api.getDefaultPrompt().then(setDefaultPrompt).catch(() => {})
  }, [])

  // load existing session
  const { data: existingCfg } = useQuery({
    queryKey: ['session', sessionName],
    queryFn: () => api.getSession(sessionName!),
    enabled: isEdit,
  })

  useEffect(() => {
    if (!existingCfg) return
    setName(existingCfg.session_name)
    setAnnotator(existingCfg.annotator)
    setLabelCol(existingCfg.label_column)
    setInfoCols(existingCfg.info_columns)
    setKbs(existingCfg.kbs.length ? existingCfg.kbs : [{ ...DEFAULT_KB }])
    setParsed({
      filename: existingCfg.input_path.split('/').pop() ?? '',
      delimiter: existingCfg.delimiter,
      columns: [],
      preview: [],
      total_rows: 0,
      tmp_path: '',
    })
    if (existingCfg.context_folder) setContextFolder(existingCfg.context_folder)
    if (existingCfg.doc_id_col) setDocIdCol(existingCfg.doc_id_col)
    if (existingCfg.span_col) setSpanCol(existingCfg.span_col)
    if (existingCfg.auto_select_secs != null) setAutoSelectSecs(existingCfg.auto_select_secs)
    if (existingCfg.debug_show_gold != null) setDebugShowGold(existingCfg.debug_show_gold)
    if (existingCfg.collect_feedback != null) setCollectFeedback(existingCfg.collect_feedback)
    if (existingCfg.score_tie_threshold != null) setScoreTieThreshold(existingCfg.score_tie_threshold)
    if (existingCfg.entity_type_col) setEntityTypeCol(existingCfg.entity_type_col)
    if (existingCfg.enable_llm_rerank != null) setEnableLlmRerank(existingCfg.enable_llm_rerank)
    if (existingCfg.llm_provider) setLlmProvider(existingCfg.llm_provider)
    if (existingCfg.llm_base_url) setLlmBaseUrl(existingCfg.llm_base_url)
    if (existingCfg.llm_model) setLlmModel(existingCfg.llm_model)
    if (existingCfg.llm_top_n != null) setLlmTopN(existingCfg.llm_top_n)
    if (existingCfg.llm_prompt) setLlmPrompt(existingCfg.llm_prompt)
    setLlmApiKeySet(!!existingCfg.llm_api_key_set)
    if (existingCfg.enable_graph != null) setEnableGraph(existingCfg.enable_graph)
  }, [existingCfg])

  // ── file upload ──────────────────────────────────────────────────────────
  const [uploading, setUploading] = useState(false)
  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const result = await api.parseFile(file)
      setParsed(result)
      setLabelCol(result.columns[0] ?? '')
      setInfoCols([])
    } catch (err: any) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  // ── KB helpers ───────────────────────────────────────────────────────────
  const updateKB = (i: number, patch: Partial<KBConfig>) =>
    setKbs(prev => prev.map((k, idx) => (idx === i ? { ...k, ...patch } : k)))
  const removeKB = (i: number) => setKbs(prev => prev.filter((_, idx) => idx !== i))
  const addKB = () => setKbs(prev => [...prev, { ...DEFAULT_KB }])

  // ── save ─────────────────────────────────────────────────────────────────
  async function handleSave() {
    setError('')
    if (!name.trim()) { setError('Session name is required'); return }
    if (!annotator.trim()) { setError('Annotator name is required'); return }
    if (!labelCol) { setError('Select an entity-label column'); return }
    if (!kbs.length) { setError('Add at least one KB'); return }
    if (!parsed?.tmp_path && !isEdit) { setError('Upload a CSV/TSV file'); return }

    setSaving(true)
    try {
      if (isEdit && existingCfg) {
        const updated: SessionConfig = {
          ...existingCfg,
          annotator,
          label_column: labelCol,
          info_columns: infoCols,
          kbs,
          context_folder: contextFolder.trim() || null,
          doc_id_col: docIdCol || null,
          span_col: spanCol || null,
          auto_select_secs: autoSelectSecs,
          score_tie_threshold: scoreTieThreshold,
          entity_type_col: entityTypeCol || null,
          enable_llm_rerank: enableLlmRerank,
          llm_provider: llmProvider,
          llm_base_url: llmBaseUrl.trim() || null,
          llm_model: llmModel,
          llm_top_n: llmTopN,
          llm_prompt: llmPrompt.trim() || null,
          llm_api_key: llmApiKey || null,   // blank = keep existing (server-side)
          enable_graph: enableGraph,
          collect_feedback: collectFeedback,
          debug_show_gold: debugShowGold,
        }
        await api.updateSession(sessionName!, updated)
        qc.invalidateQueries({ queryKey: ['session', sessionName] })
      } else {
        await api.createSession({
          session_name: name.trim(),
          annotator: annotator.trim(),
          tmp_path: parsed!.tmp_path,
          original_filename: parsed!.filename,
          delimiter: parsed!.delimiter,
          label_column: labelCol,
          info_columns: infoCols,
          kbs,
          context_folder: contextFolder.trim() || null,
          doc_id_col: docIdCol || null,
          span_col: spanCol || null,
          auto_select_secs: autoSelectSecs,
          score_tie_threshold: scoreTieThreshold,
          entity_type_col: entityTypeCol || null,
          enable_llm_rerank: enableLlmRerank,
          llm_provider: llmProvider,
          llm_base_url: llmBaseUrl.trim() || null,
          llm_model: llmModel,
          llm_top_n: llmTopN,
          llm_prompt: llmPrompt.trim() || null,
          llm_api_key: llmApiKey || null,
          enable_graph: enableGraph,
          collect_feedback: collectFeedback,
          debug_show_gold: debugShowGold,
        })
      }
      navigate(`/annotate/${isEdit ? sessionName : name.trim()}`)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!window.confirm(`Delete session "${sessionName}" and all its data? This cannot be undone.`)) return
    setDeleting(true)
    setError('')
    try {
      await api.deleteSession(sessionName!)
      qc.invalidateQueries({ queryKey: ['sessions'] })
      navigate('/')
    } catch (err: any) {
      setError(err.message)
    } finally {
      setDeleting(false)
    }
  }

  async function handleAutoAnnotate() {
    setShowAutoModal(true)
  }

  const columns = parsed?.columns ?? (existingCfg ? [] : [])

  return (
    <div className="max-w-3xl mx-auto px-6 py-10 space-y-8">
      {showMetrics && <MetricsModal sessionName={sessionName!} onClose={() => setShowMetrics(false)} />}
      {showGraphStats && <GraphStatsModal sessionName={sessionName!} onClose={() => setShowGraphStats(false)} />}
      {showAutoModal && existingCfg && (
        <AutoAnnotateModal
          sessionName={sessionName!}
          cfg={existingCfg}
          onClose={() => setShowAutoModal(false)}
          onComplete={() => {
            qc.invalidateQueries({ queryKey: ['progress', sessionName] })
          }}
        />
      )}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          {isEdit ? `Edit — ${sessionName}` : 'New session'}
        </h1>
        <p className="text-gray-500 mt-1 text-sm">
          Configure your annotation session, column mapping, and knowledge bases.
        </p>
      </div>

      {/* ── section 1: basics ─────────────────────────────────────────── */}
      <Section title="Session details">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Session name</label>
            <input
              className="input"
              placeholder="drugs_2026_05"
              value={name}
              onChange={e => setName(e.target.value)}
              disabled={isEdit}
            />
          </div>
          <div>
            <label className="label">Annotator name</label>
            <input
              className="input"
              placeholder="lakshan"
              value={annotator}
              onChange={e => setAnnotator(e.target.value)}
            />
          </div>
        </div>

        <div className="border-t border-gray-100 pt-4 mt-2">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Auto-select</p>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="label">
                Countdown (seconds)
                <span className="ml-1 text-gray-400 font-normal">0 = disabled</span>
              </label>
              <input
                className="input"
                type="number" min={0} max={60}
                value={autoSelectSecs}
                onChange={e => setAutoSelectSecs(Math.max(0, +e.target.value))}
              />
            </div>
            <div>
              <label className="label">
                Score tie threshold
                <span className="ml-1 text-gray-400 font-normal">Δ within this → use type priority</span>
              </label>
              <input
                className="input"
                type="number" min={0} max={1} step={0.005}
                value={scoreTieThreshold}
                onChange={e => setScoreTieThreshold(Math.max(0, Math.min(1, +e.target.value)))}
              />
            </div>
            <div>
              <label className="label">
                Entity type column
                <span className="ml-1 text-gray-400 font-normal">matched against semantic group</span>
              </label>
              <select
                className="select"
                value={entityTypeCol}
                onChange={e => setEntityTypeCol(e.target.value)}
              >
                <option value="">— none —</option>
                {(columns.length ? columns : existingCfg
                  ? [existingCfg.label_column, ...existingCfg.info_columns]
                  : []
                ).map(c => <option key={c}>{c}</option>)}
                {!columns.length && entityTypeCol && <option>{entityTypeCol}</option>}
              </select>
            </div>
          </div>
        </div>

        <div className="border-t border-gray-100 pt-4 mt-2">
          <label className="flex items-center gap-2 mb-3 cursor-pointer">
            <input
              type="checkbox"
              checked={enableLlmRerank}
              onChange={e => setEnableLlmRerank(e.target.checked)}
            />
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              LLM re-ranking (RAG disambiguation)
            </span>
          </label>
          <p className="text-xs text-gray-400 mb-3">
            After hybrid retrieval, a Claude model picks the best candidate from the retrieved set
            using the mention + document context. Requires <code className="bg-gray-100 rounded px-1">ANTHROPIC_API_KEY</code> on
            the API server. Falls back to retrieval ranking on any error.
          </p>
          <div className={`space-y-4 ${enableLlmRerank ? '' : 'opacity-40 pointer-events-none'}`}>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="label">Provider</label>
                <select
                  className="select"
                  value={llmProvider}
                  onChange={e => {
                    const p = e.target.value
                    setLlmProvider(p)
                    // sensible defaults when switching provider
                    if (p === 'anthropic') { setLlmModel('claude-haiku-4-5'); setLlmBaseUrl('') }
                    else { setLlmModel('gpt-4o-mini') }
                  }}
                >
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="openai">OpenAI-compatible (OpenAI / Gemini / local)</option>
                </select>
              </div>
              <div>
                <label className="label">
                  Model
                  <span className="ml-1 text-gray-400 font-normal">any id the provider accepts</span>
                </label>
                <input
                  className="input font-mono text-sm"
                  list="llm-model-presets"
                  value={llmModel}
                  onChange={e => setLlmModel(e.target.value)}
                  placeholder={llmProvider === 'anthropic' ? 'claude-haiku-4-5' : 'gpt-4o-mini'}
                />
                <datalist id="llm-model-presets">
                  {llmProvider === 'anthropic'
                    ? ['claude-haiku-4-5', 'claude-sonnet-4-6', 'claude-opus-4-8'].map(m => <option key={m} value={m} />)
                    : ['gpt-4o-mini', 'gpt-4o', 'gemini-2.0-flash', 'gemini-2.5-pro'].map(m => <option key={m} value={m} />)}
                </datalist>
              </div>
              <div>
                <label className="label">
                  Candidates
                  <span className="ml-1 text-gray-400 font-normal">top-N</span>
                </label>
                <input
                  className="input"
                  type="number" min={1} max={50}
                  value={llmTopN}
                  onChange={e => setLlmTopN(Math.max(1, Math.min(50, +e.target.value)))}
                />
              </div>
            </div>

            {llmProvider === 'openai' && (
              <div>
                <label className="label">
                  Base URL
                  <span className="ml-1 text-gray-400 font-normal">blank = OpenAI; set for Gemini / local</span>
                </label>
                <input
                  className="input font-mono text-sm"
                  value={llmBaseUrl}
                  onChange={e => setLlmBaseUrl(e.target.value)}
                  placeholder="https://generativelanguage.googleapis.com/v1beta/openai/  (Gemini)"
                />
                <p className="text-xs text-gray-400 mt-1">
                  Uses the OpenAI SDK. Key resolves from the per-session key below, else the
                  server <code className="bg-gray-100 rounded px-1">OPENAI_API_KEY</code> /
                  <code className="bg-gray-100 rounded px-1">GEMINI_API_KEY</code>.
                </p>
              </div>
            )}

            <div>
              <div className="flex items-center justify-between">
                <label className="label mb-0">
                  System prompt
                  <span className="ml-1 text-gray-400 font-normal">blank = built-in default</span>
                </label>
                {llmPrompt.trim() && (
                  <button
                    type="button"
                    className="text-xs text-brand-600 hover:underline"
                    onClick={() => setLlmPrompt('')}
                  >
                    reset to default
                  </button>
                )}
              </div>
              <textarea
                className="input font-mono text-xs leading-relaxed"
                rows={6}
                placeholder={defaultPrompt || 'Loading default prompt…'}
                value={llmPrompt}
                onChange={e => setLlmPrompt(e.target.value)}
              />
              <p className="text-xs text-gray-400 mt-1">
                The mention, document context, and candidate list are appended automatically.
                The model must still choose one candidate id from the retrieved set.
              </p>
            </div>

            <div>
              <label className="label">
                {llmProvider === 'anthropic' ? 'Anthropic' : 'Provider'} API key
                <span className="ml-1 text-gray-400 font-normal">
                  optional — overrides the server env key
                </span>
              </label>
              <input
                className="input font-mono"
                type="password"
                autoComplete="off"
                placeholder={llmApiKeySet ? '•••••••• (a key is stored — leave blank to keep it)'
                                          : (llmProvider === 'anthropic' ? 'sk-ant-… (leave blank to use the server env var)'
                                                                         : 'sk-… / AIza… (leave blank to use the server env var)')}
                value={llmApiKey}
                onChange={e => setLlmApiKey(e.target.value)}
              />
              <p className="text-xs text-amber-600 mt-1">
                ⚠ A key entered here is saved in the session config file on disk in plain text.
                Prefer the server ANTHROPIC_API_KEY env var unless you need per-session keys.
              </p>
            </div>
          </div>
        </div>

        <div className="border-t border-gray-100 pt-4 mt-2">
          <label className="flex items-center gap-2 mb-2 cursor-pointer">
            <input
              type="checkbox"
              checked={enableGraph}
              onChange={e => setEnableGraph(e.target.checked)}
            />
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              Graph neighborhood (Neo4j)
            </span>
          </label>
          <p className="text-xs text-gray-400">
            Show how a mention's candidates connect to each other and their neighbors, pulled from your
            Neo4j graph. Connection + schema are read from{' '}
            <code className="bg-gray-100 rounded px-1">neo4j.yaml</code> on the server
            (copy <code className="bg-gray-100 rounded px-1">neo4j.example.yaml</code>); env vars are a fallback.
            Default schema: <code className="bg-gray-100 rounded px-1">(:Concept {'{'}cui{'}'})-[:RELATES_TO {'{'}rel{'}'}]-(:Concept)</code>.
          </p>
        </div>

        <div className="border-t border-gray-100 pt-4 mt-2">
          <label className="flex items-center gap-2 mb-2 cursor-pointer">
            <input
              type="checkbox"
              checked={collectFeedback}
              onChange={e => setCollectFeedback(e.target.checked)}
            />
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              Feedback on each decision
            </span>
          </label>
          <p className="text-xs text-gray-400">
            After every save the annotator picks the main hint that decided it and a confidence
            level (two clicks, not counted in decision time). The panels opened on each row are
            logged, and a reflection — hints ranked by usefulness — is collected per session.
            Adds <code className="bg-gray-100 rounded px-1">_panels</code>,{' '}
            <code className="bg-gray-100 rounded px-1">_hint</code> and{' '}
            <code className="bg-gray-100 rounded px-1">_confidence</code> to the output.
          </p>
        </div>

        {/* ── DEBUG: gold answer ─────────────────────────────────────────── */}
        <div className="border-t-2 border-red-200 pt-4 mt-2 bg-red-50 -mx-5 px-5 pb-4 rounded-b-lg">
          <label className="flex items-center gap-2 mb-2 cursor-pointer">
            <input
              type="checkbox"
              checked={debugShowGold}
              onChange={e => setDebugShowGold(e.target.checked)}
            />
            <span className="text-xs font-bold text-red-700 uppercase tracking-wide">
              🛑 Debug — show the gold answer
            </span>
          </label>
          <p className="text-xs text-red-600">
            Displays the correct CUI in the annotator so retrieval failures can be inspected.
            Read from <code className="bg-red-100 rounded px-1">eval/out/study_gold.csv</code>.
          </p>
          <p className="text-xs text-red-700 font-semibold mt-1">
            Every row saved while this is on is stamped <code className="bg-red-100 rounded px-1">_debug_gold_shown</code>{' '}
            and excluded from study results. Turning it off later does <u>not</u> clean rows already saved —
            those sessions must be cleared and re-annotated.
          </p>
        </div>
      </Section>

      {/* ── section 2: file ──────────────────────────────────────────── */}
      <Section title="Input file">
        {!isEdit && (
          <>
            <input ref={fileRef} type="file" accept=".csv,.tsv,.txt" className="hidden" onChange={handleFile} />
            <button
              className="btn-secondary w-full py-8 border-dashed flex-col gap-2"
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
            >
              {uploading
                ? <><Loader2 className="w-5 h-5 animate-spin" /> Parsing…</>
                : <><Upload className="w-5 h-5" /> Click to upload CSV or TSV</>}
            </button>
          </>
        )}
        {parsed && (
          <div className="mt-3 text-sm text-gray-600 space-y-1">
            <p>
              <span className="font-medium">{parsed.filename}</span>
              {parsed.total_rows > 0 && (
                <span className="text-gray-400"> · {parsed.total_rows} rows · delimiter{' '}
                  <code className="bg-gray-100 px-1 rounded">{parsed.delimiter === '\t' ? '\\t' : parsed.delimiter}</code>
                </span>
              )}
            </p>
            {parsed.preview.length > 0 && (
              <div className="overflow-x-auto rounded-lg border border-gray-200 mt-3">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50">
                    <tr>
                      {parsed.columns.map(c => (
                        <th key={c} className="px-3 py-2 text-left font-medium text-gray-600">{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {parsed.preview.map((row, i) => (
                      <tr key={i} className="border-t border-gray-100">
                        {parsed.columns.map(c => (
                          <td key={c} className="px-3 py-1.5 text-gray-700 max-w-[180px] truncate">{row[c]}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Section>

      {/* ── section 3: column mapping ─────────────────────────────────── */}
      {(columns.length > 0 || isEdit) && (
        <Section title="Column mapping">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Entity-label column</label>
              <select className="select" value={labelCol} onChange={e => setLabelCol(e.target.value)}>
                {columns.map(c => <option key={c}>{c}</option>)}
                {isEdit && !columns.length && <option>{labelCol}</option>}
              </select>
            </div>
          </div>
          <div className="mt-4">
            <label className="label">Additional info columns (context shown during annotation)</label>
            <div className="flex flex-wrap gap-2 mt-1">
              {(columns.length ? columns : infoCols).filter(c => c !== labelCol).map(c => (
                <label key={c} className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    className="rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                    checked={infoCols.includes(c)}
                    onChange={e =>
                      setInfoCols(prev =>
                        e.target.checked ? [...prev, c] : prev.filter(x => x !== c)
                      )
                    }
                  />
                  <span className="text-sm text-gray-700">{c}</span>
                </label>
              ))}
            </div>
          </div>
        </Section>
      )}

      {/* ── section 3b: context folder ────────────────────────────────── */}
      <Section title="Context folder (optional)">
        <p className="text-sm text-gray-500">
          Folder of plain-text files named by document ID (e.g. <code className="bg-gray-100 px-1 rounded">25823274_en.txt</code>).
          The annotator will show the document passage with the entity highlighted.
        </p>
        <div>
          <label className="label">Folder path (absolute, on this machine)</label>
          <input
            className="input font-mono text-sm"
            placeholder="/path/to/texts"
            value={contextFolder}
            onChange={e => setContextFolder(e.target.value)}
          />
        </div>
        {contextFolder.trim() && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">
                Document ID column in your data
                <span className="ml-1 text-gray-400 font-normal">(matches filename stem)</span>
              </label>
              <select className="select" value={docIdCol} onChange={e => setDocIdCol(e.target.value)}>
                <option value="">— none —</option>
                {(columns.length ? columns : existingCfg
                  ? [existingCfg.label_column, ...existingCfg.info_columns]
                  : []
                ).map(c => <option key={c}>{c}</option>)}
                {!columns.length && docIdCol && <option>{docIdCol}</option>}
              </select>
            </div>
            <div>
              <label className="label">
                Span column in your data
                <span className="ml-1 text-gray-400 font-normal">(char offsets e.g. "4-11", optional)</span>
              </label>
              <select className="select" value={spanCol} onChange={e => setSpanCol(e.target.value)}>
                <option value="">— none —</option>
                {(columns.length ? columns : existingCfg
                  ? [existingCfg.label_column, ...existingCfg.info_columns]
                  : []
                ).map(c => <option key={c}>{c}</option>)}
                {!columns.length && spanCol && <option>{spanCol}</option>}
              </select>
            </div>
          </div>
        )}
      </Section>

      {/* ── section 4: KBs ────────────────────────────────────────────── */}
      <Section
        title="Knowledge bases"
        action={
          <button className="btn-secondary text-xs py-1" onClick={addKB}>
            <Plus className="w-3.5 h-3.5" /> Add KB
          </button>
        }
      >
        {kbs.map((kb, i) => (
          <KBForm
            key={i}
            kb={kb}
            index={i}
            sampleLabel={labelCol}
            onChange={patch => updateKB(i, patch)}
            onRemove={() => removeKB(i)}
          />
        ))}
        {kbs.length === 0 && (
          <p className="text-sm text-gray-400 text-center py-4">No KBs added yet.</p>
        )}
      </Section>

      {/* ── error + save ──────────────────────────────────────────────── */}
      {error && (
        <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          <XCircle className="w-4 h-4 flex-shrink-0" /> {error}
        </div>
      )}
      <div className="flex justify-between pb-10">
        {isEdit ? (
          <div className="flex gap-2">
            <button
              className="btn-secondary px-6 py-2.5 text-red-600 border-red-200 hover:bg-red-50"
              onClick={handleDelete}
              disabled={deleting || saving}
            >
              {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
              Delete session
            </button>
            <button
              className="btn-secondary px-6 py-2.5"
              onClick={handleAutoAnnotate}
              disabled={deleting || saving}
              title="Two steps: retrieval auto-select, then graph match (maximise connections)"
            >
              <Zap className="w-4 h-4" />
              Auto-annotate all
            </button>
            <button
              className="btn-secondary px-6 py-2.5"
              onClick={() => setShowMetrics(true)}
              disabled={deleting || saving}
              title="Enable latency + memory tracking and view performance insights"
            >
              <Activity className="w-4 h-4" />
              Performance
            </button>
            {enableGraph && (
              <button
                className="btn-secondary px-6 py-2.5"
                onClick={() => setShowGraphStats(true)}
                disabled={deleting || saving}
                title="Neo4j query cost and nodes tackled by graph connection-maximisation"
              >
                <Share2 className="w-4 h-4" />
                Graph stats
              </button>
            )}
          </div>
        ) : <span />}
        <button className="btn-primary px-8 py-2.5" onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
          {isEdit ? 'Save & annotate' : 'Create & annotate'}
        </button>
      </div>
    </div>
  )
}

// ── Section wrapper ──────────────────────────────────────────────────────────

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="card p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-gray-900">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  )
}

// ── KB form ──────────────────────────────────────────────────────────────────

function KBForm({
  kb, index, sampleLabel, onChange, onRemove,
}: {
  kb: KBConfig; index: number; sampleLabel: string
  onChange: (p: Partial<KBConfig>) => void; onRemove: () => void
}) {
  const [open, setOpen] = useState(index === 0)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string; cands?: Candidate[] } | null>(null)

  async function handleTest() {
    setTesting(true); setTestResult(null)
    try {
      const r = await api.testKB(kb, sampleLabel || 'test')
      console.log('test result:', r)
      setTestResult(r.ok
        ? { ok: true, msg: `${r.candidates?.length ?? 0} candidates returned`, cands: r.candidates }
        : { ok: false, msg: r.error ?? 'Unknown error' })
    } catch (e: any) {
      setTestResult({ ok: false, msg: e.message })
    } finally { setTesting(false) }
  }

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden">
      {/* header */}
      <div
        className="flex items-center gap-3 px-4 py-3 bg-gray-50 cursor-pointer hover:bg-gray-100 transition-colors"
        onClick={() => setOpen(v => !v)}
      >
        <span className="text-xs font-bold text-gray-400 uppercase w-5 text-center">#{index + 1}</span>
        <span className="flex-1 font-medium text-gray-800 text-sm">{kb.name || <span className="text-gray-400 italic">unnamed KB</span>}</span>
        <span className="text-xs text-gray-400 font-mono truncate max-w-[200px]">{kb.endpoint_url}</span>
        <button
          onClick={e => { e.stopPropagation(); onRemove() }}
          className="p-1 text-gray-400 hover:text-red-500 transition-colors"
        >
          <Trash2 className="w-4 h-4" />
        </button>
        {open ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
      </div>

      {open && (
        <div className="p-4 space-y-3 bg-white">
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="label">Name (column header)</label>
              <input className="input" value={kb.name} onChange={e => onChange({ name: e.target.value })} placeholder="UMLS" />
            </div>
            <div className="col-span-2">
              <label className="label">Endpoint URL</label>
              <input className="input" value={kb.endpoint_url} onChange={e => onChange({ endpoint_url: e.target.value })} />
            </div>
          </div>

          <div className="grid grid-cols-4 gap-3">
            <div>
              <label className="label">Method</label>
              <select className="select" value={kb.method} onChange={e => onChange({ method: e.target.value as 'GET' | 'POST' })}>
                <option>POST</option><option>GET</option>
              </select>
            </div>
            <div>
              <label className="label">top_k</label>
              <input className="input" type="number" min={1} max={100} value={kb.top_k} onChange={e => onChange({ top_k: +e.target.value })} />
            </div>
            <div>
              <label className="label">response_path</label>
              <input className="input" value={kb.response_path} onChange={e => onChange({ response_path: e.target.value })} placeholder="results" />
            </div>
            <div>
              <label className="label">headers (JSON)</label>
              <input className="input" value={kb.headers} onChange={e => onChange({ headers: e.target.value })} placeholder="{}" />
            </div>
          </div>

          <div>
            <label className="label">request_template</label>
            <input className="input font-mono text-xs" value={kb.request_template} onChange={e => onChange({ request_template: e.target.value })} />
          </div>

          <div className="grid grid-cols-3 gap-3">
            {(['id_field', 'label_field', 'score_field'] as const).map(f => (
              <div key={f}>
                <label className="label">{f}</label>
                <input className="input" value={kb[f]} onChange={e => onChange({ [f]: e.target.value })} />
              </div>
            ))}
          </div>

          {/* search strategy toggles */}
          <div className="flex items-center gap-6 py-1">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Search strategies</p>
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                className="w-4 h-4 rounded accent-brand-500"
                checked={kb.enable_dense ?? true}
                onChange={e => onChange({ enable_dense: e.target.checked })}
              />
              <span className="text-sm text-gray-700">Dense (FAISS)</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                className="w-4 h-4 rounded accent-brand-500"
                checked={kb.enable_lexical ?? false}
                onChange={e => onChange({ enable_lexical: e.target.checked })}
              />
              <span className="text-sm text-gray-700">Lexical (BM25 + n-gram)</span>
            </label>
          </div>

          {/* test */}
          <div className="flex items-center gap-3 pt-1">
            <button className="btn-secondary text-xs" onClick={handleTest} disabled={testing}>
              {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FlaskConical className="w-3.5 h-3.5" />}
              Test KB
            </button>
            {testResult && (
              <span className={`flex items-center gap-1.5 text-xs font-medium ${testResult.ok ? 'text-emerald-600' : 'text-red-600'}`}>
                {testResult.ok ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                {testResult.msg}
              </span>
            )}
          </div>

          {testResult?.ok && testResult.cands && testResult.cands.length > 0 && (
            <div className="space-y-2 pt-1">
              {testResult.cands.slice(0, 3).map((c, i) => (
                <CandidateCard
                  key={c.id}
                  candidate={c}
                  rank={i + 1}
                  selected={false}
                  onSelect={() => {}}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
