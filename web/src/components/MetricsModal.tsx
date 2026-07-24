import { useCallback, useEffect, useState } from 'react'
import { X, Activity, RefreshCw, Trash2, Download, Loader2, Cpu, Gauge } from 'lucide-react'
import * as api from '@/api/client'
import type { MetricsInsights } from '@/types'

interface Props {
  sessionName: string
  onClose: () => void
}

const OP_LABEL: Record<string, string> = {
  candidates: 'Candidate request (end-to-end)',
  'retrieval.dense': 'Dense retrieval (FAISS)',
  'retrieval.lexical': 'Lexical retrieval (BM25+n-gram)',
  'rerank.llm': 'LLM re-ranking',
  'graph.optimize': 'Graph optimise (per doc)',
  'write.batch': 'Batch write',
}

export function MetricsModal({ sessionName, onClose }: Props) {
  const [enabled, setEnabled] = useState(false)
  const [data, setData] = useState<MetricsInsights | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [s, ins] = await Promise.all([api.getMetricsStatus(), api.getMetricsInsights(sessionName)])
      setEnabled(s.enabled)
      setData(ins)
    } finally { setLoading(false) }
  }, [sessionName])

  useEffect(() => { refresh() }, [refresh])

  const toggle = async () => {
    setBusy(true)
    try { const r = await api.toggleMetrics(!enabled); setEnabled(r.enabled) }
    finally { setBusy(false) }
  }
  const reset = async () => {
    setBusy(true)
    try { await api.resetMetrics(sessionName); await refresh() }
    finally { setBusy(false) }
  }

  const maxShare = Math.max(0.0001, ...(data?.operations ?? []).map(o => o.share))

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[640px] flex flex-col max-h-[88vh]">

        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 flex-shrink-0">
          <div className="flex items-center gap-2">
            <Activity className="w-5 h-5 text-brand-500" />
            <div>
              <h2 className="font-semibold text-gray-900">Performance insights</h2>
              <p className="text-xs text-gray-400 mt-0.5">{sessionName}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>

        <div className="px-6 py-5 space-y-5 flex-1 overflow-y-auto">
          {/* toggle */}
          <div className="flex items-center justify-between bg-gray-50 border border-gray-100 rounded-xl px-4 py-3">
            <div>
              <p className="text-sm font-medium text-gray-800">Track latency &amp; memory</p>
              <p className="text-xs text-gray-400">Enable this <b>before</b> running annotation to capture samples.</p>
            </div>
            <button
              onClick={toggle}
              disabled={busy}
              className={`relative w-11 h-6 rounded-full transition-colors ${enabled ? 'bg-emerald-500' : 'bg-gray-300'}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${enabled ? 'translate-x-5' : ''}`} />
            </button>
          </div>

          {loading ? (
            <div className="flex items-center gap-2 text-gray-400 text-sm py-8 justify-center">
              <Loader2 className="w-5 h-5 animate-spin" /> Loading…
            </div>
          ) : !data || data.samples === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8">
              No samples yet. {enabled ? 'Run some annotation, then refresh.' : 'Enable tracking, then run annotation.'}
            </p>
          ) : (
            <>
              {/* summary cards */}
              <div className="grid grid-cols-3 gap-3">
                <Card icon={<Gauge className="w-4 h-4" />} value={`${data.throughput.mentions_per_s}`} label="mentions / sec" />
                <Card icon={<Cpu className="w-4 h-4" />} value={`${data.memory.peak_mb} MB`} label="peak memory" />
                <Card icon={<Activity className="w-4 h-4" />} value={`${data.samples}`} label="samples" />
              </div>
              {!data.memory.psutil && (
                <p className="text-[11px] text-amber-600">
                  Memory is approximate — install <code>psutil</code> on the API server for accurate RSS.
                </p>
              )}

              {/* per-operation table */}
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Latency by operation</p>
                <div className="space-y-2">
                  {data.operations.map(o => (
                    <div key={o.op} className="border border-gray-100 rounded-lg px-3 py-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium text-gray-800">{OP_LABEL[o.op] ?? o.op}</span>
                        <span className="text-xs text-gray-400">{o.count}×</span>
                      </div>
                      <div className="flex items-center gap-3 text-[11px] text-gray-500 mt-0.5">
                        <span>mean <b className="text-gray-700">{o.mean_ms}ms</b></span>
                        <span>p95 <b className="text-gray-700">{o.p95_ms}ms</b></span>
                        <span>max <b className="text-gray-700">{o.max_ms}ms</b></span>
                        <span className="ml-auto">{o.total_s}s total</span>
                      </div>
                      {/* time-share bar */}
                      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden mt-1.5">
                        <div className="h-full bg-brand-500 rounded-full"
                             style={{ width: `${Math.round((o.share / maxShare) * 100)}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        <div className="px-6 py-4 border-t border-gray-100 flex items-center justify-between gap-3 flex-shrink-0">
          <button onClick={onClose} className="btn-secondary text-sm">Close</button>
          <div className="flex gap-2">
            <button className="btn-secondary text-sm" onClick={refresh} disabled={loading}>
              <RefreshCw className="w-4 h-4" /> Refresh
            </button>
            <button className="btn-secondary text-sm" onClick={() => api.exportMetrics(sessionName)}>
              <Download className="w-4 h-4" /> Export
            </button>
            <button className="btn-secondary text-sm text-red-600 border-red-200 hover:bg-red-50" onClick={reset} disabled={busy}>
              <Trash2 className="w-4 h-4" /> Reset
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function Card({ icon, value, label }: { icon: React.ReactNode; value: string; label: string }) {
  return (
    <div className="bg-gray-50 border border-gray-100 rounded-xl py-3 px-2 text-center">
      <div className="flex items-center justify-center gap-1 text-gray-400">{icon}</div>
      <p className="text-lg font-bold text-gray-800 mt-0.5">{value}</p>
      <p className="text-[10px] text-gray-400">{label}</p>
    </div>
  )
}
