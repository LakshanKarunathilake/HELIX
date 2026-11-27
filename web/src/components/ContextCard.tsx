// ── ContextCard ───────────────────────────────────────────────────────────────

function highlightSpan(text: string, label: string, span: string | null) {
  // Try char-offset span "start-end" first, fall back to substring search
  let start = -1, end = -1
  if (span) {
    const parts = span.split('-').map(Number)
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      start = parts[0]; end = parts[1]
    }
  }
  if ((start < 0 || end <= start) && label) {
    const idx = text.toLowerCase().indexOf(label.toLowerCase())
    if (idx >= 0) { start = idx; end = idx + label.length }
  }
  if (start < 0 || end <= start) return [{ text, highlight: false }]
  return [
    { text: text.slice(0, start), highlight: false },
    { text: text.slice(start, end), highlight: true },
    { text: text.slice(end), highlight: false },
  ].filter(s => s.text)
}

export function ContextCard({ text, label, span }: { text: string; label: string; span: string | null }) {
  const segments = highlightSpan(text, label, span)
  return (
    <div className="card p-4 space-y-2">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Document context</p>
      <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap break-words">
        {segments.map((seg, i) =>
          seg.highlight
            ? <mark key={i} className="bg-yellow-200 text-yellow-900 rounded px-0.5">{seg.text}</mark>
            : <span key={i}>{seg.text}</span>
        )}
      </p>
      {span && <p className="text-xs text-gray-400">span: {span}</p>}
    </div>
  )
}
