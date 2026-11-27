import { Loader2 } from 'lucide-react'
import type { AgreementData } from '@/types'

const pct = (v: number | null) => (v === null ? 'n/a' : `${v.toFixed(1)}%`)
const kappa = (v: number | null) => (v === null ? 'n/a' : v.toFixed(2))
const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

/** Agreement between each pair of annotators, per KB, over human answers only. */
export function AgreementPanel({ data, error, isLoading, kbNames, names }: {
  data?: AgreementData
  error: unknown
  isLoading: boolean
  kbNames: string[]
  names: Map<number, string>
}) {
  const who = (id: number) => names.get(id) ?? `user ${id}`
  return (
    <section className="card p-4 sm:p-5 mb-5" aria-label="Agreement">
      <h2 className="text-sm font-semibold text-gray-800 mb-3">Agreement between annotators</h2>
      {isLoading && (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </p>
      )}
      {!!error && (
        <p className="text-sm text-red-600" role="alert">
          Could not load agreement: {errorText(error)}
        </p>
      )}
      {data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {kbNames.map(kb => {
              const pairs = data.by_kb[kb] ?? []
              return (
                <div key={kb}>
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">{kb}</p>
                  {pairs.length === 0 && (
                    <p className="text-sm text-gray-400">Needs two annotators with answers.</p>
                  )}
                  <ul className="text-sm text-gray-700 space-y-1">
                    {pairs.map(p => (
                      <li key={`${p.a}-${p.b}`}>
                        <span className="font-medium">{who(p.a)}</span> and{' '}
                        <span className="font-medium">{who(p.b)}</span>:{' '}
                        exact {pct(p.percent)}, kappa {kappa(p.kappa)}, overlap {pct(p.overlap)}
                        <span className="text-gray-400"> (n={p.n})</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )
            })}
          </div>
          <p className="text-xs text-gray-500 mt-3">
            {data.excluded_script_rows} script-written answers excluded
          </p>
        </>
      )}
    </section>
  )
}
