import { useState } from 'react'
import { ChevronDown, ChevronUp, Loader2, X } from 'lucide-react'
import type { FeedbackOptions, Reflection } from '@/types'

interface Props {
  options: FeedbackOptions
  existing?: Reflection | null
  onSave: (ranking: string[], comment: string) => Promise<void>
  onClose: () => void
}

/**
 * End-of-session reflection (Annotation Guide Sec. 8): rank every hint type by
 * how useful it was in this tool setting, and say what helped or misled.
 */
export function ReflectionModal({ options, existing, onSave, onClose }: Props) {
  const labels = Object.fromEntries(options.hints.map(h => [h.code, h.label]))
  const [ranking, setRanking] = useState<string[]>(() => {
    const known = new Set(options.hints.map(h => h.code))
    const saved = (existing?.ranking ?? []).filter(c => known.has(c))
    return saved.length === known.size ? saved : options.hints.map(h => h.code)
  })
  const [comment, setComment] = useState(existing?.comment ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const move = (i: number, by: -1 | 1) => setRanking(r => {
    const j = i + by
    if (j < 0 || j >= r.length) return r
    const n = [...r]
    ;[n[i], n[j]] = [n[j], n[i]]
    return n
  })

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      await onSave(ranking, comment)
    } catch (e: any) {
      setError(e.message ?? 'Could not save')
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[560px] flex flex-col max-h-[90vh]">
        <div className="px-5 py-4 border-b border-gray-100 flex items-start gap-3">
          <div className="flex-1">
            <h2 className="text-base font-semibold text-gray-900">Session reflection</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Rank the hints by how useful they were in this session, most useful first.
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" title="Close">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4 space-y-4">
          <ol className="space-y-1">
            {ranking.map((code, i) => (
              <li key={code} className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-1.5">
                <span className="w-5 text-xs font-semibold text-gray-400 tabular-nums">{i + 1}</span>
                <span className="flex-1 text-sm text-gray-700">{labels[code]}</span>
                <button
                  className="p-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
                  onClick={() => move(i, -1)} disabled={i === 0} title="Move up"
                >
                  <ChevronUp className="w-4 h-4" />
                </button>
                <button
                  className="p-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
                  onClick={() => move(i, 1)} disabled={i === ranking.length - 1} title="Move down"
                >
                  <ChevronDown className="w-4 h-4" />
                </button>
              </li>
            ))}
          </ol>

          <div>
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              What helped or misled you?
            </label>
            <textarea
              className="input w-full mt-1 text-sm"
              rows={4}
              value={comment}
              onChange={e => setComment(e.target.value)}
              placeholder="e.g. the graph view helped separate the two enzymes; list position misled me on abbreviations…"
            />
          </div>
        </div>

        <div className="px-5 py-3 border-t border-gray-100 flex items-center gap-2">
          {error && <span className="text-xs text-red-600">{error}</span>}
          {existing?.submitted_at && !error && (
            <span className="text-xs text-gray-400">Last saved {new Date(existing.submitted_at).toLocaleString()}</span>
          )}
          <div className="ml-auto flex gap-2">
            <button className="btn-secondary text-sm" onClick={onClose} disabled={busy}>Later</button>
            <button className="btn-primary text-sm" onClick={save} disabled={busy}>
              {busy && <Loader2 className="w-4 h-4 animate-spin" />} Save reflection
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
