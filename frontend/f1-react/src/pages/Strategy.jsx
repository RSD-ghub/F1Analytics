import { useState } from 'react'
import { BookOpen, Flag } from 'lucide-react'
import { getNextRace, getStrategy } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable } from '../components/Panel'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

/**
 * The strategy engine: a race plan for the next Grand Prix, built the way the
 * strategy guide builds one, with the guide's own words beside each call.
 *
 * Every call on the page is the engine's arithmetic; the quotes are citations,
 * not the source of the numbers. That is why each sits under the call it
 * supports rather than in a reading list at the bottom.
 *
 * The plans are drawn on one lap axis because that is how a pit wall reads
 * them: where each stint ends, how wide the window is, and where a safety car
 * would change the call are all positions in the race, not rows in a table.
 */
export default function Strategy() {
  const next = useAsync(() => getNextRace(), [])
  if (next.status === 'loading') return <Loading what="Finding the next race" />
  if (next.status === 'error')
    return <Unavailable what="The next race" reason={next.error.message} />
  const weekend = next.data.weekend
  if (!weekend)
    return <Unavailable what="The strategy engine" reason="No upcoming race is on the calendar." />
  return <RacePlan weekend={weekend} />
}

/** Radix Select cannot hold an empty value, so "no car" has a name of its own. */
const WHOLE_RACE = '__race__'

function RacePlan({ weekend }) {
  const [driver, setDriver] = useState('')
  const state = useAsync(
    () => getStrategy(weekend.season, weekend.round, driver),
    [weekend.season, weekend.round, driver],
  )

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">Strategy engine · Round {weekend.round} · {weekend.season}</p>
        <h1>{weekend.race_name}</h1>
        <p className="muted">
          Our plan for the race, worked out the way a pit wall works one out:
          the quickest race for a car alone, then adjusted for how hard it is to
          pass, the start, the safety car and the cars around.
        </p>
      </header>

      {state.status === 'loading' && <Loading what="Planning the race" />}
      {state.status === 'error' && (
        <Unavailable what="The strategy engine" reason={state.error.message} />
      )}
      {state.status === 'ready' && (
        <PlanView body={state.data} driver={driver} onDriver={setDriver} />
      )}
    </div>
  )
}


function PlanView({ body, driver, onDriver }) {
  const { plan, guide = {}, field = [] } = body
  if (!plan?.plans?.length)
    return <Unavailable what="The strategy engine" reason="No plan could be built for this race." />
  const who = plan.driver

  return (
    <>
      <RaceCall plan={plan} who={who} field={field} driver={driver} onDriver={onDriver}
                notFound={body.driver_not_found} />

      {body.guide_note && <div className="notice">{body.guide_note}</div>}
      {body.unavailable?.length > 0 && (
        <div className="notice warn">
          Some inputs could not be loaded ({body.unavailable.join(', ')}); the plan uses
          labelled generic figures in their place.
        </div>
      )}

      <Panel title="Plans A, B and C on the lap chart"
             subtitle={`${plan.race_laps} laps · a stop costs ${plan.pit_loss_seconds}s · shaded: the pit window`}>
        <LapChart plan={plan} />
        <ol className="plan-notes">
          {plan.plans.map((p) => (
            <li key={p.label}>
              <PlanBadge label={p.label} />
              <span><strong>{p.name}.</strong> {p.why}</span>
            </li>
          ))}
        </ol>
        <Guide quotes={[...(guide.plans || []), ...(guide.targets || [])]} />
      </Panel>

      <div className="strategy-pair">
        <Panel title="What kind of race" subtitle={`Overtaking here is ${plan.race_character.overtaking}`}>
          <CharacterScale kind={plan.race_character.kind} />
          <p className="strategy-lead">{plan.race_character.summary}</p>
          <ul className="fact-list">
            {plan.race_character.reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
          <Guide quotes={guide.race_character} />
        </Panel>

        <Panel title="Start tyre" subtitle="What goes on for lap one">
          <div className="start-tyre">
            <Tyre compound={plan.start_tyre.compound} size="lg" />
            <div>
              <div className="start-tyre-name">{titleCase(plan.start_tyre.compound)}</div>
              <p className="small muted">{plan.start_tyre.why}</p>
            </div>
          </div>
          <Guide quotes={guide.start_tyre} />
        </Panel>
      </div>

      <Panel title="Plan S: the safety car" subtitle="When a safety car changes Plan A's call">
        <div className="sc-head">
          <Ring value={plan.safety_car.safety_car_chance} label="chance of a safety car" />
          <ul className="fact-list sc-list">
            {plan.safety_car.summary.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </div>
        <Guide quotes={guide.safety_car} />
      </Panel>

      {who && (
        <>
          <Panel title="The stop, among the field"
                 subtitle={plan.pit_stop?.planned_lap ? `${who.name} stopping on lap ${plan.pit_stop.planned_lap}` : who.name}>
            <Rejoin stop={plan.pit_stop} />
            <ul className="fact-list">{plan.pit_stop?.summary?.map((l) => <li key={l}>{l}</li>)}</ul>
            <Guide quotes={guide.pit_stop} />
          </Panel>

          <Panel title="Undercut" subtitle={plan.undercut?.gain_seconds != null
            ? `Stopping a lap before a rival is worth about ${plan.undercut.gain_seconds}s`
            : undefined}>
            <Undercut section={plan.undercut} />
            <Guide quotes={guide.undercut} />
          </Panel>

          {plan.edge_cases?.summary?.length > 0 && (
            <Panel title="Edge cases" subtitle="If the cars around do the opposite">
              <div className="edge-cases">
                {plan.edge_cases.summary.map((line, i) => (
                  <div key={line} className={`edge-case ${i === 0 ? 'box' : 'stay'}`}>
                    <span className="tile-label">{i === 0 ? 'We box' : 'We stay out'}</span>
                    <p>{line.replace(/^(Box while the cars around stay out|Stay out while they box):\s*/i, '')}</p>
                  </div>
                ))}
              </div>
              <Guide quotes={guide.edge_cases} />
            </Panel>
          )}
        </>
      )}

      <Panel title="What the plan is built from">
        <ul className="fact-list small muted">{plan.inputs.map((line) => <li key={line}>{line}</li>)}</ul>
        <p className="small muted">Not modelled: {plan.not_modelled}</p>
      </Panel>
    </>
  )
}


/** The headline: who the plan is for, Plan A at a glance, and the car picker. */
function RaceCall({ plan, who, field, driver, onDriver, notFound }) {
  const a = plan.plans[0]
  return (
    <section className="race-call">
      <div className="race-call-head">
        <div>
          <p className="eyebrow">Plan A · {a.name}</p>
          <div className="race-call-plan">
            {a.sequence.map((c, i) => (
              <span key={i} className="race-call-step">
                {i > 0 && <span className="race-call-arrow" aria-hidden>→</span>}
                <Tyre compound={c} />
              </span>
            ))}
          </div>
          <p className="race-call-who">
            {who ? `${who.name} · ${who.team} · starts P${who.grid}` : 'The race as a whole'}
          </p>
        </div>
        <div className="race-call-picker">
          <span id="strategy-driver-label" className="tile-label">Plan for</span>
          <Select value={driver || WHOLE_RACE} disabled={!field.length}
                  onValueChange={(v) => onDriver(v === WHOLE_RACE ? '' : v)}>
            <SelectTrigger className="w-56" aria-labelledby="strategy-driver-label">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={WHOLE_RACE}>The race (no car)</SelectItem>
              {field.map((car) => (
                <SelectItem key={car.driver} value={car.driver}>P{car.grid} {car.driver}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!field.length && <p className="small">No grid yet, so this plans the race as a whole.</p>}
        </div>
      </div>
      {notFound && <p className="notice warn">That driver is not on this grid.</p>}
      <div className="tiles race-call-tiles">
        <Tile label={a.stops === 1 ? 'Stop' : 'Stops'} value={a.stops}
              note={`On lap ${a.pit_stops.map((s) => s.target_lap).join(' and ')}`} />
        <Tile label="Start" value={titleCase(plan.start_tyre.compound)} note="Tyre for lap one" />
        <Tile label="Safety car" value={`${Math.round(plan.safety_car.safety_car_chance * 100)}%`}
              note="Chance in our simulation" />
        <Tile label="Race type" value={titleCase(plan.race_character.kind)}
              note={`Overtaking is ${plan.race_character.overtaking}`} small />
      </div>
    </section>
  )
}

function Tile({ label, value, note, small }) {
  return (
    <div className="tile">
      <span className={`tile-value${small ? ' tile-value-sm' : ''}`}>{value}</span>
      <span className="tile-label">{label}</span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  )
}


/**
 * Every plan as stints on one lap axis, with each stop's window shaded and its
 * target lap pinned, and Plan A's safety-car windows drawn underneath.
 */
function LapChart({ plan }) {
  const laps = plan.race_laps
  const at = (lap) => `${((lap - 1) / laps) * 100}%`
  const span = (from, to) => `${((to - from + 1) / laps) * 100}%`
  const ticks = []
  for (let lap = 10; lap < laps - 3; lap += 10) ticks.push(lap)
  const sc = plan.safety_car

  return (
    <div className="lap-chart">
      {plan.plans.map((p) => (
        <div className="lap-row" key={p.label}>
          <div className="lap-row-label">
            <PlanBadge label={p.label} />
            <span className="lap-row-delta num">
              {p.expected_delta_seconds === 0 ? 'best' : `+${p.expected_delta_seconds.toFixed(1)}s`}
            </span>
          </div>
          <div className="lap-track">
            {p.pit_stops.map((s) => (
              <div key={`w${s.optimum_lap}`} className="lap-window"
                   style={{ left: at(s.window[0]), width: span(s.window[0], s.window[1]) }}
                   title={`Window laps ${s.window[0]}–${s.window[1]}, optimum lap ${s.optimum_lap}`} />
            ))}
            {p.stints.map((s, i) => (
              <div key={i} className={`stint ${compoundClass(s.compound)}`}
                   style={{ left: at(s.from_lap), width: span(s.from_lap, s.to_lap) }}
                   title={`${titleCase(s.compound)}, laps ${s.from_lap}–${s.to_lap}`}>
                <span className="stint-text">{titleCase(s.compound)} · {s.laps}</span>
              </div>
            ))}
            {p.pit_stops.map((s) => (
              <div key={`p${s.optimum_lap}`} className="pit-pin" style={{ left: at(s.target_lap + 0.5) }}>
                <span className="pit-pin-label num">L{s.target_lap}</span>
              </div>
            ))}
          </div>
        </div>
      ))}

      {sc?.windows?.length > 0 && (
        <div className="lap-row">
          <div className="lap-row-label"><span className="sc-badge">SC</span></div>
          <div className="lap-track lap-track-sc">
            {sc.windows.map((w) => (
              <div key={w.stop} className="sc-window"
                   style={{ left: at(w.opens_lap), width: span(w.opens_lap, w.planned_lap) }}
                   title={`Stop ${w.stop}: box under a safety car from lap ${w.opens_lap}`}>
                <span>box</span>
              </div>
            ))}
            {sc.extra_stop_from_lap != null && sc.extra_stop_from_lap <= laps && (
              <div className="sc-extra"
                   style={{ left: at(sc.extra_stop_from_lap), width: span(sc.extra_stop_from_lap, laps) }}
                   title={`From lap ${sc.extra_stop_from_lap}, a safety car is worth an extra stop`}>
                <span>extra stop</span>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="lap-row lap-axis-row" aria-hidden>
        <div className="lap-row-label" />
        <div className="lap-axis num">
          <span style={{ left: 0 }}>1</span>
          {ticks.map((t) => <span key={t} style={{ left: at(t) }}>{t}</span>)}
          <span className="lap-axis-end"><Flag size={11} /> {laps}</span>
        </div>
      </div>

      <div className="lap-legend small">
        {['SOFT', 'MEDIUM', 'HARD'].map((c) => (
          <span key={c}><Tyre compound={c} size="sm" /> {titleCase(c)}</span>
        ))}
        <span><i className="legend-swatch window" /> Pit window</span>
        <span><i className="legend-swatch sc" /> Safety car: box now</span>
        <span><i className="legend-swatch extra" /> Safety car: extra stop</span>
      </div>
    </div>
  )
}


/** Where this race sits between "hold position" and "run the quickest race". */
function CharacterScale({ kind }) {
  const stops = ['track position', 'balanced', 'optimum strategy']
  const index = Math.max(0, stops.indexOf(kind))
  return (
    <div className="character-scale" role="img" aria-label={`Race type: ${kind}`}>
      <div className="character-bar">
        <span className="character-marker" style={{ left: `${(index / 2) * 100}%` }} />
      </div>
      <div className="character-labels small">
        {stops.map((s, i) => (
          <span key={s} className={i === index ? 'active' : ''}>{titleCase(s)}</span>
        ))}
      </div>
    </div>
  )
}


function Ring({ value, label }) {
  const pct = Math.round((value || 0) * 100)
  const r = 34
  const c = 2 * Math.PI * r
  return (
    <div className="ring" role="img" aria-label={`${pct}% ${label}`}>
      <svg viewBox="0 0 84 84" width="112" height="112">
        <circle cx="42" cy="42" r={r} className="ring-bg" />
        <circle cx="42" cy="42" r={r} className="ring-fg"
                strokeDasharray={`${(c * pct) / 100} ${c}`} transform="rotate(-90 42 42)" />
      </svg>
      <div className="ring-text">
        <span className="ring-value num">{pct}%</span>
        <span className="ring-label">{label}</span>
      </div>
    </div>
  )
}


/** Running position before the stop, and where the car comes back out. */
function Rejoin({ stop }) {
  if (stop?.running_position == null || stop?.rejoin_position == null) return null
  const lost = stop.rejoin_position - stop.running_position
  return (
    <div className="rejoin">
      <div className="rejoin-pos">
        <span className="rejoin-value num">P{stop.running_position}</span>
        <span className="tile-label">before the stop</span>
      </div>
      <div className={`rejoin-arrow ${lost > 0 ? 'loss' : 'hold'}`}>
        <span>{lost > 0 ? `${lost} places lost` : 'no places lost'}</span>
      </div>
      <div className="rejoin-pos">
        <span className="rejoin-value num">P{stop.rejoin_position}</span>
        <span className="tile-label">
          rejoins{stop.rejoins_behind ? ` behind ${stop.rejoins_behind}` : ''}
          {stop.rejoins_behind_by_seconds != null ? ` by ${stop.rejoins_behind_by_seconds}s` : ''}
        </span>
      </div>
    </div>
  )
}


/** The gap to the cars either side, against what an undercut is worth. */
function Undercut({ section }) {
  if (!section?.summary?.length) return null
  const gain = section.gain_seconds
  const cars = [
    section.ahead && {
      side: 'Car ahead', ...section.ahead, ok: section.ahead.viable,
      verdict: section.ahead.viable ? 'Undercut reaches' : 'Out of reach',
    },
    section.behind && {
      side: 'Car behind', ...section.behind, ok: !section.behind.threat,
      verdict: section.behind.threat ? 'Can undercut us' : 'No threat',
    },
  ].filter((c) => c && c.gap_seconds != null)
  if (!cars.length || gain == null)
    return <ul className="fact-list">{section.summary.map((l) => <li key={l}>{l}</li>)}</ul>
  const scale = Math.max(gain, ...cars.map((c) => c.gap_seconds)) * 1.15
  return (
    <>
      <div className="undercut-cards">
        {cars.map((c) => (
          <div key={c.side} className="undercut-card">
            <div className="undercut-top">
              <span className="tile-label">{c.side}</span>
              <span className={`pill ${c.ok ? 'pill-good' : 'pill-dim'}`}>{c.verdict}</span>
            </div>
            <div className="undercut-driver">{c.driver}</div>
            <div className="gap-meter" aria-hidden>
              <div className="gap-bar" style={{ width: `${(c.gap_seconds / scale) * 100}%` }} />
              <div className="gap-gain" style={{ left: `${(gain / scale) * 100}%` }} />
            </div>
            <div className="small muted">
              gap <span className="num">{c.gap_seconds}s</span>, undercut worth <span className="num">{gain}s</span> (marker)
            </div>
          </div>
        ))}
      </div>
      <p className="small muted">{section.summary[0]}</p>
    </>
  )
}


function PlanBadge({ label }) {
  return <span className={`plan-badge plan-${label.toLowerCase()}`}>{label}</span>
}

/** A tyre as the sidewall shows it: the compound's colour around its initial. */
function Tyre({ compound, size = 'md' }) {
  const name = compound || ''
  return (
    <span className={`tyre tyre-${size} ${compoundClass(name)}`} title={titleCase(name)}
          role="img" aria-label={titleCase(name)}>
      {name[0]}
    </span>
  )
}

function compoundClass(compound) {
  return `c-${(compound || 'unknown').toLowerCase()}`
}

function titleCase(text) {
  const t = String(text || '').toLowerCase()
  return t.charAt(0).toUpperCase() + t.slice(1)
}


/** The guide's passages behind a call, with book and chapter. */
function Guide({ quotes }) {
  if (!quotes?.length) return null
  return (
    <div className="guide-quotes">
      {quotes.map((q) => (
        <blockquote key={`${q.chapter}-${q.excerpt.slice(0, 24)}`} className="guide-quote">
          <p>{q.excerpt}</p>
          <footer>
            <BookOpen size={13} aria-hidden />
            <span><cite>{q.title}</cite>{q.author ? `, ${q.author}` : ''} · ch. {q.chapter}, {q.chapter_title}</span>
          </footer>
        </blockquote>
      ))}
    </div>
  )
}
