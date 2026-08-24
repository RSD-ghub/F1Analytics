import { useState, useEffect, useCallback } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell
} from 'recharts'
import Layout from '../components/Layout'
import { getSeasons, getRaces, getPrediction, refreshPrediction } from '../api/f1Api'

// Confidence → colour: high=teal, mid=amber, low=red
function confidenceColor(c) {
  if (c >= 0.70) return 'var(--teal)'
  if (c >= 0.45) return 'var(--amber)'
  return 'var(--red)'
}

// Medal colours for top 3
const MEDAL = { 1: '#ffd166', 2: '#c5cfde', 3: '#cd7f32' }
const COMPOUND_COLORS = {
  SOFT: '#ff3d5a', MEDIUM: '#ffd166', HARD: '#c5cfde',
  INTERMEDIATE: '#18c6b3', WET: '#4cc9f0',
}

function fmtLapTime(secs) {
  if (!secs) return '—'
  const m = Math.floor(secs / 60)
  const s = (secs % 60).toFixed(3).padStart(6, '0')
  return `${m}:${s}`
}

export default function RacePredict() {
  const [seasons, setSeasons]           = useState([])
  const [season, setSeason]             = useState('')
  const [races, setRaces]               = useState([])
  const [round, setRound]               = useState('')
  const [prediction, setPrediction]     = useState(null)
  const [loading, setLoading]           = useState(false)
  const [loadingRaces, setLoadingRaces] = useState(false)
  const [error, setError]               = useState('')

  // Load seasons on mount
  useEffect(() => {
    getSeasons()
      .then(data => {
        const sorted = [...data].sort((a, b) => b - a)
        if (sorted.length) setSeason(String(sorted[0]))
        setSeasons(sorted)
      })
      .catch(() => setError('Failed to load seasons'))
  }, [])

  // Load races whenever season changes
  useEffect(() => {
    if (!season) return
    setLoadingRaces(true)
    setRound('')
    setPrediction(null)
    getRaces(season)
      .then(data => {
        setRaces(data)
        if (data.length) setRound(String(data[0].round))
      })
      .catch(() => setError('Failed to load races'))
      .finally(() => setLoadingRaces(false))
  }, [season])

  const runPrediction = useCallback(async (forceRefresh = false) => {
    if (!season || !round) return
    setLoading(true)
    setPrediction(null)
    setError('')
    try {
      const data = forceRefresh
        ? await refreshPrediction(season, round)
        : await getPrediction(season, round)
      if (data.error) throw new Error(data.error)
      setPrediction(data)
    } catch (e) {
      setError(e.message || 'Prediction failed. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }, [season, round])

  const selectedRace = races.find(r => String(r.round) === round)

  return (
    <Layout>
      <div className="dashboard-page">

        {/* ── Toolbar ── */}
        <div className="toolbar">
          <span className="toolbar__label">Season</span>
          <select
            className="select"
            value={season}
            onChange={e => { setSeason(e.target.value); setPrediction(null) }}
            disabled={seasons.length === 0}
          >
            {seasons.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          <span className="toolbar__label">Race</span>
          <select
            className="select"
            value={round}
            onChange={e => { setRound(e.target.value); setPrediction(null) }}
            disabled={loadingRaces || races.length === 0}
            style={{ minWidth: 220 }}
          >
            {loadingRaces
              ? <option>Loading races…</option>
              : races.map(r => (
                  <option key={r.round} value={r.round}>R{r.round} — {r.raceName}</option>
                ))
            }
          </select>

          <button
            className="btn btn--primary"
            onClick={() => runPrediction(false)}
            disabled={loading || !round || loadingRaces}
          >
            {loading ? 'Predicting…' : 'Run Prediction'}
          </button>

          {prediction && (
            <button
              className="btn btn--secondary"
              onClick={() => runPrediction(true)}
              disabled={loading}
              title="Evict cache and re-run the model"
            >
              ↻ Refresh
            </button>
          )}

          {loading && <span className="spinner" />}
          <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
            Trained on all seasons · scikit-learn
          </span>
        </div>

        {/* ── Race info bar ── */}
        {selectedRace && (
          <div className="status-bar" style={{ marginBottom: 12 }}>
            <span className="status-bar__driver">{selectedRace.raceName}</span>
            {selectedRace.circuit && (
              <span className="status-bar__scope">{selectedRace.circuit}</span>
            )}
            {selectedRace.raceDate && (
              <span style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>
                {new Date(selectedRace.raceDate).toLocaleDateString()}
              </span>
            )}
          </div>
        )}

        {/* ── Error ── */}
        {error && (
          <div className="status-bar" style={{ marginBottom: 12 }}>
            <span className="status-bar__error">{error}</span>
          </div>
        )}

        {/* ── Empty state ── */}
        {!prediction && !loading && !error && (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            justifyContent: 'center', minHeight: 320, gap: 12,
            color: 'var(--text-muted)',
          }}>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.5">
              <path d="M12 2L2 7l10 5 10-5-10-5z"/>
              <path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>
            </svg>
            <p style={{ fontSize: '0.88rem' }}>
              Select a race and click <strong style={{ color: 'var(--text)' }}>Run Prediction</strong>
            </p>
            <p style={{ fontSize: '0.78rem' }}>
              First run trains the model — takes ~10s. Subsequent runs hit the cache instantly.
            </p>
          </div>
        )}

        {/* ── Results ── */}
        {prediction && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

            {/* Model info badge */}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <Badge label="Model" value={prediction.modelInfo?.type} color="var(--teal)" />
              <Badge
                label="Trained on"
                value={`${prediction.modelInfo?.totalHistoricalRaces} races · ${
                  prediction.modelInfo?.trainedOnSeasons?.length
                } seasons`}
                color="var(--teal)"
              />
              <Badge
                label="Circuit history"
                value={`${prediction.modelInfo?.circuitHistoricalRaces} races at this circuit`}
                color="var(--amber)"
              />
            </div>

            {/* Top row: predictions table + strategy */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 16 }}>

              {/* ── Predictions table ── */}
              <div className="chart-card chart-card--neutral">
                <div className="chart-card__title">Predicted Finishing Order</div>
                <div className="chart-card__body" style={{ padding: 0 }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-muted)' }}>
                        <th style={th}>Pred</th>
                        <th style={th}>Driver</th>
                        <th style={th}>Team</th>
                        <th style={th}>Pts</th>
                        <th style={th}>Confidence</th>
                        <th style={th}>Avg @ Circuit</th>
                        <th style={th}>Season Avg</th>
                        {prediction.predictions?.some(p => p.actualPosition) && (
                          <th style={th}>Actual</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {prediction.predictions?.map((p, i) => (
                        <tr
                          key={p.driver}
                          style={{
                            borderBottom: '1px solid var(--border)',
                            background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)',
                          }}
                        >
                          <td style={{ ...td, fontWeight: 700, color: MEDAL[p.predictedPosition] || 'var(--text)' }}>
                            P{p.predictedPosition}
                          </td>
                          <td style={{ ...td, fontWeight: 600, color: 'var(--text)' }}>
                            {p.historicalWinsAtCircuit > 0 && (
                              <span title={`${p.historicalWinsAtCircuit} win(s) here`}
                                style={{ color: MEDAL[1], marginRight: 4, fontSize: '0.75rem' }}>
                                ★{p.historicalWinsAtCircuit}
                              </span>
                            )}
                            {p.driver}
                          </td>
                          <td style={{ ...td, color: 'var(--text-muted)' }}>{p.team}</td>
                          <td style={{ ...td, color: 'var(--amber)', fontWeight: 600 }}>
                            {p.predictedPoints || '—'}
                          </td>
                          <td style={td}>
                            <span style={{
                              color: confidenceColor(p.positionConfidence),
                              fontWeight: 600,
                            }}>
                              {Math.round(p.positionConfidence * 100)}%
                            </span>
                          </td>
                          <td style={{ ...td, color: 'var(--text-muted)' }}>
                            {p.historicalAvgPositionAtCircuit?.toFixed(1) ?? '—'}
                          </td>
                          <td style={{ ...td, color: 'var(--text-muted)' }}>
                            {p.seasonAvgPosition?.toFixed(1) ?? '—'}
                          </td>
                          {prediction.predictions?.some(d => d.actualPosition) && (
                            <td style={{
                              ...td,
                              color: p.actualPosition === p.predictedPosition
                                ? 'var(--teal)'
                                : Math.abs(p.actualPosition - p.predictedPosition) <= 2
                                  ? 'var(--amber)'
                                  : 'var(--text-muted)',
                              fontWeight: 600,
                            }}>
                              {p.actualPosition ? `P${p.actualPosition}` : '—'}
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* ── Strategy card ── */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="chart-card chart-card--warm">
                  <div className="chart-card__title">Pit Strategy</div>
                  <div className="chart-card__body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                    <StatRow
                      label="Recommended pit lap"
                      value={prediction.strategy?.recommendedPitLap > 0
                        ? `Lap ${prediction.strategy.recommendedPitLap}`
                        : 'N/A (no pit data)'}
                    />
                    <StatRow
                      label="Compound strategy"
                      value={prediction.strategy?.recommendedCompoundStrategy || '—'}
                      highlight
                    />
                    <StatRow
                      label="Avg pit stops here"
                      value={prediction.strategy?.avgPitStopsAtCircuit?.toFixed(1) ?? '—'}
                    />
                  </div>
                </div>

                {/* Confidence legend */}
                <div className="chart-card chart-card--neutral">
                  <div className="chart-card__title">Confidence Key</div>
                  <div className="chart-card__body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <LegendRow color="var(--teal)"  label="High (≥70%)" />
                    <LegendRow color="var(--amber)" label="Medium (45–69%)" />
                    <LegendRow color="var(--red)"   label="Low (<45%)" />
                    <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>
                      Based on how cleanly the model's raw score rounds to a position.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* ── Lap time forecast ── */}
            {prediction.lapTimeForecast?.length > 0 && (
              <div className="chart-card chart-card--cool">
                <div className="chart-card__title">
                  Average Lap Time by Compound — historical at this circuit
                </div>
                <div className="chart-card__body">
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart
                      data={prediction.lapTimeForecast}
                      margin={{ top: 8, right: 24, left: 8, bottom: 4 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.07)" />
                      <XAxis dataKey="compound" tick={{ fill: '#c5cfde', fontSize: 12 }} />
                      <YAxis
                        tickFormatter={v => fmtLapTime(v)}
                        tick={{ fill: '#c5cfde', fontSize: 11 }}
                        domain={['auto', 'auto']}
                        width={60}
                      />
                      <Tooltip
                        formatter={(v, name) => [fmtLapTime(v), 'Avg lap time']}
                        contentStyle={{ background: '#1a2035', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 8 }}
                        labelStyle={{ color: '#f7f9fc' }}
                      />
                      <Bar dataKey="predictedAvgLapTimeSeconds" radius={[4, 4, 0, 0]}>
                        {prediction.lapTimeForecast.map(entry => (
                          <Cell
                            key={entry.compound}
                            fill={COMPOUND_COLORS[entry.compound] || '#4cc9f0'}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8 }}>
                    {prediction.lapTimeForecast.map(f => (
                      <span key={f.compound} style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        <span style={{ color: COMPOUND_COLORS[f.compound] || '#4cc9f0' }}>●</span>
                        {' '}{f.compound}: {fmtLapTime(f.predictedAvgLapTimeSeconds)}
                        <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>
                          (n={f.sampleSize})
                        </span>
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            )}

          {/* ── Model Validation (past races only) ── */}
          {prediction.validation && (
            <div className="chart-card chart-card--neutral">
              <div className="chart-card__title">
                Model Accuracy — Leave-One-Out Validation
              </div>
              <div className="chart-card__body" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

                {/* Accuracy metric badges */}
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <AccBadge
                    label="Avg error"
                    value={`±${prediction.validation.mae} places`}
                    good={prediction.validation.mae < 3}
                    warn={prediction.validation.mae < 5}
                  />
                  <AccBadge
                    label="Rank correlation"
                    value={`ρ = ${prediction.validation.spearmanRho}`}
                    good={prediction.validation.spearmanRho > 0.6}
                    warn={prediction.validation.spearmanRho > 0.35}
                  />
                  <AccBadge
                    label="Winner"
                    value={prediction.validation.winnerCorrect ? '✓ Correct' : '✗ Missed'}
                    good={prediction.validation.winnerCorrect}
                    warn={false}
                  />
                  <AccBadge
                    label="Podium"
                    value={`${prediction.validation.podiumHits}/3`}
                    good={prediction.validation.podiumHits >= 2}
                    warn={prediction.validation.podiumHits >= 1}
                  />
                  <AccBadge
                    label="Top 5"
                    value={`${prediction.validation.top5Hits}/5`}
                    good={prediction.validation.top5Hits >= 3}
                    warn={prediction.validation.top5Hits >= 2}
                  />
                  <AccBadge
                    label="Top 10"
                    value={`${prediction.validation.top10Hits}/10`}
                    good={prediction.validation.top10Hits >= 7}
                    warn={prediction.validation.top10Hits >= 5}
                  />
                </div>

                {/* Predicted vs Actual comparison table */}
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-muted)' }}>
                      <th style={th}>Driver</th>
                      <th style={th}>Actual</th>
                      <th style={th}>Predicted</th>
                      <th style={th}>Error</th>
                      <th style={{ ...th, width: '40%' }}>Accuracy bar</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prediction.validation.comparisons.map((c, i) => {
                      const err   = c.error  // actual - predicted; positive = better than predicted
                      const absErr = Math.abs(err)
                      const errColor = absErr === 0 ? 'var(--teal)'
                                     : absErr <= 2  ? 'var(--amber)'
                                     :                'var(--red)'
                      const barPct = Math.max(0, 100 - absErr * 10)
                      return (
                        <tr key={c.driver} style={{
                          borderBottom: '1px solid var(--border)',
                          background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)',
                        }}>
                          <td style={td}>{c.driver}</td>
                          <td style={{ ...td, fontWeight: 600 }}>P{c.actualPosition}</td>
                          <td style={{ ...td, color: 'var(--text-muted)' }}>P{c.predictedPosition}</td>
                          <td style={{ ...td, color: errColor, fontWeight: 600 }}>
                            {err === 0 ? '✓' : err > 0 ? `+${err}` : err}
                          </td>
                          <td style={td}>
                            <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, maxWidth: 180 }}>
                              <div style={{
                                width: `${barPct}%`, height: '100%',
                                background: errColor, borderRadius: 2,
                                transition: 'width 0.4s ease',
                              }} />
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>

                {/* Feature importances */}
                {prediction.modelInfo?.featureImportances && (
                  <div>
                    <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 10 }}>
                      What the model relies on
                    </div>
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                      {Object.entries(prediction.modelInfo.featureImportances)
                        .sort((a, b) => b[1] - a[1])
                        .map(([name, val]) => (
                          <div key={name} style={{
                            background: 'var(--surface-2)', borderRadius: 8,
                            padding: '8px 12px', minWidth: 140,
                            border: '1px solid var(--border)',
                          }}>
                            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 6 }}>
                              {name.replace(/_/g, ' ')}
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <div style={{ flex: 1, height: 5, background: 'var(--border)', borderRadius: 3 }}>
                                <div style={{
                                  width: `${Math.round(val * 100)}%`, height: '100%',
                                  background: 'var(--teal)', borderRadius: 3,
                                }} />
                              </div>
                              <span style={{ fontSize: '0.82rem', color: 'var(--teal)', fontWeight: 700, minWidth: 32 }}>
                                {Math.round(val * 100)}%
                              </span>
                            </div>
                          </div>
                        ))}
                    </div>
                  </div>
                )}

                <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', margin: 0 }}>
                  Trained on all historical races <em>except</em> this one, then validated against the actual result.
                  Positive error = driver finished better than predicted. {prediction.validation.driversCompared} drivers compared.
                  As more race results are ingested, predictions for future races improve automatically.
                </p>

              </div>
            </div>
          )}

          </div>
        )}
      </div>
    </Layout>
  )
}

// ── Small helper components ───────────────────────────────────────────────────

function AccBadge({ label, value, good, warn }) {
  const color = good ? 'var(--teal)' : warn ? 'var(--amber)' : 'var(--red)'
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 2,
      background: 'var(--surface-2)', border: `1px solid ${color}40`,
      borderRadius: 8, padding: '6px 12px', minWidth: 90,
    }}>
      <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {label}
      </span>
      <span style={{ fontSize: '0.85rem', fontWeight: 700, color }}>
        {value}
      </span>
    </div>
  )
}

function Badge({ label, value, color }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 6,
      background: 'var(--surface-2)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '4px 10px', fontSize: '0.78rem',
    }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}:</span>
      <span style={{ color, fontWeight: 600 }}>{value ?? '—'}</span>
    </div>
  )
}

function StatRow({ label, value, highlight }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {label}
      </span>
      <span style={{
        fontSize: '0.9rem', fontWeight: 700,
        color: highlight ? 'var(--amber)' : 'var(--text)',
      }}>
        {value}
      </span>
    </div>
  )
}

function LegendRow({ color, label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.78rem' }}>
      <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, flexShrink: 0 }} />
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
    </div>
  )
}

// ── Inline styles ─────────────────────────────────────────────────────────────

const th = {
  padding: '8px 12px', textAlign: 'left', fontWeight: 600,
  fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.04em',
}

const td = {
  padding: '7px 12px',
}
