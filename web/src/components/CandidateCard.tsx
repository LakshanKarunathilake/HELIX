import { useState } from 'react'
import { CheckCircle2, ChevronDown, ChevronUp, BookOpen, Info } from 'lucide-react'
import type { Candidate } from '@/types'

interface Props {
  candidate: Candidate
  rank: number
  selected: boolean
  onSelect: (id: string) => void
}

function scoreColor(score: number | null) {
  if (score === null) return 'text-gray-400'
  if (score >= 0.85) return 'text-emerald-600'
  if (score >= 0.65) return 'text-amber-600'
  return 'text-red-500'
}

// Semantic group codes → readable labels
const SEMANTIC_GROUP_LABELS: Record<string, string> = {
  ACTI: 'Activity',
  ANAT: 'Anatomy',
  CHEM: 'Chemical',
  CONC: 'Concept',
  DEVI: 'Device',
  DISO: 'Disorder',
  GENE: 'Gene',
  GEOG: 'Geography',
  LIVB: 'Living Being',
  OBJC: 'Object',
  OCCU: 'Occupation',
  ORGA: 'Organization',
  PHEN: 'Phenomenon',
  PHYS: 'Physiology',
  PROC: 'Procedure',
}

function SemanticGroupBadge({ code }: { code: string }) {
  const label = SEMANTIC_GROUP_LABELS[code] ?? code
  return (
    <span
      className="inline-flex items-center rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-600/20"
      title={label}
    >
      {code}
    </span>
  )
}

function SemanticTypePill({ raw }: { raw: string }) {
  // Format: "T039:Physiologic Function" → show the human-readable part after the colon
  const name = raw.includes(':') ? raw.split(':').slice(1).join(':').trim() : raw.trim()
  return (
    <span className="inline-flex items-center rounded-full bg-violet-50 px-2 py-0.5 text-xs text-violet-700 ring-1 ring-inset ring-violet-600/20">
      {name}
    </span>
  )
}

function DefinitionTooltip({ definition }: { definition: string }) {
  const [open, setOpen] = useState(false)
  const preview = definition.length > 200 ? definition.slice(0, 200) + '…' : definition

  return (
    <div className="relative inline-flex" onClick={e => e.stopPropagation()}>
      <button
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="flex items-center gap-0.5 text-xs text-gray-400 hover:text-brand-600 transition-colors"
        aria-label="Show definition"
      >
        <Info className="w-3.5 h-3.5" />
        <span>def</span>
      </button>

      {open && (
        <div className="absolute bottom-full left-0 mb-2 z-50 w-72 rounded-lg border border-gray-200 bg-white p-3 shadow-lg text-xs text-gray-700 leading-relaxed pointer-events-none">
          <p className="font-semibold text-gray-500 mb-1 uppercase tracking-wide text-[10px]">Definition</p>
          {preview}
          <div className="absolute top-full left-3 -translate-y-0 border-4 border-transparent border-t-gray-200" />
        </div>
      )}
    </div>
  )
}

export function CandidateCard({ candidate, rank, selected, onSelect }: Props) {
  const [showAliases, setShowAliases] = useState(false)

  const others = (candidate.aliases ?? []).filter(a => a !== candidate.label)

  // Parse semantic groups (pipe- or comma-separated codes)
  const semGroups = candidate.semantic_groups
    ? candidate.semantic_groups.split(/[|,]/).map(s => s.trim()).filter(Boolean)
    : []

  // Parse semantic types (pipe- or comma-separated "TID:Name" strings)
  const semTypes = candidate.semantic_types
    ? candidate.semantic_types.split(/[|,]/).map(s => s.trim()).filter(Boolean)
    : []

  const showSemTypes = semTypes.length > 0 && semTypes.length <= 2
  const hasDefinition = !!candidate.definition

  return (
    <div
      onClick={() => onSelect(candidate.id)}
      className={`group relative cursor-pointer rounded-xl border-2 p-4 transition-all duration-150 select-none ${
        selected
          ? 'border-brand-500 bg-brand-50 shadow-md shadow-brand-100'
          : 'border-gray-200 bg-white hover:border-brand-300 hover:shadow-sm'
      }`}
    >
      {/* main row */}
      <div className="flex items-start gap-3">
        {/* rank badge */}
        <span
          className={`mt-0.5 flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-full text-xs font-bold ${
            selected
              ? 'bg-brand-500 text-white'
              : 'bg-gray-100 text-gray-500 group-hover:bg-brand-100 group-hover:text-brand-600'
          }`}
        >
          {selected ? <CheckCircle2 className="w-3.5 h-3.5" /> : rank}
        </span>

        {/* label + id + meta */}
        <div className="flex-1 min-w-0">
          <p className={`font-semibold truncate ${selected ? 'text-brand-900' : 'text-gray-900'}`}>
            {candidate.label || '(no label)'}
          </p>
          <p className="text-xs font-mono text-gray-500 mt-0.5">{candidate.id}</p>

          {candidate.matched_alias && candidate.matched_alias !== candidate.label && (
            <p className="text-xs text-gray-400 mt-1 italic">
              matched via &ldquo;{candidate.matched_alias}&rdquo;
            </p>
          )}

          {/* semantic metadata */}
          {(semGroups.length > 0 || showSemTypes || hasDefinition) && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {semGroups.map(g => <SemanticGroupBadge key={g} code={g} />)}
              {showSemTypes && semTypes.map(t => <SemanticTypePill key={t} raw={t} />)}
              {hasDefinition && <DefinitionTooltip definition={candidate.definition} />}
            </div>
          )}
        </div>

        {/* score */}
        <div className="flex-shrink-0 text-right">
          <span className={`text-sm font-bold tabular-nums ${scoreColor(candidate.score)}`}>
            {candidate.score != null ? candidate.score.toFixed(4) : '—'}
          </span>
          <p className="text-xs text-gray-400 mt-0.5">score</p>
        </div>
      </div>

      {/* synonyms */}
      {others.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-100" onClick={e => e.stopPropagation()}>
          <button
            onClick={() => setShowAliases(v => !v)}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-brand-600 transition-colors"
          >
            <BookOpen className="w-3.5 h-3.5" />
            <span>{others.length} synonym{others.length !== 1 ? 's' : ''}</span>
            {showAliases ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          {showAliases && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {others.map(a => (
                <span key={a} className="text-xs bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">
                  {a}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
