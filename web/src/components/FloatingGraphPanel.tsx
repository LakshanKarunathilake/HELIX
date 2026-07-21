import { useCallback, useRef, useState } from 'react'
import { Share2 } from 'lucide-react'
import type { GraphData } from '@/types'
import { GraphPanel } from './GraphPanel'

interface Props {
  data?: GraphData
  selectedId?: string
  onSelect?: (id: string) => void
  title?: string
}

const MIN_W = 280
const MIN_H = 240
const MAX_W = 900
const MAX_H = 760
const CARD_PAD = 24 // p-3 left+right; graph inner width = card width - pad

function loadSize(): { w: number; h: number } {
  try {
    const raw = localStorage.getItem('helix.graphSize')
    if (raw) {
      const s = JSON.parse(raw)
      if (typeof s.w === 'number' && typeof s.h === 'number') return s
    }
  } catch { /* ignore */ }
  return { w: 380, h: 340 }
}

/**
 * Floating, collapsible AND resizable graph. A chip in the corner expands into a
 * card; the card can be dragged bigger/smaller via a grip on its top-left corner
 * (it's anchored bottom-right, so it grows up-and-left, staying on screen).
 */
export function FloatingGraphPanel({ data, selectedId, onSelect, title = 'Candidate graph' }: Props) {
  const [open, setOpen] = useState(false)
  const [size, setSize] = useState(loadSize)
  const drag = useRef<{ x: number; y: number; w: number; h: number } | null>(null)

  const onPointerMove = useCallback((e: PointerEvent) => {
    if (!drag.current) return
    // Anchored bottom-right: moving the grip left/up enlarges the card.
    const dw = drag.current.x - e.clientX
    const dh = drag.current.y - e.clientY
    const w = Math.max(MIN_W, Math.min(MAX_W, drag.current.w + dw))
    const h = Math.max(MIN_H, Math.min(MAX_H, drag.current.h + dh))
    setSize({ w, h })
  }, [])

  const endDrag = useCallback(() => {
    drag.current = null
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', endDrag)
    setSize(s => {
      try { localStorage.setItem('helix.graphSize', JSON.stringify(s)) } catch { /* ignore */ }
      return s
    })
  }, [onPointerMove])

  const startDrag = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    drag.current = { x: e.clientX, y: e.clientY, w: size.w, h: size.h }
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', endDrag)
  }, [size, onPointerMove, endDrag])

  if (!data?.enabled) return null

  return (
    <div className="fixed bottom-20 right-6 z-40">
      {open ? (
        <div
          className="relative rounded-xl shadow-2xl ring-1 ring-black/5 bg-white"
          style={{ width: size.w }}
        >
          {/* resize grip (top-left) */}
          <div
            onPointerDown={startDrag}
            title="Drag to resize"
            className="absolute -top-1 -left-1 w-4 h-4 cursor-nwse-resize z-10"
            style={{
              borderTop: '2px solid #a78bfa',
              borderLeft: '2px solid #a78bfa',
              borderTopLeftRadius: 6,
            }}
          />
          <div className="overflow-y-auto" style={{ maxHeight: size.h + 90 }}>
            <GraphPanel
              data={data}
              selectedId={selectedId}
              onSelect={onSelect}
              title={title}
              width={size.w - CARD_PAD}
              height={size.h}
              onCollapse={() => setOpen(false)}
            />
          </div>
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="flex items-center gap-2 bg-white shadow-lg ring-1 ring-black/5 hover:ring-violet-300 rounded-full pl-3 pr-4 py-2 text-sm font-medium text-violet-700 transition"
          title="Show candidate graph"
        >
          <Share2 className="w-4 h-4 text-violet-500" />
          Graph
          {data.edges.length > 0 && (
            <span className="text-[10px] font-semibold bg-violet-100 text-violet-700 rounded-full px-1.5 py-0.5 tabular-nums">
              {data.edges.length}
            </span>
          )}
        </button>
      )}
    </div>
  )
}
