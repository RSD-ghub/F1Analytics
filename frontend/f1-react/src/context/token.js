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
