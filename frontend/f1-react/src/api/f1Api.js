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

import { expireStoredAuth, getStoredToken } from '../context/token'

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
  if (res.status === 401 && token) {
    // The credential we hold is no longer accepted — expired, or signed with a
    // secret that has since been rotated. Either way it will never work again,
    // so drop it here rather than letting every subsequent page report its own
    // confusing failure. Without this a stale token surfaced as "Bernie is
    // unavailable", sending the reader to look at the wrong thing entirely.
    expireStoredAuth()
  }
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
export const requestPasswordReset = (email) => post('/auth/password-reset/request', { email })
export const confirmPasswordReset = (token, password) =>
  post('/auth/password-reset/confirm', { token, password })
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

// ── Paddock news ─────────────────────────────────────────────────────────────
// Public. The payload carries its own `attribution` line, and pages render it:
// these are the outlets' reports, not our claims.

export const getNews = (limit = 30) => request(`/news?limit=${limit}`)

// ── Bernie ───────────────────────────────────────────────────────────────────

// Authenticated since Bernie's costs were bounded — every route that can spend
// a model call now needs an account behind it. The explanation itself is cached
// against the prediction, so this costs one call per forecast rather than one
// per reader.
export const whyThisPrediction = (season, round) =>
  request(`/bernie/why/${season}/${round}`, { auth: true })

export const startThread = (season, round, question) =>
  post('/bernie/threads', { season, round, question }, { auth: true })

export const continueThread = (threadId, question) =>
  post(`/bernie/threads/${threadId}`, { question }, { auth: true })

/**
 * A Bernie turn with the answer streamed in as it is written.
 *
 * `onDelta(textSoFar)` is called as fragments arrive. Resolves with the final
 * turn (the same shape as startThread/continueThread return), whose `answer`
 * is the saved text and should replace what was streamed. Errors before the
 * stream starts (sign-in, daily limit) and `error` events both reject with an
 * ApiError, so callers handle them exactly like the non-streamed calls.
 */
export async function streamTurn({ threadId, season, round, question }, onDelta) {
  const path = threadId ? `/bernie/threads/${threadId}` : '/bernie/threads'
  const body = threadId ? { question } : { season, round, question }
  const token = getStoredToken()
  const res = await fetch(`${BASE}${path}?stream=true`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  })
  if (res.status === 401 && token) expireStoredAuth()
  if (!res.ok) throw await parseError(res)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let text = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let cut
    while ((cut = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      const event = /^event: (.*)$/m.exec(block)?.[1]
      const data = /^data: (.*)$/m.exec(block)?.[1]
      if (!event || data === undefined) continue
      const payload = JSON.parse(data)
      if (event === 'delta') {
        text += payload.text
        onDelta?.(text)
      } else if (event === 'done') {
        return payload
      } else if (event === 'error') {
        throw new ApiError(payload.message, 503, payload)
      }
    }
  }
  throw new ApiError('Bernie stopped before finishing her answer', 503, { reason: 'provider_error' })
}

export const listThreads = () => request('/bernie/threads', { auth: true })
export const readThread = (threadId) =>
  request(`/bernie/threads/${threadId}`, { auth: true })

// ── Strategy engine ──────────────────────────────────────────────────────────
// Public: the plan is arithmetic and the guide quotes are a search, so nothing
// here spends a model call.

export const getStrategy = (season, round, driver) =>
  request(`/strategy/${season}/${round}${driver ? `?driver=${encodeURIComponent(driver)}` : ''}`)

export const getCircuit = (season, round) =>
  request(`/circuit/${season}/${round}`)

export const getLastRace = () => request('/last-race')

export const getBlogIndex = (season) =>
  request(season ? `/blog?season=${season}` : '/blog')

export const getBackdrop = (limit = 10) =>
  request(`/backdrop?limit=${limit}`)

export const getBlogFeed = (limit = 24) =>
  request(`/blog/feed?limit=${limit}`)

export const getGlobe = () => request('/globe')

export const getDrivers = (season) => request(`/drivers/${season}`)
export const getDriver = (season, driver) =>
  request(`/drivers/${season}/${encodeURIComponent(driver)}`)
export const getRaceReview = (season, round) => request(`/race-review/${season}/${round}`)

export const getDataHealth = () => request('/data-health')
