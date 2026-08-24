import { Routes, Route, Navigate } from 'react-router-dom'
import { SidebarProvider } from './context/SidebarContext'
import { AuthProvider, useAuth } from './context/AuthContext'
import Login      from './pages/Login'
import Register   from './pages/Register'
import Dashboard  from './pages/Dashboard'
import Driver     from './pages/Driver'
import SeasonReview from './pages/SeasonReview'
import RaceRewind from './pages/RaceRewind'
import RacePredict from './pages/RacePredict'
import Bernie from './pages/Bernie'
import Preferences from './pages/Preferences'

/**
 * Guards a route behind authentication.
 * Reads the JWT token from AuthContext — if absent, redirects to login.
 * The `replace` prop prevents the login page appearing in browser history.
 */
function ProtectedRoute({ children }) {
  const { isLoggedIn } = useAuth()
  return isLoggedIn ? children : <Navigate to="/" replace />
}

/**
 * Redirects already-authenticated users away from login/register
 * so they don't land on the auth pages when they're already signed in.
 */
function PublicRoute({ children }) {
  const { isLoggedIn } = useAuth()
  return isLoggedIn ? <Navigate to="/dashboard" replace /> : children
}

export default function App() {
  return (
    <AuthProvider>
      <SidebarProvider>
        <Routes>
          {/* Public — redirect to dashboard if already logged in */}
          <Route path="/"         element={<PublicRoute><Login /></PublicRoute>} />
          <Route path="/register" element={<PublicRoute><Register /></PublicRoute>} />

          {/* Protected — redirect to login if not authenticated */}
          <Route path="/dashboard"    element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
          <Route path="/driver"       element={<ProtectedRoute><Driver /></ProtectedRoute>} />
          <Route path="/season-review" element={<ProtectedRoute><SeasonReview /></ProtectedRoute>} />
          <Route path="/race-rewind"   element={<ProtectedRoute><RaceRewind /></ProtectedRoute>} />
          <Route path="/race-predict"  element={<ProtectedRoute><RacePredict /></ProtectedRoute>} />
          <Route path="/bernie"        element={<ProtectedRoute><Bernie /></ProtectedRoute>} />
          <Route path="/preferences"   element={<ProtectedRoute><Preferences /></ProtectedRoute>} />

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </SidebarProvider>
    </AuthProvider>
  )
}
