import { useEffect, useState } from 'react'
import { connectF1tv, disconnectF1tv, me } from '../api/f1Api'
import { useAuth } from '../context/useAuth'
import { Panel } from './Panel'
import { Button } from '@/components/ui/button'

function expiry(status) {
  if (!status?.expires_at) return null
  const when = new Date(status.expires_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  return status.connected ? `Connected until ${when}` : `Expired ${when}`
}

/**
 * Whether official F1 timing is reachable, and for the site owner the form
 * that connects it.
 *
 * Since October 2026 F1's timing archive needs an F1TV Access login. With it,
 * laps, tyres, weather and race control are the official ones; without it,
 * new races fall back to OpenF1. The password goes to ingestion-service,
 * which signs in once and keeps only the token. F1 issues no refresh token,
 * so it lasts about four days and is renewed here.
 */
export default function F1TVConnection({ status: initial }) {
  const { isLoggedIn } = useAuth()
  const [status, setStatus] = useState(initial)
  const [adminCheck, setAdminCheck] = useState(false)
  const isAdmin = isLoggedIn && adminCheck
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!isLoggedIn) return undefined
    let live = true
    me().then((p) => live && setAdminCheck(Boolean(p?.is_admin))).catch(() => live && setAdminCheck(false))
    return () => { live = false }
  }, [isLoggedIn])

  async function connect(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setStatus(await connectF1tv(email.trim(), password))
      setPassword('')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    setBusy(true)
    setError(null)
    try {
      setStatus(await disconnectF1tv())
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!status && !isAdmin) return null
  const connected = Boolean(status?.connected)

  return (
    <Panel title="Official F1 timing" subtitle="F1TV account used to fetch laps, tyres, weather and race control">
      <p>
        <span className={connected ? 'good' : 'bad'}>{connected ? 'Connected' : 'Not connected'}</span>
        {expiry(status) && <span className="muted"> · {expiry(status)}</span>}
      </p>
      {!connected && (
        <p className="muted small">
          Until it is connected, laps for new races come from OpenF1, which is free
          but sometimes records a tyre change on the wrong lap.
        </p>
      )}
      {status && status.available === false && (
        <p className="muted small">{status.detail}</p>
      )}

      {isAdmin && status?.available !== false && (
        <form className="stack" onSubmit={connect} autoComplete="off">
          <label>
            F1TV email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                   required autoComplete="username" />
          </label>
          <label>
            F1TV password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                   required autoComplete="current-password" />
          </label>
          <p className="muted small">
            Used once to sign in to F1 and not stored. Only the access token is kept,
            and it lasts about four days; connect again here when it runs out.
          </p>
          {error && <p className="bad" role="alert">{error}</p>}
          <div>
            <Button type="submit" disabled={busy}>{busy ? 'Signing in…' : connected ? 'Reconnect' : 'Connect F1TV'}</Button>
            {connected && (
              <Button type="button" variant="outline" disabled={busy} onClick={disconnect}>Disconnect</Button>
            )}
          </div>
        </form>
      )}
    </Panel>
  )
}
