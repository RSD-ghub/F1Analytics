/**
 * How often the page re-reads itself. Only during the race weekend: a forecast
 * locks after qualifying and again 45 minutes before the start, and a reader
 * who left the page open should see the new one arrive. The rest of the week
 * nothing changes for days, so the page does not poll at all.
 */
const WEEKEND_REFRESH_MS = 60_000
const WEEKEND_BEFORE_MS = 3 * 24 * 3600_000
const WEEKEND_AFTER_MS = 4 * 3600_000

export function refreshInterval(data, now = Date.now()) {
  const start = data?.weekend?.race_start_utc
  if (!start) return 0
  const until = new Date(start).getTime() - now
  return until < WEEKEND_BEFORE_MS && until > -WEEKEND_AFTER_MS ? WEEKEND_REFRESH_MS : 0
}
