/**
 * Parsing for the KB's semantic metadata strings.
 *
 * Semantic types look like "T116:Amino Acid, Peptide, or Protein||T126:Enzyme".
 * Type names can themselves contain commas, so only pipes separate types; a comma
 * (or semicolon) counts as a separator only when the next part starts with a type id,
 * as in "T047:Disease or Syndrome, T191:Neoplastic Process".
 */
export function parseSemanticTypes(raw?: string | null): string[] {
  if (!raw) return []
  const parts = raw.split(/\|+|[,;]\s*(?=T\d{3}\s*:)/).map(s => s.trim()).filter(Boolean)
  return [...new Set(parts)]
}

/** "T126:Enzyme" -> "Enzyme"; a bare name is returned as is. */
export function semanticTypeName(raw: string): string {
  return raw.includes(':') ? raw.split(':').slice(1).join(':').trim() : raw.trim()
}

/** Semantic-group codes: "CHEM", "CHEM|DISO" or "CHEM,DISO". Codes never contain commas. */
export function parseSemanticGroups(raw?: string | null): string[] {
  if (!raw) return []
  return [...new Set(raw.split(/[|,]/).map(s => s.trim()).filter(Boolean))]
}
