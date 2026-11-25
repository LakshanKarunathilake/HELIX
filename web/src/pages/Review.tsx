import { memo, useCallback, useId, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, Gavel, Loader2 } from 'lucide-react'
import * as api from '@/api/client'
import type { AgreementData, ReviewDecision, ReviewRow } from '@/types'

type Filter = 'all' | 'disagree' | 'script' | 'needs'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'disagree', label: 'Disagreements' },
  { id: 'script', label: 'Script-only' },
  { id: 'needs', label: 'Needs review' },
]

const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

/** A review row plus what the filters and the table need, computed once per fetch. */
interface RowView {
  row: ReviewRow
  /** Every decision on the row was written by a script (and there is at least one). */
  scriptOnly: boolean
  needsReview: boolean
  byUser: Map<number, ReviewDecision>
  /** Inputs the adjudication form starts from. */
  prefill: Record<string, string>
}

/**
 * Derives the filter flags for one row.
 *
 * - Disagreements: the server's `disagree` (two or more humans chose different values).
 * - Script-only: at least one decision, and every decision has `script: true`.
 * - Needs review: no adjudication, and (`disagree`, or fewer than two non-script decisions,
 *   which includes script-only rows). Each decision is one user's latest, so the non-script
 *   count is the number of distinct humans. This matches the `_needs_review` column of the
 *   final export for every case except two humans who agree on some KBs but not others,
 *   which `disagree` already covers.
 *
 * Adjudication prefill: the current adjudication; else the agreed value (all humans match,
 * which is the first human's value); else the first human decision's value; else, for a
 * script-only row, the first decision's value. Missing KBs start empty.
 */
function toView(row: ReviewRow, kbNames: string[]): RowView {
  const humans = row.decisions.filter(d => !d.script)
  const scriptOnly = row.decisions.length > 0 && humans.length === 0
  const source = row.adjudication?.kb_values ?? humans[0]?.kb_values ?? row.decisions[0]?.kb_values ?? {}
  return {
    row,
    scriptOnly,
    needsReview: !row.adjudication && (row.disagree || humans.length < 2),
    byUser: new Map(row.decisions.map(d => [d.user_id, d])),
    prefill: Object.fromEntries(kbNames.map(kb => [kb, source[kb] ?? ''])),
  }
}

const matches: Record<Filter, (v: RowView) => boolean> = {
  all: () => true,
  disagree: v => v.row.disagree,
  script: v => v.scriptOnly,
  needs: v => v.needsReview,
}

const pct = (v: number | null) => (v === null ? 'n/a' : `${v.toFixed(1)}%`)
const kappa = (v: number | null) => (v === null ? 'n/a' : v.toFixed(2))

function Loading() {
  return (
    <div className="flex items-center justify-center py-16 text-gray-400 gap-2 text-sm">
      <Loader2 className="w-4 h-4 animate-spin" /> Loading…
    </div>
  )
}

function AgreementStrip({ agreement, kbNames, names }: {
  agreement: { data?: AgreementData; error: unknown; isLoading: boolean }
  kbNames: string[]
  names: Map<number, string>
}) {
  const who = (id: number) => names.get(id) ?? `user ${id}`
  return (
    <section className="card p-5 mb-5" aria-label="Agreement">
      <h2 className="text-sm font-semibold text-gray-800 mb-3">Agreement between annotators</h2>
      {agreement.isLoading && <Loading />}
      {!!agreement.error && (
        <p className="text-sm text-red-600" role="alert">
          Could not load agreement: {errorText(agreement.error)}
        </p>
      )}
      {agreement.data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {kbNames.map(kb => {
              const pairs = agreement.data?.by_kb[kb] ?? []
              return (
                <div key={kb}>
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">{kb}</p>
                  {pairs.length === 0 && (
                    <p className="text-sm text-gray-400">Needs two annotators with decisions.</p>
                  )}
                  <ul className="text-sm text-gray-700 space-y-0.5">
                    {pairs.map(p => (
                      <li key={`${p.a}-${p.b}`}>
                        {who(p.a)} and {who(p.b)}:{' '}
                        <span className="font-medium">{pct(p.percent)}</span> agreement,
                        {' '}kappa <span className="font-medium">{kappa(p.kappa)}</span>
                        <span className="text-gray-400"> (n={p.n})</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )
            })}
          </div>
          <p className="text-xs text-gray-500 mt-3">
            {agreement.data.excluded_script_rows} script-written rows excluded
          </p>
        </>
      )}
    </section>
  )
}

function ScriptBadge() {
  return (
    <span className="ml-1.5 text-[10px] font-semibold uppercase tracking-wide border rounded-full px-1.5 py-0.5 bg-violet-50 text-violet-700 border-violet-200">
      script
    </span>
  )
}

/** One input per KB (the server rejects partial adjudications) plus an optional note. */
function AdjudicationForm({ kbNames, prefill, saving, onSave, onCancel }: {
  kbNames: string[]
  prefill: Record<string, string>
  saving: boolean
  onSave: (kbValues: Record<string, string>, note: string) => Promise<unknown>
  onCancel: () => void
}) {
  const [values, setValues] = useState(prefill)
  const [note, setNote] = useState('')
  const uid = useId()

  const submit = (e: FormEvent) => {
    e.preventDefault()
    // A failed save drops the typed values back to what the server holds; the row shows the error.
    onSave(values, note.trim()).then(onCancel, () => { setValues(prefill); setNote('') })
  }

  return (
    <form onSubmit={submit} className="grid gap-2 sm:grid-cols-2 max-w-2xl">
      {kbNames.map(kb => (
        <div key={kb}>
          <label className="label !text-xs" htmlFor={`${uid}-${kb}`}>{kb}</label>
          <input
            id={`${uid}-${kb}`}
            className="input"
            value={values[kb] ?? ''}
            disabled={saving}
            onChange={e => setValues(prev => ({ ...prev, [kb]: e.target.value }))}
          />
        </div>
      ))}
      <div className="sm:col-span-2">
        <label className="label !text-xs" htmlFor={`${uid}-note`}>Note (optional)</label>
        <input
          id={`${uid}-note`}
          className="input"
          value={note}
          disabled={saving}
          onChange={e => setNote(e.target.value)}
        />
      </div>
      <div className="sm:col-span-2 flex gap-2">
        <button className="btn-primary !py-1" type="submit" disabled={saving}>
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />} Save adjudication
        </button>
        <button className="btn-secondary !py-1" type="button" disabled={saving} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

interface RowProps {
  view: RowView
  kbNames: string[]
  userIds: number[]
  saving: boolean
  onSave: (idx: number, kbValues: Record<string, string>, note: string) => Promise<unknown>
}

const ReviewTableRow = memo(function ReviewTableRow({ view, kbNames, userIds, saving, onSave }: RowProps) {
  const { row } = view
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const adj = row.adjudication
  const highlight = row.disagree && !adj

  const save = useCallback(
    (kbValues: Record<string, string>, note: string) =>
      onSave(row.idx, kbValues, note).then(
        () => setError(''),
        (e: unknown) => { setError(errorText(e)); throw e },
      ),
    [onSave, row.idx],
  )

  return (
    <tr className={`align-top ${highlight ? 'bg-red-50' : ''}`} data-disagree={highlight || undefined}>
      <td className="px-4 py-2 text-gray-400 tabular-nums">{row.idx + 1}</td>
      <td className="px-3 py-2 min-w-[12rem]">
        <p className="text-gray-900 font-medium">{row.label}</p>
        {highlight && (
          <span className="text-[10px] font-semibold uppercase tracking-wide text-red-700">Disagreement</span>
        )}
        {adj && (
          <div className="mt-1 text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-md px-2 py-1">
            <p className="font-semibold">Adjudicated by {adj.reviewer_name}</p>
            {kbNames.map(kb => <p key={kb}>{kbNames.length > 1 && `${kb}: `}{adj.kb_values[kb] || '—'}</p>)}
            {adj.note && <p className="italic">{adj.note}</p>}
          </div>
        )}
      </td>
      {userIds.map(id => {
        const d = view.byUser.get(id)
        return (
          <td key={id} className="px-3 py-2 text-gray-700">
            {d ? (
              <>
                {kbNames.map(kb => (
                  <p key={kb}>
                    {kbNames.length > 1 && <span className="text-gray-400">{kb}: </span>}
                    {d.kb_values[kb] || '—'}
                  </p>
                ))}
                {d.script && <ScriptBadge />}
              </>
            ) : <span className="text-gray-300">—</span>}
          </td>
        )
      })}
      <td className="px-4 py-2 min-w-[8rem]">
        {open ? (
          <AdjudicationForm
            // A refetch that changes what the server holds restarts the form from that state.
            key={JSON.stringify(view.prefill)}
            kbNames={kbNames}
            prefill={view.prefill}
            saving={saving}
            onSave={save}
            onCancel={() => setOpen(false)}
          />
        ) : (
          <button className="btn-secondary !py-1" onClick={() => { setError(''); setOpen(true) }}>
            <Gavel className="w-3.5 h-3.5" /> {adj ? 'Re-adjudicate' : 'Adjudicate'}
          </button>
        )}
        {error && <p className="text-xs text-red-600 mt-1" role="alert">{error}</p>}
      </td>
    </tr>
  )
})

export function Review() {
  const { sessionName = '' } = useParams<{ sessionName: string }>()
  const qc = useQueryClient()
  const [filter, setFilter] = useState<Filter>('all')

  const review = useQuery({
    queryKey: ['review', sessionName],
    queryFn: () => api.getReview(sessionName),
    enabled: !!sessionName,
  })
  const agreement = useQuery({
    queryKey: ['agreement', sessionName],
    queryFn: () => api.getAgreement(sessionName),
    enabled: !!sessionName,
  })

  const save = useMutation({
    mutationFn: (v: { row_idx: number; kb_values: Record<string, string>; note: string }) =>
      api.postAdjudication(sessionName, v),
    // Success or failure, show what the server holds (no optimistic value is ever kept).
    onSettled: () => Promise.all([
      qc.invalidateQueries({ queryKey: ['review', sessionName] }),
      qc.invalidateQueries({ queryKey: ['agreement', sessionName] }),
    ]),
  })
  const { mutateAsync } = save
  const onSave = useCallback(
    (idx: number, kbValues: Record<string, string>, note: string) =>
      mutateAsync({ row_idx: idx, kb_values: kbValues, note }),
    [mutateAsync],
  )

  const kbNames = review.data?.kb_names
  const views = useMemo(
    () => (review.data ? review.data.rows.map(r => toView(r, review.data.kb_names)) : []),
    [review.data],
  )
  // One column per user who appears in the decisions, by name.
  const { users, names } = useMemo(() => {
    const byId = new Map<number, string>()
    for (const v of views) for (const d of v.row.decisions) byId.set(d.user_id, d.user_name)
    const sorted = [...byId.entries()].sort((x, y) => x[1].localeCompare(y[1]) || x[0] - y[0])
    return { users: sorted.map(([id]) => id), names: byId }
  }, [views])
  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: 0, disagree: 0, script: 0, needs: 0 }
    for (const v of views) for (const f of FILTERS) if (matches[f.id](v)) c[f.id] += 1
    return c
  }, [views])
  const shown = useMemo(() => views.filter(matches[filter]), [views, filter])

  const savingIdx = save.isPending ? save.variables?.row_idx : undefined

  return (
    <div className="max-w-6xl mx-auto px-6 py-10">
      <Link to="/" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-brand-600 mb-3">
        <ArrowLeft className="w-4 h-4" /> Sessions
      </Link>
      <div className="flex items-center justify-between gap-4 mb-6">
        <h1 className="text-2xl font-bold text-gray-900 truncate">Review: {sessionName}</h1>
        <a className="btn-secondary shrink-0" href={api.finalExportUrl(sessionName)}>
          <Download className="w-4 h-4" /> Export final
        </a>
      </div>

      {review.isLoading && <Loading />}
      {review.isError && (
        <div className="card p-4 text-sm text-red-600" role="alert">
          Could not load the review: {errorText(review.error)}
        </div>
      )}

      {kbNames && (
        <>
          <AgreementStrip agreement={agreement} kbNames={kbNames} names={names} />

          {views.length === 0 ? (
            <div className="card p-10 text-center text-sm text-gray-500">
              No decisions logged for this session yet.
            </div>
          ) : (
            <>
              <div className="flex flex-wrap gap-2 mb-3" role="group" aria-label="Filter rows">
                {FILTERS.map(f => (
                  <button
                    key={f.id}
                    aria-pressed={filter === f.id}
                    onClick={() => setFilter(f.id)}
                    className={`text-sm rounded-full border px-3 py-1 transition-colors ${
                      filter === f.id
                        ? 'bg-brand-600 border-brand-600 text-white'
                        : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
                    }`}
                  >
                    {f.label} <span className="opacity-70">{counts[f.id]}</span>
                  </button>
                ))}
              </div>

              <div className="card overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-gray-500 bg-gray-50">
                    <tr>
                      <th className="px-4 py-2 font-medium">#</th>
                      <th className="px-3 py-2 font-medium">Mention</th>
                      {users.map(id => (
                        <th key={id} className="px-3 py-2 font-medium">{names.get(id)}</th>
                      ))}
                      <th className="px-4 py-2 font-medium">Adjudication</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {shown.map(v => (
                      <ReviewTableRow
                        key={v.row.idx}
                        view={v}
                        kbNames={kbNames}
                        userIds={users}
                        saving={savingIdx === v.row.idx}
                        onSave={onSave}
                      />
                    ))}
                  </tbody>
                </table>
                {shown.length === 0 && (
                  <p className="p-6 text-center text-sm text-gray-500">No rows match this filter.</p>
                )}
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
