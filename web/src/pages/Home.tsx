import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { PenLine, Settings, Trash2, ArrowRight, PlusCircle, Loader2 } from 'lucide-react'
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
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Entity Linking Annotator</h1>
        <p className="text-gray-500">
          Human-in-the-loop tool for linking entity mentions to FAISS-backed knowledge bases.
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

  return (
    <div className="card p-5 flex items-center gap-4 hover:shadow-md transition-shadow">
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-gray-900 truncate">{name}</p>
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
