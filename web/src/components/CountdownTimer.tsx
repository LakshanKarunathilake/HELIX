import { useEffect, useState } from 'react'
import { Clock } from 'lucide-react'

interface Props {
  seconds: number
  active: boolean
  onDismiss: () => void
}

export function CountdownTimer({ seconds, active, onDismiss }: Props) {
  const [remaining, setRemaining] = useState(seconds)

  useEffect(() => {
    setRemaining(seconds)
    if (!active) return
    const iv = setInterval(() => {
      setRemaining(prev => {
        if (prev <= 1) { clearInterval(iv); return 0 }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(iv)
  }, [active, seconds])

  if (!active) return null

  const pct = (remaining / seconds) * 100

  return (
    <div className="flex items-start gap-3 bg-brand-50 border border-brand-200 rounded-xl px-4 py-3 mb-4">
      <Clock className="w-4 h-4 text-brand-500 mt-0.5 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <p className="text-sm text-brand-700 font-medium">
            Auto-selected top candidate — change below if needed
          </p>
          <span className="text-sm font-bold text-brand-600 ml-3 tabular-nums">{remaining}s</span>
        </div>
        <div className="mt-2 h-1.5 bg-brand-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-brand-500 rounded-full transition-all duration-1000 ease-linear"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      <button
        onClick={onDismiss}
        className="text-brand-400 hover:text-brand-600 text-xs font-medium flex-shrink-0 mt-0.5"
      >
        dismiss
      </button>
    </div>
  )
}
