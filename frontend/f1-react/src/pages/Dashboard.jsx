import { useState, useEffect, useCallback } from 'react'
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid
} from 'recharts'
import Layout from '../components/Layout'
import ChartCard from '../components/ChartCard'
import { getResults, getSeasons, getAnalytics, syncCurrentSeason, syncSeason } from '../api/f1Api'

const DRIVER_COLORS = ['#ff3d5a','#ff6b35','#ffd166','#e63946','#ff9f1c','#ef476f','#f77f00','#d62828','#fcbf49','#e45c3a']
const CTOR_COLORS   = ['#18c6b3','#4cc9f0','#0096c7','#023e8a','#48cae4','#00b4d8','#90e0ef','#0077b6','#caf0f8','#0466c8']

function PieLabel({ cx, cy, midAngle, outerRadius, name, value }) {
  const RADIAN = Math.PI / 180
  const radius = outerRadius + 28
  const x = cx + radius * Math.cos(-midAngle * RADIAN)
  const y = cy + radius * Math.sin(-midAngle * RADIAN)
  return (
    <text x={x} y={y} fill="#c5cfde" fontSize={10} textAnchor={x > cx ? 'start' : 'end'} dominantBaseline="central">
      {name} ({value})
    </text>
  )
}

const fmtDate = (v) => v ? new Date(v).toLocaleDateString() : '—'

export default function Dashboard() {
  const [seasons, setSeasons] = useState([])
  const [season, setSeason] = useState('')
  const [syncSeasonVal, setSyncSeasonVal] = useState(String(new Date().getFullYear()))
  const [analytics, setAnalytics] = useState(null)
  const [results, setResults] = useState([])
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)

  const loadSeasons = useCallback(async () => {
    try {
      const data = await getSeasons()
      const sorted = [...data].sort((a, b) => b - a)
      setSeasons(sorted)
      if (sorted.length) setSeason(String(sorted[0]))
    } catch (e) { console.error(e) }
  }, [])

  const loadAnalytics = useCallback(async (s) => {
    if (!s) return
    setLoading(true)
    try {
      const data = await getAnalytics(s)
      setAnalytics(data)
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [])

  const loadResults = useCallback(async () => {
    try {
      const data = await getResults()
      setResults(Array.isArray(data) ? data : [])
    } catch (e) { console.error(e) }
  }, [])

  useEffect(() => { loadSeasons() }, [loadSeasons])
  useEffect(() => { if (season) loadAnalytics(season) }, [season, loadAnalytics])

  async function handleSyncCurrent() {
    setSyncing(true)
    try { await syncCurrentSeason(); await loadAnalytics(season) }
    catch (e) { console.error(e) }
    finally { setSyncing(false) }
  }

  async function handleSyncSeason() {
    if (!syncSeasonVal) return
    setSyncing(true)
    try { await syncSeason(syncSeasonVal); await loadAnalytics(syncSeasonVal) }
    catch (e) { console.error(e) }
    finally { setSyncing(false) }
  }

  function openDrawer() { loadResults(); setDrawerOpen(true) }

  const driverTop10 = analytics?.driverTop10?.map(d => ({ name: d.name, value: Math.round(d.points) })) ?? []
  const ctorTop10   = analytics?.constructorTop10?.map(d => ({ name: d.name, value: Math.round(d.points) })) ?? []
  const teamPoints  = analytics?.teamPoints?.slice(0, 10).map(d => ({ name: d.team, value: Math.round(d.points) })) ?? []
  const raceWinners = analytics?.raceWinners?.slice(0, 10).map(d => ({ name: d.driver, value: d.wins })) ?? []

  return (
    <Layout>
      <div className="dashboard-page">
        {/* Toolbar */}
        <div className="toolbar">
          <button className="btn btn--secondary" onClick={openDrawer}>Race Results</button>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span className="toolbar__label">Season</span>
            <select className="select" value={season} onChange={e => setSeason(e.target.value)}>
              {seasons.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <button className="btn btn--primary" onClick={handleSyncCurrent} disabled={syncing}>
              {syncing ? 'Syncing…' : 'Sync Current'}
            </button>
            <input
              className="input-text"
              type="number"
              min="2010"
              max={new Date().getFullYear()}
              value={syncSeasonVal}
              onChange={e => setSyncSeasonVal(e.target.value)}
              style={{ width: 80 }}
            />
            <button className="btn btn--teal" onClick={handleSyncSeason} disabled={syncing}>
              Sync Season
            </button>
          </div>
        </div>

        {loading && <p style={{ color: 'var(--text-muted)', marginBottom: 16, fontSize: '0.85rem' }}>Loading {season} data…</p>}

        {/* Charts grid */}
        <div className="dashboard-grid">
          {/* Driver Points */}
          <ChartCard
            title="Driver Points (Top 10)"
            kpi={driverTop10.length ? `${driverTop10[0]?.name} leads with ${driverTop10[0]?.value} pts` : ''}
            variant="warm"
          >
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={driverTop10}
                  cx="50%" cy="50%"
                  outerRadius={90}
                  dataKey="value"
                  label={PieLabel}
                  labelLine={{ stroke: '#c5cfde', strokeWidth: 0.8 }}
                >
                  {driverTop10.map((_, i) => <Cell key={i} fill={DRIVER_COLORS[i % DRIVER_COLORS.length]} />)}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#1a2035', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, fontSize: 12 }}
                  formatter={(v) => [`${v} pts`]}
                />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>

          {/* Constructor Points */}
          <ChartCard
            title="Constructor Points (Top 10)"
            kpi={ctorTop10.length ? `${ctorTop10[0]?.name} leads with ${ctorTop10[0]?.value} pts` : ''}
            variant="cool"
          >
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={ctorTop10}
                  cx="50%" cy="50%"
                  outerRadius={90}
                  dataKey="value"
                  label={PieLabel}
                  labelLine={{ stroke: '#c5cfde', strokeWidth: 0.8 }}
                >
                  {ctorTop10.map((_, i) => <Cell key={i} fill={CTOR_COLORS[i % CTOR_COLORS.length]} />)}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#1a2035', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, fontSize: 12 }}
                  formatter={(v) => [`${v} pts`]}
                />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>

          {/* Team Points */}
          <ChartCard
            title="Team Points"
            kpi={teamPoints.length ? `${teamPoints.length} constructors` : ''}
            variant="amber"
          >
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={teamPoints} layout="vertical" margin={{ left: 8, right: 24 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" horizontal={false} />
                <XAxis type="number" tick={{ fill: '#c5cfde', fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="name" tick={{ fill: '#c5cfde', fontSize: 10 }} width={100} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#1a2035', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, fontSize: 12 }}
                  formatter={(v) => [`${v} pts`]}
                />
                <Bar dataKey="value" fill="#ffd166" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>

          {/* Race Winners */}
          <ChartCard
            title="Race Winners"
            kpi={raceWinners.length ? `${raceWinners[0]?.name}: ${raceWinners[0]?.value} win(s)` : ''}
            variant="warm"
          >
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={raceWinners} margin={{ top: 4, right: 8, bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: '#c5cfde', fontSize: 10 }} angle={-35} textAnchor="end" interval={0} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#c5cfde', fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#1a2035', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, fontSize: 12 }}
                  formatter={(v) => [`${v} win(s)`]}
                />
                <Bar dataKey="value" fill="#ff3d5a" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
      </div>

      {/* Race Results Drawer */}
      {drawerOpen && (
        <div className="drawer-overlay" onClick={() => setDrawerOpen(false)}>
          <div className="drawer" onClick={e => e.stopPropagation()}>
            <div className="drawer__header">
              <span className="drawer__title">Race Results</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn--teal" onClick={loadResults}>Refresh</button>
                <button className="btn btn--secondary" onClick={() => setDrawerOpen(false)}>Close</button>
              </div>
            </div>
            <div className="drawer__body">
              <div className="results-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Season</th><th>Round</th><th>Race</th><th>Circuit</th>
                      <th>Date</th><th>Driver</th><th>Team</th><th>Pos</th><th>Points</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((r, i) => (
                      <tr key={r.id ?? i}>
                        <td>{r.season}</td>
                        <td>{r.round}</td>
                        <td>{r.raceName}</td>
                        <td>{r.circuit}</td>
                        <td>{fmtDate(r.raceDate)}</td>
                        <td>{r.driver}</td>
                        <td>{r.team}</td>
                        <td>{r.position}</td>
                        <td>{r.points}</td>
                      </tr>
                    ))}
                    {results.length === 0 && (
                      <tr><td colSpan={9} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 24 }}>No results loaded</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}
    </Layout>
  )
}
