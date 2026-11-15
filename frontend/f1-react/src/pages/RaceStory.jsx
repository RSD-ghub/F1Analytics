import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { getBlogIndex, getLiveReplay, getRaceStory } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { usePageTitle } from '../hooks/usePageTitle'
import { Loading, Panel, Unavailable } from '../components/Panel'
import { Pill } from '../components/Pit'
import { teamColour } from '../lib/teams'
import { Button } from '@/components/ui/button'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

const WINDOW_LABEL = { pre_quali: 'Before qualifying', post_quali: 'After qualifying', final_grid: 'Final grid' }
const FLAG_LABEL = { SC: 'Safety car', VSC: 'Virtual safety car', RED: 'Red flag' }
/** Gaps above this are drawn at the top edge: a lapped car is off the chart. */
const GAP_CAP = 60
const DEFAULT_SHOWN = 6

/**
 * A finished race told lap by lap: where every car was, how far behind, on
 * what tyres, under which flags, and what we had forecast before the start.
 * Doubles as the check on the forecasts: the result sits next to every number
 * we published, and the live forecast's lap-by-lap replay can be laid over it.
 */
export default function RaceStory() {
  const { season, round } = useParams()
  const navigate = useNavigate()
  const index = useAsync(() => getBlogIndex(season), [season])
  const finished = (index.data?.weekends || []).filter((w) => w.has_run)
  const chosenSeason = season ? Number(season) : index.data?.season
  const chosen = round ? Number(round) : finished.at(-1)?.round
  const pick = (s, r) => navigate(r ? `/race/${s}/${r}` : `/race/${s}`)
  usePageTitle('Race story')

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">Race story</p>
        <h1>How the race unfolded, lap by lap</h1>
        <p className="lede">
          Every car's place and gap on every lap, with stops, tyres and safety
          cars, and the forecasts we locked before the start set against what
          actually happened.
        </p>
      </header>

      {index.status === 'ready' && (
        <div className="live-pick">
          {(index.data?.seasons || []).length > 0 && (
            <Select value={String(chosenSeason)} onValueChange={(v) => pick(v)}>
              <SelectTrigger className="w-28" aria-label="Season"><SelectValue /></SelectTrigger>
              <SelectContent>
                {index.data.seasons.map((s) => <SelectItem key={s} value={String(s)}>{s}</SelectItem>)}
              </SelectContent>
            </Select>
          )}
          {finished.length > 0 && (
            <Select value={chosen ? String(chosen) : undefined} onValueChange={(v) => pick(chosenSeason, v)}>
              <SelectTrigger className="w-64" aria-label="Race"><SelectValue placeholder="Choose a race" /></SelectTrigger>
              <SelectContent>
                {finished.map((w) => (
                  <SelectItem key={w.round} value={String(w.round)}>R{w.round} · {w.race_name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      )}

      {chosenSeason && chosen
        ? <Story key={`${chosenSeason}-${chosen}`} season={chosenSeason} round={chosen} />
        : index.status === 'loading' ? <Loading what="Loading the races" /> : null}
    </div>
  )
}

function Story({ season, round }) {
  const state = useAsync(() => getRaceStory(season, round), [season, round])
  const [lap, setLap] = useState(null)
  const [picked, setPicked] = useState(null)
  const [replay, setReplay] = useState({ status: 'idle' })

  if (state.status === 'loading') return <Loading what="Reading the race" />
  if (state.status === 'error') return <Unavailable what="The race" reason={state.error.message} />
  const story = state.data
  const total = story.total_laps
  const current = lap ?? total
  const shown = picked ?? story.drivers.filter((d) => d.finish).slice(0, DEFAULT_SHOWN).map((d) => d.driver)
  const toggle = (driver) => {
    const next = shown.includes(driver) ? shown.filter((d) => d !== driver) : [...shown, driver]
    setPicked(next)
  }
  const loadReplay = () => {
    setReplay({ status: 'loading' })
    getLiveReplay(season, round)
      .then((data) => setReplay({ status: 'ready', data }))
      .catch((error) => setReplay({ status: 'error', error }))
  }
  const frame = replay.status === 'ready'
    ? replay.data.frames.findLast((f) => f.lap <= Math.max(current - 1, 0)) : null

  return (
    <>
      <Verdict story={story} />

      {!story.has_laps ? (
        <div className="notice">
          No lap timing is stored for this race (lap data starts in 2018), so
          only the result and the forecasts are shown.
        </div>
      ) : (
        <>
          <Panel title={story.race_name} subtitle={`${total} laps · tap a name to show or hide it`}>
            <div className="live-controls">
              <input className="live-slider" type="range" min={1} max={total} value={current}
                     onChange={(e) => setLap(Number(e.target.value))} aria-label="Lap" />
              <span className="live-lap mono">Lap {current}/{total}</span>
              {story.neutral[current] && <Pill tone="soft">{FLAG_LABEL[story.neutral[current]]}</Pill>}
            </div>
            <LapChart story={story} shown={shown} current={current} />
            <GapChart story={story} shown={shown} current={current} />
            <Legend story={story} shown={shown} toggle={toggle} />
          </Panel>

          <OrderAtLap story={story} lap={current} frame={frame} season={season} />
          <Messages story={story} lap={current} />

          <Panel title="Live forecast, lap by lap"
                 subtitle="The live forecast replayed from what was known at each lap">
            {replay.status === 'idle' && (
              <p className="small">
                <Button size="sm" onClick={loadReplay}>Add the live forecast</Button>{' '}
                <span className="muted">Simulates every lap, so the first time takes up to a minute.</span>
              </p>
            )}
            {replay.status === 'loading' && <Loading what="Replaying the race lap by lap" />}
            {replay.status === 'error' && <Unavailable what="The live replay" reason={replay.error.message} />}
            {replay.status === 'ready' && (
              <>
                <p className="small muted">
                  Win and podium chances now show in the order table at the chosen lap.{' '}
                  <Link className="link" to={`/live/${season}/${round}`}>Play it on the Live page →</Link>
                </p>
                {replay.data.scorecard?.length > 0 && <ScoreTable rows={replay.data.scorecard} />}
              </>
            )}
          </Panel>
        </>
      )}

      <Forecasts story={story} />
      <p className="muted small">
        <Link className="link" to={`/race-review/${season}/${round}`}>Pit stop review →</Link>{' '}
        <Link className="link" to={`/blog/${season}/${round}`}>The weekend in One Blog →</Link>
      </p>
    </>
  )
}

/** The result in one line, and whether our forecasts called it. */
function Verdict({ story }) {
  const winner = story.drivers.find((d) => d.finish === 1)
  const last = story.forecasts.at(-1)
  return (
    <Panel title="Result" subtitle={winner ? `${winner.driver} won` : 'No classified winner'}>
      <ol className="live-result">
        {story.drivers.filter((d) => d.finish && d.finish <= 3).map((d) => (
          <li key={d.driver}>
            <span className="num">P{d.finish}</span> {d.driver}
            <span className="muted small"> · {d.team} · from P{d.grid ?? '?'}</span>
          </li>
        ))}
      </ol>
      {last ? (
        <p className="small">
          {WINDOW_LABEL[last.window]} forecast:{' '}
          {last.favourite
            ? <>favourite <strong>{last.favourite}</strong> {last.favourite_won ? 'won' : 'did not win'}
              {last.winner_chance != null && <> · the winner was given {pct(last.winner_chance)}</>} · </>
            : null}
          {last.podium_hits} of 3 podium picks on the podium.
        </p>
      ) : (
        <p className="small muted">No forecast was locked for this race.</p>
      )}
    </Panel>
  )
}

/** Measure the element so the charts draw at the size they are shown. */
function useWidth() {
  const box = useRef(null)
  const [width, setWidth] = useState(640)
  useEffect(() => {
    const el = box.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [box, Math.max(width, 280)]
}

function useColours(drivers) {
  return useMemo(() => {
    const seen = new Set()
    const out = {}
    for (const d of drivers) {
      const colour = teamColour(d.team)
      out[d.driver] = { colour, dashed: seen.has(colour) }
      seen.add(colour)
    }
    return out
  }, [drivers])
}

function Bands({ story, x, top, height, step }) {
  return Object.entries(story.neutral).map(([lap, kind]) => (
    <rect key={lap} x={x(Number(lap)) - step / 2} width={step} y={top} height={height}
          className={kind === 'RED' ? 'race-red' : 'live-flag'} />
  ))
}

function LapChart({ story, shown, current }) {
  const [box, W] = useWidth()
  const colours = useColours(story.drivers)
  const field = story.drivers.length
  const H = Math.max(220, Math.min(field * 16, 380))
  const pad = { l: 28, r: 8, t: 8, b: 20 }
  const total = Math.max(story.total_laps, 2)
  const plot = W - pad.l - pad.r
  const step = plot / (total - 1)
  const x = (lap) => pad.l + ((lap - 1) / (total - 1)) * plot
  const y = (pos) => pad.t + ((pos - 1) / Math.max(field - 1, 1)) * (H - pad.t - pad.b)
  const line = (d) => d.positions.map((p, i) => (p ? `${x(i + 1)},${y(p)}` : null)).filter(Boolean).join(' ')
  const order = [...story.drivers].sort((a, b) => shown.includes(a.driver) - shown.includes(b.driver))

  return (
    <figure className="live-chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Every car's place at the end of each lap">
        <Bands story={story} x={x} top={pad.t} height={H - pad.t - pad.b} step={step} />
        {[1, 5, 10, 15, 20].filter((p) => p <= field).map((p) => (
          <g key={p}>
            <line x1={pad.l} x2={W - pad.r} y1={y(p)} y2={y(p)} className="live-grid" />
            <text x={pad.l - 6} y={y(p) + 4} textAnchor="end" className="live-axis">P{p}</text>
          </g>
        ))}
        {order.map((d) => {
          const on = shown.includes(d.driver)
          const c = colours[d.driver]
          return (
            <g key={d.driver} opacity={on ? 1 : 0.15}>
              <polyline fill="none" stroke={c.colour} strokeWidth={on ? 2 : 1} strokeLinejoin="round"
                        strokeDasharray={c.dashed ? '5 3' : undefined} points={line(d)} />
              {on && d.stops.map((lap) => d.positions[lap - 1] && (
                <circle key={lap} cx={x(lap)} cy={y(d.positions[lap - 1])} r="3.5"
                        fill="var(--bg, #0b090b)" stroke={c.colour} strokeWidth="2">
                  <title>{d.driver} pitted on lap {lap}</title>
                </circle>
              ))}
            </g>
          )
        })}
        <line x1={x(current)} x2={x(current)} y1={pad.t} y2={H - pad.b} className="live-cursor" />
        <text x={pad.l} y={H - 4} className="live-axis">Lap 1</text>
        <text x={W - pad.r} y={H - 4} textAnchor="end" className="live-axis">Lap {story.total_laps}</text>
      </svg>
      <figcaption className="muted small">Place at the end of each lap. Rings mark pit stops.</figcaption>
    </figure>
  )
}

function GapChart({ story, shown, current }) {
  const [box, W] = useWidth()
  const colours = useColours(story.drivers)
  const H = W < 480 ? 160 : 200
  const pad = { l: 38, r: 8, t: 8, b: 20 }
  const total = Math.max(story.total_laps, 2)
  const plot = W - pad.l - pad.r
  const step = plot / (total - 1)
  const visible = story.drivers.filter((d) => shown.includes(d.driver))
  const peak = Math.max(5, ...visible.flatMap((d) => d.gaps.filter((g) => g != null && g <= GAP_CAP)))
  const cap = Math.min(GAP_CAP, Math.ceil(peak / 5) * 5)
  const x = (lap) => pad.l + ((lap - 1) / (total - 1)) * plot
  const y = (g) => pad.t + (Math.min(g, cap) / cap) * (H - pad.t - pad.b)
  const ticks = [0, cap / 2, cap]
  return (
    <figure className="live-chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Seconds behind the leader at the end of each lap">
        <Bands story={story} x={x} top={pad.t} height={H - pad.t - pad.b} step={step} />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="live-grid" />
            <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" className="live-axis">
              {t === 0 ? 'Lead' : `+${Math.round(t)}s`}
            </text>
          </g>
        ))}
        {visible.map((d) => {
          const c = colours[d.driver]
          const points = d.gaps.map((g, i) => (g != null ? `${x(i + 1)},${y(g)}` : null)).filter(Boolean).join(' ')
          return <polyline key={d.driver} fill="none" stroke={c.colour} strokeWidth="2" strokeLinejoin="round"
                           strokeDasharray={c.dashed ? '5 3' : undefined} points={points} />
        })}
        <line x1={x(current)} x2={x(current)} y1={pad.t} y2={H - pad.b} className="live-cursor" />
      </svg>
      <figcaption className="muted small">
        Seconds behind the leader. Gaps over {cap}s sit on the bottom edge; a
        lap with no time leaves a break. Shaded laps: safety car, VSC or red flag.
      </figcaption>
    </figure>
  )
}

function Legend({ story, shown, toggle }) {
  const colours = useColours(story.drivers)
  return (
    <div className="race-legend" role="group" aria-label="Drivers shown">
      {story.drivers.map((d) => (
        <button key={d.driver} type="button" onClick={() => toggle(d.driver)}
                className={`race-chip${shown.includes(d.driver) ? ' is-on' : ''}`}
                aria-pressed={shown.includes(d.driver)}>
          <span className={`live-swatch${colours[d.driver].dashed ? ' is-dashed' : ''}`}
                style={{ borderColor: colours[d.driver].colour }} aria-hidden="true" />
          {d.driver}
        </button>
      ))}
    </div>
  )
}

/** Running order at the chosen lap, with the live forecast's chances when loaded. */
function OrderAtLap({ story, lap, frame, season }) {
  const rows = story.drivers
    .map((d) => ({ d, pos: d.positions[lap - 1], gap: d.gaps[lap - 1] }))
    .filter((r) => r.pos)
    .sort((a, b) => a.pos - b.pos)
  const out = story.drivers.filter((d) => !d.positions[lap - 1])
  const chances = Object.fromEntries((frame?.cars || []).map((c) => [c.driver, c]))
  return (
    <Panel title={`After lap ${lap}`} subtitle={frame ? `Live forecast from after lap ${frame.lap}` : 'In running order'}>
      <div className="table-scroll">
        <table className="datatable compact live-table">
          <thead>
            <tr>
              <th className="num">Pos</th><th>Driver</th><th className="num">Gap</th><th>Tyre</th>
              <th className="num">Stops</th>
              {frame && <><th className="num">Win</th><th className="num">Podium</th></>}
              <th className="num">Finished</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ d, pos, gap }) => (
              <tr key={d.driver}>
                <td className="num">{pos}</td>
                <td>
                  <span className="team-stripe" style={{ background: teamColour(d.team) }} aria-hidden="true" />
                  <Link className="link" to={`/drivers/${season}/${encodeURIComponent(d.driver)}`}>{d.driver}</Link>
                </td>
                <td className="num mono">{pos === 1 ? 'Leader' : gap == null ? '—' : `+${gap.toFixed(1)}`}</td>
                <td>{d.compounds[lap - 1]
                  ? <span className="live-tyre">
                      <span className={`tyre tyre-sm c-${d.compounds[lap - 1].toLowerCase()}`} role="img"
                            aria-label={d.compounds[lap - 1]}>{d.compounds[lap - 1][0]}</span>
                      <span className="muted small">{d.tyre_ages[lap - 1]}</span>
                    </span>
                  : '—'}</td>
                <td className="num">{d.stops.filter((s) => s <= lap).length}</td>
                {frame && <>
                  <td className="num">{pct(chances[d.driver]?.win)}</td>
                  <td className="num">{pct(chances[d.driver]?.podium)}</td>
                </>}
                <td className="num">{d.finish ? `P${d.finish}` : d.status || 'DNF'}</td>
              </tr>
            ))}
            {out.map((d) => (
              <tr key={d.driver} className="is-out">
                <td className="num">Out</td><td>{d.driver}</td>
                <td colSpan={frame ? 5 : 3} className="muted small">{d.status || 'Not running'} · last lap {d.last_lap}</td>
                <td className="num">{d.finish ? `P${d.finish}` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function Messages({ story, lap }) {
  const recent = story.messages.filter((m) => m.lap <= lap).slice(-6).reverse()
  if (!recent.length) return null
  return (
    <Panel title="Race control" subtitle={`The latest messages up to lap ${lap}`}>
      <ul className="race-messages">
        {recent.map((m, i) => (
          <li key={`${m.lap}-${i}`}><span className="mono muted">L{m.lap}</span> {m.message}</li>
        ))}
      </ul>
    </Panel>
  )
}

/** Every locked forecast, each driver's chances beside where they finished. */
function Forecasts({ story }) {
  if (!story.forecasts.length) return null
  const windows = story.forecasts
  return (
    <Panel title="What we forecast" subtitle="Podium chance in each locked forecast, against the result">
      <div className="table-scroll">
        <table className="datatable compact">
          <thead>
            <tr>
              <th>Driver</th><th className="num">Grid</th>
              {windows.map((w) => <th key={w.window} className="num">{WINDOW_LABEL[w.window]}</th>)}
              <th className="num">Finished</th>
            </tr>
          </thead>
          <tbody>
            {story.drivers.map((d) => (
              <tr key={d.driver} className={d.finish && d.finish <= 3 ? 'strong' : undefined}>
                <td>
                  <span className="team-stripe" style={{ background: teamColour(d.team) }} aria-hidden="true" />
                  {d.driver}
                </td>
                <td className="num">{d.grid ?? '—'}</td>
                {windows.map((w) => {
                  const p = w.drivers[d.driver]
                  return (
                    <td key={w.window} className="num">
                      {pct(p?.podium)}
                      {p?.win != null && <span className="muted small"> · win {pct(p.win)}</span>}
                    </td>
                  )
                })}
                <td className="num">{d.finish ? `P${d.finish}` : d.status || 'DNF'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Bold rows finished on the podium. Before qualifying we publish no win
        chance: measured on two seasons it was no better than guessing.
      </p>
    </Panel>
  )
}

function ScoreTable({ rows }) {
  const methods = [['live', 'Live'], ['pre_race', 'Pre-race'], ['order_holds', 'Order holds']]
  return (
    <div className="table-scroll">
      <table className="datatable compact">
        <thead>
          <tr><th>Stage</th>{methods.map(([k, l]) => <th key={k} className="num">{l} · winner log loss</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const values = methods.map(([k]) => r[k]?.winner_log_loss)
            const best = Math.min(...values.filter((v) => v != null))
            return (
              <tr key={r.checkpoint}>
                <td>{Math.round(r.checkpoint * 100)}% · lap {r.lap}</td>
                {values.map((v, i) => (
                  <td key={methods[i][0]} className={`num${v === best ? ' strong' : ''}`}>
                    {v == null ? '—' : v.toFixed(2)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="muted small">Lower is better. One race is one sample.</p>
    </div>
  )
}

function pct(p) {
  if (p == null) return '—'
  if (p > 0 && p < 0.005) return '<1%'
  return `${Math.round(p * 100)}%`
}
