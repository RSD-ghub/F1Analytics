import { useState } from 'react'
import { getNextRace, getStrategy } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable } from '../components/Panel'
import { Badge } from '@/components/ui/badge'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'

/**
 * The strategy engine: a race plan for the next Grand Prix, built the way the
 * strategy guide builds one, with the guide's own words beside each call.
 *
 * Every call on the page is the engine's arithmetic; the quotes are citations,
 * not the source of the numbers. That is why each sits under the call it
 * supports rather than in a reading list at the bottom.
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


function PlanView({ body, driver, onDriver }) {
  const { plan, guide = {}, field = [] } = body
  if (!plan?.plans?.length)
    return <Unavailable what="The strategy engine" reason="No plan could be built for this race." />
  const who = plan.driver

  return (
    <>
      <Panel title="Plan for" subtitle={who
        ? `${who.name} · ${who.team} · starts P${who.grid}`
        : 'The race as a whole. Pick a car to plan its stop around the cars near it.'}>
        <label className="small muted" htmlFor="strategy-driver">Car </label>
        <select id="strategy-driver" value={driver} onChange={(e) => onDriver(e.target.value)}
                disabled={!field.length}>
          <option value="">The race (no car)</option>
          {field.map((car) => (
            <option key={car.driver} value={car.driver}>P{car.grid} {car.driver}</option>
          ))}
        </select>
        {!field.length && (
          <p className="small muted">No grid is held for this race yet, so the plan is for the race as a whole.</p>
        )}
        {body.driver_not_found && <p className="notice warn">That driver is not on this grid.</p>}
      </Panel>

      {body.guide_note && <div className="notice">{body.guide_note}</div>}
      {body.unavailable?.length > 0 && (
        <div className="notice warn">
          Some inputs could not be loaded ({body.unavailable.join(', ')}); the plan uses
          labelled generic figures in their place.
        </div>
      )}

      <Panel title="What kind of race" subtitle={`Overtaking here is ${plan.race_character.overtaking}`}>
        <p><Badge variant="outline">{plan.race_character.kind}</Badge> {plan.race_character.summary}</p>
        <ul className="small muted">
          {plan.race_character.reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
        <Guide quotes={guide.race_character} />
      </Panel>

      <Panel title="Plans A, B and C" subtitle={`${plan.race_laps} laps · a stop costs ${plan.pit_loss_seconds}s`}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Plan</TableHead>
              <TableHead>Stints</TableHead>
              <TableHead>Stops</TableHead>
              <TableHead className="num">vs best</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {plan.plans.map((p) => (
              <TableRow key={p.label}>
                <TableCell><strong>{p.label}</strong> {p.name}</TableCell>
                <TableCell>
                  {p.stints.map((s) => `${s.compound.toLowerCase()} ${s.from_lap}–${s.to_lap}`).join(', ')}
                </TableCell>
                <TableCell>
                  {p.pit_stops.map((s) => (
                    <div key={s.optimum_lap}>
                      lap {s.target_lap}
                      <span className="muted small"> (optimum {s.optimum_lap}, window {s.window[0]}–{s.window[1]})</span>
                    </div>
                  ))}
                </TableCell>
                <TableCell className="num">
                  {p.expected_delta_seconds === 0 ? 'best' : `+${p.expected_delta_seconds.toFixed(1)}s`}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <ul className="small">
          {plan.plans.map((p) => <li key={p.label}><strong>{p.label}.</strong> {p.why}</li>)}
        </ul>
        <Guide quotes={guide.plans} />
        <Guide quotes={guide.targets} />
      </Panel>

      <Panel title="Start tyre" subtitle={plan.start_tyre.compound.toLowerCase()}>
        <p>{plan.start_tyre.why}</p>
        <Guide quotes={guide.start_tyre} />
      </Panel>

      <Panel title="Plan S: the safety car"
             subtitle={`${Math.round(plan.safety_car.safety_car_chance * 100)}% chance of a safety car in our simulation`}>
        <ul>{plan.safety_car.summary.map((line) => <li key={line}>{line}</li>)}</ul>
        <Guide quotes={guide.safety_car} />
      </Panel>

      {who && (
        <>
          <Section title="The stop, among the field" section={plan.pit_stop} quotes={guide.pit_stop} />
          <Section title="Undercut" section={plan.undercut} quotes={guide.undercut} />
          <Section title="Edge cases" section={plan.edge_cases} quotes={guide.edge_cases} />
        </>
      )}

      <Panel title="What the plan is built from">
        <ul className="small muted">{plan.inputs.map((line) => <li key={line}>{line}</li>)}</ul>
        <p className="small muted">Not modelled: {plan.not_modelled}</p>
      </Panel>
    </>
  )
}


function Section({ title, section, quotes }) {
  if (!section?.summary?.length) return null
  return (
    <Panel title={title}>
      <ul>{section.summary.map((line) => <li key={line}>{line}</li>)}</ul>
      <Guide quotes={quotes} />
    </Panel>
  )
}


/** The guide's passage behind a call, with its book and chapter. */
function Guide({ quotes }) {
  if (!quotes?.length) return null
  return quotes.map((q) => (
    <blockquote key={`${q.chapter}-${q.excerpt.slice(0, 24)}`} className="guide-quote small">
      “{q.excerpt}”
      <footer className="muted">
        {q.title}{q.author ? `, ${q.author}` : ''} · chapter {q.chapter}, {q.chapter_title}
      </footer>
    </blockquote>
  ))
}
