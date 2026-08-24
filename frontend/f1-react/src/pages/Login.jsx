import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { loginUser } from '../api/f1Api'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)

  const { login } = useAuth()
  const navigate  = useNavigate()

  async function handleLogin() {
    const u = username.trim()
    if (!u || !password) {
      setError('Please enter your username and password')
      return
    }
    setLoading(true)
    setError('')
    try {
      const data = await loginUser(u, password)
      login(data.token, data.username)
      navigate('/dashboard')
    } catch (err) {
      setError(err.message || 'Sign in failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  function handleKey(e) {
    if (e.key === 'Enter') handleLogin()
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <span className="login-card__logo">F1</span>

        <div>
          <p className="login-card__title">Formula 1 Analytics</p>
          <p className="login-card__subtitle">Sign in to your account</p>
        </div>

        {error && <div className="login-card__error">{error}</div>}

        <div className="login-card__fields">
          <div className="login-card__input-wrap">
            <label className="login-card__label">Username</label>
            <input
              className="input-text"
              type="text"
              placeholder="Enter username"
              value={username}
              onChange={e => setUsername(e.target.value)}
              onKeyDown={handleKey}
              autoFocus
              autoComplete="username"
            />
          </div>
          <div className="login-card__input-wrap">
            <label className="login-card__label">Password</label>
            <input
              className="input-text"
              type="password"
              placeholder="Enter password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={handleKey}
              autoComplete="current-password"
            />
          </div>
        </div>

        <button
          className="btn btn--primary login-card__btn"
          onClick={handleLogin}
          disabled={loading || !username.trim() || !password}
        >
          {loading && <span className="spinner" />}
          {loading ? 'Signing in…' : 'Sign In'}
        </button>

        <p className="login-card__footer">
          Don't have an account?{' '}
          <Link to="/register" className="login-card__link">Create one</Link>
        </p>
      </div>
    </div>
  )
}
