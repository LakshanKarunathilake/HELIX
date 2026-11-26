import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ChevronLeft, ChevronRight, Download, Loader2 } from 'lucide-react'
import * as api from '@/api/client'
import { AgreementPanel } from '@/components/review/AgreementPanel'
import { DecisionCard } from '@/components/review/DecisionCard'
import { PAGE_SIZE, RowList } from '@/components/review/RowList'
import type { BulkResult, BulkSkipReason, ReviewRow } from '@/types'

type Filter = 'to_decide' | 'agreed' | 'decided' | 'all'

const FILTERS: { id: Filter; label: string; matches: (r: ReviewRow) => boolean }[] = [
  { id: 'to_decide', label: 'To decide',
    matches: r => r.status === 'disagree' || r.status === 'single' || r.status === 'changed' },
  { id: 'agreed', label: 'Agreed', matches: r => r.status === 'agreed' },
  { id: 'decided', label: 'Decided', matches: r => r.status === 'decided' },
  { id: 'all', label: 'All', matches: () => true },
]

const SKIP_REASON: Record<BulkSkipReason, string> = {
  changed: 'its answers changed after you ticked it',
  not_agreed: 'it is no longer an agreed row waiting for review',
  pending: 'an annotator has not finished it yet',
  out_of_range: 'the row no longer exists',
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

/** The rows being stepped through: the filtered order when a row was opened, frozen
 *  so that deciding a row (which moves it to another filter) does not reshuffle it. */
interface OpenState {
  queue: number[]
  idx: number
}

export function Review() {
  const { sessionName = '' } = useParams<{ sessionName: string }>()
  const qc = useQueryClient()
  const [filter, setFilter] = useState<Filter>('to_decide')
  const [page, setPage] = useState(0)
  /** Row idx -> the row's version when it was ticked. */
  const [selected, setSelected] = useState<Map<number, string>>(() => new Map())
  const [bulkResult, setBulkResult] = useState<BulkResult | null>(null)
  const [bulkError, setBulkError] = useState('')
  const [open, setOpen] = useState<OpenState | null>(null)
  /** Kept here, not in the card, so it survives the card restarting on a new version. */
  const [saveError, setSaveError] = useState('')

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

  // Success or failure, show what the server holds now.
  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['review', sessionName] }),
    qc.invalidateQueries({ queryKey: ['agreement', sessionName] }),
    qc.invalidateQueries({ queryKey: ['review-summary', sessionName] }),
  ])
  const save = useMutation({
    mutationFn: (v: { row_idx: number; kb_values: Record<string, string>; note: string; version: string }) =>
      api.postAdjudication(sessionName, v),
    onSettled: refresh,
  })
  const bulk = useMutation({
    mutationFn: (rows: { row_idx: number; version: string }[]) =>
      api.postBulkAdjudications(sessionName, rows),
    onSettled: refresh,
  })

  const data = review.data
  const kbNames = data?.kb_names
  const rowsByIdx = useMemo(() => new Map((data?.rows ?? []).map(r => [r.idx, r])), [data])
  const names = useMemo(() => {
    const m = new Map<number, string>()
    for (const r of data?.rows ?? []) for (const a of r.answers) m.set(a.user_id, a.user_name)
    return m
  }, [data])
  const counts = useMemo(() => Object.fromEntries(FILTERS.map(f =>
    [f.id, (data?.rows ?? []).filter(f.matches).length])) as Record<Filter, number>, [data])
  const shown = useMemo(
    () => (data?.rows ?? []).filter(FILTERS.find(f => f.id === filter)!.matches),
    [data, filter],
  )
  // A refetch can shrink the list under the current page.
  const pageNow = Math.min(page, Math.max(0, Math.ceil(shown.length / PAGE_SIZE) - 1))

  useEffect(() => { window.scrollTo(0, 0) }, [open?.idx])

  const changeFilter = (f: Filter) => {
    setFilter(f)
    setPage(0)
    setSelected(new Map())
    setBulkResult(null)
    setBulkError('')
  }
  const changePage = (p: number) => {
    setPage(p)
    setSelected(new Map())
  }

  const openRow = (idx: number) => {
    setSaveError('')
    setOpen({ queue: shown.map(r => r.idx), idx })
  }
  const step = (delta: number) => {
    if (!open) return
    const next = open.queue[open.queue.indexOf(open.idx) + delta]
    if (next === undefined) return
    setSaveError('')
    setOpen({ ...open, idx: next })
  }
  const backToList = () => {
    setSaveError('')
    setOpen(null)
  }

  const decide = (row: ReviewRow, kbValues: Record<string, string>, note: string) => {
    setSaveError('')
    save.mutate(
      { row_idx: row.idx, kb_values: kbValues, note, version: row.version },
      {
        // Next row of the frozen queue; after the last, back to the list.
        onSuccess: () => setOpen(prev => {
          if (!prev || prev.idx !== row.idx) return prev
          const next = prev.queue[prev.queue.indexOf(row.idx) + 1]
          return next === undefined ? null : { ...prev, idx: next }
        }),
        onError: e => setSaveError(errorText(e)),
      },
    )
  }

  const confirmSelected = () => {
    setBulkResult(null)
    setBulkError('')
    bulk.mutate(
      [...selected].map(([row_idx, version]) => ({ row_idx, version })),
      {
        onSuccess: result => {
          setBulkResult(result)
          setSelected(new Map())
        },
        onError: e => setBulkError(errorText(e)),
      },
    )
  }
  const toggleRow = (row: ReviewRow, ticked: boolean) => setSelected(prev => {
    const next = new Map(prev)
    if (ticked) next.set(row.idx, row.version)
    else next.delete(row.idx)
    return next
  })
  const selectPage = (rows: ReviewRow[], ticked: boolean) => setSelected(prev => {
    const next = new Map(prev)
    for (const r of rows) {
      if (ticked) { if (!next.has(r.idx)) next.set(r.idx, r.version) }
      else next.delete(r.idx)
    }
    return next
  })

  const openRowData = open ? rowsByIdx.get(open.idx) : undefined
  const position = open ? open.queue.indexOf(open.idx) : -1

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 sm:py-10">
      <Link to="/" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-brand-600 mb-3">
        <ArrowLeft className="w-4 h-4" /> Sessions
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <h1 className="text-2xl font-bold text-gray-900 break-words min-w-0">Review: {sessionName}</h1>
        <a className="btn-secondary shrink-0" href={api.finalExportUrl(sessionName)}>
          <Download className="w-4 h-4" /> Export final
        </a>
      </div>

      {review.isLoading && (
        <div className="flex items-center justify-center py-16 text-gray-400 gap-2 text-sm">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </div>
      )}
      {review.isError && (
        <div className="card p-4 text-sm text-red-600 mb-4" role="alert">
          Could not load the review: {errorText(review.error)}
        </div>
      )}

      {data && kbNames && !open && (
        <>
          <AgreementPanel
            data={agreement.data}
            error={agreement.error}
            isLoading={agreement.isLoading}
            kbNames={kbNames}
            names={names}
          />

          <div className="flex flex-wrap items-center gap-2 mb-3">
            <div className="flex flex-wrap gap-2" role="group" aria-label="Filter rows">
              {FILTERS.map(f => (
                <button
                  key={f.id}
                  type="button"
                  aria-pressed={filter === f.id}
                  onClick={() => changeFilter(f.id)}
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
            {data.counts.waiting > 0 && (
              <span className="text-sm text-gray-500">
                {data.counts.waiting} row{data.counts.waiting === 1 ? '' : 's'} waiting for annotators
              </span>
            )}
          </div>

          {bulkError && (
            <p className="card p-3 mb-3 text-sm text-red-600" role="alert">{bulkError}</p>
          )}
          {bulkResult && (
            <div className="card p-3 mb-3 text-sm text-gray-700" role="status">
              <p>
                Confirmed {bulkResult.confirmed.length} row{bulkResult.confirmed.length === 1 ? '' : 's'}.
                {bulkResult.skipped.length > 0 && ` Skipped ${bulkResult.skipped.length}:`}
              </p>
              {bulkResult.skipped.length > 0 && (
                <ul className="mt-1 list-disc pl-5 text-gray-600">
                  {bulkResult.skipped.map(s => (
                    <li key={s.row_idx}>Row {s.row_idx + 1}: {SKIP_REASON[s.reason] ?? s.reason}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <RowList
            rows={shown}
            page={pageNow}
            onPage={changePage}
            kbNames={kbNames}
            session={sessionName}
            onOpen={openRow}
            bulk={filter === 'agreed' ? {
              selected,
              onToggle: toggleRow,
              onSelectPage: selectPage,
              onConfirm: confirmSelected,
              busy: bulk.isPending,
            } : undefined}
          />
        </>
      )}

      {data && kbNames && open && (
        <>
          <nav className="flex flex-wrap items-center gap-2 mb-4" aria-label="Rows in this queue">
            <button type="button" className="btn-secondary !py-1" disabled={save.isPending} onClick={backToList}>
              <ArrowLeft className="w-4 h-4" /> Back to list
            </button>
            <button
              type="button"
              className="btn-secondary !py-1"
              disabled={save.isPending || position <= 0}
              onClick={() => step(-1)}
            >
              <ChevronLeft className="w-4 h-4" /> Previous
            </button>
            <button
              type="button"
              className="btn-secondary !py-1"
              disabled={save.isPending || position >= open.queue.length - 1}
              onClick={() => step(1)}
            >
              Next <ChevronRight className="w-4 h-4" />
            </button>
            <span className="text-sm text-gray-500">{position + 1} of {open.queue.length} in this queue</span>
          </nav>

          {saveError && (
            <p className="card p-3 mb-4 text-sm text-red-600" role="alert">{saveError}</p>
          )}

          {openRowData ? (
            <DecisionCard
              key={`${openRowData.idx}-${openRowData.version}`}
              session={sessionName}
              row={openRowData}
              kbNames={kbNames}
              saving={save.isPending}
              onSave={(kbValues, note) => decide(openRowData, kbValues, note)}
            />
          ) : (
            <div className="card p-6 text-sm text-gray-600">
              Row {open.idx + 1} is no longer listed: no annotator has answered it.
            </div>
          )}
        </>
      )}
    </div>
  )
}
