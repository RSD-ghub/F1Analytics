import { getStoredToken } from '../context/AuthContext'

const BASE = '/f1'

// ── Core request helper ───────────────────────────────────────────────────────

async function request(path, options = {}) {
  const res = await fetch(BASE + path, options)
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  return res.json()
}

/**
 * Authenticated request — attaches the stored JWT as a Bearer token.
 * Used for endpoints protected by @Secured on the backend.
 * If the server returns 401, throws a descriptive error so the UI
 * can prompt the user to log in again.
 */
async function authFetch(path, options = {}) {
  const token = getStoredToken()
  const headers = {
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
  const res = await fetch(BASE + path, { ...options, headers })
  if (res.status === 401) {
    throw new Error('Session expired. Please sign in again.')
  }
  return res // caller handles streaming or json
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function loginUser(username, password) {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Login failed')
  return data // { token, username }
}

export async function registerUser(username, password) {
  const res = await fetch(`${BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Registration failed')
  return data // { message }
}

// ── F1 Data ───────────────────────────────────────────────────────────────────

export const getResults       = ()             => request('/results')
export const getSeasons       = ()             => request('/seasons')
export const getDrivers       = ()             => request('/drivers')
export const getAnalytics     = (season)       => request(`/analytics/${season}`)
export const getDriverAnalytics = (name, season) => {
  const params = new URLSearchParams({ name })
  if (season && season !== 'ALL') params.set('season', season)
  return request(`/analytics/driver?${params}`)
}
export const syncCurrentSeason = ()           => request('/sync/current', { method: 'POST' })
export const syncSeason        = (season)     => request(`/sync/${season}`, { method: 'POST' })
export const getRaces          = (season)     => request(`/races/${season}`)

// ── Predictions (authenticated) ───────────────────────────────────────────────

export const getPrediction = (season, round) =>
  authFetch(`/predict/race?season=${season}&round=${round}`).then(r => r.json())

export const refreshPrediction = (season, round) =>
  authFetch(`/predict/race/refresh?season=${season}&round=${round}`, { method: 'POST' }).then(r => r.json())

// ── AI (authenticated — require Bearer token) ─────────────────────────────────

export const generateSeasonReview = (season) =>
  authFetch(`/ai/season-review?season=${season}`, { method: 'POST' })

export const generateRaceRewind = (season, round) =>
  authFetch(`/ai/race-rewind?season=${season}&round=${round}`, { method: 'POST' })

// ── Bernie strategist agent ───────────────────────────────────────────────────

export const getBernieUpcomingRaces = (season) =>
  authFetch(`/bernie/upcoming-races?season=${season}`).then(r => r.json())

export const bernieChat = (message, history, season, round) =>
  authFetch('/bernie/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, history, season, round }),
  })
