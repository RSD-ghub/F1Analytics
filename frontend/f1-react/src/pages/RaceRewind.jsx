import { useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Layout from '../components/Layout'
import { getSeasons, getRaces, generateRaceRewind } from '../api/f1Api'

export default function RaceRewind() {
  const [currentSeason, setCurrentSeason] = useState(null)
  const [races, setRaces] = useState([])
  const [selectedRound, setSelectedRound] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingRaces, setLoadingRaces] = useState(false)
  const [markdownText, setMarkdownText] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    getSeasons()
      .then(data => {
        const sorted = [...data].sort((a, b) => b - a)
        if (sorted.length) setCurrentSeason(sorted[0])
      })
      .catch(() => setError('Failed to load seasons'))
  }, [])

  useEffect(() => {
    if (!currentSeason) return
    setLoadingRaces(true)
    getRaces(currentSeason)
      .then(data => {
        setRaces(data)
        if (data.length) setSelectedRound(String(data[0].round))
      })
      .catch(() => setError('Failed to load races for current season'))
      .finally(() => setLoadingRaces(false))
  }, [currentSeason])

  const handleAnalyze = useCallback(async () => {
    if (!currentSeason || !selectedRound) return
    setLoading(true)
    setMarkdownText('')
    setError('')
    try {
      const res = await generateRaceRewind(currentSeason, selectedRound)
      if (!res.ok) {
        const msg = await res.text()
        throw new Error(msg || `HTTP ${res.status}`)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        setMarkdownText(prev => prev + decoder.decode(value, { stream: true }))
      }
    } catch (e) {
      setError(e.message || 'Failed to generate race analysis. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }, [currentSeason, selectedRound])

  const selectedRace = races.find(r => String(r.round) === selectedRound)

  return (
    <Layout>
      <div className="dashboard-page">
        <div className="toolbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="toolbar__label">Season</span>
            <span style={{
              background: 'rgba(255,61,90,0.15)', color: 'var(--red)',
              padding: '4px 10px', borderRadius: 6, fontWeight: 700, fontSize: '0.85rem'
            }}>
              {currentSeason ?? '—'} (Current)
            </span>
          </div>
          <span className="toolbar__label">Race</span>
          <select
            className="select"
            value={selectedRound}
            onChange={e => { setSelectedRound(e.target.value); setMarkdownText('') }}
            disabled={loadingRaces || races.length === 0}
            style={{ minWidth: 220 }}
          >
            {loadingRaces
              ? <option>Loading races…</option>
              : races.map(r => (
                  <option key={r.round} value={r.round}>
                    R{r.round} — {r.raceName}
                  </option>
                ))
            }
          </select>
          <button
            className="btn btn--primary"
            onClick={handleAnalyze}
            disabled={loading || !selectedRound || loadingRaces}
          >
            {loading ? 'Analyzing…' : 'Analyze Race'}
          </button>
          {loading && <span className="spinner" />}
          <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
            Powered by Claude AI
          </span>
        </div>

        {selectedRace && (
          <div className="status-bar" style={{ marginBottom: 12 }}>
            <span className="status-bar__driver">
              {selectedRace.raceName}
            </span>
            {selectedRace.circuit && <span className="status-bar__scope">{selectedRace.circuit}</span>}
            {selectedRace.raceDate && (
              <span style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>
                {new Date(selectedRace.raceDate).toLocaleDateString()}
              </span>
            )}
          </div>
        )}

        {error && (
          <div className="status-bar" style={{ marginBottom: 12 }}>
            <span className="status-bar__error">{error}</span>
          </div>
        )}

        {!markdownText && !loading && !error && (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            minHeight: 300, gap: 12, color: 'var(--text-muted)'
          }}>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 21l1.65-3.8a9 9 0 1 1 3.4 2.9L3 21" />
            </svg>
            <p style={{ fontSize: '0.88rem' }}>
              Select a race and click <strong style={{ color: 'var(--text)' }}>Analyze Race</strong> for an AI-powered race breakdown
            </p>
            <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              Only {currentSeason} season races are available for Race Rewind
            </p>
          </div>
        )}

        {markdownText && (
          <div className="ai-report-card">
            <div className="ai-report-card__body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdownText}</ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}
