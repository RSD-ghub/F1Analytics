import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Pause, Play, RotateCcw } from 'lucide-react'
import { getBlogIndex, getLiveReplay } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { usePageTitle } from '../hooks/usePageTitle'
import { Bar, Loading, Panel, Unavailable } from '../components/Panel'
import { Pill } from '../components/Pit'
import { teamColour } from '../lib/teams'
import { Button } from '@/components/ui/button'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

/** Laps per second of playback. */
const SPEEDS = [
  { value: '1', label: '1 lap/s' },
  { value: '2', label: '2 laps/s' },
  { value: '5', label: '5 laps/s' },
]
const CHART_DRIVERS = 5
const FLAG_LABEL = { SC: 'Safety car', VSC: 'Virtual safety car', RED: 'Red flag' }

/**
 * Live forecasts. Until the live feed is connected this replays a finished
 * race: the forecast made at each lap from what was known at that lap only,
 * the same way the backtest scored it. The live feed will drive the same
 * view, one lap at a time.
 */
export default function Live() {
  const { season, round } = useParams()
  const navigate = useNavigate()
  const index = useAsync(() => getBlogIndex(season), [season])
  usePageTitle('Live forecast')

  if (index.status === 'loading') return <Loading what="Loading the races" />
  if (index.status === 'error')
    return <Unavailable what="The race list" reason={index.error.message} />

  const finished = (index.data?.weekends || []).filter((w) => w.has_run)
  const chosen = round ? Number(round) : finished.at(-1)?.round
  const chosenSeason = season ? Number(season) : index.data?.season
  const pick = (s, r) => navigate(r ? `/live/${s}/${r}` : `/live/${s}`)

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">Live forecast · race replay</p>
        <h1>How the forecast moves during a race</h1>
        <p className="lede">
          A finished race, forecast again at every lap from what was known at
          that lap only: gaps, tyres, stops, safety cars and each car's pace so
          far. Once the live feed is connected, this page will follow the race
          as it happens.
        </p>
      </header>

      <div className="live-pick">
        {(index.data?.seasons || []).length > 0 && (
          <Select value={String(chosenSeason)} onValueChange={(v) => pick(v)}>
            <SelectTrigger className="w-28" aria-label="Season"><SelectValue /></SelectTrigger>
            <SelectContent>
              {index.data.seasons.map((s) => (
                <SelectItem key={s} value={String(s)}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {finished.length > 0 && (
          <Select value={chosen ? String(chosen) : undefined}
                  onValueChange={(v) => pick(chosenSeason, v)}>
            <SelectTrigger className="w-64" aria-label="Race">
              <SelectValue placeholder="Choose a race" />
            </SelectTrigger>
            <SelectContent>
              {finished.map((w) => (
                <SelectItem key={w.round} value={String(w.round)}>
                  R{w.round} · {w.race_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {chosen ? (
        <Replay key={`${chosenSeason}-${chosen}`} season={chosenSeason} round={chosen} />
      ) : (
        <p className="muted">No race of {chosenSeason} has been run yet.</p>
      )}
    </div>
  )
}

function Replay({ season, round }) {
  const state = useAsync(() => getLiveReplay(season, round), [season, round])
  const [frame, setFrame] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState('2')
  const frames = state.data?.frames || []
  const last = Math.max(frames.length - 1, 0)

  useEffect(() => {
    if (!playing) return undefined
    const timer = setInterval(() => {
      setFrame((f) => Math.min(f + 1, last))
    }, 1000 / Number(speed))
    return () => clearInterval(timer)
  }, [playing, speed, last])

  const atEnd = frames.length > 0 && frame >= last
  const isPlaying = playing && !atEnd

  if (state.status === 'loading')
    return <Loading what="Replaying the race lap by lap (the first time can take a minute)" />
  if (state.status === 'error')
    return <Unavailable what="The replay" reason={state.error.message} />
  if (!frames.length)
    return <Unavailable what="The replay" reason="No laps were replayed for this race." />

  const data = state.data
  const now = frames[Math.min(frame, last)]
  const toggle = () => {
    if (atEnd) { setFrame(0); setPlaying(true); return }
    setPlaying(!isPlaying)
  }

  return (
    <>
      <Panel
        title={data.race_name || `Round ${round}`}
        subtitle={`${data.total_laps} laps · ${data.runs} simulated races at every lap`}
      >
        <div className="live-controls">
          <Button size="sm" onClick={toggle}>
            {isPlaying ? <Pause /> : atEnd ? <RotateCcw /> : <Play />}
            {isPlaying ? 'Pause' : atEnd ? 'Replay' : 'Play'}
          </Button>
          <input
            className="live-slider"
            type="range" min={0} max={last} value={frame}
            onChange={(e) => { setPlaying(false); setFrame(Number(e.target.value)) }}
            aria-label="Lap"
          />
          <span className="live-lap mono">
            {now.lap === 0 ? 'Grid' : `Lap ${now.lap}/${data.total_laps}`}
          </span>
          <Select value={speed} onValueChange={setSpeed}>
            <SelectTrigger className="w-28" size="sm" aria-label="Playback speed">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SPEEDS.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
            </SelectContent>
          </Select>
          {now.flag && <Pill tone="soft">{FLAG_LABEL[now.flag] || now.flag}</Pill>}
        </div>
        <WinChart frames={frames} current={Math.min(frame, last)} />
        <p className="muted small">
          {data.wet
            ? 'A wet race. The simulator only knows dry tyres, so treat these chances with care; the correcting network is switched off in the wet.'
            : data.network
              ? 'Win and podium chances include the correcting network, which beat the simulator alone on the 2024-25 races.'
              : 'Chances come from the lap simulator, restarted from the race as it stood.'}
        </p>
      </Panel>

      <Panel title={now.lap === 0 ? 'Before the start' : `After lap ${now.lap}`}
             subtitle="In running order">
        <div className="table-scroll">
          <table className="datatable compact live-table">
            <thead>
              <tr>
                <th className="num">Pos</th><th>Driver</th><th>Win</th>
                <th className="num">Podium</th><th className="num">Gap</th>
                <th>Tyre</th><th className="num">Stops</th><th className="num">Exp. place</th>
              </tr>
            </thead>
            <tbody>
              {now.cars.map((c) => (
                <tr key={c.driver} className={c.retired ? 'is-out' : undefined}>
                  <td className="num">{c.retired ? 'Out' : c.position}</td>
                  <td>
                    <span className="team-stripe" style={{ background: teamColour(c.team) }}
                          aria-hidden="true" />
                    <Link className="link" to={`/drivers/${season}/${encodeURIComponent(c.driver)}`}>
                      {c.driver}
                    </Link>
                  </td>
                  <td>
                    <span className="live-win">
                      <Bar value={c.win} />
                      <span className="num">{pct(c.win)}</span>
                    </span>
                  </td>
                  <td className="num">{pct(c.podium)}</td>
                  <td className="num mono">{gap(c, now.lap)}</td>
                  <td>{c.compound ? <Tyre compound={c.compound} age={c.tyre_age} /> : '—'}</td>
                  <td className="num">{now.lap === 0 ? '—' : c.stops}</td>
                  <td className="num">{c.retired ? '—' : c.expected_position.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {atEnd && (
        <Panel title="How it finished" subtitle="The official result">
          <ol className="live-result">
            {data.result.filter((r) => r.position).slice(0, 10).map((r) => (
              <li key={r.driver}>
                <span className="num">P{r.position}</span> {r.driver}
                <span className="muted small"> · {r.team}</span>
              </li>
            ))}
          </ol>
          <p className="muted small">
            <Link className="link" to={`/race-review/${season}/${round}`}>Pit stop review →</Link>
          </p>
        </Panel>
      )}
    </>
  )
}

/** Win chance by lap for the drivers who ever had the best of it. */
function WinChart({ frames, current }) {
  // Drawn at the width it is shown at, so the labels stay readable on a phone.
  const box = useRef(null)
  const [width, setWidth] = useState(640)
  useEffect(() => {
    const el = box.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  const series = useMemo(() => {
    const peak = {}
    const team = {}
    for (const f of frames) {
      for (const c of f.cars) {
        peak[c.driver] = Math.max(peak[c.driver] || 0, c.win)
        team[c.driver] = c.team
      }
    }
    const top = Object.keys(peak).sort((a, b) => peak[b] - peak[a]).slice(0, CHART_DRIVERS)
    const seen = new Set()
    return top.map((driver) => {
      const colour = teamColour(team[driver])
      // Team-mates share a colour; the second one is dashed.
      const dashed = seen.has(colour)
      seen.add(colour)
      return {
        driver, colour, dashed,
        points: frames.map((f) => f.cars.find((c) => c.driver === driver)?.win ?? 0),
      }
    })
  }, [frames])

  const W = Math.max(width, 280)
  const H = W < 480 ? 180 : 220
  const pad = { l: 38, r: 8, t: 8, b: 20 }
  const n = Math.max(frames.length - 1, 1)
  const plot = W - pad.l - pad.r
  const x = (i) => pad.l + (i / n) * plot
  const y = (p) => pad.t + (1 - p) * (H - pad.t - pad.b)
  const lastLap = frames.at(-1)?.lap ?? 0

  return (
    <figure className="live-chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="Chance of winning at each lap for the leading contenders">
        {[0, 0.25, 0.5, 0.75, 1].map((p) => (
          <g key={p}>
            <line x1={pad.l} x2={W - pad.r} y1={y(p)} y2={y(p)} className="live-grid" />
            <text x={pad.l - 6} y={y(p) + 4} textAnchor="end" className="live-axis">
              {p * 100}%
            </text>
          </g>
        ))}
        {frames.map((f, i) => f.flag && (
          <rect key={i} x={x(i) - plot / n / 2} width={plot / n}
                y={pad.t} height={H - pad.t - pad.b} className="live-flag" />
        ))}
        {series.map((s) => (
          <polyline key={s.driver} fill="none" stroke={s.colour} strokeWidth="2"
                    strokeLinejoin="round"
                    strokeDasharray={s.dashed ? '5 3' : undefined}
                    points={s.points.map((p, i) => `${x(i)},${y(p)}`).join(' ')} />
        ))}
        <line x1={x(current)} x2={x(current)} y1={pad.t} y2={H - pad.b} className="live-cursor" />
        <text x={pad.l} y={H - 4} className="live-axis">Grid</text>
        <text x={W - pad.r} y={H - 4} textAnchor="end" className="live-axis">Lap {lastLap}</text>
      </svg>
      <figcaption className="live-legend">
        {series.map((s) => (
          <span key={s.driver}>
            <span className={`live-swatch${s.dashed ? ' is-dashed' : ''}`}
                  style={{ borderColor: s.colour }} aria-hidden="true" />
            {s.driver} <strong>{pct(s.points[current])}</strong>
          </span>
        ))}
        <span className="muted">
          <span className="live-swatch is-flag" aria-hidden="true" /> safety car or red flag
        </span>
      </figcaption>
    </figure>
  )
}

function Tyre({ compound, age }) {
  return (
    <span className="live-tyre" title={`${compound}, ${age} laps old`}>
      <span className={`tyre tyre-sm c-${compound.toLowerCase()}`} role="img"
            aria-label={compound}>{compound[0]}</span>
      <span className="muted small">{age}</span>
    </span>
  )
}

function pct(p) {
  if (p == null) return '—'
  if (p > 0 && p < 0.005) return '<1%'
  return `${Math.round(p * 100)}%`
}

function gap(car, lap) {
  if (lap === 0 || car.retired) return '—'
  if (car.position === 1) return 'Leader'
  return car.gap == null ? '—' : `+${car.gap.toFixed(1)}`
}
