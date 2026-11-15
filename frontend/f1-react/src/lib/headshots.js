import { useSyncExternalStore } from 'react'
import { splitName } from './teams'

/**
 * Driver headshots, looked up from OpenF1's current entry list.
 *
 * The pictures are Formula 1's own, served from their media host. They are
 * linked at view time and never copied into this repository. Anything that
 * fails (offline, the API down, a driver not on the current grid, a broken
 * image) falls back to the initials avatar, so the pictures are a nicety and
 * never a dependency.
 *
 * The list is fetched once per browser session and kept for a day in
 * localStorage, so a page of twenty avatars costs one request at most.
 */
const SOURCE = 'https://api.openf1.org/v1/drivers?session_key=latest'
const STORE_KEY = 'f1.headshots.v1'
const TTL_MS = 24 * 60 * 60 * 1000

/** "Nico Hülkenberg" and "Nico HULKENBERG" → "nico hulkenberg". */
export function nameKey(name) {
  return String(name || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z ]/g, '').trim()
}

let photos = null          // Map(key → url) once loaded
let started = false
const listeners = new Set()

function publish(map) {
  photos = map
  listeners.forEach((fn) => fn())
}

function build(rows) {
  const map = new Map()
  for (const r of rows) {
    if (!r?.headshot_url) continue
    const url = r.headshot_url
    if (r.last_name) map.set(nameKey(r.last_name), url)
    if (r.first_name && r.last_name) map.set(nameKey(`${r.first_name} ${r.last_name}`), url)
  }
  return map
}

function readCache() {
  try {
    const raw = window.localStorage.getItem(STORE_KEY)
    if (!raw) return null
    const { at, rows } = JSON.parse(raw)
    if (!Array.isArray(rows) || Date.now() - at > TTL_MS) return null
    return rows
  } catch {
    return null
  }
}

function writeCache(rows) {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify({ at: Date.now(), rows }))
  } catch { /* private mode or full storage: just refetch next time */ }
}

function load() {
  if (started || typeof window === 'undefined') return
  started = true
  const cached = readCache()
  if (cached) { publish(build(cached)); return }
  fetch(SOURCE)
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((rows) => {
      const slim = (Array.isArray(rows) ? rows : [])
        .map(({ first_name, last_name, headshot_url }) => ({ first_name, last_name, headshot_url }))
      writeCache(slim)
      publish(build(slim))
    })
    .catch(() => publish(new Map()))
}

function subscribe(fn) {
  listeners.add(fn)
  load()
  return () => listeners.delete(fn)
}

/** The headshot URL for a driver's full name, or null. */
export function useHeadshot(name) {
  const map = useSyncExternalStore(subscribe, () => photos, () => null)
  if (!map || !name) return null
  return map.get(nameKey(name)) || map.get(nameKey(splitName(name).last)) || null
}
