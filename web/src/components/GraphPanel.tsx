import { useMemo, useState } from 'react'
import { Share2, AlertCircle, Minus } from 'lucide-react'
import type { GraphData } from '@/types'

interface Props {
  data: GraphData
  selectedId?: string          // highlighted (e.g. currently chosen candidate)
  onSelect?: (id: string) => void
  title?: string
  width?: number
  height?: number
  onCollapse?: () => void      // when set, header shows a minimize button
  suggestionIds?: Set<string>  // nodes to draw as candidate "suggestions" (distinct color)
  isolatedIds?: Set<string>    // override which nodes count as isolated (else uses node.isolated)
}

interface Placed { id: string; name: string; x: number; y: number; cand: boolean; isolated?: boolean; degree?: number; r: number }

const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

/**
 * Radial graph that stays legible at any node count: node size and ring spacing
 * adapt to how many nodes there are, and labels (node ids + relation names) show
 * only for the hovered / selected node — so a 40-node document graph reads as a
 * clean ring instead of overlapping text. Pure SVG, no external libs.
 */
export function GraphPanel({ data, selectedId, onSelect, title = 'Graph neighborhood', width = 356, height = 300, onCollapse, suggestionIds, isolatedIds }: Props) {
  const [hover, setHover] = useState<string | null>(null)

  const W = width
  const H = height
  const cx = W / 2
  const cy = H / 2
  const scale = Math.max(0.85, Math.min(2.4, Math.min(W, H) / 300))

  const placed = useMemo<Record<string, Placed>>(() => {
    // Inner ring = the "main" nodes (selected entities / candidates). Outer ring =
    // neighbors AND candidate suggestions, so suggestions sit outside the main circle.
    const inner = data.nodes.filter(n => n.is_candidate && !suggestionIds?.has(n.id))
    const outer = data.nodes.filter(n => !n.is_candidate || suggestionIds?.has(n.id))
    const hasOuter = outer.length > 0
    const out: Record<string, Placed> = {}

    const ring = (list: typeof data.nodes, r: number, base: number) => {
      const n = list.length
      // space available per node along the ring → cap node radius so they don't touch
      const gap = n > 0 ? (2 * Math.PI * r) / n : 999
      const nodeR = Math.max(2.5, Math.min(base * scale, gap * 0.42))
      list.forEach((node, i) => {
        const angle = n === 1 && r === 0 ? 0 : (2 * Math.PI * i) / Math.max(1, n) - Math.PI / 2
        out[node.id] = {
          id: node.id, name: node.name, cand: node.is_candidate, isolated: node.isolated,
          degree: node.degree,
          x: cx + r * Math.cos(angle),
          y: cy + r * Math.sin(angle),
          r: nodeR,
        }
      })
    }

    const R = Math.min(W, H)
    if (hasOuter) {
      ring(inner, inner.length <= 1 ? 0 : R * 0.24, 9)
      ring(outer, R * 0.44, 6)
    } else {
      ring(inner, inner.length <= 1 ? 0 : R * 0.42, 9)
    }
    return out
  }, [data.nodes, cx, cy, W, H, scale, suggestionIds])

  // node -> incident edge indices (for hover highlighting)
  const incident = useMemo(() => {
    const m: Record<string, number[]> = {}
    data.edges.forEach((e, i) => {
      ;(m[e.source] ??= []).push(i)
      ;(m[e.target] ??= []).push(i)
    })
    return m
  }, [data.edges])

  if (!data.enabled) return null

  if (data.error) {
    return (
      <div className="card p-3">
        <Header title={title} onCollapse={onCollapse} />
        <div className="flex items-center gap-2 text-xs text-amber-700 mt-2">
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
          Graph unavailable: {data.error}
        </div>
      </div>
    )
  }

  if (!data.nodes.length) {
    return (
      <div className="card p-3">
        <Header title={title} onCollapse={onCollapse} />
        <p className="text-xs text-gray-400 mt-2">No graph connections found for these ids.</p>
      </div>
    )
  }

  const focus = hover ?? selectedId ?? null
  const focusEdges = focus ? new Set(incident[focus] ?? []) : new Set<number>()
  const labelFont = Math.max(9, 8 * scale)

  const isIsolated = (p: Placed) => (isolatedIds ? isolatedIds.has(p.id) : !!p.isolated)
  const fillFor = (p: Placed) =>
    p.id === selectedId ? '#10b981'
      : suggestionIds?.has(p.id) ? '#3b82f6'   // candidate suggestion (overlay)
      : !p.cand ? '#9ca3af'
      : isIsolated(p) ? '#f59e0b'
      : '#7c3aed'

  return (
    <div className="card p-3">
      <Header title={title} count={data.edges.length} nodes={data.nodes.length} onCollapse={onCollapse} />
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="mt-1"
           onMouseLeave={() => setHover(null)}>
        {/* edges — light by default; a focused node's edges are highlighted */}
        {data.edges.map((e, i) => {
          const a = placed[e.source]; const b = placed[e.target]
          if (!a || !b) return null
          const on = focusEdges.has(i)
          return (
            <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={on ? '#7c3aed' : '#e5e7eb'}
                  strokeWidth={(on ? 1.6 : 0.7) * scale} />
          )
        })}
        {/* relation labels only for the focused node's edges (few, so readable) */}
        {focus && data.edges.map((e, i) => {
          if (!focusEdges.has(i) || !e.rel) return null
          const a = placed[e.source]; const b = placed[e.target]
          if (!a || !b) return null
          return (
            <text key={`r${i}`} x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 2}
                  textAnchor="middle" className="fill-violet-600"
                  style={{ fontSize: labelFont, paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}>
              {e.rel}
            </text>
          )
        })}
        {/* nodes */}
        {Object.values(placed).map(p => {
          const isFocus = p.id === focus
          return (
            <g key={p.id}
               onMouseEnter={() => setHover(p.id)}
               onClick={() => p.cand && onSelect?.(p.id)}
               style={{ cursor: p.cand && onSelect ? 'pointer' : 'default' }}>
              <circle cx={p.x} cy={p.y} r={isFocus ? p.r + 2 * scale : p.r}
                      fill={fillFor(p)}
                      stroke={p.id === selectedId ? '#065f46' : 'white'}
                      strokeWidth={(p.id === selectedId ? 2 : 1) * scale} />
            </g>
          )
        })}
        {/* label only for the focused node (avoids the text pile-up) — the name, not the CUI */}
        {focus && placed[focus] && (
          <text x={placed[focus].x} y={placed[focus].y + placed[focus].r + labelFont + 2}
                textAnchor="middle" className="fill-gray-800"
                style={{ fontSize: labelFont, fontWeight: 600,
                         paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}>
            {truncate(placed[focus].name || placed[focus].id, 26)}
          </text>
        )}
      </svg>
      {/* focused node info panel — name headline + the CUI (and link count) beneath */}
      <div className="mt-1 min-h-[30px]">
        {focus && placed[focus] ? (
          <div className="text-[11px] leading-tight">
            <p className="font-medium text-gray-700 truncate">{placed[focus].name || placed[focus].id}</p>
            <p className="text-gray-500 flex items-center gap-1.5 flex-wrap">
              <span>CUI <code className="font-mono text-gray-700">{placed[focus].id}</code></span>
              {placed[focus].degree != null && (
                <span className="text-gray-400">· {placed[focus].degree} {placed[focus].degree === 1 ? 'link' : 'links'}</span>
              )}
              {isIsolated(placed[focus]) && <span className="text-amber-600">· isolated</span>}
            </p>
          </div>
        ) : (
          <p className="text-[11px] text-gray-300">Hover a node to inspect · click to select</p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-gray-400 mt-1">
        <span className="flex items-center gap-1"><Dot c="#7c3aed" /> connected</span>
        <span className="flex items-center gap-1"><Dot c="#f59e0b" /> isolated</span>
        <span className="flex items-center gap-1"><Dot c="#10b981" /> selected</span>
        {suggestionIds && suggestionIds.size > 0 && (
          <span className="flex items-center gap-1"><Dot c="#3b82f6" /> candidate</span>
        )}
        <span className="flex items-center gap-1"><Dot c="#9ca3af" /> neighbor</span>
      </div>
    </div>
  )
}

function Header({ title, count, nodes, onCollapse }: { title: string; count?: number; nodes?: number; onCollapse?: () => void }) {
  return (
    <div className="flex items-center gap-2">
      <Share2 className="w-3.5 h-3.5 text-violet-500" />
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{title}</p>
      {count != null && (
        <span className="text-[10px] text-gray-400 ml-auto">
          {nodes != null ? `${nodes} nodes · ` : ''}{count} edges
        </span>
      )}
      {onCollapse && (
        <button
          onClick={onCollapse}
          className={`text-gray-400 hover:text-gray-600 ${count != null ? 'ml-2' : 'ml-auto'}`}
          title="Minimize"
        >
          <Minus className="w-4 h-4" />
        </button>
      )}
    </div>
  )
}

function Dot({ c }: { c: string }) {
  return <span className="inline-block w-2 h-2 rounded-full" style={{ background: c }} />
}
