import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { registerUser } from '../api/f1Api'

export default function Register() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm]   = useState('')
  const [error, setError]       = useState('')
  const [success, setSuccess]   = useState('')
  const [loading, setLoading]   = useState(false)

  const navigate = useNavigate()

  async function handleRegister() {
    const u = username.trim()

    // Client-side validation (mirrors server rules)
    if (!u) { setError('Username is required'); return }
    if (u.length < 3) { setError('Username must be at least 3 characters'); return }
    if (password.length < 6) { setError('Password must be at least 6 characters'); return }
    if (password !== confirm) { setError('Passwords do not match'); return }

    setLoading(true)
    setError('')
    try {
      await registerUser(u, password)
      setSuccess('Account created! Redirecting to sign in…')
      setTimeout(() => navigate('/'), 1600)
    } catch (err) {
      setError(err.message || 'Registration failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  function handleKey(e) {
    if (e.key === 'Enter') handleRegister()
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <span className="login-card__logo">F1</span>

        <div>
          <p className="login-card__title">Create Account</p>
          <p className="login-card__subtitle">Join Formula 1 Analytics</p>
        </div>

        {error   && <div className="login-card__error">{error}</div>}
        {success && <div className="login-card__success">{success}</div>}

        <div className="login-card__fields">
          <div className="login-card__input-wrap">
            <label className="login-card__label">Username</label>
            <input
              className="input-text"
              type="text"
              placeholder="Choose a username (min 3 chars)"
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
              placeholder="Choose a password (min 6 chars)"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={handleKey}
              autoComplete="new-password"
            />
          </div>
          <div className="login-card__input-wrap">
            <label className="login-card__label">Confirm Password</label>
            <input
              className="input-text"
              type="password"
              placeholder="Repeat your password"
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
              onKeyDown={handleKey}
              autoComplete="new-password"
            />
          </div>
        </div>

        <button
          className="btn btn--primary login-card__btn"
          onClick={handleRegister}
          disabled={loading || !username.trim() || !password || !confirm}
        >
          {loading && <span className="spinner" />}
          {loading ? 'Creating account…' : 'Create Account'}
        </button>

        <p className="login-card__footer">
          Already have an account?{' '}
          <Link to="/" className="login-card__link">Sign in</Link>
        </p>
      </div>
    </div>
  )
}
