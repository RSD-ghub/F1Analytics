/**
 * Team colours, for the bar beside a driver's name and the ring round their
 * initials. Matched on a fragment of the team name so the feed's variants
 * ("Red Bull Racing", "Oracle Red Bull Racing", "Red Bull") all land on one
 * colour, and older names in the record still get one.
 *
 * Order matters: "racing bulls" must be tried before "red bull".
 */
const TEAMS = [
  ['mercedes', '#27f4d2'],
  ['ferrari', '#e8002d'],
  ['mclaren', '#ff8000'],
  ['racing bulls', '#6c98ff'],
  ['visa cash app', '#6c98ff'],
  ['alphatauri', '#5e8faa'],
  ['toro rosso', '#469bff'],
  ['rb f1', '#6c98ff'],
  ['red bull', '#3671c6'],
  ['aston martin', '#229971'],
  ['racing point', '#f596c8'],
  ['alpine', '#ff87bc'],
  ['renault', '#fff500'],
  ['williams', '#64c4ff'],
  ['haas', '#b6babd'],
  ['audi', '#ff2d00'],
  ['sauber', '#52e252'],
  ['alfa romeo', '#c92d4b'],
  ['cadillac', '#c4c9d1'],
]

const FALLBACK = '#8b7aa8'

export function teamColour(team) {
  if (!team) return FALLBACK
  const name = String(team).toLowerCase()
  const hit = TEAMS.find(([key]) => name.includes(key))
  return hit ? hit[1] : FALLBACK
}

/** "Andrea Kimi Antonelli" → { first: "Andrea Kimi", last: "Antonelli" }. */
export function splitName(full) {
  const parts = String(full || '').trim().split(/\s+/)
  if (parts.length < 2) return { first: '', last: parts[0] || '' }
  return { first: parts.slice(0, -1).join(' '), last: parts[parts.length - 1] }
}

export function initials(full) {
  const { first, last } = splitName(full)
  return ((first[0] || '') + (last[0] || '')).toUpperCase() || '?'
}
