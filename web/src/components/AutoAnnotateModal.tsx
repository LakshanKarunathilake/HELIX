import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Play, Pause, Square, CheckCircle2, Loader2 } from 'lucide-react'
import * as api from '@/api/client'
import type { Candidate, SessionConfig } from '@/types'

interface Props {
  sessionName: string
  cfg: SessionConfig
  onClose: () => void
  onComplete: () => void
}

type Status = 'idle' | 'running' | 'paused' | 'done' | 'stopped'

function pickBest(
  dense: Candidate[],
  lexical: Candidate[],
  entityType: string,
  threshold: number,
): Candidate | null {
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
    const match = tied.find(c =>
      c.semantic_groups?.split(/[|,]/).map(s => s.trim()).includes(entityType),
    )
    if (match) return match
  }
  return pool[0]
}

export function AutoAnnotateModal({ sessionName, cfg, onClose, onComplete }: Props) {
  const [status, setStatus] = useState<Status>('idle')
  const [total, setTotal] = useState(0)
  const [current, setCurrent] = useState(0)
  const [annotated, setAnnotated] = useState(0)
  const [skipped, setSkipped] = useState(0)
  const [errors, setErrors] = useState(0)
  const [currentLabel, setCurrentLabel] = useState('')
  const [log, setLog] = useState<string[]>([])
  const logEndRef = useRef<HTMLDivElement>(null)

  const shouldStop = useRef(false)
  const isPaused = useRef(false)
  const pauseResolve = useRef<(() => void) | null>(null)

  // Fetch total rows on mount
  useEffect(() => {
    api.getProgress(sessionName).then(p => setTotal(p.total)).catch(() => {})
  }, [sessionName])

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [log])

  const addLog = useCallback((msg: string) => {
    setLog(prev => [...prev.slice(-99), msg])
  }, [])

  const run = useCallback(async () => {
    shouldStop.current = false
    isPaused.current = false
    setStatus('running')
    setAnnotated(0)
    setSkipped(0)
    setErrors(0)
    setCurrent(0)
    setCurrentLabel('')
    setLog([])

    const progress = await api.getProgress(sessionName)
    const rowTotal = progress.total
    setTotal(rowTotal)

    const threshold = cfg.score_tie_threshold ?? 0.02
    const entityTypeCol = cfg.entity_type_col ?? null

    let annotatedCount = 0
    let skippedCount = 0
    let errorCount = 0

    for (let idx = 0; idx < rowTotal; idx++) {
      if (shouldStop.current) break

      // Pause: wait until resumed or stopped
      if (isPaused.current) {
        await new Promise<void>(resolve => {
          pauseResolve.current = resolve
        })
      }
      if (shouldStop.current) break

      setCurrent(idx + 1)

      // Fetch row
      let rowData: Awaited<ReturnType<typeof api.getRow>>
      try {
        rowData = await api.getRow(sessionName, idx)
      } catch {
        errorCount++
        setErrors(errorCount)
        addLog(`Row ${idx + 1}: ✗ failed to fetch`)
        continue
      }

      // Skip already-annotated rows (all KBs filled)
      const allFilled = cfg.kbs.every(kb => rowData.annotations[kb.name]?.trim())
      if (allFilled) {
        skippedCount++
        setSkipped(skippedCount)
        continue
      }

      setCurrentLabel(rowData.label)
      const entityType = entityTypeCol ? (rowData.info[entityTypeCol] ?? '') : ''

      const newAnnotations: Record<string, string> = { ...rowData.annotations }
      let anyNew = false

      for (const kb of cfg.kbs) {
        if (newAnnotations[kb.name]?.trim()) continue
        try {
          const result = await api.getCandidates(
            sessionName, rowData.label, kb.name,
            kb.enable_dense ?? true,
            kb.enable_lexical ?? true,
          )
          const best = pickBest(result.dense ?? [], result.lexical ?? [], entityType, threshold)
          if (best) {
            newAnnotations[kb.name] = best.id
            anyNew = true
          }
        } catch {
          errorCount++
          setErrors(errorCount)
          addLog(`Row ${idx + 1} [${kb.name}]: ✗ query failed`)
        }
      }

      if (anyNew) {
        try {
          await api.saveRow(sessionName, idx, newAnnotations)
          annotatedCount++
          setAnnotated(annotatedCount)
          const ids = cfg.kbs.map(kb => newAnnotations[kb.name] ?? '—').join(', ')
          addLog(`Row ${idx + 1}: ${rowData.label} → ${ids}`)
        } catch {
          errorCount++
          setErrors(errorCount)
          addLog(`Row ${idx + 1}: ✗ save failed`)
        }
      }
    }

    if (shouldStop.current) {
      setStatus('stopped')
    } else {
      setStatus('done')
      onComplete()
    }
  }, [sessionName, cfg, onComplete, addLog])

  const handleStart = () => run()

  const handlePause = () => {
    isPaused.current = true
    setStatus('paused')
  }

  const handleResume = () => {
    isPaused.current = false
    setStatus('running')
    pauseResolve.current?.()
    pauseResolve.current = null
  }

  const handleStop = () => {
    shouldStop.current = true
    // Unblock pause if waiting
    if (isPaused.current) {
      isPaused.current = false
      pauseResolve.current?.()
      pauseResolve.current = null
    }
  }

  const pct = total > 0 ? Math.round((current / total) * 100) : 0
  const isActive = status === 'running' || status === 'paused'

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[540px] flex flex-col max-h-[85vh]">

        {/* ── header ── */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 flex-shrink-0">
          <div>
            <h2 className="font-semibold text-gray-900">Auto-annotate</h2>
            <p className="text-xs text-gray-400 mt-0.5">{sessionName}</p>
          </div>
          <button
            onClick={onClose}
            disabled={isActive}
            className="text-gray-400 hover:text-gray-600 disabled:opacity-30 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* ── body ── */}
        <div className="px-6 py-5 space-y-5 flex-1 overflow-y-auto">

          {/* progress bar */}
          <div>
            <div className="flex justify-between text-sm text-gray-600 mb-1.5">
              <span className="font-medium">{current} <span className="text-gray-400 font-normal">/ {total} rows</span></span>
              <span className="font-semibold text-brand-600">{pct}%</span>
            </div>
            <div className="h-2.5 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-brand-500 rounded-full transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>

          {/* stats */}
          <div className="grid grid-cols-3 gap-3 text-center">
            <div className="bg-emerald-50 border border-emerald-100 rounded-xl py-3 px-2">
              <p className="text-2xl font-bold text-emerald-700">{annotated}</p>
              <p className="text-xs text-emerald-600 mt-0.5">Annotated</p>
            </div>
            <div className="bg-gray-50 border border-gray-100 rounded-xl py-3 px-2">
              <p className="text-2xl font-bold text-gray-500">{skipped}</p>
              <p className="text-xs text-gray-400 mt-0.5">Already done</p>
            </div>
            <div className={`border rounded-xl py-3 px-2 ${errors > 0 ? 'bg-red-50 border-red-100' : 'bg-gray-50 border-gray-100'}`}>
              <p className={`text-2xl font-bold ${errors > 0 ? 'text-red-600' : 'text-gray-400'}`}>{errors}</p>
              <p className={`text-xs mt-0.5 ${errors > 0 ? 'text-red-500' : 'text-gray-400'}`}>Errors</p>
            </div>
          </div>

          {/* current item */}
          {isActive && currentLabel && (
            <div className="flex items-center gap-2.5 text-sm text-gray-700 bg-gray-50 border border-gray-100 rounded-xl px-4 py-2.5">
              {status === 'running'
                ? <Loader2 className="w-4 h-4 animate-spin text-brand-500 flex-shrink-0" />
                : <Pause className="w-4 h-4 text-amber-500 flex-shrink-0" />}
              <span className="truncate">{currentLabel}</span>
            </div>
          )}

          {/* done / stopped banners */}
          {status === 'done' && (
            <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
              All rows processed — {annotated} newly annotated, {skipped} already done.
            </div>
          )}
          {status === 'stopped' && (
            <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
              <Square className="w-4 h-4 flex-shrink-0" />
              Stopped at row {current} — {annotated} annotated so far.
            </div>
          )}

          {/* activity log */}
          {log.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Log</p>
              <div className="bg-gray-950 rounded-xl p-3 text-xs font-mono text-gray-300 max-h-44 overflow-y-auto space-y-0.5">
                {log.map((l, i) => (
                  <p key={i} className={l.includes('✗') ? 'text-red-400' : ''}>{l}</p>
                ))}
                <div ref={logEndRef} />
              </div>
            </div>
          )}
        </div>

        {/* ── footer ── */}
        <div className="px-6 py-4 border-t border-gray-100 flex items-center justify-between gap-3 flex-shrink-0">
          <button
            onClick={onClose}
            disabled={isActive}
            className="btn-secondary text-sm disabled:opacity-40"
          >
            {status === 'done' || status === 'stopped' ? 'Close' : 'Cancel'}
          </button>

          <div className="flex gap-2">
            {status === 'idle' && (
              <button className="btn-primary" onClick={handleStart}>
                <Play className="w-4 h-4" /> Start
              </button>
            )}

            {status === 'running' && (
              <>
                <button className="btn-secondary" onClick={handlePause}>
                  <Pause className="w-4 h-4" /> Pause
                </button>
                <button
                  className="btn-secondary text-red-600 border-red-200 hover:bg-red-50"
                  onClick={handleStop}
                >
                  <Square className="w-4 h-4" /> Stop
                </button>
              </>
            )}

            {status === 'paused' && (
              <>
                <button className="btn-primary" onClick={handleResume}>
                  <Play className="w-4 h-4" /> Resume
                </button>
                <button
                  className="btn-secondary text-red-600 border-red-200 hover:bg-red-50"
                  onClick={handleStop}
                >
                  <Square className="w-4 h-4" /> Stop
                </button>
              </>
            )}

            {(status === 'done' || status === 'stopped') && (
              <button className="btn-primary" onClick={handleStart}>
                <Play className="w-4 h-4" /> Run again
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
