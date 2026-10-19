import { useEffect, useState } from 'react'
import { ArrowLeft, Loader2, MessageSquareText } from 'lucide-react'
import type { FeedbackOptions, RowFeedback } from '@/types'

interface Props {
  term: string
  options: FeedbackOptions
  /** Pre-fill when correcting feedback already recorded for this row. */
  initial?: RowFeedback | null
  onSubmit: (hint: string, confidence: string) => Promise<void>
  /** Only offered when editing; a first-time prompt cannot be skipped. */
  onCancel?: () => void
}

/**
 * Two clicks after each saved decision (Annotation Guide Sec. 8): the main hint
 * that decided it, then how sure the annotator is. Shown after the save, so it
 * never counts toward the measured decision time.
 *
 * Keys: 1–9 pick the hint, then 1–3 pick confidence. Backspace goes back.
 */
export function FeedbackPrompt({ term, options, initial, onSubmit, onCancel }: Props) {
  const [hint, setHint] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (confidence: string) => {
    if (!hint || busy) return
    setBusy(true)
    setError('')
    try {
      await onSubmit(hint, confidence)
    } catch (e: any) {
      setError(e.message ?? 'Could not save feedback')
      setBusy(false)
    }
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (busy) return
      if (e.key === 'Escape' && onCancel) { onCancel(); return }
      if (e.key === 'Backspace' && hint) { setHint(null); return }
      const n = Number(e.key)
      if (!Number.isInteger(n) || n < 1) return
      if (!hint && n <= options.hints.length) setHint(options.hints[n - 1].code)
      else if (hint && n <= options.confidence.length) submit(options.confidence[n - 1].code)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [hint, busy, options, onCancel])

  const hintLabel = options.hints.find(h => h.code === hint)?.label

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[520px]">
        <div className="px-5 pt-4 pb-3 border-b border-gray-100">
          <p className="flex items-center gap-2 text-xs font-semibold text-gray-400 uppercase tracking-wide">
            <MessageSquareText className="w-3.5 h-3.5" /> Saved · feedback
          </p>
          <p className="text-sm text-gray-700 mt-1">
            {hint
              ? <>How sure are you about <span className="font-semibold text-gray-900">{term}</span>?</>
              : <>What mainly decided <span className="font-semibold text-gray-900">{term}</span>?</>}
          </p>
        </div>

        <div className="p-3">
          {!hint ? (
            <div className="space-y-1">
              {options.hints.map((h, i) => (
                <button
                  key={h.code}
                  onClick={() => setHint(h.code)}
                  className={`w-full flex items-center gap-3 text-left text-sm rounded-lg px-3 py-2 transition-colors ${
                    initial?.hint === h.code
                      ? 'bg-brand-50 text-brand-800 ring-1 ring-brand-200'
                      : 'hover:bg-gray-50 text-gray-700'
                  }`}
                >
                  <kbd className="w-5 h-5 flex items-center justify-center rounded bg-gray-100 text-[11px] font-semibold text-gray-500">
                    {i + 1}
                  </kbd>
                  {h.label}
                </button>
              ))}
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex gap-2">
                {options.confidence.map((c, i) => (
                  <button
                    key={c.code}
                    disabled={busy}
                    onClick={() => submit(c.code)}
                    className={`flex-1 flex flex-col items-center gap-1 rounded-xl border-2 px-3 py-3 text-sm font-medium transition-colors disabled:opacity-50 ${
                      initial?.confidence === c.code
                        ? 'border-brand-400 bg-brand-50 text-brand-800'
                        : 'border-gray-200 hover:border-brand-300 text-gray-700'
                    }`}
                  >
                    <kbd className="w-5 h-5 flex items-center justify-center rounded bg-gray-100 text-[11px] font-semibold text-gray-500">
                      {i + 1}
                    </kbd>
                    {c.label}
                  </button>
                ))}
              </div>
              <button
                className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700"
                onClick={() => setHint(null)}
                disabled={busy}
              >
                <ArrowLeft className="w-3.5 h-3.5" /> {hintLabel}
              </button>
            </div>
          )}
        </div>

        <div className="px-5 py-2.5 border-t border-gray-100 flex items-center gap-3 text-[11px] text-gray-400">
          {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {error
            ? <span className="text-red-600">{error} — pick again to retry</span>
            : <span>"Guess" and "Unsure" are valid answers — honest beats confident.</span>}
          {onCancel && (
            <button className="ml-auto text-gray-500 hover:text-gray-700" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
