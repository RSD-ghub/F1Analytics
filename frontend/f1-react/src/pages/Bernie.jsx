import { useEffect, useRef, useState } from 'react'
import { usePageTitle } from '../hooks/usePageTitle'
import { useAuth } from '../context/useAuth'
import { Link } from 'react-router-dom'
import { getNextRace, listThreads, readThread, streamTurn } from '../api/f1Api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Panel, Loading, Unavailable } from '../components/Panel'

/**
 * Bernie — a conversational pit-wall strategist.
 *
 * The disclaimer is rendered from the server's own payload rather than
 * hardcoded here. Bernie is an AI persona and the product must not present her
 * as a person; making that depend on the frontend remembering to add a line
 * would be the wrong place for the guarantee to live.
 *
 * Threads are scoped to one race weekend, which is a deliberate limit: Bernie's
 * facts are re-derived from that weekend's record on every turn, so a
 * conversation that wandered across races would accumulate context its
 * grounding cannot support.
 */
export default function Bernie() {
  usePageTitle('Bernie')
  const { auth } = useAuth()
  const [threads, setThreads] = useState([])
  const [active, setActive] = useState(null)
  const [race, setRace] = useState(null)
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  // The question being answered and Bernie's answer so far, while it streams.
  const [pending, setPending] = useState(null)
  const [error, setError] = useState(null)
  const endRef = useRef(null)

  useEffect(() => {
    if (!auth?.token) return
    listThreads().then(setThreads).catch(() => {})
    getNextRace().then((d) => setRace(d.weekend)).catch(() => {})
  }, [auth])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [active?.turns?.length, pending?.answer])

  if (!auth?.token) {
    /*
     * The signed-out state used to be a bare panel headed "Sign in to talk to
     * Bernie", which asked for an account without ever saying what the
     * account was for. Someone arriving from the masthead had no way to know
     * what Bernie knows or why she is worth the sign-up.
     */
    return (
      <div className="stack">
        <header className="page-head">
          <p className="eyebrow">Bernie</p>
          <h1>Plan the race with Bernie</h1>
          <p className="lede">
            Bernie is a race strategist. Ask for a plan and she simulates the
            options from this circuit's recent races — tyre wear, what a stop
            costs, how often the safety car comes out — then weighs them
            against the grid, practice pace and the forecast. She also answers
            from the stored record and the FIA regulations, citing the article
            when she leans on a rule.
          </p>
        </header>

        <Panel title="What she can be asked">
          <ul className="examples">
            <li>What's the best strategy for a car starting tenth?</li>
            <li>One stop or two here, and when should the leader pit?</li>
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
    setPending({ question: text, answer: '' })
    setQuestion('')

    try {
      const response = await streamTurn(
        active
          ? { threadId: active.thread_id, question: text }
          : { season: race.season, round: race.round, question: text },
        (soFar) => setPending({ question: text, answer: soFar }),
      )
      const thread = await readThread(response.thread_id)
      setActive(thread)
      setThreads(await listThreads())
    } catch (err) {
      setError(err)
      // The server keeps the question when the model fails mid-answer, so
      // reload the thread rather than guess; a new thread may not exist yet.
      if (active) readThread(active.thread_id).then(setActive).catch(() => {})
      setQuestion(text)
    } finally {
      setPending(null)
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
        {pending && (
          <>
            <div className="turn user"><p>{pending.question}</p></div>
            {pending.answer ? (
              <div className="turn assistant" aria-live="polite">
                {pending.answer.split('\n\n').map((para, j) => <p key={j}>{para}</p>)}
              </div>
            ) : (
              <Loading what="Bernie is thinking" />
            )}
          </>
        )}
        <div ref={endRef} />

        {error && <BernieError error={error} />}

        <form className="composer" onSubmit={send}>
          <Input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={active ? 'Ask a follow-up…' : 'What strategy would you run from P5?'}
            disabled={busy || !race}
            maxLength={2000}
            aria-label="Ask Bernie about this weekend"
          />
          <Button type="submit" disabled={busy || !question.trim() || !race}>
            Send
          </Button>
        </form>

        <p className="disclaimer">
          Bernie is an AI pit-wall strategist inspired by the role of a real race
          strategist. She is not a real person and does not represent anyone.
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
    billing: 'The language model account needs credit before she can answer.',
    provider_error: 'The language model could not be reached just now — try again shortly.',
  }
  return <Unavailable what="Bernie" reason={messages[reason] ?? error.message} />
}
