import { useMemo, useRef, useState, type ReactNode } from 'react'
import { Share2, AlertCircle, Minus, Download, Image as ImageIcon, FileCode, Loader2 } from 'lucide-react'
import type { GraphData } from '@/types'

type ExportOpts = { names: boolean; rels: boolean; expand: boolean }

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
  downloadable?: boolean       // show a "download image" control (PNG / SVG) for papers
  // for the "expand candidates" export option: fetch a fuller graph (all candidates + edges)
  onExpandCandidates?: () => Promise<{ data: GraphData; suggestionIds?: Set<string> }>
}

interface Placed { id: string; name: string; x: number; y: number; cand: boolean; isolated?: boolean; degree?: number; r: number }

const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// Radial layout: inner ring = main nodes, outer ring = neighbors/candidate suggestions.
// Extracted so both the live render and the (possibly expanded) export share it.
function computeLayout(nodes: GraphData['nodes'], W: number, H: number, scale: number,
                       suggestionIds?: Set<string>): Record<string, Placed> {
  const cx = W / 2, cy = H / 2
  const inner = nodes.filter(n => n.is_candidate && !suggestionIds?.has(n.id))
  const outer = nodes.filter(n => !n.is_candidate || suggestionIds?.has(n.id))
  const hasOuter = outer.length > 0
  const out: Record<string, Placed> = {}
  const ring = (list: GraphData['nodes'], r: number, base: number) => {
    const m = list.length
    const gap = m > 0 ? (2 * Math.PI * r) / m : 999
    const nodeR = Math.max(2.5, Math.min(base * scale, gap * 0.42))
    list.forEach((node, i) => {
      const angle = m === 1 && r === 0 ? 0 : (2 * Math.PI * i) / Math.max(1, m) - Math.PI / 2
      out[node.id] = {
        id: node.id, name: node.name, cand: node.is_candidate, isolated: node.isolated,
        degree: node.degree, x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle), r: nodeR,
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
}

// Build a standalone, publication-quality SVG figure from a laid-out graph.
function buildSvg(gdata: GraphData, placed: Record<string, Placed>, W: number, H: number,
                  title: string, opts: ExportOpts, fill: (p: Placed) => string): { svg: string; w: number; h: number } {
  const cx = W / 2, cy = H / 2, font = 12
  const nodes = Object.values(placed)
  const longest = opts.names && nodes.length ? Math.max(...nodes.map(p => (p.name || p.id).length)) : 0
  const padX = opts.names ? Math.ceil(Math.min(30, Math.max(8, longest)) * 6.6) + 18 : 24
  const padTop = 44, padBottom = 52
  const vbX = -padX, vbY = -padTop, vbW = W + padX * 2, vbH = H + padTop + padBottom
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const n = (v: number) => v.toFixed(1)
  const out: string[] = []
  out.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${vbW}" height="${vbH}" viewBox="${vbX} ${vbY} ${vbW} ${vbH}" font-family="Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif">`)
  out.push(`<rect x="${vbX}" y="${vbY}" width="${vbW}" height="${vbH}" fill="#ffffff"/>`)
  out.push(`<text x="${vbX + 8}" y="${vbY + 20}" font-size="13" font-weight="700" fill="#374151">${esc(title)}</text>`)
  for (const e of gdata.edges) {                                          // edges
    const a = placed[e.source], b = placed[e.target]
    if (a && b) out.push(`<line x1="${n(a.x)}" y1="${n(a.y)}" x2="${n(b.x)}" y2="${n(b.y)}" stroke="#d1d5db" stroke-width="1"/>`)
  }
  if (opts.rels) {                                                        // optional relation labels
    for (const e of gdata.edges) {
      const a = placed[e.source], b = placed[e.target]
      if (a && b && e.rel) out.push(`<text x="${n((a.x + b.x) / 2)}" y="${n((a.y + b.y) / 2 - 2)}" font-size="9" fill="#7c3aed" text-anchor="middle" paint-order="stroke" stroke="#ffffff" stroke-width="2.5">${esc(e.rel)}</text>`)
    }
  }
  for (const p of nodes) {                                                // nodes (+ optional radial names)
    out.push(`<circle cx="${n(p.x)}" cy="${n(p.y)}" r="${n(p.r)}" fill="${fill(p)}" stroke="#ffffff" stroke-width="1.2"/>`)
    if (!opts.names) continue
    const label = esc(truncate(p.name || p.id, 30))
    const centered = Math.hypot(p.x - cx, p.y - cy) < 1
    let lx = p.x, ly = p.y + p.r + font + 2, anchor = 'middle'
    if (!centered) {
      const ang = Math.atan2(p.y - cy, p.x - cx)
      const dx = Math.cos(ang), dy = Math.sin(ang)
      lx = p.x + dx * (p.r + 5); ly = p.y + dy * (p.r + 5) + font * 0.34
      anchor = dx >= 0 ? 'start' : 'end'
    }
    out.push(`<text x="${n(lx)}" y="${n(ly)}" font-size="${font}" fill="#111827" text-anchor="${anchor}" paint-order="stroke" stroke="#ffffff" stroke-width="3">${label}</text>`)
  }
  const legend: [string, string][] = [['#7c3aed', 'connected'], ['#f59e0b', 'isolated'], ['#10b981', 'selected'], ['#3b82f6', 'candidate'], ['#9ca3af', 'neighbor']]
  let lgx = vbX + 8
  const lgy = H + 22
  for (const [c, lab] of legend) {
    out.push(`<circle cx="${lgx + 4}" cy="${lgy - 4}" r="4" fill="${c}"/>`)
    out.push(`<text x="${lgx + 13}" y="${lgy}" font-size="11" fill="#6b7280">${lab}</text>`)
    lgx += 22 + lab.length * 6.2
  }
  out.push('</svg>')
  return { svg: out.join(''), w: vbW, h: vbH }
}

function svgToPng(svg: string, w: number, h: number, scale: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new window.Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(w * scale)
      canvas.height = Math.round(h * scale)
      const ctx = canvas.getContext('2d')
      if (!ctx) { reject(new Error('no 2d context')); return }
      ctx.setTransform(scale, 0, 0, scale, 0, 0)
      ctx.drawImage(img, 0, 0)
      canvas.toBlob(b => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png')
    }
    img.onerror = () => reject(new Error('SVG image failed to load'))
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
  })
}

/**
 * Radial graph that stays legible at any node count: node size and ring spacing
 * adapt to how many nodes there are, and labels (node ids + relation names) show
 * only for the hovered / selected node — so a 40-node document graph reads as a
 * clean ring instead of overlapping text. Pure SVG, no external libs.
 */
export function GraphPanel({ data, selectedId, onSelect, title = 'Graph neighborhood', width = 356, height = 300, onCollapse, suggestionIds, isolatedIds, downloadable, onExpandCandidates }: Props) {
  const [hover, setHover] = useState<string | null>(null)

  const W = width
  const H = height
  const cx = W / 2
  const cy = H / 2
  const scale = Math.max(0.85, Math.min(2.4, Math.min(W, H) / 300))

  const placed = useMemo<Record<string, Placed>>(
    () => computeLayout(data.nodes, W, H, scale, suggestionIds),
    [data.nodes, W, H, scale, suggestionIds],
  )

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

  // ── publication-quality export (options + optional all-candidate expansion) ─
  const fileBase = (title || 'graph').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'graph'

  const doExport = async (fmt: 'svg' | 'png', opts: ExportOpts) => {
    let gdata = data, sug = suggestionIds, iso = isolatedIds
    if (opts.expand && onExpandCandidates) {
      const r = await onExpandCandidates()
      gdata = r.data
      sug = r.suggestionIds
      iso = undefined   // in the expanded figure, isolation follows the drawn edges
    }
    const pl = computeLayout(gdata.nodes, W, H, scale, sug)
    const fill = (p: Placed) =>
      p.id === selectedId ? '#10b981'
        : sug?.has(p.id) ? '#3b82f6'
        : !p.cand ? '#9ca3af'
        : (iso ? iso.has(p.id) : !!p.isolated) ? '#f59e0b'
        : '#7c3aed'
    const { svg, w, h } = buildSvg(gdata, pl, W, H, title, opts, fill)
    if (fmt === 'svg') {
      triggerDownload(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }), `${fileBase}.svg`)
    } else {
      triggerDownload(await svgToPng(svg, w, h, 3), `${fileBase}.png`)   // 3× ≈ 300+ DPI
    }
  }

  // ── exact-preview export: serialise the LIVE svg node, exactly as shown ──────
  const svgRef = useRef<SVGSVGElement>(null)
  const exportExact = async (fmt: 'svg' | 'png') => {
    const src = svgRef.current
    if (!src) return
    const NS = 'http://www.w3.org/2000/svg'
    const clone = src.cloneNode(true) as SVGSVGElement
    clone.setAttribute('xmlns', NS)
    clone.setAttribute('width', String(W))
    clone.setAttribute('height', String(H))
    clone.setAttribute('viewBox', `0 0 ${W} ${H}`)
    // Inline computed styles FIRST — clone and src are structurally identical here,
    // so querySelectorAll('*') aligns 1:1. This makes class-based colours
    // (relation/name labels) survive outside the app's stylesheet. (The white
    // background is added AFTER, so it doesn't shift this index alignment.)
    const srcEls = src.querySelectorAll('*')
    const clEls = clone.querySelectorAll('*')
    const props = ['fill', 'stroke', 'stroke-width', 'font-size', 'font-family',
                   'font-weight', 'opacity', 'paint-order', 'text-anchor']
    srcEls.forEach((el, i) => {
      const t = clEls[i] as SVGElement | undefined
      if (!t) return
      const cs = getComputedStyle(el as Element)
      props.forEach(p => { const v = cs.getPropertyValue(p); if (v && v !== 'none') t.style.setProperty(p, v) })
      t.removeAttribute('class')
    })
    // opaque white background, prepended AFTER inlining so alignment stays intact
    const bg = document.createElementNS(NS, 'rect')
    bg.setAttribute('x', '0'); bg.setAttribute('y', '0')
    bg.setAttribute('width', String(W)); bg.setAttribute('height', String(H))
    bg.setAttribute('fill', 'white')
    clone.insertBefore(bg, clone.firstChild)
    const svg = new XMLSerializer().serializeToString(clone)
    if (fmt === 'svg') {
      triggerDownload(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }), `${fileBase}-preview.svg`)
    } else {
      triggerDownload(await svgToPng(svg, W, H, 3), `${fileBase}-preview.png`)
    }
  }

  return (
    <div className="card p-3">
      <Header title={title} count={data.edges.length} nodes={data.nodes.length} onCollapse={onCollapse}
              action={downloadable ? <GraphExportMenu onExport={doExport} onExportExact={exportExact} canExpand={!!onExpandCandidates} /> : undefined} />
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="mt-1"
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

function Header({ title, count, nodes, onCollapse, action }: { title: string; count?: number; nodes?: number; onCollapse?: () => void; action?: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <Share2 className="w-3.5 h-3.5 text-violet-500" />
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{title}</p>
      {count != null && (
        <span className="text-[10px] text-gray-400 ml-auto">
          {nodes != null ? `${nodes} nodes · ` : ''}{count} edges
        </span>
      )}
      {action && <span className={count == null ? 'ml-auto' : ''}>{action}</span>}
      {onCollapse && (
        <button
          onClick={onCollapse}
          className={`text-gray-400 hover:text-gray-600 ${count != null || action ? 'ml-2' : 'ml-auto'}`}
          title="Minimize"
        >
          <Minus className="w-4 h-4" />
        </button>
      )}
    </div>
  )
}

function GraphExportMenu({ onExport, onExportExact, canExpand }: {
  onExport: (fmt: 'svg' | 'png', opts: ExportOpts) => Promise<void> | void
  onExportExact: (fmt: 'svg' | 'png') => Promise<void> | void
  canExpand: boolean
}) {
  const [open, setOpen] = useState(false)
  const [names, setNames] = useState(true)
  const [rels, setRels] = useState(false)
  const [expand, setExpand] = useState(false)
  const [busy, setBusy] = useState(false)

  const run = async (fmt: 'svg' | 'png') => {
    setBusy(true)
    try { await onExport(fmt, { names, rels, expand }) }
    catch (e) { console.error('graph export failed', e) }
    finally { setBusy(false); setOpen(false) }
  }
  const runExact = async (fmt: 'svg' | 'png') => {
    setBusy(true)
    try { await onExportExact(fmt) }
    catch (e) { console.error('graph export failed', e) }
    finally { setBusy(false); setOpen(false) }
  }

  const row = 'flex items-center gap-2 px-3 py-1.5 text-sm text-gray-700 cursor-pointer hover:bg-gray-50 select-none'
  return (
    <div className="relative">
      <button className="text-gray-400 hover:text-gray-600" title="Download graph image (for a paper)"
              onClick={() => setOpen(o => !o)}>
        <Download className="w-4 h-4" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => !busy && setOpen(false)} />
          <div className="absolute right-0 mt-1 z-20 w-60 bg-white border border-gray-200 rounded-lg shadow-lg py-1">
            <p className="px-3 pt-1 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">Include</p>
            <label className={row}>
              <input type="checkbox" checked={names} onChange={e => setNames(e.target.checked)} /> Node names
            </label>
            <label className={row}>
              <input type="checkbox" checked={rels} onChange={e => setRels(e.target.checked)} /> Relationship labels
            </label>
            {canExpand && (
              <label className={row}>
                <input type="checkbox" checked={expand} onChange={e => setExpand(e.target.checked)} /> Expand candidates + edges
              </label>
            )}
            {expand && (
              <p className="px-3 pb-1 text-[10px] text-gray-400 leading-snug">
                Fetches every mention's candidates — may take a moment.
              </p>
            )}
            <div className="border-t border-gray-100 mt-1 pt-2 px-2 flex gap-2">
              <button disabled={busy} onClick={() => run('png')}
                      className="btn-secondary flex-1 justify-center !py-1 text-xs disabled:opacity-50">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ImageIcon className="w-3.5 h-3.5" />} PNG
              </button>
              <button disabled={busy} onClick={() => run('svg')}
                      className="btn-secondary flex-1 justify-center !py-1 text-xs disabled:opacity-50">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileCode className="w-3.5 h-3.5" />} SVG
              </button>
            </div>
            <p className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400 border-t border-gray-100 mt-1">As shown</p>
            <p className="px-3 pb-1 text-[10px] text-gray-400 leading-snug">Downloads the exact preview, exactly as displayed.</p>
            <div className="pb-1 px-2 flex gap-2">
              <button disabled={busy} onClick={() => runExact('png')}
                      className="btn-secondary flex-1 justify-center !py-1 text-xs disabled:opacity-50">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ImageIcon className="w-3.5 h-3.5" />} PNG
              </button>
              <button disabled={busy} onClick={() => runExact('svg')}
                      className="btn-secondary flex-1 justify-center !py-1 text-xs disabled:opacity-50">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileCode className="w-3.5 h-3.5" />} SVG
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function Dot({ c }: { c: string }) {
  return <span className="inline-block w-2 h-2 rounded-full" style={{ background: c }} />
}
