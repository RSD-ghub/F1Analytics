import { Link, useParams } from 'react-router-dom'
import { getRaceReview } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { usePageTitle } from '../hooks/usePageTitle'
import { Panel, Loading, Unavailable } from '../components/Panel'

const COMPOUND = {
  SOFT: 'soft', MEDIUM: 'medium', HARD: 'hard', INTERMEDIATE: 'intermediate', WET: 'wet',
}

/**
 * How a finished race was run: each car's stints on a lap axis, its stops,
 * and how the stop counts compared. Only what was recorded; a season before
 * lap data (2018) shows the result and says the rest is missing.
 */
export default function RaceReview() {
  const { season, round } = useParams()
  const state = useAsync(() => getRaceReview(season, round), [season, round])
  usePageTitle(state.data?.race_name ? `${state.data.race_name} pit stops` : 'Pit stop review')

  if (state.status === 'loading') return <Loading what="Loading the pit stop review" />
  if (state.status === 'error')
    return <Unavailable what="The pit stop review" reason={state.error.message} />

  const { race_name: raceName, laps, drivers, fastest_stops: fastest, strategies, has_stint_data: hasStints } = state.data

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">Round {round} · {season}</p>
        <h1>{raceName} · pit stops</h1>
        <p className="muted">
          Every car's tyres and stops, in finishing order.{' '}
          <Link className="link" to={`/blog/${season}/${round}`}>Read the weekend →</Link>
        </p>
      </header>

      {!hasStints && (
        <div className="notice">
          No stint or pit stop data is recorded for this race. Lap data starts
          in 2018; for a later race, the full backfill has not reached it yet.
        </div>
      )}

      {hasStints && strategies.length > 0 && (
        <Panel title="How the strategies compared" subtitle="Classified finishers only">
          <div className="tiles">
            {strategies.map((s) => (
              <div className="tile" key={s.stops}>
                <span className="tile-value">{s.drivers}</span>
                <span className="tile-label">{s.stops}-stop{s.stops === 1 ? '' : 's'}</span>
                <span className="tile-note">
                  Best P{s.best_finish}
                  {s.mean_places_gained != null &&
                    ` · ${s.mean_places_gained >= 0 ? '+' : ''}${s.mean_places_gained} places on average from the grid`}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {hasStints && (
        <Panel title="Tyre stints" subtitle={laps ? `${laps} laps` : undefined}>
          <ol className="stint-chart">
            {drivers.map((d) => (
              <li key={d.driver}>
                <span className="stint-pos">{d.finish ? `P${d.finish}` : 'DNF'}</span>
                <Link className="stint-name link" to={`/drivers/${season}/${encodeURIComponent(d.driver)}`}>
                  {d.driver}
                </Link>
                <span className="stint-track" aria-label={describe(d)}>
                  {d.stints.map((s, i) => (
                    <span
                      key={i}
                      className={`rr-stint c-${COMPOUND[s.compound?.toUpperCase()] ?? 'unknown'}`}
                      style={{ flexGrow: s.laps || 1 }}
                      title={`${s.compound}: laps ${s.from_lap}–${s.to_lap}`}
                    >
                      {s.laps >= 6 ? s.compound[0] : ''}
                    </span>
                  ))}
                  {laps && d.stints.length > 0 && (
                    <span style={{ flexGrow: Math.max(0, laps - d.stints.at(-1).to_lap) }} />
                  )}
                </span>
              </li>
            ))}
          </ol>
          <p className="muted small">
            S soft · M medium · H hard · I intermediate · W wet. A bar that stops
            short is a car that did not finish.
          </p>
        </Panel>
      )}

      {fastest.length > 0 && (
        <Panel title="Quickest stops" subtitle="Time in the pit lane, entry to exit">
          <table className="datatable compact">
            <thead><tr><th>Driver</th><th>Team</th><th className="num">Lap</th><th className="num">Time</th></tr></thead>
            <tbody>
              {fastest.map((s) => (
                <tr key={`${s.driver}-${s.lap}`}>
                  <td>{s.driver}</td><td className="muted">{s.team}</td>
                  <td className="num">{s.lap}</td><td className="num">{s.duration_seconds.toFixed(1)}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {hasStints && (
        <Panel title="Every stop">
          <table className="datatable compact">
            <thead>
              <tr><th>Driver</th><th className="num">Grid</th><th className="num">Finish</th><th>Stops (lap · time)</th></tr>
            </thead>
            <tbody>
              {drivers.map((d) => (
                <tr key={d.driver}>
                  <td>{d.driver}</td>
                  <td className="num">{d.grid ? `P${d.grid}` : 'Pit lane'}</td>
                  <td className="num">{d.finish ? `P${d.finish}` : <span className="muted">{d.status || 'DNF'}</span>}</td>
                  <td className="mono small">
                    {d.stops.length === 0 ? '—' : d.stops.map((s) =>
                      `L${s.lap}${s.duration_seconds ? ` · ${s.duration_seconds.toFixed(1)}s` : ''}`).join('   ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  )
}

function describe(d) {
  return d.stints.map((s) => `${s.compound} laps ${s.from_lap} to ${s.to_lap}`).join(', ')
}
