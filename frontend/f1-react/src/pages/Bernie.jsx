import { useState, useEffect, useRef, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Layout from '../components/Layout'
import { getBernieUpcomingRaces, bernieChat } from '../api/f1Api'

const CURRENT_SEASON = new Date().getFullYear()

const STARTER_PROMPTS = [
  'What would be the best one-stop strategy for Lewis Hamilton?',
  'Who is most likely to benefit from an undercut here?',
  'How does the safety car change the strategy picture?',
  'Which drivers historically struggle at this circuit?',
]

export default function Bernie() {
  const [season]          = useState(CURRENT_SEASON)
  const [races, setRaces] = useState([])
  const [round, setRound] = useState(null)

  const [messages, setMessages]   = useState([])   // [{role, content, streaming?}]
  const [input, setInput]         = useState('')
  const [loading, setLoading]     = useState(false)
  const [racesLoading, setRacesLoading] = useState(true)
  const [error, setError]         = useState('')

  const bottomRef   = useRef(null)
  const inputRef    = useRef(null)
  const abortRef    = useRef(null)

  // ── Load upcoming races ───────────────────────────────────────────────────
  useEffect(() => {
    setRacesLoading(true)
    getBernieUpcomingRaces(season)
      .then(all => {
        const today = new Date().toISOString().slice(0, 10)
        const upcoming = all.filter(r => r.raceDate > today)
        setRaces(upcoming.length ? upcoming : all.slice(-5)) // fallback: last 5
        if (upcoming.length) setRound(upcoming[0].round)
        else if (all.length) setRound(all[all.length - 1].round)
      })
      .catch(() => setError('Could not load race schedule.'))
      .finally(() => setRacesLoading(false))
  }, [season])

  // ── Auto-scroll ───────────────────────────────────────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // ── Send message ──────────────────────────────────────────────────────────
  const sendMessage = useCallback(async (text) => {
    const userText = (text || input).trim()
    if (!userText || loading) return
    setInput('')
    setError('')

    const history = messages.map(m => ({ role: m.role, content: m.content }))
    const userMsg = { role: 'user', content: userText }
    const assistantMsg = { role: 'assistant', content: '', streaming: true }

    setMessages(prev => [...prev, userMsg, assistantMsg])
    setLoading(true)

    try {
      const res = await bernieChat(userText, history, season, round || 0)
      if (!res.body) throw new Error('No response body')

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let accumulated = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        accumulated += decoder.decode(value, { stream: true })
        setMessages(prev => {
          const updated = [...prev]
          updated[updated.length - 1] = { role: 'assistant', content: accumulated, streaming: true }
          return updated
        })
      }

      setMessages(prev => {
        const updated = [...prev]
        updated[updated.length - 1] = { role: 'assistant', content: accumulated, streaming: false }
        return updated
      })
    } catch (e) {
      setError(e.message || 'Bernie is temporarily unavailable.')
      setMessages(prev => prev.slice(0, -1)) // remove empty assistant bubble
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [input, loading, messages, season, round])

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  const selectedRace = races.find(r => r.round === round)

  return (
    <Layout>
      <div className="bernie-shell">

        {/* ── Top bar ──────────────────────────────────────────────────── */}
        <div className="bernie-topbar">
          <div className="bernie-topbar__left">
            <div className="bernie-avatar">B</div>
            <div>
              <div className="bernie-topbar__name">Bernie</div>
              <div className="bernie-topbar__role">F1 Race Strategist</div>
            </div>
          </div>

          <div className="bernie-topbar__right">
            {racesLoading ? (
              <span className="bernie-topbar__label">Loading schedule…</span>
            ) : races.length === 0 ? (
              <span className="bernie-topbar__label">No upcoming races found</span>
            ) : (
              <>
                <span className="bernie-topbar__label">Race context:</span>
                <select
                  className="bernie-race-select"
                  value={round || ''}
                  onChange={e => setRound(Number(e.target.value))}
                >
                  {races.map(r => (
                    <option key={r.round} value={r.round}>
                      R{r.round} — {r.raceName}
                    </option>
                  ))}
                </select>
                {selectedRace && (
                  <span className="bernie-topbar__date">{selectedRace.raceDate}</span>
                )}
              </>
            )}
          </div>
        </div>

        {/* ── Chat area ────────────────────────────────────────────────── */}
        <div className="bernie-messages">
          {messages.length === 0 && (
            <div className="bernie-welcome">
              <div className="bernie-welcome__intro">
                I'm Bernie — your F1 race strategist. I have access to lap-time
                telemetry, historical strategy patterns, and a predictive model
                trained on {CURRENT_SEASON > 2010 ? CURRENT_SEASON - 2010 : 1}+ seasons of data.
                Ask me anything about the upcoming race.
              </div>
              <div className="bernie-starters">
                {STARTER_PROMPTS.map(p => (
                  <button
                    key={p}
                    className="bernie-starter"
                    onClick={() => sendMessage(p)}
                    disabled={loading || !round}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`bernie-msg bernie-msg--${msg.role}`}>
              {msg.role === 'assistant' && (
                <div className="bernie-msg__avatar">B</div>
              )}
              <div className="bernie-msg__bubble">
                {msg.role === 'assistant' ? (
                  <>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {msg.content || '…'}
                    </ReactMarkdown>
                    {msg.streaming && <span className="bernie-cursor" />}
                  </>
                ) : (
                  msg.content
                )}
              </div>
            </div>
          ))}

          {error && (
            <div className="bernie-error">{error}</div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* ── Input bar ────────────────────────────────────────────────── */}
        <div className="bernie-inputbar">
          <textarea
            ref={inputRef}
            className="bernie-input"
            rows={1}
            placeholder={round ? `Ask Bernie about R${round}…` : 'Select a race above first'}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            disabled={loading || !round}
          />
          <button
            className="bernie-send"
            onClick={() => sendMessage()}
            disabled={loading || !input.trim() || !round}
          >
            {loading ? <span className="spinner" /> : '↑'}
          </button>
        </div>

      </div>
    </Layout>
  )
}
