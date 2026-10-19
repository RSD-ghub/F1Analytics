import { useState } from 'react'
import { BookOpen, Flag, Timer, TriangleAlert, ArrowRight } from 'lucide-react'
import { getNextRace, getStrategy } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable } from '../components/Panel'

/**
 * The strategy engine: a race plan for the next Grand Prix, built the way the
 * strategy guide builds one, with the guide's own words beside each call.
 *
 * Every call on the page is the engine's arithmetic; the quotes are citations,
 * not the source of the numbers. That is why each sits under the call it
 * supports rather than in a reading list at the bottom.
 *
 * A plan is drawn as a race, not listed as one: stints laid along the laps in
 * their tyre colours, with the stop and its window marked where they fall, so
 * plans A, B and C can be compared by eye before any figure is read.
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


/* Pirelli's sidewall colours, so a compound reads before its name does. */
const COMPOUNDS = {
  SOFT: { letter: 'S', name: 'soft' },
  MEDIUM: { letter: 'M', name: 'medium' },
  HARD: { letter: 'H', name: 'hard' },
  INTERMEDIATE: { letter: 'I', name: 'intermediate' },
  WET: { letter: 'W', name: 'wet' },
}
const compound = (c) => COMPOUNDS[c] || { letter: c?.[0] || '?', name: (c || '').toLowerCase() }


function PlanView({ body, driver, onDriver }) {
  const { plan, guide = {}, field = [] } = body
  if (!plan?.plans?.length)
    return <Unavailable what="The strategy engine" reason="No plan could be built for this race." />
  const who = plan.driver
  const best = plan.plans[0]
  const sc = plan.safety_car

  return (
    <>
      <div className="strat-bar">
        <div className="strat-for">
          <span className="tile-label">Planning for</span>
          <strong>{who ? who.name : 'The race as a whole'}</strong>
          {who && <span className="muted small">{who.team} · starts P{who.grid}</span>}
        </div>
        <div className="strat-picker">
          <label className="sr-only" htmlFor="strategy-driver">Car</label>
          <select id="strategy-driver" value={driver} onChange={(e) => onDriver(e.target.value)}
                  disabled={!field.length}>
            <option value="">The race (no car)</option>
            {field.map((car) => (
              <option key={car.driver} value={car.driver}>P{car.grid} {car.driver}</option>
            ))}
          </select>
          {!field.length && (
            <span className="small muted">No grid yet: pick a car once qualifying is in.</span>
          )}
        </div>
      </div>

      {body.driver_not_found && <div className="notice warn">That driver is not on this grid.</div>}
      {body.guide_note && <div className="notice">{body.guide_note}</div>}
      {body.unavailable?.length > 0 && (
        <div className="notice warn">
          Some inputs could not be loaded ({body.unavailable.join(', ')}); the plan uses
          labelled generic figures in their place.
        </div>
      )}

      <div className="tiles strat-tiles">
        <div className="tile">
          <span className="tile-label">Plan A</span>
          <span className="strat-tile-tyres"><TyreRun sequence={best.sequence} /></span>
          <span className="tile-note">
            {best.stops} stop{best.stops === 1 ? '' : 's'} · box lap {best.pit_stops.map((s) => s.target_lap).join(' & ')}
          </span>
        </div>
        <div className="tile">
          <span className="tile-label">Start on</span>
          <span className="strat-tile-tyres">
            <Tyre compound={plan.start_tyre.compound} size={34} />
            <span className="strat-tile-word">{compound(plan.start_tyre.compound).name}</span>
          </span>
          <span className="tile-note">{plan.start_tyre.hardest_first ? 'hardest first' : 'off the line'}</span>
        </div>
        <div className="tile">
          <span className="tile-label">Safety car</span>
          <span className="tile-value">{Math.round(sc.safety_car_chance * 100)}<small>%</small></span>
          <span className="tile-note">chance in our simulation</span>
        </div>
        <div className="tile">
          <span className="tile-label">Race</span>
          <span className="tile-value">{plan.race_laps}<small> laps</small></span>
          <span className="tile-note">a stop costs {plan.pit_loss_seconds}s</span>
        </div>
      </div>

      <Panel title="Plans A, B and C" subtitle="Stints along the race, with each stop's target lap and window">
        <div className="plans">
          {plan.plans.map((p, i) => (
            <PlanRow key={p.label} plan={p} laps={plan.race_laps} lead={i === 0} />
          ))}
          <LapAxis laps={plan.race_laps} />
        </div>
        <TyreKey plans={plan.plans} />
        <Guide quotes={guide.plans} />
        <Guide quotes={guide.targets} />
      </Panel>

      <Panel title="What kind of race" subtitle={`Overtaking here is ${plan.race_character.overtaking}`}>
        <div className="character">
          <div>
            <span className={`tag tag-${plan.race_character.kind}`}>{plan.race_character.kind}</span>
            <OvertakingMeter level={plan.race_character.overtaking} />
          </div>
          <p className="character-summary">{plan.race_character.summary}</p>
        </div>
        <ul className="evidence">
          {plan.race_character.reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
        <Guide quotes={guide.race_character} />
      </Panel>

      <div className="strat-pair">
        <Panel title="Start tyre" subtitle={`Start on the ${compound(plan.start_tyre.compound).name}`}>
          <div className="start-tyre">
            <Tyre compound={plan.start_tyre.compound} size={64} />
            <p>{plan.start_tyre.why}</p>
          </div>
          <Guide quotes={guide.start_tyre} />
        </Panel>

        <Panel title="Plan S: the safety car"
               subtitle={`${Math.round(sc.safety_car_chance * 100)}% chance of a safety car in our simulation`}>
          <SafetyCarStrip safetyCar={sc} laps={plan.race_laps} />
          <ul className="calls">{sc.summary.map((line) => <li key={line}>{line}</li>)}</ul>
          <Guide quotes={guide.safety_car} />
        </Panel>
      </div>

      {who && (
        <>
          <PitStop section={plan.pit_stop} quotes={guide.pit_stop} />
          <Undercut section={plan.undercut} quotes={guide.undercut} />
          <EdgeCases section={plan.edge_cases} quotes={guide.edge_cases} />
        </>
      )}

      <Panel title="What the plan is built from">
        <details className="sources">
          <summary>{plan.inputs.length} inputs, and what is not modelled</summary>
          <ul>{plan.inputs.map((line) => <li key={line}>{line}</li>)}</ul>
          <p>Not modelled: {plan.not_modelled}</p>
        </details>
      </Panel>
    </>
  )
}


/** A tyre seen side-on: the compound's colour on the sidewall, its letter in the middle. */
function Tyre({ compound: c, size = 22 }) {
  const { letter, name } = compound(c)
  return (
    <svg className={`tyre tyre-${(c || '').toLowerCase()}`} width={size} height={size}
         viewBox="0 0 32 32" role="img" aria-label={`${name} tyre`}>
      <circle cx="16" cy="16" r="15" className="tyre-rubber" />
      <circle cx="16" cy="16" r="11.2" className="tyre-band" fill="none" strokeWidth="3.2" />
      <text x="16" y="16" dy="0.36em" textAnchor="middle" className="tyre-letter">{letter}</text>
    </svg>
  )
}


function TyreRun({ sequence, size = 30 }) {
  return (
    <span className="tyre-run">
      {sequence.map((c, i) => (
        <span key={i} className="tyre-run-step">
          {i > 0 && <ArrowRight size={14} className="muted" aria-hidden />}
          <Tyre compound={c} size={size} />
        </span>
      ))}
    </span>
  )
}


function PlanRow({ plan: p, laps, lead }) {
  const pct = (lap) => `${((lap - 1) / laps) * 100}%`
  const width = (from, to) => `${((to - from + 1) / laps) * 100}%`
  return (
    <div className={`plan-row${lead ? ' is-lead' : ''}`}>
      <div className="plan-label">
        <span className="plan-letter">{p.label}</span>
        <div>
          <div className="plan-name">{p.name}</div>
          <div className="plan-delta num">
            {p.expected_delta_seconds === 0 ? 'quickest' : `+${p.expected_delta_seconds.toFixed(1)}s`}
          </div>
        </div>
      </div>

      <div className="plan-track" role="img"
           aria-label={p.stints.map((s) => `${compound(s.compound).name} laps ${s.from_lap} to ${s.to_lap}`).join(', ')}>
        {p.stints.map((s) => (
          <span key={s.from_lap} className={`stint stint-${s.compound.toLowerCase()}`}
                style={{ left: pct(s.from_lap), width: width(s.from_lap, s.to_lap) }}>
            <span className="stint-text">{compound(s.compound).letter} · {s.laps}</span>
          </span>
        ))}
        {p.pit_stops.map((s) => (
          <span key={`w${s.optimum_lap}`} className="pit-window"
                style={{ left: pct(s.window[0]), width: width(s.window[0], s.window[1]) }}
                title={`Window laps ${s.window[0]}–${s.window[1]}, optimum ${s.optimum_lap}`} />
        ))}
        {p.pit_stops.map((s) => (
          <span key={`p${s.target_lap}`} className="pit-mark" style={{ left: pct(s.target_lap) }}>
            <span className="pit-flag num">L{s.target_lap}</span>
          </span>
        ))}
      </div>

      <div className="plan-meta">
        <p className="plan-why">{p.why}</p>
        {p.pit_stops.map((s) => (
          <span key={s.optimum_lap} className="plan-stop small muted">
            <Timer size={13} aria-hidden /> box lap <strong className="num">{s.target_lap}</strong>
            {' '}· optimum {s.optimum_lap} · window {s.window[0]}–{s.window[1]}
          </span>
        ))}
        {p.quickest_share != null && (
          <span className="plan-share small muted">
            <span className="share-track"><span className="share-fill" style={{ width: `${p.quickest_share * 100}%` }} /></span>
            quickest in {Math.round(p.quickest_share * 100)}% of simulated races
          </span>
        )}
      </div>
    </div>
  )
}


function LapAxis({ laps }) {
  const step = laps > 40 ? 10 : 5
  const ticks = [1]
  for (let lap = step; lap < laps; lap += step) ticks.push(lap)
  ticks.push(laps)
  return (
    <div className="plan-row plan-axis" aria-hidden>
      <div />
      <div className="lap-axis">
        {ticks.map((lap) => (
          <span key={lap} className="lap-tick num" style={{ left: `${((lap - 1) / laps) * 100}%` }}>{lap}</span>
        ))}
      </div>
      <div />
    </div>
  )
}


function TyreKey({ plans }) {
  const used = [...new Set(plans.flatMap((p) => p.sequence))]
  return (
    <div className="tyre-key small muted">
      {used.map((c) => (
        <span key={c} className="is-compound"><Tyre compound={c} size={16} /> {compound(c).name}</span>
      ))}
      <span><span className="key-window" /> pit window</span>
      <span><span className="key-pit" /> target lap</span>
    </div>
  )
}


function OvertakingMeter({ level }) {
  const steps = ['hard', 'moderate', 'easy']
  const known = { low: 0, difficult: 0, hard: 0, moderate: 1, medium: 1, high: 2, easy: 2 }
  const at = known[level] ?? 1
  return (
    <div className="meter" aria-label={`Overtaking: ${level}`}>
      <span className="tile-label">Overtaking</span>
      <div className="meter-steps">
        {steps.map((s, i) => <span key={s} className={`meter-step${i <= at ? ' is-on' : ''}`} />)}
      </div>
      <span className="small muted">{level}</span>
    </div>
  )
}


function SafetyCarStrip({ safetyCar, laps }) {
  const pct = (lap) => `${((lap - 1) / laps) * 100}%`
  return (
    <div className="sc-strip" aria-hidden>
      <div className="sc-track">
        {safetyCar.windows.map((w) => (
          <span key={w.stop} className="sc-window"
                style={{ left: pct(w.opens_lap), width: `${((w.planned_lap - w.opens_lap) / laps) * 100}%` }} />
        ))}
        {safetyCar.extra_stop_from_lap != null && (
          <span className="sc-extra" style={{ left: pct(safetyCar.extra_stop_from_lap), right: `${(3 / laps) * 100}%` }} />
        )}
        <span className="sc-late" style={{ width: `${(3 / laps) * 100}%` }} />
      </div>
      <div className="sc-legend small muted">
        <span><span className="key sc-k-window" /> box under a safety car</span>
        {safetyCar.extra_stop_from_lap != null && <span><span className="key sc-k-extra" /> worth an extra stop</span>}
        <span><span className="key sc-k-late" /> stay out</span>
      </div>
    </div>
  )
}


function PitStop({ section, quotes }) {
  if (!section?.summary?.length) return null
  return (
    <Panel title="The stop, among the field" subtitle={`Boxing on lap ${section.planned_lap}`}>
      {section.rejoin_position != null && (
        <div className="positions">
          <div className="pos"><span className="tile-label">Running</span><span className="pos-value num">P{section.running_position}</span></div>
          <ArrowRight className="muted" aria-hidden />
          <div className="pos"><span className="tile-label">Rejoins</span><span className="pos-value num">P{section.rejoin_position}</span></div>
          {section.rejoins_behind && (
            <p className="small muted pos-note">
              {section.rejoins_behind_by_seconds}s behind {section.rejoins_behind}
              {section.places_lost > 0 && ` · ${section.places_lost} place${section.places_lost === 1 ? '' : 's'} to win back`}
            </p>
          )}
        </div>
      )}
      <ul className="calls">{section.summary.map((line) => <li key={line}>{line}</li>)}</ul>
      <Guide quotes={quotes} />
    </Panel>
  )
}


function Undercut({ section, quotes }) {
  if (!section?.summary?.length) return null
  const cards = [
    section.ahead && { key: 'ahead', label: 'Car ahead', ...section.ahead, on: section.ahead.viable,
                       verdict: section.ahead.viable ? 'undercut on' : 'out of reach' },
    section.behind && { key: 'behind', label: 'Car behind', ...section.behind, on: section.behind.threat,
                        verdict: section.behind.threat ? 'can undercut us' : 'no threat' },
  ].filter(Boolean)
  return (
    <Panel title="Undercut" subtitle={section.gain_seconds != null
      ? `Pitting a lap early is worth ${section.gain_seconds > 0 ? '+' : ''}${section.gain_seconds}s here` : undefined}>
      {cards.length > 0 && (
        <div className="rivals">
          {cards.map((c) => (
            <div key={c.key} className={`rival${c.on ? ' is-on' : ''}`}>
              <span className="tile-label">{c.label}</span>
              <strong>{c.driver}</strong>
              <span className="num small">{c.gap_seconds}s</span>
              <span className={`rival-verdict${c.on ? ' is-on' : ''}`}>{c.verdict}</span>
            </div>
          ))}
        </div>
      )}
      <ul className="calls">{section.summary.map((line) => <li key={line}>{line}</li>)}</ul>
      <Guide quotes={quotes} />
    </Panel>
  )
}


function EdgeCases({ section, quotes }) {
  if (!section?.summary?.length) return null
  return (
    <Panel title="Edge cases" subtitle="When the cars around do the opposite">
      <div className="edges">
        {section.summary.map((line, i) => (
          <div key={line} className="edge">
            {i === 0 ? <Flag size={16} aria-hidden /> : <TriangleAlert size={16} aria-hidden />}
            <p>{line}</p>
          </div>
        ))}
      </div>
      <Guide quotes={quotes} />
    </Panel>
  )
}


/** The guide's passage behind a call, with its book and chapter. */
function Guide({ quotes }) {
  if (!quotes?.length) return null
  return quotes.map((q) => (
    <blockquote key={`${q.chapter}-${q.excerpt.slice(0, 24)}`} className="guide-quote">
      <p>{q.excerpt}</p>
      <footer>
        <BookOpen size={13} aria-hidden />
        <span><cite>{q.title}</cite>{q.author ? `, ${q.author}` : ''}</span>
        <span className="guide-chapter">Ch. {q.chapter} · {q.chapter_title}</span>
      </footer>
    </blockquote>
  ))
}
