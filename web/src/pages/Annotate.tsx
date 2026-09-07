import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronLeft, ChevronRight, Save, Download, Ban,
  X, AlertCircle, CheckCircle2, Loader2, PartyPopper,
} from 'lucide-react'
import { Sparkles } from 'lucide-react'
import * as api from '@/api/client'
import { CandidateCard } from '@/components/CandidateCard'
import { CountdownTimer } from '@/components/CountdownTimer'
import { FloatingGraphPanel } from '@/components/FloatingGraphPanel'
import { DocumentReview } from '@/components/DocumentReview'
import { Network } from 'lucide-react'
import type { Candidate, GraphData, LLMRerank, SessionConfig } from '@/types'

const DEFAULT_AUTO_SELECT_SECS = 5

// A mention can map to several correct CUIs — selections are stored pipe-joined
// (e.g. "C0024109|C0024117"). "NIL" and "" are single-valued sentinels.
const parseIds = (v?: string): string[] =>
  v && v !== 'NIL' ? v.split('|').map(s => s.trim()).filter(Boolean) : []

// "provider/model" when the accepted selection includes the LLM's pick (for any KB),
// else "" (the LLM did not produce the final annotation — e.g. a manual override).
function computeLlmUsed(
  selections: Record<string, string>,
  candidates: Record<string, { llm: LLMRerank | null }>,
  cfg?: SessionConfig,
): string {
  if (!cfg?.enable_llm_rerank) return ''
  for (const kb of cfg.kbs) {
    const llm = candidates[kb.name]?.llm
    if (llm && !llm.error && llm.entity_id && parseIds(selections[kb.name]).includes(llm.entity_id)) {
      return `${llm.provider ?? cfg.llm_provider ?? 'anthropic'}/${llm.model}`
    }
  }
  return ''
}

export function Annotate() {
  const { sessionName } = useParams<{ sessionName: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  // ── session config ─────────────────────────────────────────────────────
  const { data: cfg, isLoading: cfgLoading } = useQuery({
    queryKey: ['session', sessionName],
    queryFn: () => api.getSession(sessionName!),
    enabled: !!sessionName,
  })

  // ── progress ───────────────────────────────────────────────────────────
  const { data: progress, isLoading: progressLoading } = useQuery({
    queryKey: ['progress', sessionName],
    queryFn: () => api.getProgress(sessionName!),
    enabled: !!sessionName,
  })

  // ── row navigation ─────────────────────────────────────────────────────
  const [rowIdx, setRowIdx] = useState<number>(0)
  const initialJumpDone = useRef(false)

  useEffect(() => {
    // Only jump to resume_idx once on mount — never again, even if progress
    // refetches (e.g. after a save), so manual Prev navigation to row 0 is safe.
    if (initialJumpDone.current || !progress) return
    initialJumpDone.current = true
    setRowIdx(Math.min(progress.resume_idx, progress.total - 1))
  }, [progress])

  const total = progress?.total ?? 0
  const done  = progress?.done  ?? 0
  const pct   = total > 0 ? (done / total) * 100 : 0
  const pctLabel = total > 0
    ? pct < 1 ? `${done} annotated · <1%` : `${done} annotated · ${Math.round(pct)}%`
    : '0 annotated'

  // ── current row ────────────────────────────────────────────────────────
  const { data: rowData, isLoading: rowLoading } = useQuery({
    queryKey: ['row', sessionName, rowIdx],
    queryFn: () => api.getRow(sessionName!, rowIdx),
    enabled: !!sessionName && total > 0,
  })

  type KBCandidates = { dense: Candidate[]; lexical: Candidate[]; llm: LLMRerank | null }

  // ── per-row state (resets on row change) ───────────────────────────────
  const [selections, setSelections] = useState<Record<string, string>>({})
  const [savedAnnotations, setSavedAnnotations] = useState<Record<string, string>>({})
  const [candidates, setCandidates] = useState<Record<string, KBCandidates>>({})
  const [graphs, setGraphs] = useState<Record<string, GraphData>>({})
  const [loadingKBs, setLoadingKBs] = useState<Record<string, boolean>>({})
  const [kbErrors, setKbErrors] = useState<Record<string, string>>({})
  const [timerActive, setTimerActive] = useState(false)
  const [userInteracted, setUserInteracted] = useState(false)
  const [manualIds, setManualIds] = useState<Record<string, string>>({})
  const [warning, setWarning] = useState('')
  const [activeTab, setActiveTab] = useState(0)
  const [showDocReview, setShowDocReview] = useState(false)
  const prevRowIdx = useRef<number>(-1)
  // ── pilot study (Sec. 4.6) ─────────────────────────────────────────────
  // Wall-clock on the current row, and whether the id was typed by hand.
  // Both are reported raw; the server classifies and stamps the condition.
  const rowStartRef = useRef<number>(performance.now())
  const manualEntryRef = useRef(false)

  // Reset when row changes
  useEffect(() => {
    if (prevRowIdx.current === rowIdx) return
    prevRowIdx.current = rowIdx
    setSelections({})
    setSavedAnnotations({})
    setCandidates({})
    setKbErrors({})
    setTimerActive(false)
    setUserInteracted(false)
    setManualIds({})
    setWarning('')
    rowStartRef.current = performance.now()   // pilot study: time on this row
    manualEntryRef.current = false
  }, [rowIdx])

  // Populate selections from saved annotations when row data arrives
  useEffect(() => {
    if (!rowData || !cfg) return
    const hasAnnotations = Object.values(rowData.annotations).some(v => v)
    if (hasAnnotations) {
      setSelections(rowData.annotations)
      setSavedAnnotations(rowData.annotations)
      setUserInteracted(true)
    }
  }, [rowData?.idx])

  // Clear candidates and errors when the row changes, then fetch fresh results
  useEffect(() => {
    if (!rowData || !cfg) return
    setCandidates({})
    setGraphs({})
    setKbErrors({})
    cfg.kbs.forEach(kb => {
      setLoadingKBs(prev => ({ ...prev, [kb.name]: true }))
      api.getCandidates(
        sessionName!, rowData.label, kb.name,
        kb.enable_dense ?? true,
        kb.enable_lexical ?? false,
        rowData.context_text ?? null,
      )
        .then(result => {
          setCandidates(prev => ({ ...prev, [kb.name]: result }))
          setLoadingKBs(prev => ({ ...prev, [kb.name]: false }))
          // Individual-level graph: how this mention's candidates connect.
          if (cfg.enable_graph) {
            const ids = [...new Set([...result.dense, ...result.lexical].map(c => c.id))]
            if (ids.length) {
              api.getCandidateGraph(sessionName!, ids)
                .then(g => setGraphs(prev => ({ ...prev, [kb.name]: g })))
                .catch(() => {})
            }
          }
        })
        .catch(err => {
          setKbErrors(prev => ({ ...prev, [kb.name]: err.message }))
          setLoadingKBs(prev => ({ ...prev, [kb.name]: false }))
        })
    })
  }, [rowData?.idx, cfg])

  // Pick best candidate: sort by score, then within tie-threshold prefer entity_type match
  const pickBest = useCallback((
    dense: Candidate[], lexical: Candidate[],
    entityType: string, threshold: number,
  ): Candidate | null => {
    const all = [...dense, ...lexical]
    if (!all.length) return null
    // Deduplicate by id, keeping highest score
    const byId = new Map<string, Candidate>()
    for (const c of all) {
      const prev = byId.get(c.id)
      if (!prev || (c.score ?? 0) > (prev.score ?? 0)) byId.set(c.id, c)
    }
    const pool = [...byId.values()].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    const topScore = pool[0].score ?? 0
    // Candidates within threshold of the top score
    const tied = pool.filter(c => topScore - (c.score ?? 0) <= threshold)
    if (entityType) {
      const typeMatch = tied.find(c =>
        c.semantic_groups?.split(/[|,]/).map(s => s.trim()).includes(entityType)
      )
      if (typeMatch) return typeMatch
    }
    return pool[0]
  }, [])

  // Auto-select once per row, after candidates arrive
  useEffect(() => {
    if (userInteracted || !cfg) return
    if (cfg.enable_auto_select === false) return   // C1: no suggestion at all
    let changed = false
    const newSel = { ...selections }
    const threshold = cfg.score_tie_threshold ?? 0.02
    const entityTypeCol = cfg.entity_type_col ?? null
    cfg.kbs.forEach(kb => {
      const result = candidates[kb.name]
      if (!result || newSel[kb.name]) return
      // Prefer the LLM's grounded pick when re-ranking is enabled and succeeded.
      if (result.llm?.entity_id && !result.llm.error) {
        newSel[kb.name] = result.llm.entity_id; changed = true; return
      }
      const entityType = entityTypeCol ? (rowData?.info[entityTypeCol] ?? '') : ''
      const best = pickBest(result.dense ?? [], result.lexical ?? [], entityType, threshold)
      if (best) { newSel[kb.name] = best.id; changed = true }
    })
    if (changed) { setSelections(newSel); setTimerActive(true) }
  }, [candidates, userInteracted])

  // Stable refs so the auto-advance timeout always sees current values
  const rowIdxRef = useRef(rowIdx)
  rowIdxRef.current = rowIdx
  const selectionsRef = useRef(selections)
  selectionsRef.current = selections
  const candidatesRef = useRef(candidates)
  candidatesRef.current = candidates
  const totalRef = useRef(total)
  totalRef.current = total

  // Pilot-study signals for one save. `top1_id` is the dense rank-1 candidate,
  // matching the Acc@1 definition used throughout the paper, so a `user_override`
  // is exactly a mention the annotator moved off retrieval's first choice.
  const provenanceFor = useCallback((
    sels: Record<string, string>,
    cands: Record<string, KBCandidates>,
    interacted: boolean,
  ): api.RowProvenance => {
    const kb = cfg?.kbs[0]?.name
    const dense = kb ? (cands[kb]?.dense ?? []) : []
    const chosen = parseIds(kb ? sels[kb] : '')[0] ?? ''
    const rank = dense.findIndex(c => c.id === chosen)
    return {
      elapsed_ms: Math.round(performance.now() - rowStartRef.current),
      selected_rank: rank >= 0 ? rank + 1 : null,
      top1_id: dense[0]?.id ?? '',
      user_interacted: interacted,
      manual_entry: manualEntryRef.current,
    }
  }, [cfg])

  // Auto-advance: when the countdown fires (and the user hasn't interacted),
  // save the auto-selected annotations and move to the next row.
  // A single setTimeout keyed on `timerActive` — no double-fire, no stale closure.
  useEffect(() => {
    if (!timerActive || userInteracted || !cfg || !sessionName) return
    const secs = cfg.auto_select_secs ?? DEFAULT_AUTO_SELECT_SECS
    if (secs === 0) return
    const t = setTimeout(async () => {
      const idx  = rowIdxRef.current
      const sels = selectionsRef.current
      if (Object.keys(sels).length === 0) return
      setTimerActive(false)
      try {
        await api.saveRow(sessionName, idx, sels, computeLlmUsed(sels, candidatesRef.current, cfg),
          provenanceFor(sels, candidatesRef.current, false))   // countdown expired untouched
        qc.invalidateQueries({ queryKey: ['progress', sessionName] })
        qc.invalidateQueries({ queryKey: ['row', sessionName, idx] })
        if (idx < totalRef.current - 1) setRowIdx(idx + 1)
        else setWarning('🎉 All rows annotated!')
      } catch (_) { /* silent */ }
    }, secs * 1000)
    return () => clearTimeout(t)
  }, [timerActive, userInteracted])

  // ── actions ────────────────────────────────────────────────────────────
  const kbNames = cfg?.kbs.map(k => k.name) ?? []
  const allFilled = kbNames.length > 0 && kbNames.every(kb => selections[kb])

  // Toggle a candidate in/out of the (possibly multi-valued) selection for a KB.
  // NIL replaces everything; picking a real id clears a prior NIL.
  const select = useCallback((kb: string, id: string) => {
    setSelections(prev => {
      if (id === 'NIL') return { ...prev, [kb]: 'NIL' }
      const cur = parseIds(prev[kb])
      const next = cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id]
      const joined = next.join('|')
      const n = { ...prev }
      if (joined) n[kb] = joined
      else delete n[kb]
      return n
    })
    setUserInteracted(true)
    setTimerActive(false)
    setWarning('')
  }, [])

  const markNIL = (kb: string) => {
    select(kb, 'NIL')
  }

  const clearSel = (kb: string) => {
    setSelections(prev => { const n = { ...prev }; delete n[kb]; return n })
    setUserInteracted(true)
    setTimerActive(false)
  }

  const saveRow = async () => {
    if (!sessionName) return
    await api.saveRow(sessionName, rowIdx, selections, computeLlmUsed(selections, candidates, cfg),
      provenanceFor(selections, candidates, userInteracted))
    qc.invalidateQueries({ queryKey: ['progress', sessionName] })
    qc.invalidateQueries({ queryKey: ['row', sessionName, rowIdx] })
  }

  const goNext = async () => {
    if (!allFilled) {
      const pending = kbNames.filter(kb => !selections[kb])
      setWarning(`Select an option for: ${pending.join(', ')}`)
      return
    }
    if (rowIdx >= total - 1) {
      setWarning('🎉 All rows annotated!')
      return
    }
    await saveRow()
    setRowIdx(r => r + 1)
  }

  const goPrev = async () => {
    if (rowIdx <= 0) return
    if (allFilled) await saveRow()
    setRowIdx(r => r - 1)
  }

  // keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (['INPUT', 'TEXTAREA'].includes((e.target as HTMLElement)?.tagName)) return
      if (e.key === 'ArrowRight' || e.key === 'n') goNext()
      if (e.key === 'ArrowLeft'  || e.key === 'p') goPrev()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [allFilled, rowIdx, selections])

  // ── render guards ──────────────────────────────────────────────────────
  if (!cfgLoading && !cfg) return <Err msg="Session not found" />

  return (
    <div className="relative flex flex-col h-[calc(100vh-56px)]">

      {/* ── sticky top bar ──────────────────────────────────────────────── */}
      <div className="bg-white border-b border-gray-200 px-6 py-3 flex-shrink-0">
        <div className="max-w-4xl mx-auto">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-4">
              {progressLoading || cfgLoading ? (
                <>
                  <div className="h-4 w-24 bg-gray-200 rounded animate-pulse" />
                  <div className="h-4 w-32 bg-gray-200 rounded animate-pulse" />
                  <div className="h-5 w-20 bg-gray-100 rounded animate-pulse" />
                </>
              ) : (
                <>
                  <span className="text-sm font-semibold text-gray-900">
                    Row {rowIdx + 1} <span className="text-gray-400 font-normal">/ {total}</span>
                  </span>
                  <span className="text-sm text-gray-500">{pctLabel}</span>
                  <span className="text-xs text-gray-400 bg-gray-100 rounded px-2 py-0.5">
                    {cfg!.annotator}
                  </span>
                </>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400">Jump to</span>
              <input
                type="number"
                min={1}
                max={total}
                value={rowIdx + 1}
                onChange={e => setRowIdx(Math.max(0, Math.min(total - 1, +e.target.value - 1)))}
                className="w-16 input text-center py-1 text-xs"
              />
              {cfg?.enable_graph && cfg?.doc_id_col && rowData?.doc_progress && (
                <button
                  className="btn-secondary text-xs py-1"
                  onClick={() => setShowDocReview(true)}
                  title="Review & rearrange this document's selections on the graph"
                >
                  <Network className="w-3.5 h-3.5" /> Review document
                </button>
              )}
              <button className="btn-secondary text-xs py-1" onClick={() => api.exportCSV(sessionName!)}>
                <Download className="w-3.5 h-3.5" /> CSV
              </button>
            </div>
          </div>
          {/* progress bar */}
          <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-brand-500 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      </div>

      {/* ── two-column body ──────────────────────────────────────────────── */}
      <div className="flex-1 overflow-hidden flex gap-0">
        {(cfgLoading || progressLoading) && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/60 z-10 pointer-events-none">
            <Loader2 className="w-8 h-8 animate-spin text-brand-500" />
          </div>
        )}

        {/* ── LEFT: entity + context (sticky, scrolls independently) ─────── */}
        <div className="w-[42%] border-r border-gray-200 overflow-y-auto">
          <div className="px-5 py-5 space-y-4">

            {/* entity label card */}
            <div className="card p-4">
              {rowLoading ? (
                <div className="h-8 bg-gray-100 rounded animate-pulse w-1/2" />
              ) : (
                <>
                  <p className="text-2xl font-bold text-gray-900">{rowData?.label}</p>
                  <p className="text-xs text-gray-400 mt-1">
                    label column: <code className="bg-gray-100 rounded px-1">{rowData?.label_column}</code>
                  </p>
                  {rowData && Object.keys(rowData.info).length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-3">
                      {Object.entries(rowData.info).map(([k, v]) => (
                        <span key={k} className="text-xs bg-gray-100 text-gray-600 rounded-full px-3 py-1">
                          <span className="font-medium">{k}:</span> {v}
                        </span>
                      ))}
                    </div>
                  )}
                  {rowData?.doc_progress && rowData.doc_progress.total > 0 && (() => {
                    const dp = rowData.doc_progress!
                    const dpct = Math.round((dp.done / dp.total) * 100)
                    const complete = dp.done >= dp.total
                    return (
                      <div className="mt-3 pt-3 border-t border-gray-100">
                        <div className="flex items-center justify-between mb-1.5">
                          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
                            Document
                            {dp.position != null && (
                              <span className="ml-1 text-gray-400 font-normal normal-case">
                                · entity {dp.position} of {dp.total}
                              </span>
                            )}
                          </p>
                          <span className={`text-xs font-semibold tabular-nums ${complete ? 'text-emerald-600' : 'text-gray-500'}`}>
                            {dp.done} / {dp.total} annotated{complete ? ' ✓' : ''}
                          </span>
                        </div>
                        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all duration-500 ${complete ? 'bg-emerald-500' : 'bg-brand-500'}`}
                            style={{ width: `${dpct}%` }}
                          />
                        </div>
                        <p className="text-[11px] text-gray-400 mt-1 font-mono">{dp.doc_id}</p>
                      </div>
                    )
                  })()}
                  {Object.keys(savedAnnotations).length > 0 && (
                    <div className="mt-3 pt-3 border-t border-gray-100">
                      <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Last annotation</p>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(savedAnnotations).map(([kb, id]) => id ? (
                          <span key={kb} className="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded-full px-3 py-1">
                            <span className="font-medium">{kb}:</span> <code className="font-mono">{id}</code>
                          </span>
                        ) : null)}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* context passage */}
            {!rowLoading && rowData?.context_text && (
              <ContextCard
                text={rowData.context_text}
                label={rowData.label}
                span={rowData.entity_span ?? null}
              />
            )}
          </div>
        </div>

        {/* ── RIGHT: KB candidates (scrolls independently) ────────────────── */}
        <div className="flex-1 overflow-y-auto">
          <div className="px-5 py-5 space-y-4">

            {/* KB tabs */}
            {cfg && cfg.kbs.length > 1 && (
              <div className="flex gap-1 border-b border-gray-200">
                {cfg.kbs.map((kb, i) => (
                  <button
                    key={kb.name}
                    onClick={() => setActiveTab(i)}
                    className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                      activeTab === i
                        ? 'border-brand-500 text-brand-700'
                        : 'border-transparent text-gray-500 hover:text-gray-700'
                    }`}
                  >
                    {kb.name}
                    {selections[kb.name] && (
                      <CheckCircle2 className="w-3.5 h-3.5 inline ml-1.5 text-emerald-500" />
                    )}
                  </button>
                ))}
              </div>
            )}

            {/* countdown timer — single instance, outside KB loop */}
            {cfg && (
            <CountdownTimer
              key={rowIdx}
              seconds={cfg.auto_select_secs ?? DEFAULT_AUTO_SELECT_SECS}
              active={timerActive && !userInteracted}
              onDismiss={() => { setTimerActive(false); setUserInteracted(true) }}
            />
            )}

            {/* KB panel */}
            {cfg && cfg.kbs.map((kb, i) => (
              <div key={kb.name} className={cfg.kbs.length > 1 && i !== activeTab ? 'hidden' : 'space-y-3'}>
                {/* selection status + actions */}
                <div className="flex items-center gap-3">
                  {parseIds(selections[kb.name]).length > 0 ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="flex items-center gap-1 text-sm font-medium text-emerald-700">
                        <CheckCircle2 className="w-4 h-4" /> Selected:
                      </span>
                      {parseIds(selections[kb.name]).map(id => (
                        <span key={id} className="inline-flex items-center gap-1 text-xs bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-full pl-2 pr-1 py-0.5">
                          <code className="font-mono">{id}</code>
                          <button
                            onClick={() => select(kb.name, id)}
                            className="hover:text-emerald-900"
                            title="Remove"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  ) : selections[kb.name] === 'NIL' ? (
                    <span className="flex items-center gap-1 text-sm font-medium text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-1.5">
                      <Ban className="w-4 h-4" /> NIL (no match)
                    </span>
                  ) : (
                    <span className="text-sm text-gray-400 italic">No selection yet — click one or more candidates</span>
                  )}
                  <div className="ml-auto flex items-center gap-2">
                    <button className="btn-danger text-xs py-1.5" onClick={() => markNIL(kb.name)}>
                      <Ban className="w-3.5 h-3.5" /> NIL
                    </button>
                    <button
                      className="btn-secondary text-xs py-1.5"
                      onClick={() => clearSel(kb.name)}
                      disabled={!selections[kb.name]}
                    >
                      <X className="w-3.5 h-3.5" /> Clear
                    </button>
                  </div>
                </div>

                {/* LLM re-rank pick */}
                {candidates[kb.name]?.llm && (
                  <LLMPickBanner llm={candidates[kb.name]!.llm!} />
                )}

                {/* candidates */}
                {loadingKBs[kb.name] && (
                  <div className="flex items-center justify-center py-12 text-gray-400 gap-2">
                    <Loader2 className="w-5 h-5 animate-spin" /> Fetching candidates…
                  </div>
                )}
                {kbErrors[kb.name] && (
                  <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    <span>Query failed: {kbErrors[kb.name]}</span>
                    <button
                      className="ml-auto text-xs underline"
                      onClick={() => {
                        setKbErrors(prev => { const n = { ...prev }; delete n[kb.name]; return n })
                        setCandidates(prev => { const n = { ...prev }; delete n[kb.name]; return n })
                      }}
                    >
                      retry
                    </button>
                  </div>
                )}
                {!loadingKBs[kb.name] && !kbErrors[kb.name] && candidates[kb.name] && (() => {
                  const result = candidates[kb.name]
                  const denseCands = result?.dense ?? []
                  const lexicalCands = result?.lexical ?? []
                  const showBoth = denseCands.length > 0 && lexicalCands.length > 0
                  const renderList = (list: Candidate[], listLabel: string, labelClass: string) => (
                    <div>
                      {showBoth && (
                        <p className={`text-xs font-semibold uppercase tracking-wide mb-2 ${labelClass}`}>{listLabel}</p>
                      )}
                      <div className="space-y-2">
                        {list.map((c, j) => (
                          <CandidateCard
                            key={c.id + j}
                            candidate={c}
                            rank={j + 1}
                            selected={parseIds(selections[kb.name]).includes(c.id)}
                            onSelect={id => select(kb.name, id)}
                          />
                        ))}
                      </div>
                    </div>
                  )
                  return (
                    <div className="space-y-5">
                      {denseCands.length > 0 && renderList(denseCands, 'Dense search (FAISS)', 'text-brand-600')}
                      {lexicalCands.length > 0 && renderList(lexicalCands, 'Lexical search (BM25 + n-gram)', 'text-violet-600')}
                    </div>
                  )
                })()}

                {/* manual id */}
                <div className="flex gap-2 pt-1">
                  <input
                    className="input text-sm flex-1"
                    placeholder="Or enter a KB id manually…"
                    value={manualIds[kb.name] ?? ''}
                    onChange={e => setManualIds(prev => ({ ...prev, [kb.name]: e.target.value }))}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && manualIds[kb.name]?.trim()) {
                        manualEntryRef.current = true
                        select(kb.name, manualIds[kb.name].trim())
                        setManualIds(prev => ({ ...prev, [kb.name]: '' }))
                      }
                    }}
                  />
                  <button
                    className="btn-secondary text-sm"
                    disabled={!manualIds[kb.name]?.trim()}
                    onClick={() => {
                      if (manualIds[kb.name]?.trim()) {
                        manualEntryRef.current = true
                        select(kb.name, manualIds[kb.name].trim())
                        setManualIds(prev => ({ ...prev, [kb.name]: '' }))
                      }
                    }}
                  >
                    Use
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── sticky footer ────────────────────────────────────────────────── */}
      <div className="bg-white border-t border-gray-200 px-6 py-3 flex-shrink-0">
        <div className="flex items-center gap-3">

          {/* warning */}
          {warning && (
            <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 flex-1">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {warning}
            </div>
          )}
          {!warning && (
            <div className="flex-1 text-xs text-gray-400">
              Tip: <kbd className="bg-gray-100 rounded px-1">→</kbd> Next &nbsp;
              <kbd className="bg-gray-100 rounded px-1">←</kbd> Prev
            </div>
          )}

          <div className="flex items-center gap-2 flex-shrink-0">
            <button className="btn-secondary" onClick={goPrev} disabled={rowIdx <= 0}>
              <ChevronLeft className="w-4 h-4" /> Prev
            </button>
            <button className="btn-secondary" onClick={goNext} disabled={!allFilled}>
              <Save className="w-4 h-4" /> Save
            </button>
            <button className="btn-primary" onClick={goNext}>
              {rowIdx >= total - 1 ? (
                <><PartyPopper className="w-4 h-4" /> Finish</>
              ) : (
                <>Next <ChevronRight className="w-4 h-4" /></>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* floating, collapsible candidate graph (active KB) */}
      {(() => {
        const activeKb = cfg?.kbs?.[cfg.kbs.length > 1 ? activeTab : 0]
        if (!activeKb) return null
        return (
          <FloatingGraphPanel
            data={graphs[activeKb.name]}
            selectedId={parseIds(selections[activeKb.name])[0]}
            onSelect={id => select(activeKb.name, id)}
            title="Candidate graph (Neo4j)"
          />
        )
      })()}

      {/* Level-2 document negotiation */}
      {showDocReview && cfg && (() => {
        const activeKb = cfg.kbs?.[cfg.kbs.length > 1 ? activeTab : 0]
        if (!activeKb) return null
        return (
          <DocumentReview
            sessionName={sessionName!}
            rowIdx={rowIdx}
            kb={activeKb}
            onClose={() => setShowDocReview(false)}
            onChanged={() => {
              qc.invalidateQueries({ queryKey: ['progress', sessionName] })
              qc.invalidateQueries({ queryKey: ['row', sessionName, rowIdx] })
            }}
          />
        )
      })()}
    </div>
  )
}

function Loading() {
  return (
    <div className="flex items-center justify-center h-64 text-gray-400 gap-2">
      <Loader2 className="w-5 h-5 animate-spin" /> Loading…
    </div>
  )
}

function Err({ msg }: { msg: string }) {
  return (
    <div className="flex items-center justify-center h-64 text-red-500 gap-2">
      <AlertCircle className="w-5 h-5" /> {msg}
    </div>
  )
}

// ── LLMPickBanner ─────────────────────────────────────────────────────────────

function LLMPickBanner({ llm }: { llm: LLMRerank }) {
  if (llm.error) {
    return (
      <div className="flex items-center gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
        <Sparkles className="w-3.5 h-3.5 flex-shrink-0" />
        LLM re-rank unavailable — using retrieval ranking. <span className="text-amber-500">({llm.error})</span>
      </div>
    )
  }
  const pct = Math.round((llm.confidence ?? 0) * 100)
  return (
    <div className="bg-violet-50 border border-violet-200 rounded-lg px-3 py-2.5 space-y-1">
      <div className="flex items-center gap-2 text-sm text-violet-800 font-medium">
        <Sparkles className="w-4 h-4 flex-shrink-0" />
        LLM pick: <code className="font-mono text-xs bg-violet-100 rounded px-1">{llm.entity_id}</code>
        <span className="ml-auto text-xs font-semibold text-violet-600 tabular-nums">{pct}% confident</span>
      </div>
      {llm.rationale && <p className="text-xs text-violet-700 leading-snug">{llm.rationale}</p>}
      <p className="text-[10px] text-violet-400">
        {llm.model}{llm.cached ? ' · cached' : ''}
      </p>
    </div>
  )
}

// ── ContextCard ───────────────────────────────────────────────────────────────

function highlightSpan(text: string, label: string, span: string | null) {
  // Try char-offset span "start-end" first, fall back to substring search
  let start = -1, end = -1
  if (span) {
    const parts = span.split('-').map(Number)
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      start = parts[0]; end = parts[1]
    }
  }
  if ((start < 0 || end <= start) && label) {
    const idx = text.toLowerCase().indexOf(label.toLowerCase())
    if (idx >= 0) { start = idx; end = idx + label.length }
  }
  if (start < 0 || end <= start) return [{ text, highlight: false }]
  return [
    { text: text.slice(0, start), highlight: false },
    { text: text.slice(start, end), highlight: true },
    { text: text.slice(end), highlight: false },
  ].filter(s => s.text)
}

function ContextCard({ text, label, span }: { text: string; label: string; span: string | null }) {
  const segments = highlightSpan(text, label, span)
  return (
    <div className="card p-4 space-y-2">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Document context</p>
      <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap break-words">
        {segments.map((seg, i) =>
          seg.highlight
            ? <mark key={i} className="bg-yellow-200 text-yellow-900 rounded px-0.5">{seg.text}</mark>
            : <span key={i}>{seg.text}</span>
        )}
      </p>
      {span && <p className="text-xs text-gray-400">span: {span}</p>}
    </div>
  )
}
