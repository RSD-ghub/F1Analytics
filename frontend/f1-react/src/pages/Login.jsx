import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { login, register } from '../api/f1Api'
import { useAuth } from '../context/useAuth'
import { Panel } from '../components/Panel'

/**
 * Sign in / register.
 *
 * The API returns an identical 401 for a wrong password and an unknown
 * account, so the message here is deliberately identical too. Being more
 * helpful — "no account with that address" — would turn the form into a free
 * account-enumeration tool.
 */
export default function Login({ mode = 'login' }) {
  const isRegister = mode === 'register'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const { login: signIn } = useAuth()
  const navigate = useNavigate()

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const data = isRegister
        ? await register(email, password)
        : await login(email, password)
      signIn(data.access_token, email)
      navigate('/bernie')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="narrow">
      <Panel title={isRegister ? 'Create an account' : 'Sign in'}>
        <p className="muted">
          An account is only needed to talk to Bernie. Forecasts, the track
          record and the weekend blog are open to everyone.
        </p>
        <form className="form" onSubmit={submit}>
          <label>
            Email
            <input type="email" value={email} required
                   onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Password
            <input type="password" value={password} required minLength={10}
                   onChange={(e) => setPassword(e.target.value)} />
            {isRegister && (
              <span className="hint">
                At least 10 characters. Length is what resists an offline attack,
                so there are no symbol or digit rules.
              </span>
            )}
          </label>
          {error && <p className="error">{error}</p>}
          <button className="button" disabled={busy}>
            {busy ? 'Working…' : isRegister ? 'Create account' : 'Sign in'}
          </button>
        </form>
        <p className="muted small">
          {isRegister ? (
            <>Already have an account? <Link to="/login">Sign in</Link></>
          ) : (
            <>No account? <Link to="/register">Create one</Link></>
          )}
        </p>
      </Panel>
    </div>
  )
}
