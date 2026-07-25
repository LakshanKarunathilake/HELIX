import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Play, Pause, Square, CheckCircle2, Loader2, Search, Share2, Download, Activity } from 'lucide-react'
import * as api from '@/api/client'
import type { Candidate, MetricsInsights, SessionConfig } from '@/types'

interface Props {
  sessionName: string
  cfg: SessionConfig
  onClose: () => void
  onComplete: () => void
}

type Status = 'idle' | 'running' | 'paused' | 'done' | 'stopped'

const OP_LABEL: Record<string, string> = {
  candidates: 'Candidate request', 'retrieval.dense': 'Dense retrieval',
  'retrieval.lexical': 'Lexical retrieval', 'rerank.llm': 'LLM re-ranking',
  'graph.optimize': 'Graph optimise', 'write.batch': 'Batch write',
}

function pickBest(dense: Candidate[], lexical: Candidate[], entityType: string, threshold: number): Candidate | null {
  const all = [...dense, ...lexical]
  if (!all.length) return null
  const byId = new Map<string, Candidate>()
  for (const c of all) {
    const prev = byId.get(c.id)
    if (!prev || (c.score ?? 0) > (prev.score ?? 0)) byId.set(c.id, c)
  }
  const pool = [...byId.values()].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
  const topScore = pool[0].score ?? 0
  const tied = pool.filter(c => topScore - (c.score ?? 0) <= threshold)
  if (entityType) {
    const match = tied.find(c => c.semantic_groups?.split(/[|,]/).map(s => s.trim()).includes(entityType))
    if (match) return match
  }
  return pool[0]
}

export function AutoAnnotateModal({ sessionName, cfg, onClose, onComplete }: Props) {
  const graphStep = !!(cfg.enable_graph && cfg.doc_id_col && cfg.kbs.length)

  // current saved progress of the session (so the modal reflects real status)
  const [progress, setProgress] = useState<{ total: number; done: number } | null>(null)
  const pct = progress && progress.total ? Math.round((progress.done / progress.total) * 100) : 0

  const [active, setActive] = useState<'none' | 's1' | 's2'>('none')
  const [currentLabel, setCurrentLabel] = useState('')
  const [snaps, setSnaps] = useState({ retrieval: false, graph: false })

  // step 1 (retrieval)
  const [s1, setS1] = useState({ status: 'idle' as Status, current: 0, total: 0, annotated: 0, skipped: 0, errors: 0 })
  // step 2 (graph match)
  const [s2, setS2] = useState({ status: 'idle' as Status, docCurrent: 0, docTotal: 0, refined: 0, before: 0, after: 0 })

  const [log, setLog] = useState<string[]>([])
  const logEndRef = useRef<HTMLDivElement>(null)
  const addLog = useCallback((m: string) => setLog(prev => [...prev.slice(-99), m]), [])
  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [log])

  // performance tracking
  const [perf, setPerf] = useState(false)
  const [insights, setInsights] = useState<MetricsInsights | null>(null)

  const shouldStop = useRef(false)
  const isPaused = useRef(false)
  const pauseResolve = useRef<(() => void) | null>(null)

  const refreshProgress = useCallback(async () => {
    try { setProgress(await api.getProgress(sessionName)) } catch { /* ignore */ }
    onComplete()
  }, [sessionName, onComplete])

  useEffect(() => {
    refreshProgress()
    api.listSnapshots(sessionName).then(st => setSnaps({ retrieval: st.includes('retrieval'), graph: st.includes('graph') })).catch(() => {})
    api.getMetricsStatus().then(s => setPerf(s.enabled)).catch(() => {})
  }, [sessionName, refreshProgress])

  const waitIfPaused = useCallback(async () => {
    if (isPaused.current) await new Promise<void>(res => { pauseResolve.current = res })
  }, [])
  const loadInsights = useCallback(async () => {
    if (!perf) return
    try { setInsights(await api.getMetricsInsights(sessionName)) } catch { /* ignore */ }
  }, [perf, sessionName])
  const togglePerf = async () => { try { const r = await api.toggleMetrics(!perf); setPerf(r.enabled) } catch { /* ignore */ } }

  // ── step 1: retrieval ────────────────────────────────────────────────────
  const runStep1 = useCallback(async () => {
    if (active !== 'none') return
    setActive('s1'); shouldStop.current = false; isPaused.current = false
    setInsights(null); setCurrentLabel('')
    if (perf) { try { await api.resetMetrics(sessionName) } catch { /* ignore */ } }
    const prog = await api.getProgress(sessionName)
    setS1({ status: 'running', current: 0, total: prog.total, annotated: 0, skipped: 0, errors: 0 })
    const threshold = cfg.score_tie_threshold ?? 0.02
    const etCol = cfg.entity_type_col ?? null
    let ann = 0, skip = 0, err = 0
    for (let idx = 0; idx < prog.total; idx++) {
      if (shouldStop.current) break
      await waitIfPaused()
      if (shouldStop.current) break
      setS1(s => ({ ...s, current: idx + 1 }))
      let row: Awaited<ReturnType<typeof api.getRow>>
      try { row = await api.getRow(sessionName, idx) } catch { setS1(s => ({ ...s, errors: ++err })); continue }
      if (cfg.kbs.every(kb => row.annotations[kb.name]?.trim())) { setS1(s => ({ ...s, skipped: ++skip })); continue }
      setCurrentLabel(row.label)
      const et = etCol ? (row.info[etCol] ?? '') : ''
      const newAnn: Record<string, string> = { ...row.annotations }
      let any = false
      for (const kb of cfg.kbs) {
        if (newAnn[kb.name]?.trim()) continue
        try {
          const r = await api.getCandidates(sessionName, row.label, kb.name, kb.enable_dense ?? true, kb.enable_lexical ?? false, row.context_text ?? null)
          const best = pickBest(r.dense ?? [], r.lexical ?? [], et, threshold)
          if (best) { newAnn[kb.name] = best.id; any = true }
        } catch { setS1(s => ({ ...s, errors: ++err })) }
      }
      if (any) {
        try { await api.saveRow(sessionName, idx, newAnn); setS1(s => ({ ...s, annotated: ++ann })) }
        catch { setS1(s => ({ ...s, errors: ++err })) }
      }
    }
    const stopped = shouldStop.current
    try { await api.snapshotStage(sessionName, 'retrieval'); setSnaps(s => ({ ...s, retrieval: true })) } catch { /* ignore */ }
    await refreshProgress(); await loadInsights()
    addLog(`Step 1 ${stopped ? 'stopped' : 'complete'} — ${ann} annotated, ${skip} already done.`)
    setS1(s => ({ ...s, status: stopped ? 'stopped' : 'done' }))
    setActive('none')
  }, [active, cfg, sessionName, perf, waitIfPaused, refreshProgress, loadInsights, addLog])

  // ── step 2: graph match ──────────────────────────────────────────────────
  const runStep2 = useCallback(async () => {
    if (active !== 'none' || !graphStep) return
    setActive('s2'); shouldStop.current = false; isPaused.current = false
    setInsights(null); setCurrentLabel('')
    if (perf) { try { await api.resetMetrics(sessionName) } catch { /* ignore */ } }
    const kb = cfg.kbs[0]
    const docs = await api.listDocuments(sessionName)
    setS2({ status: 'running', docCurrent: 0, docTotal: docs.length, refined: 0, before: 0, after: 0 })
    let eb = 0, ea = 0, ref = 0
    for (let d = 0; d < docs.length; d++) {
      if (shouldStop.current) break
      await waitIfPaused()
      if (shouldStop.current) break
      setS2(s => ({ ...s, docCurrent: d + 1 }))
      setCurrentLabel(docs[d].doc_id)
      try {
        const r = await api.optimizeDocument(sessionName, docs[d].first_idx, kb.name)
        if (r.error) { addLog(`Doc ${docs[d].doc_id}: ✗ ${r.error}`); continue }
        const changes = Object.entries(r.selections)
        if (changes.length) {
          await api.saveRowsBatch(sessionName, changes.map(([i, cui]) => ({ idx: +i, annotations: { [kb.name]: cui }, llm_used: '' })))
        }
        eb += r.edges_before; ea += r.edges_after; ref += r.changed
        setS2(s => ({ ...s, before: eb, after: ea, refined: ref }))
      } catch (e: any) { addLog(`Doc ${docs[d].doc_id}: ✗ ${e?.message ?? 'failed'}`) }
    }
    const stopped = shouldStop.current
    try { await api.snapshotStage(sessionName, 'graph'); setSnaps(s => ({ ...s, graph: true })) } catch { /* ignore */ }
    await refreshProgress(); await loadInsights()
    addLog(`Step 2 ${stopped ? 'stopped' : 'complete'} — ${ref} refined; connections ${eb}→${ea}.`)
    setS2(s => ({ ...s, status: stopped ? 'stopped' : 'done' }))
    setActive('none')
  }, [active, graphStep, cfg, sessionName, perf, waitIfPaused, refreshProgress, loadInsights, addLog])

  // ── controls (apply to the active step) ──────────────────────────────────
  const pause = () => { isPaused.current = true; setStepStatus('paused') }
  const resume = () => { isPaused.current = false; pauseResolve.current?.(); pauseResolve.current = null; setStepStatus('running') }
  const stop = () => { shouldStop.current = true; if (isPaused.current) { isPaused.current = false; pauseResolve.current?.(); pauseResolve.current = null } }
  function setStepStatus(st: Status) {
    if (active === 's1') setS1(s => ({ ...s, status: st }))
    else if (active === 's2') setS2(s => ({ ...s, status: st }))
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[600px] flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 flex-shrink-0">
          <div>
            <h2 className="font-semibold text-gray-900">Auto-annotate</h2>
            <p className="text-xs text-gray-400 mt-0.5">{sessionName}</p>
          </div>
          <button onClick={onClose} disabled={active !== 'none'} className="text-gray-400 hover:text-gray-600 disabled:opacity-30">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-4 flex-1 overflow-y-auto">
          {/* current saved status of the session */}
          <div className="bg-gray-50 border border-gray-100 rounded-xl px-4 py-3">
            <div className="flex items-center justify-between text-sm mb-1.5">
              <span className="font-medium text-gray-800">Current progress</span>
              <span className="text-gray-500">{progress ? `${progress.done} / ${progress.total} annotated` : '…'} · {pct}%</span>
            </div>
            <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
              <div className="h-full bg-emerald-500 rounded-full transition-all" style={{ width: `${pct}%` }} />
            </div>
          </div>

          {/* perf toggle */}
          <label className="flex items-center justify-between text-xs">
            <span className="flex items-center gap-1.5 text-gray-500"><Activity className="w-3.5 h-3.5 text-brand-500" /> Track latency &amp; memory</span>
            <button onClick={togglePerf} disabled={active !== 'none'} className={`relative w-9 h-5 rounded-full ${perf ? 'bg-emerald-500' : 'bg-gray-300'} disabled:opacity-50`}>
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${perf ? 'translate-x-4' : ''}`} />
            </button>
          </label>

          {/* ── Step 1 card ── */}
          <StepCard
            n={1} icon={<Search className="w-4 h-4" />} title="Retrieval" color="brand"
            status={s1.status}
            progressText={`${s1.current} / ${s1.total} rows`}
            pctVal={s1.total ? Math.round((s1.current / s1.total) * 100) : 0}
            stats={[['Annotated', s1.annotated], ['Already done', s1.skipped], ['Errors', s1.errors]]}
            canRun={active === 'none'}
            isActive={active === 's1'}
            onRun={runStep1} onPause={pause} onResume={resume} onStop={stop}
            hasSnapshot={snaps.retrieval}
            onExport={() => api.exportCSV(sessionName, 'retrieval')}
            currentLabel={active === 's1' ? currentLabel : ''}
          />

          {/* ── Step 2 card ── */}
          {graphStep ? (
            <StepCard
              n={2} icon={<Share2 className="w-4 h-4" />} title="Graph match" color="violet"
              status={s2.status}
              progressText={`${s2.docCurrent} / ${s2.docTotal} docs`}
              pctVal={s2.docTotal ? Math.round((s2.docCurrent / s2.docTotal) * 100) : 0}
              stats={[['Refined', s2.refined], ['Connections', `${s2.before}→${s2.after}`]]}
              canRun={active === 'none'}
              isActive={active === 's2'}
              onRun={runStep2} onPause={pause} onResume={resume} onStop={stop}
              hasSnapshot={snaps.graph}
              onExport={() => api.exportCSV(sessionName, 'graph')}
              currentLabel={active === 's2' ? `Document: ${currentLabel}` : ''}
            />
          ) : (
            <p className="text-[11px] text-gray-400">Step 2 (graph match) needs the Neo4j graph and a document-id column enabled.</p>
          )}

          {/* insights */}
          {insights && insights.samples > 0 && (
            <div className="border border-gray-100 rounded-xl px-4 py-3">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Performance ({insights.throughput.mentions_per_s}/s · {insights.memory.peak_mb}MB peak)</p>
              {insights.operations.slice(0, 5).map(o => (
                <div key={o.op} className="flex justify-between text-[11px] text-gray-500">
                  <span>{OP_LABEL[o.op] ?? o.op}</span>
                  <span>mean {o.mean_ms}ms · p95 {o.p95_ms}ms · {Math.round(o.share * 100)}%</span>
                </div>
              ))}
            </div>
          )}

          {log.length > 0 && (
            <div className="bg-gray-950 rounded-xl p-3 text-[11px] font-mono text-gray-300 max-h-28 overflow-y-auto space-y-0.5">
              {log.map((l, i) => <p key={i} className={l.includes('✗') ? 'text-red-400' : ''}>{l}</p>)}
              <div ref={logEndRef} />
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-gray-100 flex justify-between flex-shrink-0">
          <button onClick={onClose} disabled={active !== 'none'} className="btn-secondary text-sm disabled:opacity-40">Close</button>
          <button className="btn-secondary text-sm" onClick={() => api.exportCSV(sessionName)} title="Export the current annotations">
            <Download className="w-4 h-4" /> Export final
          </button>
        </div>
      </div>
    </div>
  )
}

function StepCard(p: {
  n: number; icon: React.ReactNode; title: string; color: 'brand' | 'violet'
  status: Status; progressText: string; pctVal: number
  stats: [string, number | string][]
  canRun: boolean; isActive: boolean
  onRun: () => void; onPause: () => void; onResume: () => void; onStop: () => void
  hasSnapshot: boolean; onExport: () => void; currentLabel: string
}) {
  const bar = p.color === 'violet' ? 'bg-violet-500' : 'bg-brand-500'
  const done = p.status === 'done' || p.status === 'stopped'
  return (
    <div className={`border rounded-xl px-4 py-3 ${p.isActive ? 'border-gray-300 bg-gray-50/60' : 'border-gray-100'}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`w-6 h-6 rounded-full flex items-center justify-center text-white ${bar}`}>{p.icon}</span>
        <span className="text-sm font-semibold text-gray-800">Step {p.n} · {p.title}</span>
        <StatusBadge status={p.status} />
        <div className="ml-auto flex items-center gap-1.5">
          {p.status === 'running' && <>
            <IconBtn onClick={p.onPause} label="Pause"><Pause className="w-3.5 h-3.5" /></IconBtn>
            <IconBtn onClick={p.onStop} label="Stop" danger><Square className="w-3.5 h-3.5" /></IconBtn>
          </>}
          {p.status === 'paused' && <>
            <IconBtn onClick={p.onResume} label="Resume"><Play className="w-3.5 h-3.5" /></IconBtn>
            <IconBtn onClick={p.onStop} label="Stop" danger><Square className="w-3.5 h-3.5" /></IconBtn>
          </>}
          {(p.status === 'idle' || done) && (
            <button className="btn-primary text-xs py-1" onClick={p.onRun} disabled={!p.canRun}>
              <Play className="w-3.5 h-3.5" /> {done ? 'Re-run' : 'Run'}
            </button>
          )}
          <button className="btn-secondary text-xs py-1 disabled:opacity-40" onClick={p.onExport} disabled={!p.hasSnapshot} title={p.hasSnapshot ? 'Export this step’s output' : 'Run this step first'}>
            <Download className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      {(p.status !== 'idle') && (
        <>
          <div className="flex justify-between text-[11px] text-gray-500 mb-1">
            <span>{p.progressText}</span><span>{p.pctVal}%</span>
          </div>
          <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div className={`h-full ${bar} rounded-full transition-all`} style={{ width: `${p.pctVal}%` }} />
          </div>
          <div className="flex gap-4 mt-2 text-[11px] text-gray-500">
            {p.stats.map(([k, v]) => <span key={k}>{k}: <b className="text-gray-700">{v}</b></span>)}
          </div>
          {p.currentLabel && <p className="text-[11px] text-gray-400 mt-1 truncate">{p.currentLabel}</p>}
        </>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: Status }) {
  const map: Record<Status, string> = {
    idle: 'text-gray-400 bg-gray-100', running: 'text-brand-700 bg-brand-50',
    paused: 'text-amber-700 bg-amber-50', done: 'text-emerald-700 bg-emerald-50', stopped: 'text-red-600 bg-red-50',
  }
  const label: Record<Status, React.ReactNode> = {
    idle: 'idle', running: <><Loader2 className="w-3 h-3 animate-spin inline" /> running</>,
    paused: 'paused', done: <><CheckCircle2 className="w-3 h-3 inline" /> done</>, stopped: 'stopped',
  }
  return <span className={`text-[10px] font-medium rounded-full px-2 py-0.5 ${map[status]}`}>{label[status]}</span>
}

function IconBtn({ children, onClick, label, danger }: { children: React.ReactNode; onClick: () => void; label: string; danger?: boolean }) {
  return (
    <button onClick={onClick} title={label}
      className={`p-1.5 rounded-lg border ${danger ? 'text-red-600 border-red-200 hover:bg-red-50' : 'text-gray-500 border-gray-200 hover:bg-gray-50'}`}>
      {children}
    </button>
  )
}
