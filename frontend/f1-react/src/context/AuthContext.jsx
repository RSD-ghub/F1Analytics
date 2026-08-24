import { createContext, useContext, useState, useCallback } from 'react'

const AuthContext = createContext(null)

const STORAGE_KEY = 'f1_auth'

function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function AuthProvider({ children }) {
  // Initialise from localStorage so auth survives a page refresh
  const [auth, setAuth] = useState(() => loadFromStorage())

  const login = useCallback((token, username) => {
    const data = { token, username }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    setAuth(data)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setAuth(null)
  }, [])

  return (
    <AuthContext.Provider value={{ auth, login, logout, isLoggedIn: !!auth?.token }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}

/**
 * Utility to read the token without requiring the hook (usable outside React).
 * Used by f1Api.js to attach Bearer headers to AI requests.
 */
export function getStoredToken() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw)?.token : null
  } catch {
    return null
  }
}
