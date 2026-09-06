/**
 * The only surface the browser talks to: core-api, via the /api proxy.
 *
 * Two conventions worth knowing before reading further:
 *
 * 1. Most of this API is deliberately PUBLIC. Forecasts, the track record and
 *    the weekend blog need no token. A track record behind a login is a
 *    marketing claim rather than a verifiable one, so only Bernie's
 *    conversations — which spend model calls and are private to their owner —
 *    require auth.
 *
 * 2. Aggregate endpoints can succeed partially. They return an `unavailable`
 *    list naming panels that could not be loaded, and the UI is expected to
 *    say so rather than render a silently emptier page.
 */

import { getStoredToken } from '../context/token'

const BASE = '/api'

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

async function parseError(res) {
  let detail
  try {
    const body = await res.json()
    detail = body.detail ?? body
  } catch {
    detail = res.statusText
  }
  // core-api returns a structured detail for Bernie failures so the UI can
  // distinguish "not configured" from "out of credit" from "provider down".
  const message =
    typeof detail === 'object' && detail?.message
      ? detail.message
      : typeof detail === 'string'
        ? detail
        : `Request failed (${res.status})`
  return new ApiError(message, res.status, detail)
}

async function request(path, { auth = false, ...options } = {}) {
  const token = auth ? getStoredToken() : null
  const res = await fetch(BASE + path, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })
  if (!res.ok) throw await parseError(res)
  return res.status === 204 ? null : res.json()
}

const post = (path, body, opts = {}) =>
  request(path, { method: 'POST', body: JSON.stringify(body), ...opts })

// ── Auth ─────────────────────────────────────────────────────────────────────
// The API keys on email, not a username. Login returns an identical 401 for a
// wrong password and an unknown account, so the UI must not try to tell the
// user which it was — it genuinely does not know.

export const register = (email, password) => post('/auth/register', { email, password })
export const login = (email, password) => post('/auth/login', { email, password })
export const me = () => request('/auth/me', { auth: true })

// ── Public: forecasts, record, championship ──────────────────────────────────

export const getNextRace = () => request('/next-race')
export const getTrackRecord = (season) =>
  request(`/track-record${season ? `?season=${season}` : ''}`)
export const getChampionship = (season) => request(`/championship/${season}`)
export const getSystemStatus = () => request('/status')

// ── One Blog ─────────────────────────────────────────────────────────────────

export const getWeekend = (season, round, { narrate = true } = {}) =>
  request(`/blog/${season}/${round}?narrate=${narrate}`)

// ── Bernie ───────────────────────────────────────────────────────────────────

export const whyThisPrediction = (season, round) =>
  request(`/bernie/why/${season}/${round}`)

export const startThread = (season, round, question) =>
  post('/bernie/threads', { season, round, question }, { auth: true })

export const continueThread = (threadId, question) =>
  post(`/bernie/threads/${threadId}`, { question }, { auth: true })

export const listThreads = () => request('/bernie/threads', { auth: true })
export const readThread = (threadId) =>
  request(`/bernie/threads/${threadId}`, { auth: true })
