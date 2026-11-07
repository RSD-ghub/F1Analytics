import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { confirmPasswordReset, requestPasswordReset } from '../api/f1Api'
import { Panel } from '../components/Panel'
import { usePageTitle } from '../hooks/usePageTitle'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

/**
 * Both halves of a password reset: asking for a link (no token in the URL)
 * and choosing a new password (token present). The request step says the
 * same thing whether or not the address has an account.
 */
export default function PasswordReset() {
  const [params] = useSearchParams()
  const token = params.get('token')
  usePageTitle(token ? 'Choose a new password' : 'Reset your password')
  return token ? <ChooseNew token={token} /> : <AskForLink />
}

function AskForLink() {
  const [email, setEmail] = useState('')
  const [state, setState] = useState({ busy: false, done: null, error: null })

  async function submit(event) {
    event.preventDefault()
    setState({ busy: true, done: null, error: null })
    try {
      const data = await requestPasswordReset(email)
      setState({ busy: false, done: data.detail, error: null })
    } catch (err) {
      setState({ busy: false, done: null, error: err.message })
    }
  }

  return (
    <div className="narrow">
      <header className="page-head auth-head">
        <h1>Reset your password</h1>
        <p className="lede">We'll email a link that lets you choose a new one.</p>
      </header>
      <Panel>
        {state.done ? (
          <p role="status">{state.done}</p>
        ) : (
          <form className="form" onSubmit={submit}>
            <div className="field">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" value={email} required autoComplete="email" autoFocus
                     onChange={(e) => setEmail(e.target.value)} />
            </div>
            {state.error && <p className="error" role="alert">{state.error}</p>}
            <Button type="submit" disabled={state.busy}>{state.busy ? 'Sending…' : 'Send reset link'}</Button>
          </form>
        )}
        <p className="muted small"><Link to="/login">Back to sign in</Link></p>
      </Panel>
    </div>
  )
}

function ChooseNew({ token }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await confirmPasswordReset(token, password)
      navigate('/login?reset=1', { replace: true })
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="narrow">
      <header className="page-head auth-head">
        <h1>Choose a new password</h1>
        <p className="lede">Signing in anywhere else with the old password will stop working.</p>
      </header>
      <Panel>
        <form className="form" onSubmit={submit}>
          <div className="field">
            <Label htmlFor="password">New password</Label>
            <Input id="password" type="password" value={password} required minLength={10}
                   autoComplete="new-password" autoFocus onChange={(e) => setPassword(e.target.value)} />
            <span className="hint">At least 10 characters.</span>
          </div>
          {error && (
            <p className="error" role="alert">
              {error} {error.includes('expired') && <Link to="/reset-password">Ask for a new link</Link>}
            </p>
          )}
          <Button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Set new password'}</Button>
        </form>
      </Panel>
    </div>
  )
}
