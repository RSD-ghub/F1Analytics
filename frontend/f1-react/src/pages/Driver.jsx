import { Link, useParams } from 'react-router-dom'
import { getDriver } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { usePageTitle } from '../hooks/usePageTitle'
import { Panel, Loading, Unavailable } from '../components/Panel'

/** One driver: this season race by race, and their career season by season. */
export default function Driver() {
  const { season, driver } = useParams()
  usePageTitle(driver)
  const state = useAsync(() => getDriver(season, driver), [season, driver])

  if (state.status === 'loading') return <Loading what={`Loading ${driver}`} />
  if (state.status === 'error')
    return <Unavailable what={driver} reason={state.error.message} />

  const { team, summary, races, career, career_total: total } = state.data

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">{season}{team && ` · ${team}`}</p>
        <h1>{driver}</h1>
        <p className="muted">
          {total.starts} Grand Prix start{total.starts === 1 ? '' : 's'} on record,{' '}
          {total.wins} win{total.wins === 1 ? '' : 's'}, {total.podiums} podium{total.podiums === 1 ? '' : 's'}.
        </p>
      </header>

      {summary ? (
        <div className="tiles">
          <Tile label="Points" value={summary.points} />
          <Tile label="Wins" value={summary.wins} />
          <Tile label="Podiums" value={summary.podiums} />
          <Tile label="Best finish" value={summary.best_finish ? `P${summary.best_finish}` : '—'} />
          <Tile label="Retirements" value={summary.retirements} />
        </div>
      ) : (
        <p className="muted">No {season} races on record for {driver}.</p>
      )}

      {races.length > 0 && (
        <Panel title={`${season}, race by race`}
               subtitle="Places gained compare the starting grid with the classified finish.">
          <table className="datatable">
            <thead>
              <tr>
                <th>Round</th><th>Race</th><th className="num">Grid</th>
                <th className="num">Finish</th><th className="num">+/−</th>
                <th className="num">Points</th><th />
              </tr>
            </thead>
            <tbody>
              {races.map((r) => (
                <tr key={r.round}>
                  <td className="num">{r.round}</td>
                  <td>{r.race_name}</td>
                  <td className="num">{r.grid ? `P${r.grid}` : 'Pit lane'}</td>
                  <td className="num">{r.finish ? `P${r.finish}` : <span className="muted">{r.status || 'DNF'}</span>}</td>
                  <td className={`num ${r.places_gained > 0 ? 'good' : r.places_gained < 0 ? 'bad' : ''}`}>
                    {r.places_gained == null ? '—' : `${r.places_gained > 0 ? '+' : ''}${r.places_gained}`}
                  </td>
                  <td className="num">{r.points}</td>
                  <td>
                    <Link className="link small" to={`/race-review/${season}/${r.round}`}>Pit stops</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      <Panel title="Career">
        <table className="datatable compact">
          <thead>
            <tr>
              <th>Season</th><th>Team</th><th className="num">Starts</th>
              <th className="num">Wins</th><th className="num">Podiums</th><th className="num">Points</th>
            </tr>
          </thead>
          <tbody>
            {career.map((c) => (
              <tr key={c.season}>
                <td><Link className="link" to={`/drivers/${c.season}/${encodeURIComponent(driver)}`}>{c.season}</Link></td>
                <td className="muted">{c.team}</td>
                <td className="num">{c.starts}</td>
                <td className="num">{c.wins}</td>
                <td className="num">{c.podiums}</td>
                <td className="num">{c.points}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Link className="link" to={`/drivers?season=${season}`}>← All {season} drivers</Link>
    </div>
  )
}

function Tile({ label, value }) {
  return (
    <div className="tile">
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
    </div>
  )
}
