import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/useAuth'
import { Link } from 'react-router-dom'
import { continueThread, getNextRace, listThreads, readThread, startThread } from '../api/f1Api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Panel, Loading, Unavailable } from '../components/Panel'

/**
 * Bernie — a conversational pit-wall strategist.
 *
 * The disclaimer is rendered from the server's own payload rather than
 * hardcoded here. Bernie is an AI persona and the product must not present him
 * as a person; making that depend on the frontend remembering to add a line
 * would be the wrong place for the guarantee to live.
 *
 * Threads are scoped to one race weekend, which is a deliberate limit: Bernie's
 * facts are re-derived from that weekend's record on every turn, so a
 * conversation that wandered across races would accumulate context its
 * grounding cannot support.
 */
export default function Bernie() {
  const { auth } = useAuth()
  const [threads, setThreads] = useState([])
  const [active, setActive] = useState(null)
  const [race, setRace] = useState(null)
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const endRef = useRef(null)

  useEffect(() => {
    if (!auth?.token) return
    listThreads().then(setThreads).catch(() => {})
    getNextRace().then((d) => setRace(d.weekend)).catch(() => {})
  }, [auth])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [active?.turns?.length])

  if (!auth?.token) {
    /*
     * The signed-out state used to be a bare panel headed "Sign in to talk to
     * Bernie", which asked for an account without ever saying what the
     * account was for. Someone arriving from the masthead had no way to know
     * what Bernie knows or why he is worth the sign-up.
     */
    return (
      <div className="stack">
        <header className="page-head">
          <p className="eyebrow">Bernie</p>
          <h1>Ask about the weekend</h1>
          <p className="lede">
            Bernie answers from the stored record — practice pace, qualifying,
            the confirmed grid and its penalties, the locked forecast, and the
            text of the FIA regulations. He cites the article when he leans on
            a rule, and says he does not know when the record does not cover
            the question.
          </p>
        </header>

        <Panel title="What he can be asked">
          <ul className="examples">
            <li>Why does a driver start further back than they qualified?</li>
            <li>How many power unit elements are allowed before a penalty?</li>
            <li>What did the forecast say about this race, and how did it score?</li>
            <li>Who was quickest over a race stint in practice?</li>
          </ul>
          <p className="small muted">
            Conversations are private to your account, and they are the only
            part of the service that spends model calls — so this is the one
            page that needs a login. Forecasts, the track record and One Blog
            are open to everyone.
          </p>
          <Button asChild><Link to="/login">Sign in to ask</Link></Button>
        </Panel>
      </div>
    )
  }

  async function send(event) {
    event.preventDefault()
    const text = question.trim()
    if (!text || busy) return

    setBusy(true)
    setError(null)
    // Show the question immediately. The server records it before calling the
    // model for the same reason: a slow or failed turn must not make the user's
    // own message disappear.
    const optimistic = { role: 'user', content: text, created_at: new Date().toISOString() }
    setActive((prev) => prev && { ...prev, turns: [...prev.turns, optimistic] })
    setQuestion('')

    try {
      const response = active
        ? await continueThread(active.thread_id, text)
        : await startThread(race.season, race.round, text)
      const thread = await readThread(response.thread_id)
      setActive(thread)
      setThreads(await listThreads())
    } catch (err) {
      setError(err)
      // Roll back the optimistic turn — it was never recorded.
      setActive((prev) =>
        prev && { ...prev, turns: prev.turns.filter((t) => t !== optimistic) },
      )
      setQuestion(text)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bernie">
      <aside className="threads">
        <Button variant="outline" onClick={() => setActive(null)}>
          New conversation
        </Button>
        {threads.map((thread) => (
          <button
            key={thread.thread_id}
            className={`thread ${active?.thread_id === thread.thread_id ? 'active' : ''}`}
            onClick={() => readThread(thread.thread_id).then(setActive)}
          >
            <span className="thread-race">{thread.season} R{thread.round}</span>
            <span className="thread-q">{thread.opening_question}</span>
          </button>
        ))}
      </aside>

      <section className="conversation">
        {!active && race && (
          <Panel title={`Ask about ${race.race_name}`}>
            <p className="muted">
              Bernie answers from the weekend's stored record — practice pace,
              qualifying, the locked forecast — and will tell you when the data
              does not cover your question rather than guessing.
            </p>
          </Panel>
        )}

        {active?.turns?.map((turn, i) => (
          <div key={i} className={`turn ${turn.role}`}>
            {turn.content.split('\n\n').map((para, j) => <p key={j}>{para}</p>)}
          </div>
        ))}
        {busy && <Loading what="Bernie is thinking" />}
        <div ref={endRef} />

        {error && <BernieError error={error} />}

        <form className="composer" onSubmit={send}>
          <Input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={active ? 'Ask a follow-up…' : 'Who looks strongest this weekend?'}
            disabled={busy || !race}
            aria-label="Ask Bernie about this weekend"
          />
          <Button type="submit" disabled={busy || !question.trim() || !race}>
            Send
          </Button>
        </form>

        <p className="disclaimer">
          Bernie is an AI pit-wall strategist inspired by the role of a real race
          strategist. He is not a real person and does not represent anyone.
        </p>
      </section>
    </div>
  )
}

/**
 * The three failure modes look identical to a reader and are not identical to
 * an operator, so the server distinguishes them and the UI says which.
 */
function BernieError({ error }) {
  const reason = error.detail?.reason
  // Phrased to follow "Bernie is unavailable." — the subject is already named
  // by the component, so repeating it reads as a stutter.
  const messages = {
    not_configured: 'No language model is configured on this deployment. Everything else on the site works without one.',
    billing: 'The language model account needs credit before he can answer.',
    provider_error: 'The language model could not be reached just now — try again shortly.',
  }
  return <Unavailable what="Bernie" reason={messages[reason] ?? error.message} />
}
