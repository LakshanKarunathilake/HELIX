import { useCallback, useEffect, useState } from 'react'
import { X, Share2, RefreshCw, Trash2, Loader2, Database, Clock, Layers } from 'lucide-react'
import * as api from '@/api/client'
import type { GraphStats } from '@/types'

interface Props {
  sessionName: string
  onClose: () => void
}

export function GraphStatsModal({ sessionName, onClose }: Props) {
  const [data, setData] = useState<GraphStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try { setData(await api.getGraphStats(sessionName)) } finally { setLoading(false) }
  }, [sessionName])
  useEffect(() => { refresh() }, [refresh])

  const reset = async () => {
    setBusy(true)
    try { await api.resetGraphStats(sessionName); await refresh() } finally { setBusy(false) }
  }

  const docs = data?.docs ?? []

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[720px] flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 flex-shrink-0">
          <div className="flex items-center gap-2">
            <Share2 className="w-5 h-5 text-violet-500" />
            <div>
              <h2 className="font-semibold text-gray-900">Graph query stats</h2>
              <p className="text-xs text-gray-400 mt-0.5">Neo4j cost of connection-maximisation · {sessionName}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>

        <div className="px-6 py-5 space-y-6 flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center gap-2 text-gray-400 text-sm py-10 justify-center">
              <Loader2 className="w-5 h-5 animate-spin" /> Loading…
            </div>
          ) : !data || data.count === 0 ? (
            <p className="text-sm text-gray-400 text-center py-10">
              No graph-optimisation stats yet. Run a graph match (Auto-annotate step 2, or the
              document review Auto-match), then reopen this.
            </p>
          ) : (
            <>
              {/* summary cards */}
              <div className="grid grid-cols-4 gap-2">
                <Card icon={<Share2 className="w-4 h-4" />} value={`${data.count}`} label="optimisations" />
                <Card icon={<Database className="w-4 h-4" />} value={fmt(data.totals.nodes)} label="nodes tackled" />
                <Card icon={<Layers className="w-4 h-4" />} value={fmt(data.totals.edges)} label="edges evaluated" />
                <Card icon={<Clock className="w-4 h-4" />} value={`${(data.totals.query_ms / 1000).toFixed(1)}s`} label="total query time" />
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-500">
                <span>query latency — mean <b className="text-gray-700">{data.query_ms.mean}ms</b></span>
                <span>p95 <b className="text-gray-700">{data.query_ms.p95}ms</b></span>
                <span>max <b className="text-gray-700">{data.query_ms.max}ms</b></span>
                <span className="ml-auto">nodes/call — mean <b className="text-gray-700">{Math.round(data.nodes.mean)}</b>, max <b className="text-gray-700">{data.nodes.max}</b></span>
              </div>

              {/* scatter: cost vs scale */}
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Query cost vs. nodes tackled</p>
                <Scatter docs={docs} />
                <p className="text-[11px] text-gray-400 mt-1">Each point is one document optimisation — x: candidate nodes sent to Neo4j, y: edge-query latency (ms).</p>
              </div>

              {/* bar: most expensive documents */}
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Most expensive documents</p>
                <BarList docs={docs.slice(0, 12)} />
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
            <button className="btn-secondary text-sm text-red-600 border-red-200 hover:bg-red-50" onClick={reset} disabled={busy}>
              <Trash2 className="w-4 h-4" /> Reset
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function fmt(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`
}

function Card({ icon, value, label }: { icon: React.ReactNode; value: string; label: string }) {
  return (
    <div className="bg-gray-50 border border-gray-100 rounded-xl py-2.5 px-1 text-center">
      <div className="flex items-center justify-center text-gray-400">{icon}</div>
      <p className="text-lg font-bold text-gray-800 mt-0.5">{value}</p>
      <p className="text-[10px] text-gray-400">{label}</p>
    </div>
  )
}

function Scatter({ docs }: { docs: { nodes: number; query_ms: number; doc: string }[] }) {
  const W = 660, H = 220, pl = 44, pb = 28, pt = 10, pr = 12
  const xmax = Math.max(1, ...docs.map(d => d.nodes))
  const ymax = Math.max(1, ...docs.map(d => d.query_ms))
  const x = (v: number) => pl + (v / xmax) * (W - pl - pr)
  const y = (v: number) => H - pb - (v / ymax) * (H - pt - pb)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" className="border border-gray-100 rounded-lg">
      {/* axes */}
      <line x1={pl} y1={pt} x2={pl} y2={H - pb} stroke="#e5e7eb" />
      <line x1={pl} y1={H - pb} x2={W - pr} y2={H - pb} stroke="#e5e7eb" />
      {/* ticks */}
      <text x={pl} y={H - pb + 14} textAnchor="middle" className="fill-gray-400" style={{ fontSize: 9 }}>0</text>
      <text x={W - pr} y={H - pb + 14} textAnchor="end" className="fill-gray-400" style={{ fontSize: 9 }}>{xmax} nodes</text>
      <text x={pl - 6} y={H - pb} textAnchor="end" className="fill-gray-400" style={{ fontSize: 9 }}>0</text>
      <text x={pl - 6} y={pt + 8} textAnchor="end" className="fill-gray-400" style={{ fontSize: 9 }}>{Math.round(ymax)}ms</text>
      {/* points */}
      {docs.map((d, i) => (
        <circle key={i} cx={x(d.nodes)} cy={y(d.query_ms)} r={4} fill="#7c3aed" fillOpacity={0.55}>
          <title>{d.doc}: {d.nodes} nodes, {d.query_ms}ms</title>
        </circle>
      ))}
    </svg>
  )
}

function BarList({ docs }: { docs: { doc: string; query_ms: number; nodes: number; edges: number }[] }) {
  const max = Math.max(1, ...docs.map(d => d.query_ms))
  return (
    <div className="space-y-1.5">
      {docs.map((d, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <span className="w-32 truncate font-mono text-gray-600" title={d.doc}>{d.doc}</span>
          <div className="flex-1 h-4 bg-gray-100 rounded overflow-hidden">
            <div className="h-full bg-violet-500 rounded" style={{ width: `${(d.query_ms / max) * 100}%` }} />
          </div>
          <span className="w-16 text-right tabular-nums text-gray-700">{d.query_ms}ms</span>
          <span className="w-20 text-right tabular-nums text-gray-400">{d.nodes}n · {d.edges}e</span>
        </div>
      ))}
    </div>
  )
}
