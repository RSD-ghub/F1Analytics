import { useState, useEffect, useCallback } from 'react'
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  LineChart, Line
} from 'recharts'
import Layout from '../components/Layout'
import ChartCard from '../components/ChartCard'
import KpiCard from '../components/KpiCard'
import { getDrivers, getSeasons, getDriverAnalytics } from '../api/f1Api'

const TEAM_COLORS = ['#ff3d5a','#ffd166','#18c6b3','#4cc9f0','#ef476f','#f77f00','#0096c7','#a7c957','#e63946','#48cae4']
const COMPOUND_COLOR = { SOFT: '#e63946', MEDIUM: '#ffd166', HARD: '#c5cfde', INTERMEDIATE: '#57cc99', WET: '#4cc9f0' }

const TT = {
  contentStyle: { background: '#1a2035', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, fontSize: 11 }
}

const tickStyle = { fill: '#c5cfde', fontSize: 10 }
const gridStyle = { stroke: 'rgba(255,255,255,0.08)' }

export default function Driver() {
  const [drivers, setDrivers] = useState([])
  const [seasons, setSeasons] = useState([])
  const [selectedDriver, setSelectedDriver] = useState('')
  const [selectedSeason, setSelectedSeason] = useState('ALL')
  const [analytics, setAnalytics] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadMeta = useCallback(async () => {
    try {
      const [drvs, seas] = await Promise.all([getDrivers(), getSeasons()])
      const sortedSeas = [...seas].sort((a, b) => b - a)
      setDrivers(drvs)
      setSeasons(sortedSeas)
      if (drvs.length) setSelectedDriver(drvs[0])
    } catch (e) { setError('Failed to load metadata') }
  }, [])

  useEffect(() => { loadMeta() }, [loadMeta])

  const loadAnalytics = useCallback(async (driver, season) => {
    if (!driver) return
    setLoading(true)
    setError('')
    try {
      const data = await getDriverAnalytics(driver, season === 'ALL' ? null : season)
      setAnalytics(data)
    } catch (e) {
      setError('Failed to load driver analytics')
      setAnalytics(null)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (selectedDriver) loadAnalytics(selectedDriver, selectedSeason)
  }, [selectedDriver, selectedSeason, loadAnalytics])

  const a = analytics

  // Domain availability count
  const domainCount = [a?.lapTrend, a?.stintSummary, a?.pitStopStats, a?.weatherSummary, a?.telemetrySummary, a?.raceControlTimeline]
    .filter(x => Array.isArray(x) && x.length > 0).length

  // chart4: points by season (ALL) or points by race (single)
  const chart4Data = selectedSeason === 'ALL'
    ? (a?.pointsBySeason ?? []).map(d => ({ name: String(d.season), value: d.points }))
    : (a?.pointsByRace ?? []).map(d => ({ name: d.label ?? `R${d.round}`, value: d.points }))
  const chart4Title = selectedSeason === 'ALL' ? 'Points by Season' : 'Points by Round'

  // Stint compound chart
  const stintData = (a?.stintSummary ?? []).map(d => ({
    name: d.compound,
    value: d.laps,
    fill: COMPOUND_COLOR[d.compound?.toUpperCase()] ?? '#888'
  }))

  // Pit stops
  const pitData = (a?.pitStopStats ?? []).map(d => ({ name: d.label, value: d.avgDurationSeconds }))

  // Weather
  const weatherData = (a?.weatherSummary ?? []).map(d => ({ name: d.label, air: d.avgAirTemp, track: d.avgTrackTemp }))

  // Telemetry
  const telData = (a?.telemetrySummary ?? []).map(d => ({ name: d.label, max: d.maxSpeed, avg: d.avgSpeed }))

  // Race control
  const rcCategories = (a?.raceControlTimeline ?? []).reduce((acc, e) => {
    acc[e.category] = (acc[e.category] ?? 0) + 1
    return acc
  }, {})
  const rcBarData = Object.entries(rcCategories).map(([k, v]) => ({ name: k, value: v }))
  const rcPreview = (a?.raceControlTimeline ?? []).slice(-6).reverse()

  return (
    <Layout>
      {/* Toolbar */}
      <div className="toolbar">
        <button className="btn btn--secondary" onClick={() => loadAnalytics(selectedDriver, selectedSeason)}>Refresh</button>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginLeft: 'auto' }}>
          <span className="toolbar__label">Driver</span>
          <select className="select" value={selectedDriver} onChange={e => setSelectedDriver(e.target.value)}>
            {drivers.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
          <span className="toolbar__label">Season</span>
          <select className="select" value={selectedSeason} onChange={e => setSelectedSeason(e.target.value)}>
            <option value="ALL">All Seasons</option>
            {seasons.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>

      {/* Status bar */}
      <div className="status-bar">
        <span className="status-bar__driver">{selectedDriver || '—'}</span>
        <span className="status-bar__scope">{selectedSeason === 'ALL' ? 'All Seasons' : `Season ${selectedSeason}`}</span>
        {domainCount > 0 && <span className="status-bar__domain">{domainCount}/6 data feeds</span>}
        {loading && <span className="status-bar__loading">Loading…</span>}
        {error && <span className="status-bar__error">{error}</span>}
      </div>

      {/* KPI cards */}
      <div className="kpi-grid">
        <KpiCard label="Total Points" value={a?.totalPoints != null ? a.totalPoints.toFixed(1) : null} />
        <KpiCard label="Wins" value={a?.wins} />
        <KpiCard label="Podiums" value={a?.podiums} />
        <KpiCard label="Races" value={a?.races} />
        <KpiCard label="Avg Finish" value={a?.avgFinish != null ? a.avgFinish.toFixed(1) : null} />
      </div>

      {/* Analytics charts */}
      <div className="analytics-grid">

        {/* 1. Points by Race */}
        <ChartCard title="Points by Race" variant="warm">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={(a?.pointsByRace ?? []).map(d => ({ name: d.label, value: d.points }))} margin={{ bottom: 36, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} />
              <XAxis dataKey="name" tick={{ ...tickStyle }} angle={-40} textAnchor="end" interval="preserveStartEnd" />
              <YAxis tick={{ ...tickStyle }} />
              <Tooltip {...TT} formatter={v => [`${v} pts`]} />
              <Line type="monotone" dataKey="value" stroke="#ff3d5a" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 2. Finish Position */}
        <ChartCard title="Finish Position Trend" kpi="lower = better" variant="cool">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={(a?.finishByRace ?? []).map(d => ({ name: d.label, value: d.position }))} margin={{ bottom: 36, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} />
              <XAxis dataKey="name" tick={{ ...tickStyle }} angle={-40} textAnchor="end" interval="preserveStartEnd" />
              <YAxis reversed tick={{ ...tickStyle }} />
              <Tooltip {...TT} formatter={v => [`P${v}`]} />
              <Line type="monotone" dataKey="value" stroke="#4cc9f0" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 3. Team Points Share */}
        <ChartCard title="Team Points Share" variant="warm">
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie data={(a?.teamPoints ?? []).map(d => ({ name: d.team, value: Math.round(d.points) }))} cx="50%" cy="50%" outerRadius={85} dataKey="value">
                {(a?.teamPoints ?? []).map((_, i) => <Cell key={i} fill={TEAM_COLORS[i % TEAM_COLORS.length]} />)}
              </Pie>
              <Tooltip {...TT} formatter={v => [`${v} pts`]} />
              <Legend wrapperStyle={{ fontSize: 10, color: '#c5cfde' }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 4. Points by Season / Round */}
        <ChartCard title={chart4Title} variant="cool">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chart4Data} margin={{ bottom: 36, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} vertical={false} />
              <XAxis dataKey="name" tick={{ ...tickStyle }} angle={-40} textAnchor="end" interval="preserveStartEnd" />
              <YAxis tick={{ ...tickStyle }} />
              <Tooltip {...TT} formatter={v => [`${v} pts`]} />
              <Bar dataKey="value" fill="#18c6b3" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 5. Podium / Win Breakdown */}
        <ChartCard title="Win / Podium Breakdown" variant="neutral">
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie
                data={(a?.podiumWinBreakdown ?? []).map(d => ({ name: d.bucket, value: d.count }))}
                cx="50%" cy="50%" outerRadius={85} dataKey="value"
              >
                {(a?.podiumWinBreakdown ?? []).map((d) => {
                  const c = d.bucket === 'Win' ? '#ff3d5a' : d.bucket === 'Podium' ? '#ffd166' : '#6b7280'
                  return <Cell key={d.bucket} fill={c} />
                })}
              </Pie>
              <Tooltip {...TT} />
              <Legend wrapperStyle={{ fontSize: 10, color: '#c5cfde' }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 6. Lap Trend */}
        <ChartCard title="Avg Lap Time Trend" kpi="from FastF1" variant="cool">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={(a?.lapTrend ?? []).map(d => ({ name: d.label, value: d.avgLapTimeSeconds }))} margin={{ bottom: 36, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} />
              <XAxis dataKey="name" tick={{ ...tickStyle }} angle={-40} textAnchor="end" interval="preserveStartEnd" />
              <YAxis tick={{ ...tickStyle }} unit="s" />
              <Tooltip {...TT} formatter={v => [`${Number(v).toFixed(3)}s`]} />
              <Line type="monotone" dataKey="value" stroke="#90e0ef" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 7. Stint Compound Summary */}
        <ChartCard title="Stint Compound Summary" variant="amber">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={stintData} layout="vertical" margin={{ left: 8, right: 24 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} horizontal={false} />
              <XAxis type="number" tick={{ ...tickStyle }} unit=" laps" />
              <YAxis type="category" dataKey="name" tick={{ ...tickStyle }} width={90} />
              <Tooltip {...TT} formatter={v => [`${v} laps`]} />
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {stintData.map((d, i) => <Cell key={i} fill={d.fill} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 8. Pit Stop Summary */}
        <ChartCard title="Pit Stop Duration" kpi="avg seconds per race" variant="amber">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={pitData} margin={{ bottom: 36, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} vertical={false} />
              <XAxis dataKey="name" tick={{ ...tickStyle }} angle={-40} textAnchor="end" interval="preserveStartEnd" />
              <YAxis tick={{ ...tickStyle }} unit="s" />
              <Tooltip {...TT} formatter={v => [`${Number(v).toFixed(2)}s`]} />
              <Bar dataKey="value" fill="#ffd166" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 9. Weather Summary */}
        <ChartCard title="Weather Summary" kpi="air & track temperature" variant="cool">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={weatherData} margin={{ bottom: 36, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} />
              <XAxis dataKey="name" tick={{ ...tickStyle }} angle={-40} textAnchor="end" interval="preserveStartEnd" />
              <YAxis tick={{ ...tickStyle }} unit="°C" />
              <Tooltip {...TT} formatter={v => [`${Number(v).toFixed(1)}°C`]} />
              <Line type="monotone" dataKey="air" stroke="#4cc9f0" dot={false} strokeWidth={2} name="Air" />
              <Line type="monotone" dataKey="track" stroke="#ffd166" dot={false} strokeWidth={2} name="Track" />
              <Legend wrapperStyle={{ fontSize: 10, color: '#c5cfde' }} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 10. Telemetry Speed */}
        <ChartCard title="Speed Summary" kpi="max & avg km/h" variant="warm">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={telData} margin={{ bottom: 36, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} vertical={false} />
              <XAxis dataKey="name" tick={{ ...tickStyle }} angle={-40} textAnchor="end" interval="preserveStartEnd" />
              <YAxis tick={{ ...tickStyle }} unit=" km/h" />
              <Tooltip {...TT} formatter={v => [`${Math.round(v)} km/h`]} />
              <Bar dataKey="max" fill="#ff3d5a" radius={[4, 4, 0, 0]} name="Max Speed" />
              <Bar dataKey="avg" fill="#18c6b3" radius={[4, 4, 0, 0]} name="Avg Speed" />
              <Legend wrapperStyle={{ fontSize: 10, color: '#c5cfde' }} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* 11. Race Control */}
        <ChartCard title="Race Control Events" variant="cool" style={{ gridColumn: 'span 2' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={rcBarData} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} horizontal={false} />
                <XAxis type="number" tick={{ ...tickStyle }} />
                <YAxis type="category" dataKey="name" tick={{ ...tickStyle }} width={90} />
                <Tooltip {...TT} />
                <Bar dataKey="value" fill="#18c6b3" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <div className="rc-feed">
              {rcPreview.length === 0
                ? <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>No race control data</span>
                : rcPreview.map((e, i) => (
                  <div key={i} className="rc-feed__item">
                    <span className="rc-feed__meta">{e.label} — {e.category}</span>
                    <span className="rc-feed__msg">{e.message}</span>
                  </div>
                ))
              }
            </div>
          </div>
        </ChartCard>
      </div>

      {/* Race Results Table */}
      <div style={{ marginTop: 24 }}>
        <h3 style={{ fontSize: '0.9rem', color: 'var(--text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Race Results
        </h3>
        <div className="results-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Season</th><th>Round</th><th>Race</th><th>Circuit</th>
                <th>Date</th><th>Team</th><th>Pos</th><th>Points</th>
              </tr>
            </thead>
            <tbody>
              {(a?.raceResults ?? []).map((r, i) => (
                <tr key={r.id ?? i}>
                  <td>{r.season}</td>
                  <td>{r.round}</td>
                  <td>{r.raceName}</td>
                  <td>{r.circuit}</td>
                  <td>{r.raceDate ? new Date(r.raceDate).toLocaleDateString() : '—'}</td>
                  <td>{r.team}</td>
                  <td>{r.position}</td>
                  <td>{r.points}</td>
                </tr>
              ))}
              {(!a?.raceResults?.length) && (
                <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 24 }}>
                  {loading ? 'Loading…' : 'No results'}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </Layout>
  )
}
