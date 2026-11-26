import { useEffect, useId, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, Loader2, Plus } from 'lucide-react'
import * as api from '@/api/client'
import { ContextCard } from '@/components/ContextCard'
import type { Adjudication, Basis, ReviewRow } from '@/types'
import { ConceptName } from './ConceptName'
import { StatusBadge } from './RowList'

const NIL = 'NIL'

/** How long Accept and Confirm stay disabled after the card shows something new, so a
 *  repeated click meant for the previous screen cannot decide a row nobody looked at. */
const SETTLE_MS = 700

const BASIS_LABEL: Record<Basis, string> = {
  accepted: 'accepted an annotator’s answer',
  combined: 'combined annotators’ concepts',
  override: 'override',
  bulk_agreed: 'confirmed in bulk (annotators agreed)',
  legacy: 'legacy (saved before answers were tracked)',
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

type PerKb = Record<string, string[]>

/** Ticks a fresh card starts from: the reviewer's own decision on a decided row, else
 *  only the concepts every answering annotator chose (the server's `preticked`). */
function initialTicks(row: ReviewRow, kbNames: string[]): PerKb {
  return Object.fromEntries(kbNames.map(kb => [kb,
    row.status === 'decided' && row.decision
      ? [...(row.decision.kb_ids[kb] ?? [])]
      : (row.options[kb] ?? []).filter(o => o.preticked).map(o => o.id),
  ]))
}

/** A decided row's own concepts that no annotator chose: chipped so that unticking
 *  one does not make it vanish, and labelled as coming from the stored decision. */
function decisionExtras(row: ReviewRow, kbNames: string[]): PerKb {
  return Object.fromEntries(kbNames.map(kb => {
    const proposed = new Set((row.options[kb] ?? []).map(o => o.id))
    const own = row.status === 'decided' && row.decision ? row.decision.kb_ids[kb] ?? [] : []
    return [kb, own.filter(id => id !== NIL && !proposed.has(id))]
  }))
}

/** Ids typed into the add field: split on `|`, trimmed, any-case nil as NIL, and NIL
 *  dropped beside a concept (it is never combined with one). */
function parseTyped(text: string): string[] {
  const ids = [...new Set(text.split('|').map(s => s.trim()).filter(Boolean)
    .map(s => (s.toUpperCase() === NIL ? NIL : s)))]
  return ids.length > 1 ? ids.filter(id => id !== NIL) : ids
}

const joinIds = (ids: string[]) => [...new Set(ids)].sort().join('|')

function DecisionSummary({ decision, kbNames, session }: {
  decision: Adjudication
  kbNames: string[]
  session: string
}) {
  return (
    <div className="text-sm text-gray-700 space-y-0.5">
      {kbNames.map(kb => {
        const ids = decision.kb_ids[kb] ?? []
        return (
          <p key={kb} className="break-words">
            {kbNames.length > 1 && <span className="text-gray-400">{kb}: </span>}
            {ids.length === 0 ? '—' : ids.map((id, i) => (
              <span key={id}>{i > 0 && ', '}<ConceptName session={session} kb={kb} id={id} /></span>
            ))}
          </p>
        )
      })}
      <p className="text-xs text-gray-500">
        By {decision.reviewer_name}: {BASIS_LABEL[decision.basis] ?? decision.basis}
      </p>
      {decision.note && <p className="text-xs italic text-gray-600">Note: {decision.note}</p>}
    </div>
  )
}

/**
 * One row to decide: the annotators' answers side by side, each with Accept, and a
 * final answer built from tickable concepts. The page keys it by idx and version, so
 * changed answers start it afresh.
 */
export function DecisionCard({ session, row, kbNames, saving, settleKey, onSave }: {
  session: string
  row: ReviewRow
  kbNames: string[]
  saving: boolean
  /** Changing it (e.g. a save error appearing above the card) restarts the settle period. */
  settleKey?: string
  onSave: (kbValues: Record<string, string>, note: string) => void
}) {
  const uid = useId()
  const [ticks, setTicks] = useState<PerKb>(() => initialTicks(row, kbNames))
  const [fromDecision] = useState<PerKb>(() => decisionExtras(row, kbNames))
  /** Concepts typed into this card. */
  const [added, setAdded] = useState<PerKb>(() => Object.fromEntries(kbNames.map(kb => [kb, []])))
  const [typed, setTyped] = useState<Record<string, string>>({})
  const [note, setNote] = useState('')
  // The card is keyed by row and version, so it mounts afresh for anything new on screen.
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    setSettled(false)
    const t = window.setTimeout(() => setSettled(true), SETTLE_MS)
    return () => window.clearTimeout(t)
  }, [settleKey])

  const context = useQuery({
    queryKey: ['review-row', session, row.idx],
    queryFn: () => api.getReviewRow(session, row.idx),
    staleTime: Infinity,
  })

  const names = useMemo(() => new Map(row.answers.map(a => [a.user_id, a.user_name])), [row.answers])
  const proposed = useMemo(
    () => Object.fromEntries(kbNames.map(kb => [kb, new Set((row.options[kb] ?? []).map(o => o.id))])),
    [row.options, kbNames],
  )

  const hasAnswers = row.answers.length > 0
  const untickedKbs = kbNames.filter(kb => (ticks[kb] ?? []).length === 0)
  const override = kbNames.some(kb => (ticks[kb] ?? []).some(id => !proposed[kb].has(id)))
  const noteMissing = override && !note.trim()
  const canConfirm = hasAnswers && settled && !saving && untickedKbs.length === 0 && !noteMissing

  const toggle = (kb: string, id: string, on: boolean) => setTicks(prev => {
    const cur = prev[kb] ?? []
    if (!on) return { ...prev, [kb]: cur.filter(x => x !== id) }
    if (id === NIL) return { ...prev, [kb]: [NIL] }
    return { ...prev, [kb]: [...cur.filter(x => x !== NIL && x !== id), id] }
  })

  const addTyped = (kb: string) => {
    const ids = parseTyped(typed[kb] ?? '')
    if (ids.length === 0) return
    if (ids[0] === NIL) {
      toggle(kb, NIL, true)
    } else {
      setAdded(prev => {
        const cur = prev[kb] ?? []
        const fresh = ids.filter(id => !proposed[kb].has(id) && !cur.includes(id)
          && !(fromDecision[kb] ?? []).includes(id))
        return { ...prev, [kb]: [...cur, ...fresh] }
      })
      setTicks(prev => {
        const cur = (prev[kb] ?? []).filter(x => x !== NIL)
        return { ...prev, [kb]: [...cur, ...ids.filter(id => !cur.includes(id))] }
      })
    }
    setTyped(prev => ({ ...prev, [kb]: '' }))
  }

  const confirm = () => {
    if (!canConfirm) return
    onSave(Object.fromEntries(kbNames.map(kb => [kb, joinIds(ticks[kb])])), note.trim())
  }

  const ctx = context.data
  const infoEntries = ctx ? Object.entries(ctx.info) : []

  return (
    <div className="space-y-4">
      <section className="card p-4 sm:p-5 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold text-gray-900 break-words">{row.label}</h2>
          <StatusBadge status={row.status} />
        </div>
        <p className="text-xs text-gray-500">
          Row {row.idx + 1}{row.doc_id && <> · document {row.doc_id}</>}
        </p>
        {infoEntries.length > 0 && (
          <dl className="grid gap-x-4 gap-y-1 grid-cols-[auto_1fr] text-sm">
            {infoEntries.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-gray-500">{k}</dt>
                <dd className="text-gray-800 break-words">{v == null ? '' : String(v)}</dd>
              </div>
            ))}
          </dl>
        )}
        {context.isError && (
          <p className="text-sm text-red-600" role="alert">
            Could not load the row's details: {errorText(context.error)}
          </p>
        )}
        {row.pending > 0 && (
          <p className="text-sm text-gray-600">
            {row.pending} annotator{row.pending === 1 ? ' has' : 's have'} not finished this row yet.
          </p>
        )}
        {row.status === 'changed' && row.decision && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 space-y-1">
            <p className="text-sm font-medium text-amber-900">Answers changed since this decision</p>
            <DecisionSummary decision={row.decision} kbNames={kbNames} session={session} />
          </div>
        )}
        {row.status === 'decided' && row.decision && (
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-1">
            <p className="text-sm font-medium text-gray-800">Current decision</p>
            <DecisionSummary decision={row.decision} kbNames={kbNames} session={session} />
          </div>
        )}
      </section>

      {ctx?.context_text && (
        <ContextCard text={ctx.context_text} label={row.label} span={ctx.entity_span} />
      )}

      <section className="card p-4 sm:p-5" aria-label="Annotators' answers">
        <h3 className="text-sm font-semibold text-gray-800 mb-2">Annotators' answers</h3>
        {!hasAnswers ? (
          <p className="text-sm text-gray-600">
            No annotator answer remains for this row. It can be decided once an annotator answers again.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {row.answers.map(a => {
              const missing = kbNames.filter(kb => (a.kb_ids[kb] ?? []).length === 0)
              return (
                <li key={a.user_id} className="py-2 flex flex-wrap items-start gap-3">
                  <div className="flex-1 min-w-[12rem]">
                    <p className="text-sm font-medium text-gray-900">{a.user_name}</p>
                    {kbNames.map(kb => {
                      const ids = a.kb_ids[kb] ?? []
                      return (
                        <p key={kb} className="text-sm text-gray-700 break-words">
                          {kbNames.length > 1 && <span className="text-gray-400">{kb}: </span>}
                          {ids.length === 0 ? <span className="text-gray-400">no value</span> : ids.map((id, i) => (
                            <span key={id}>{i > 0 && ', '}<ConceptName session={session} kb={kb} id={id} /></span>
                          ))}
                        </p>
                      )
                    })}
                    {!a.complete && (
                      <p className="text-xs text-gray-500">No value for {missing.join(', ')}, so it cannot be accepted.</p>
                    )}
                  </div>
                  <button
                    type="button"
                    className="btn-secondary !py-1"
                    aria-label={`Accept ${a.user_name}'s answer`}
                    disabled={saving || !settled || !a.complete}
                    onClick={() => onSave(
                      Object.fromEntries(kbNames.map(kb => [kb, joinIds(a.kb_ids[kb] ?? [])])),
                      note.trim(),
                    )}
                  >
                    <Check className="w-3.5 h-3.5" /> Accept
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {hasAnswers && (
        <section className="card p-4 sm:p-5 space-y-4" aria-label="Final answer">
          <h3 className="text-sm font-semibold text-gray-800">Final answer</h3>
          {kbNames.map(kb => {
            const options = row.options[kb] ?? []
            const chips = [
              ...options.map(o => o.id),
              ...(proposed[kb].has(NIL) ? [] : [NIL]),
              ...(fromDecision[kb] ?? []),
              ...(added[kb] ?? []),
            ]
            const chosenBy = new Map(options.map(o => [o.id, o.user_ids]))
            const ticked = new Set(ticks[kb] ?? [])
            return (
              <fieldset key={kb} className="space-y-2">
                <legend className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">{kb}</legend>
                <div className="flex flex-wrap gap-2">
                  {chips.map(id => {
                    const by = chosenBy.get(id)
                    const isStored = !by && (fromDecision[kb] ?? []).includes(id)
                    const isAdded = !by && id !== NIL && !isStored
                    return (
                      <label
                        key={id}
                        className={`inline-flex items-start gap-2 rounded-lg border px-2.5 py-1.5 text-sm cursor-pointer ${
                          ticked.has(id) ? 'border-brand-600 bg-brand-50' : 'border-gray-300 bg-white'
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          checked={ticked.has(id)}
                          disabled={saving}
                          onChange={e => toggle(kb, id, e.target.checked)}
                        />
                        <span className="min-w-0 break-words">
                          <ConceptName session={session} kb={kb} id={id} warn={isAdded || isStored} />
                          {by && (
                            <span className="block text-xs text-gray-500">
                              chosen by {by.map(u => names.get(u) ?? `user ${u}`).join(', ')}
                            </span>
                          )}
                          {isStored && <span className="block text-xs text-gray-500">from the current decision</span>}
                          {isAdded && <span className="block text-xs text-gray-500">added by you</span>}
                        </span>
                      </label>
                    )
                  })}
                </div>
                <div className="flex gap-2 max-w-md">
                  <label className="sr-only" htmlFor={`${uid}-add-${kb}`}>Concept id to add for {kb}</label>
                  <input
                    id={`${uid}-add-${kb}`}
                    className="input !py-1"
                    placeholder="Concept id"
                    value={typed[kb] ?? ''}
                    disabled={saving}
                    onChange={e => setTyped(prev => ({ ...prev, [kb]: e.target.value }))}
                    onKeyDown={e => {
                      if (e.key === 'Enter') { e.preventDefault(); addTyped(kb) }
                    }}
                  />
                  <button
                    type="button"
                    className="btn-secondary !py-1 shrink-0"
                    aria-label={`Add concept to ${kb}`}
                    disabled={saving || !(typed[kb] ?? '').trim()}
                    onClick={() => addTyped(kb)}
                  >
                    <Plus className="w-3.5 h-3.5" /> Add
                  </button>
                </div>
              </fieldset>
            )
          })}

          <div>
            <label className="label" htmlFor={`${uid}-note`}>Note{override ? '' : ' (optional)'}</label>
            <textarea
              id={`${uid}-note`}
              className="input"
              rows={2}
              value={note}
              disabled={saving}
              aria-describedby={override ? `${uid}-override` : undefined}
              onChange={e => setNote(e.target.value)}
            />
            {override && (
              <p id={`${uid}-override`} className="text-sm text-amber-700 mt-1">
                This is an override. Add a note.
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className="btn-primary" disabled={!canConfirm} onClick={confirm}>
              {saving && <Loader2 className="w-4 h-4 animate-spin" />} Confirm
            </button>
            {untickedKbs.length > 0 && (
              <p className="text-xs text-gray-500">
                Tick a concept or NIL for {untickedKbs.join(', ')}.
              </p>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
