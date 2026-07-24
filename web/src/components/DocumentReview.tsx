import { useCallback, useEffect, useMemo, useState } from 'react'
import { X, AlertTriangle, CheckCircle2, Loader2, Ban, Zap } from 'lucide-react'
import * as api from '@/api/client'
import { CandidateCard } from './CandidateCard'
import { GraphPanel } from './GraphPanel'
import type { Candidate, DocMention, GraphData, KBConfig } from '@/types'

interface Props {
  sessionName: string
  rowIdx: number          // any row of the document to review
  kb: KBConfig            // the KB whose selections we rearrange
  onClose: () => void
  onChanged?: () => void  // called after any selection change so the parent can refresh
}

type KBCands = { dense: Candidate[]; lexical: Candidate[] }

/**
 * Level-2 document negotiation. Shows every mention of a document, the graph of
 * the currently-selected entities, flags isolated picks, and lets the annotator
 * switch any id — the graph and flags update live so corrections propagate.
 */
export function DocumentReview({ sessionName, rowIdx, kb, onClose, onChanged }: Props) {
  const [docId, setDocId] = useState('')
  const [mentions, setMentions] = useState<DocMention[]>([])
  const [selected, setSelected] = useState<Record<number, string>>({})   // idx -> id
  const [graph, setGraph] = useState<GraphData | null>(null)
  const [activeIdx, setActiveIdx] = useState<number | null>(null)
  const [cands, setCands] = useState<Record<number, KBCands>>({})
  const [loadingCands, setLoadingCands] = useState(false)
  const [loading, setLoading] = useState(true)
  const [showCandidates, setShowCandidates] = useState(false)   // overlay off by default
  const [optimizing, setOptimizing] = useState(false)
  const [optResult, setOptResult] = useState('')

  // ── load the document's mentions + current selections ────────────────────
  useEffect(() => {
    let alive = true
    api.getDocumentMentions(sessionName, rowIdx).then(d => {
      if (!alive) return
      setDocId(d.doc_id)
      setMentions(d.mentions)
      setSelected(Object.fromEntries(d.mentions.map(m => [m.idx, m.selected[kb.name] || ''])))
      setLoading(false)
    })
    return () => { alive = false }
  }, [sessionName, rowIdx, kb.name])

  // ── the CUI sets that make up the graph ──────────────────────────────────
  const selectedCuis = useMemo(
    () => [...new Set(Object.values(selected).filter(v => v && v !== 'NIL'))],
    [selected],
  )
  const selectedSet = useMemo(() => new Set(selectedCuis), [selectedCuis])

  // candidates of the active mention (drives the optional suggestion overlay)
  const activeCandCuis = useMemo<string[]>(() => {
    if (activeIdx == null) return []
    const c = cands[activeIdx]
    if (!c) return []
    return [...new Set([...c.dense, ...c.lexical].map(x => x.id))]
  }, [activeIdx, cands])

  const suggestionCuis = useMemo(
    () => (showCandidates ? activeCandCuis.filter(id => id !== 'NIL' && !selectedSet.has(id)) : []),
    [showCandidates, activeCandCuis, selectedSet],
  )
  const suggestionSet = useMemo(() => new Set(suggestionCuis), [suggestionCuis])

  // graph is built over selected entities + (optionally) the active mention's candidates
  const graphCuis = useMemo(
    () => [...new Set([...selectedCuis, ...suggestionCuis])],
    [selectedCuis, suggestionCuis],
  )
  useEffect(() => {
    if (loading) return
    if (!graphCuis.length) { setGraph({ nodes: [], edges: [], enabled: true }); return }
    api.getDocumentGraph(sessionName, graphCuis).then(setGraph).catch(() => {})
  }, [sessionName, graphCuis, loading])

  // ── isolation is judged among SELECTED entities only (suggestions don't count) ──
  const selectedDegree = useMemo(() => {
    const deg: Record<string, number> = {}
    for (const e of graph?.edges ?? []) {
      if (selectedSet.has(e.source) && selectedSet.has(e.target)) {
        deg[e.source] = (deg[e.source] || 0) + 1
        deg[e.target] = (deg[e.target] || 0) + 1
      }
    }
    return deg
  }, [graph, selectedSet])

  const isolatedIds = useMemo(() => {
    const s = new Set<string>()
    for (const cui of selectedCuis) if (!(selectedDegree[cui] > 0)) s.add(cui)
    return s
  }, [selectedCuis, selectedDegree])

  const statusOf = useCallback((idx: number): 'unset' | 'nil' | 'isolated' | 'ok' => {
    const id = selected[idx]
    if (!id) return 'unset'
    if (id === 'NIL') return 'nil'
    return (selectedDegree[id] ?? 0) > 0 ? 'ok' : 'isolated'
  }, [selected, selectedDegree])

  const isolatedCount = mentions.filter(m => statusOf(m.idx) === 'isolated').length

  // ── switching a selection ────────────────────────────────────────────────
  const openMention = useCallback((idx: number) => {
    setActiveIdx(idx)
    if (cands[idx]) return
    const m = mentions.find(x => x.idx === idx)
    if (!m) return
    setLoadingCands(true)
    api.getCandidates(sessionName, m.label, kb.name, kb.enable_dense ?? true, kb.enable_lexical ?? true, null)
      .then(r => setCands(prev => ({ ...prev, [idx]: { dense: r.dense, lexical: r.lexical } })))
      .finally(() => setLoadingCands(false))
  }, [cands, mentions, sessionName, kb])

  const switchTo = useCallback(async (idx: number, id: string) => {
    setSelected(prev => ({ ...prev, [idx]: id }))
    await api.saveRow(sessionName, idx, { [kb.name]: id }, '')  // manual/graph → not LLM
    onChanged?.()
  }, [sessionName, kb.name, onChanged])

  // ── one-click: maximise graph connections across the document ─────────────
  const autoMatch = useCallback(async () => {
    setOptimizing(true)
    setOptResult('')
    try {
      const r = await api.optimizeDocument(sessionName, rowIdx, kb.name)
      if (r.error) { setOptResult(`Auto-match unavailable: ${r.error}`); return }
      const changes = Object.entries(r.selections).filter(([i, cui]) => selected[+i] !== cui)
      setSelected(prev => ({ ...prev, ...Object.fromEntries(Object.entries(r.selections).map(([i, c]) => [+i, c])) }))
      // ONE atomic write — avoids the concurrent read-modify-write race that
      // previously dropped rows and corrupted the progress count.
      if (changes.length) {
        await api.saveRowsBatch(sessionName, changes.map(([i, cui]) => ({
          idx: +i, annotations: { [kb.name]: cui }, llm_used: '',
        })))
      }
      onChanged?.()
      setOptResult(`Connections ${r.edges_before} → ${r.edges_after} · ${r.changed} selection(s) updated`)
    } catch (e: any) {
      setOptResult(`Auto-match failed: ${e?.message ?? e}`)
    } finally {
      setOptimizing(false)
    }
  }, [sessionName, rowIdx, kb.name, selected, onChanged])

  const active = activeIdx != null ? mentions.find(m => m.idx === activeIdx) : null
  const activeCands = activeIdx != null ? cands[activeIdx] : undefined

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden">
        {/* header */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-200">
          <div>
            <p className="text-sm font-semibold text-gray-900">Document review</p>
            <p className="text-xs text-gray-400 font-mono">{docId}</p>
          </div>
          <div className="ml-4 flex items-center gap-3 text-xs">
            <span className="text-gray-500">{mentions.length} mentions</span>
            {isolatedCount > 0 ? (
              <span className="flex items-center gap-1 text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
                <AlertTriangle className="w-3.5 h-3.5" /> {isolatedCount} isolated
              </span>
            ) : (
              <span className="flex items-center gap-1 text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5">
                <CheckCircle2 className="w-3.5 h-3.5" /> all connected
              </span>
            )}
          </div>
          {optResult && (
            <span className="text-xs text-brand-700 bg-brand-50 border border-brand-200 rounded-full px-2 py-0.5">
              {optResult}
            </span>
          )}
          <button
            onClick={autoMatch}
            disabled={optimizing || loading}
            className="ml-auto btn-primary text-xs py-1.5 disabled:opacity-60"
            title="Automatically pick candidates across the document to maximise graph connections"
          >
            {optimizing
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Optimising…</>
              : <><Zap className="w-3.5 h-3.5" /> Auto-match (maximise connections)</>}
          </button>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
            <X className="w-5 h-5" />
          </button>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center text-gray-400 gap-2">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading document…
          </div>
        ) : (
          <div className="flex-1 flex overflow-hidden">
            {/* left: mention list */}
            <div className="w-[42%] border-r border-gray-200 overflow-y-auto p-3 space-y-1">
              {mentions.map(m => {
                const st = statusOf(m.idx)
                const isActive = m.idx === activeIdx
                return (
                  <button
                    key={m.idx}
                    onClick={() => openMention(m.idx)}
                    className={`w-full text-left rounded-lg px-3 py-2 border transition ${
                      isActive ? 'border-brand-400 bg-brand-50'
                               : 'border-transparent hover:bg-gray-50'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <StatusDot status={st} />
                      <span className="text-sm font-medium text-gray-800 truncate">{m.label}</span>
                      <code className="ml-auto text-[11px] font-mono text-gray-500">
                        {selected[m.idx] || '—'}
                      </code>
                    </div>
                  </button>
                )
              })}
            </div>

            {/* right: graph + candidate switcher */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* candidate-overlay toggle (off by default) */}
              <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showCandidates}
                  onChange={e => setShowCandidates(e.target.checked)}
                />
                <span className="flex items-center gap-1">
                  <span className="inline-block w-2 h-2 rounded-full" style={{ background: '#3b82f6' }} />
                  Show candidate suggestions on the graph
                </span>
                {showCandidates && activeIdx == null && (
                  <span className="text-gray-400">— select a mention to load its candidates</span>
                )}
              </label>

              {graph && (
                <GraphPanel
                  data={graph}
                  selectedId={activeIdx != null ? selected[activeIdx] : undefined}
                  suggestionIds={suggestionSet}
                  isolatedIds={isolatedIds}
                  onSelect={id => {
                    // clicking a candidate suggestion switches the active mention to it
                    if (active && suggestionSet.has(id)) { switchTo(active.idx, id); return }
                    const m = mentions.find(x => selected[x.idx] === id)
                    if (m) openMention(m.idx)
                  }}
                  title="Document graph"
                  width={520}
                  height={360}
                />
              )}

              {active && (
                <div className="card p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold text-gray-900">{active.label}</p>
                    <code className="text-xs font-mono text-gray-500">
                      current: {selected[active.idx] || '—'}
                    </code>
                    <button
                      className="btn-danger text-xs py-1 ml-auto"
                      onClick={() => switchTo(active.idx, 'NIL')}
                    >
                      <Ban className="w-3.5 h-3.5" /> NIL
                    </button>
                  </div>
                  {loadingCands && !activeCands ? (
                    <div className="flex items-center gap-2 text-gray-400 text-sm py-4">
                      <Loader2 className="w-4 h-4 animate-spin" /> Loading candidates…
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {[...(activeCands?.dense ?? []), ...(activeCands?.lexical ?? [])].map((c, j) => (
                        <CandidateCard
                          key={c.id + j}
                          candidate={c}
                          rank={j + 1}
                          selected={selected[active.idx] === c.id}
                          onSelect={id => switchTo(active.idx, id)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
              {!active && (
                <p className="text-xs text-gray-400">
                  Click a mention on the left (or a node in the graph) to review and switch its id.
                  Amber-ringed nodes are isolated — not connected to the rest of the document.
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function StatusDot({ status }: { status: 'unset' | 'nil' | 'isolated' | 'ok' }) {
  const map = {
    ok: { c: 'bg-emerald-500', t: 'connected' },
    isolated: { c: 'bg-amber-500', t: 'isolated' },
    nil: { c: 'bg-gray-400', t: 'NIL' },
    unset: { c: 'bg-gray-200', t: 'not annotated' },
  }[status]
  return <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${map.c}`} title={map.t} />
}
