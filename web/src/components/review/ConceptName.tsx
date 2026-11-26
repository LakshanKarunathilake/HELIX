import { useQuery } from '@tanstack/react-query'
import * as api from '@/api/client'

/** The KB lookup for one id, shared through the cache by every place that names it. */
function useConcept(session: string, kb: string, id: string) {
  return useQuery({
    queryKey: ['entity', session, kb, id],
    queryFn: () => api.lookupEntity(session, kb, id),
    enabled: id !== 'NIL' && !!session,
    staleTime: Infinity,
    retry: false,
  })
}

/**
 * A concept id shown by its name when the KB knows it. `NIL` needs no lookup; an id the
 * KB does not know, or cannot be asked about, is shown as the id. With `warn`, a miss
 * also says so (used for concepts the reviewer typed in).
 */
export function ConceptName({ session, kb, id, warn = false }: {
  session: string
  kb: string
  id: string
  warn?: boolean
}) {
  const q = useConcept(session, kb, id)
  if (id === 'NIL') return <span>NIL (no match)</span>
  const found = q.data?.found ? q.data : null
  return (
    <span>
      {found ? (
        <>
          {found.label} <span className="text-gray-400 text-xs">{id}</span>
        </>
      ) : id}
      {warn && q.data && !q.data.found && (
        <span className="ml-1 text-xs text-amber-700">(not in the index)</span>
      )}
      {warn && q.isError && (
        <span className="ml-1 text-xs text-amber-700">(could not check)</span>
      )}
    </span>
  )
}
