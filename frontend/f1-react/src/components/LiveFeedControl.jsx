import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getLiveFeed, getNextRace, me, startLiveFeed, stopLiveFeed } from '../api/f1Api'
import { useAuth } from '../context/useAuth'
import { Panel } from './Panel'
import { Button } from '@/components/ui/button'

const REPLAY_SPEEDS = [0, 10, 30, 60]

function raceOfRecording(name) {
  const m = /^(\d{4})-(\d{2})-/.exec(name || '')
  return m ? { season: Number(m[1]), round: Number(m[2]) } : null
}

/**
 * The owner's switch for the live race feed.
 *
 * "Start live" runs FastF1's live recorder on the machine ingestion runs on,
 * for the next race, and the Live page then follows that race lap by lap.
 * Start it any time before lights out; it stops itself 15 minutes after the
 * flag. Every race is kept as a recording, so it can be replayed through the
 * same path later at up to 60 times real speed.
 *
 * Hidden from everyone else, and checked on the server either way.
 */
export default function LiveFeedControl() {
  const { isLoggedIn } = useAuth()
  const [isAdmin, setIsAdmin] = useState(false)
  const [status, setStatus] = useState(null)
  const [next, setNext] = useState(null)
  const [recording, setRecording] = useState('')
  const [speed, setSpeed] = useState(30)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!isLoggedIn) return undefined
    let alive = true
    me().then((p) => alive && setIsAdmin(Boolean(p?.is_admin))).catch(() => {})
    return () => { alive = false }
  }, [isLoggedIn])

  useEffect(() => {
    if (!isAdmin) return undefined
    let alive = true
    const load = () => getLiveFeed()
      .then((s) => alive && setStatus(s))
      .catch((e) => alive && setError(e.message))
    load()
    getNextRace().then((n) => alive && setNext(n)).catch(() => {})
    const timer = setInterval(load, 10000)
    return () => { alive = false; clearInterval(timer) }
  }, [isAdmin])

  if (!isAdmin) return null

  async function act(call) {
    setBusy(true)
    setError(null)
    try {
      const out = await call()
      setStatus((s) => ({ ...out, recordings: s?.recordings || [] }))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const race = next?.weekend
  const chosen = recording || status?.recordings?.[0] || ''
  const running = Boolean(status?.running)

  return (
    <Panel title="Live race feed" subtitle="F1's live timing, recorded and forecast lap by lap">
      <p>
        <span className={running ? 'good' : 'muted'}>
          {running ? (status.mode === 'replay' ? 'Replaying' : 'Recording live') : 'Off'}
        </span>
        {status?.season ? (
          <span className="muted">
            {' '}· {status.season} round {status.round}
            {status.session?.meeting ? `, ${status.session.meeting}` : ''}
            {status.lap ? ` · lap ${status.lap}${status.total_laps ? `/${status.total_laps}` : ''}` : ''}
            {status.status ? ` · ${status.status}` : ''}
          </span>
        ) : null}
      </p>
      {status?.error && <p className="bad small">{status.error}</p>}
      {status?.unnamed_cars?.length > 0 && (
        <p className="muted small">
          Car numbers not in this season's entry list: {status.unnamed_cars.join(', ')}. They
          keep F1's spelling and are left out of the forecast.
        </p>
      )}
      {error && <p className="bad" role="alert">{error}</p>}

      <div className="live-controls">
        {running ? (
          <Button disabled={busy} variant="outline" onClick={() => act(stopLiveFeed)}>Stop</Button>
        ) : (
          <Button disabled={busy || !race}
                  onClick={() => act(() => startLiveFeed({ season: race.season, round: race.round }))}>
            Start live{race?.race_name ? `: ${race.race_name}` : ''}
          </Button>
        )}
        {running && <Link className="link" to="/live">Open the Live page →</Link>}
      </div>

      {!running && status?.recordings?.length > 0 && (
        <div className="live-controls">
          <select value={chosen} onChange={(e) => setRecording(e.target.value)}
                  aria-label="Recording to replay">
            {status.recordings.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}
                  aria-label="Replay speed">
            {REPLAY_SPEEDS.map((s) => (
              <option key={s} value={s}>{s === 0 ? 'All at once' : `${s}× speed`}</option>
            ))}
          </select>
          <Button variant="outline" disabled={busy || !raceOfRecording(chosen)}
                  onClick={() => act(() => startLiveFeed({
                    ...raceOfRecording(chosen), recording: chosen, speed }))}>
            Replay recording
          </Button>
        </div>
      )}
      <p className="muted small">
        Start any time before the race; it stops itself 15 minutes after the flag. It needs
        F1TV connected (above) for the whole race.
      </p>
    </Panel>
  )
}
