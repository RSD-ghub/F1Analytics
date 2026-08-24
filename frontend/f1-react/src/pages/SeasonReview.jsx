import { useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Layout from '../components/Layout'
import { getSeasons, generateSeasonReview } from '../api/f1Api'

export default function SeasonReview() {
  const [seasons, setSeasons] = useState([])
  const [season, setSeason] = useState('')
  const [loading, setLoading] = useState(false)
  const [markdownText, setMarkdownText] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    getSeasons()
      .then(data => {
        const sorted = [...data].sort((a, b) => b - a)
        setSeasons(sorted)
        if (sorted.length) setSeason(String(sorted[0]))
      })
      .catch(() => setError('Failed to load seasons'))
  }, [])

  const handleGenerate = useCallback(async () => {
    if (!season) return
    setLoading(true)
    setMarkdownText('')
    setError('')
    try {
      const res = await generateSeasonReview(season)
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
      setError(e.message || 'Failed to generate season review. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }, [season])

  return (
    <Layout>
      <div className="dashboard-page">
        <div className="toolbar">
          <span className="toolbar__label">Season</span>
          <select className="select" value={season} onChange={e => { setSeason(e.target.value); setMarkdownText('') }}>
            {seasons.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <button
            className="btn btn--primary"
            onClick={handleGenerate}
            disabled={loading || !season}
          >
            {loading ? 'Generating…' : 'Generate Summary'}
          </button>
          {loading && <span className="spinner" />}
          <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
            Powered by Claude AI
          </span>
        </div>

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
              <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p style={{ fontSize: '0.88rem' }}>Select a season and click <strong style={{ color: 'var(--text)' }}>Generate Summary</strong> to get an AI-powered review</p>
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
