import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { PenLine, Settings, ArrowRight, PlusCircle, Loader2, Download, ChevronDown, CheckCircle2 } from 'lucide-react'
import * as api from '@/api/client'

export function Home() {
  const navigate = useNavigate()
  const { data: sessions, isLoading, error } = useQuery({
    queryKey: ['sessions'],
    queryFn: api.getSessions,
  })

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      {/* hero */}
      <div className="mb-10">
        <h1 className="text-3xl font-bold mb-1 w-fit bg-gradient-to-r from-cyan-500 via-blue-600 to-fuchsia-500 bg-clip-text text-transparent">
          HELIX
        </h1>
        <p className="text-sm font-medium text-gray-400 mb-2">
          Hybrid Entity Linking with Interactive eXpert-in-the-loop Annotation
        </p>
        <p className="text-gray-500">
          A configurable, human-in-the-loop tool for linking entity mentions to a knowledge base —
          combining hybrid dense + lexical retrieval, grounded LLM re-ranking, and knowledge-graph-aware review.
        </p>
      </div>

      {/* actions */}
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-gray-800">Sessions</h2>
        <button className="btn-primary" onClick={() => navigate('/configure')}>
          <PlusCircle className="w-4 h-4" />
          New session
        </button>
      </div>

      {/* list */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-gray-400 gap-2">
          <Loader2 className="w-5 h-5 animate-spin" /> Loading…
        </div>
      )}
      {error && (
        <div className="card p-6 text-red-600 text-sm">
          Could not load sessions — is the API server running?{' '}
          <code className="text-xs">uvicorn api.main:app --reload --port 8000</code>
        </div>
      )}
      {sessions && sessions.length === 0 && (
        <div className="card p-12 flex flex-col items-center gap-4 text-center">
          <PenLine className="w-10 h-10 text-gray-300" />
          <p className="text-gray-500">No sessions yet. Create one to get started.</p>
          <button className="btn-primary" onClick={() => navigate('/configure')}>
            <PlusCircle className="w-4 h-4" /> New session
          </button>
        </div>
      )}
      {sessions && sessions.length > 0 && (
        <div className="grid gap-3">
          {sessions.map(name => (
            <SessionCard key={name} name={name} />
          ))}
        </div>
      )}
    </div>
  )
}

function SessionCard({ name }: { name: string }) {
  const navigate = useNavigate()
  const { data: progress } = useQuery({
    queryKey: ['progress', name],
    queryFn: () => api.getProgress(name),
    refetchOnMount: 'always',
    staleTime: 0,
  })

  const pct = progress ? Math.round((progress.done / progress.total) * 100) : 0
  const complete = !!progress && progress.total > 0 && progress.done >= progress.total

  return (
    <div className="card p-5 flex items-center gap-4 hover:shadow-md transition-shadow">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="font-semibold text-gray-900 truncate">{name}</p>
          {complete && (
            <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5">
              <CheckCircle2 className="w-3 h-3" /> Complete
            </span>
          )}
        </div>
        {progress ? (
          <div className="mt-2">
            <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
              <span>{progress.done} / {progress.total} annotated</span>
              <span>{pct}%</span>
            </div>
            <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-brand-500 rounded-full transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        ) : (
          <p className="text-xs text-gray-400 mt-1 animate-pulse">Loading progress…</p>
        )}
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <ExportMenu name={name} />
        <button
          className="btn-secondary"
          onClick={() => navigate(`/configure/${name}`)}
          title="Edit configuration"
        >
          <Settings className="w-4 h-4" />
        </button>
        <button
          className="btn-primary"
          onClick={() => navigate(`/annotate/${name}`)}
        >
          Annotate <ArrowRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

function ExportMenu({ name }: { name: string }) {
  const [open, setOpen] = useState(false)
  const { data: stages } = useQuery({
    queryKey: ['snapshots', name],
    queryFn: () => api.listSnapshots(name),
    staleTime: 0,
  })
  const has = (s: string) => (stages ?? []).includes(s)
  const pick = (stage?: string) => { api.exportCSV(name, stage); setOpen(false) }

  const item = 'w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 flex items-center gap-2'
  return (
    <div className="relative">
      <button className="btn-secondary" onClick={() => setOpen(o => !o)} title="Export annotations">
        <Download className="w-4 h-4" /> <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-1 z-20 w-56 bg-white border border-gray-200 rounded-lg shadow-lg py-1">
            <button className={item} onClick={() => pick()}>
              <Download className="w-3.5 h-3.5 text-gray-400" /> Annotations (final)
            </button>
            {has('retrieval') && (
              <button className={item} onClick={() => pick('retrieval')}>
                <Download className="w-3.5 h-3.5 text-gray-400" /> Retrieval — step 1
              </button>
            )}
            {has('graph') && (
              <button className={item} onClick={() => pick('graph')}>
                <Download className="w-3.5 h-3.5 text-violet-400" /> Graph match — step 2
              </button>
            )}
            {!has('retrieval') && !has('graph') && (
              <p className="px-3 py-1 text-[11px] text-gray-400">
                Run Auto-annotate to create per-step versions.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
