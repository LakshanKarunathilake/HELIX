import { useQuery } from '@tanstack/react-query'
import { getInfo } from '@/api/client'

/** The deployment's data source attribution, as a footer. Renders nothing without one. */
export function SourceNotice() {
  const { data } = useQuery({ queryKey: ['info'], queryFn: getInfo, staleTime: Infinity })
  if (!data?.notice) return null
  return (
    <footer className="border-t border-gray-200 bg-white px-4 py-3 text-center text-xs text-gray-500 break-words">
      {data.notice}
    </footer>
  )
}
