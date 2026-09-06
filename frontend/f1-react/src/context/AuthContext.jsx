import { useCallback, useState } from 'react'
import { AuthContext } from './useAuth'
import { readStoredAuth, writeStoredAuth } from './token'

/**
 * Holds the signed-in user. Initialised from localStorage so a refresh does not
 * sign the user out.
 */
export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(() => readStoredAuth())

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
