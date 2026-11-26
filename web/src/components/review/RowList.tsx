import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react'
import type { ReviewRow, ReviewStatus } from '@/types'
import { ConceptName } from './ConceptName'

export const PAGE_SIZE = 50

export const STATUS_LABEL: Record<ReviewStatus, string> = {
  disagree: 'Disagree',
  single: 'One annotator',
  agreed: 'Agreed',
  changed: 'Changed',
  decided: 'Decided',
}

export function StatusBadge({ status }: { status: ReviewStatus }) {
  return (
    <span className="text-[10px] font-semibold uppercase tracking-wide border rounded-full px-1.5 py-0.5 bg-gray-50 text-gray-600 border-gray-200">
      {STATUS_LABEL[status]}
    </span>
  )
}

/** Each annotator's concept names for one row, one line per annotator. */
function Answers({ row, kbNames, session }: { row: ReviewRow; kbNames: string[]; session: string }) {
  if (row.answers.length === 0) return <p className="text-xs text-gray-400">No annotator answers</p>
  return (
    <ul className="text-xs text-gray-600 space-y-0.5">
      {row.answers.map(a => (
        <li key={a.user_id} className="break-words">
          <span className="font-medium text-gray-700">{a.user_name}:</span>{' '}
          {kbNames.map((kb, k) => {
            const ids = a.kb_ids[kb] ?? []
            return (
              <span key={kb}>
                {k > 0 && '; '}
                {kbNames.length > 1 && <span className="text-gray-400">{kb} </span>}
                {ids.length === 0 ? '—' : ids.map((id, i) => (
                  <span key={id}>{i > 0 && ', '}<ConceptName session={session} kb={kb} id={id} /></span>
                ))}
              </span>
            )
          })}
        </li>
      ))}
    </ul>
  )
}

/**
 * One page of rows. In the Agreed view, eligible rows get a checkbox for bulk confirm;
 * the selection belongs to the page, which clears it on filter or page change.
 */
export function RowList({
  rows, page, onPage, kbNames, session, onOpen, bulk,
}: {
  /** Every row of the current filter; only `page` of them is rendered. */
  rows: ReviewRow[]
  page: number
  onPage: (page: number) => void
  kbNames: string[]
  session: string
  onOpen: (idx: number) => void
  /** Present in the Agreed view only. */
  bulk?: {
    selected: Map<number, string>
    onToggle: (row: ReviewRow, ticked: boolean) => void
    onSelectPage: (rows: ReviewRow[], ticked: boolean) => void
    onConfirm: () => void
    busy: boolean
  }
}) {
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const shown = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const eligible = shown.filter(r => r.bulk_eligible)
  const allTicked = !!bulk && eligible.length > 0 && eligible.every(r => bulk.selected.has(r.idx))

  return (
    <div className="card">
      {bulk && (
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-gray-100">
          <label className="inline-flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={allTicked}
              disabled={eligible.length === 0 || bulk.busy}
              onChange={e => bulk.onSelectPage(eligible, e.target.checked)}
            />
            Select all on this page
          </label>
          <button
            type="button"
            className="btn-primary !py-1"
            disabled={bulk.selected.size === 0 || bulk.busy}
            onClick={bulk.onConfirm}
          >
            {bulk.busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Confirm selected ({bulk.selected.size})
          </button>
        </div>
      )}

      {rows.length === 0 ? (
        <p className="p-6 text-center text-sm text-gray-500">No rows here.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {shown.map(row => (
            <li key={row.idx} className="flex items-start gap-3 px-4 py-3">
              {bulk && (
                <div className="pt-1 w-4 shrink-0">
                  {row.bulk_eligible && (
                    <input
                      type="checkbox"
                      aria-label={`Select row ${row.idx + 1} for bulk confirm`}
                      checked={bulk.selected.has(row.idx)}
                      disabled={bulk.busy}
                      onChange={e => bulk.onToggle(row, e.target.checked)}
                    />
                  )}
                </div>
              )}
              <button
                type="button"
                className="flex-1 min-w-0 text-left rounded-md hover:bg-gray-50 -m-1 p-1"
                aria-label={`Open row ${row.idx + 1}: ${row.label}`}
                onClick={() => onOpen(row.idx)}
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-1">
                  <span className="text-gray-400 tabular-nums text-sm">#{row.idx + 1}</span>
                  <span className="font-medium text-gray-900 break-words">{row.label}</span>
                  {row.doc_id && <span className="text-xs text-gray-400">doc {row.doc_id}</span>}
                  <StatusBadge status={row.status} />
                </div>
                <Answers row={row} kbNames={kbNames} session={session} />
                {bulk && !row.bulk_eligible && (
                  <p className="text-xs text-gray-500 mt-1">
                    {row.pending > 0
                      ? `${row.pending} annotator${row.pending === 1 ? '' : 's'} pending`
                      : 'Answer incomplete'}
                  </p>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      {pages > 1 && (
        <nav className="flex items-center justify-between gap-2 px-4 py-3 border-t border-gray-100" aria-label="Pages">
          <button
            type="button"
            className="btn-secondary !py-1"
            disabled={page === 0}
            onClick={() => onPage(page - 1)}
          >
            <ChevronLeft className="w-4 h-4" /> Previous page
          </button>
          <span className="text-sm text-gray-500">Page {page + 1} of {pages}</span>
          <button
            type="button"
            className="btn-secondary !py-1"
            disabled={page >= pages - 1}
            onClick={() => onPage(page + 1)}
          >
            Next page <ChevronRight className="w-4 h-4" />
          </button>
        </nav>
      )}
    </div>
  )
}
