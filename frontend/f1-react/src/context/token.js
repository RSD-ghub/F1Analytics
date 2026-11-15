/**
 * Token access outside React.
 *
 * Split out of AuthContext so that module exports components only — mixing
 * component and non-component exports breaks Vite's fast refresh, and the API
 * client needs the token from outside any component tree.
 */
const STORAGE_KEY = 'f1_auth'

export function getStoredToken() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw)?.token : null
  } catch {
    return null
  }
}

export function readStoredAuth() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function writeStoredAuth(value) {
  if (value) localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  else localStorage.removeItem(STORAGE_KEY)
}

/**
 * Fired when the API rejects the stored token. The API client lives outside
 * React, so clearing localStorage alone left AuthProvider holding the dead
 * token in state: pages kept rendering as signed in while every request went
 * out with no header at all, and core-api answered "missing Authorization
 * header" instead of the user being asked to sign in again.
 */
export const AUTH_EXPIRED_EVENT = 'f1-auth-expired'

export function expireStoredAuth() {
  writeStoredAuth(null)
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}
