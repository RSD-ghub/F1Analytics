import { useCallback, useEffect, useState } from 'react'
import { AuthContext } from './useAuth'
import { AUTH_EXPIRED_EVENT, readStoredAuth, writeStoredAuth } from './token'

/**
 * Holds the signed-in user. Initialised from localStorage so a refresh does not
 * sign the user out.
 */
export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(() => readStoredAuth())

  // The API client drops a rejected token from storage; follow it here so the
  // UI falls back to signed out rather than calling on with no credential.
  useEffect(() => {
    const onExpired = () => setAuth(null)
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [])

  const login = useCallback((token, username) => {
    const data = { token, username }
    writeStoredAuth(data)
    setAuth(data)
  }, [])

  const logout = useCallback(() => {
    writeStoredAuth(null)
    setAuth(null)
  }, [])

  return (
    <AuthContext.Provider value={{ auth, login, logout, isLoggedIn: !!auth?.token }}>
      {children}
    </AuthContext.Provider>
  )
}
