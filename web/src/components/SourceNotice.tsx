import { useLayoutEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getInfo } from '@/api/client'

/** The footer's measured height, for layouts that must leave room for it. Unset without a notice. */
const HEIGHT_VAR = '--notice-h'

/** The deployment's data source attribution, as a footer. Renders nothing without one. */
export function SourceNotice() {
  const { data } = useQuery({ queryKey: ['info'], queryFn: getInfo, staleTime: Infinity })
  // State rather than a ref, so the effect runs once the footer exists.
  const [footer, setFooter] = useState<HTMLElement | null>(null)

  useLayoutEffect(() => {
    if (!footer) return
    const root = document.documentElement
    const publish = () => root.style.setProperty(HEIGHT_VAR, `${footer.getBoundingClientRect().height}px`)
    publish()
    // The text wraps, so the height changes with the window width.
    const observer = new ResizeObserver(publish)
    observer.observe(footer)
    return () => {
      observer.disconnect()
      root.style.removeProperty(HEIGHT_VAR)
    }
  }, [footer])

  if (!data?.notice) return null
  return (
    <footer ref={setFooter} className="border-t border-gray-200 bg-white px-4 py-3 text-center text-xs text-gray-500 break-words">
      {data.notice}
    </footer>
  )
}
