import { createContext, useContext } from 'react'

/**
 * The context object and its hook, kept out of the provider's module.
 *
 * Vite's fast refresh only works on modules that export components alone, so
 * co-locating `useAuth` with `AuthProvider` — the obvious arrangement — costs
 * hot reloading for every component beneath the provider. Splitting the
 * non-component exports here restores it.
 *
 * Named `useAuth.js` rather than `authContext.js` deliberately: the latter
 * differs from `AuthContext.jsx` only by case, and macOS and Windows treat
 * those as the same file. The bundler resolved `./context/AuthContext` to the
 * wrong one and the build failed with a missing export — a class of bug that
 * does not reproduce on a case-sensitive CI filesystem.
 */
export const AuthContext = createContext(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === null) {
    // Without this the default null propagates into whichever component
    // destructures it, and the error surfaces as "cannot destructure property
    // 'auth' of null" somewhere deep in the tree — pointing at the symptom
    // rather than at the missing provider.
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return context
}
