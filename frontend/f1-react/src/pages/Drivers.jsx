import { Link, useSearchParams } from 'react-router-dom'
import { getDrivers } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { usePageTitle } from '../hooks/usePageTitle'
import { Panel, Loading, Unavailable } from '../components/Panel'

const THIS_SEASON = new Date().getFullYear()

/** Every driver who started a race in a season, by points. */
export default function Drivers() {
  const [params, setParams] = useSearchParams()
  const season = Number(params.get('season')) || THIS_SEASON
  usePageTitle(`Drivers ${season}`)
  const state = useAsync(() => getDrivers(season), [season])

  return (
    <div className="stack">
      <header className="page-head">
        <h1>Drivers</h1>
        <p className="muted">
          Every driver who started a Grand Prix in {season}. Wins and podiums
          count classified finishes only; a retirement is not a result.
        </p>
        <label className="season-pick">
          Season{' '}
          <select value={season} onChange={(e) => setParams({ season: e.target.value })}>
            {Array.from({ length: THIS_SEASON - 2009 }, (_, i) => THIS_SEASON - i).map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </label>
      </header>

      {state.status === 'loading' && <Loading what="Loading drivers" />}
      {state.status === 'error' && <Unavailable what="The driver list" reason={state.error.message} />}
      {state.status === 'ready' && (
        state.data.length === 0 ? (
          <Unavailable what="Drivers" reason={`No results are recorded for ${season}.`} />
        ) : (
          <Panel>
            <table className="datatable">
              <thead>
                <tr>
                  <th>Driver</th><th>Team</th><th className="num">Starts</th>
                  <th className="num">Wins</th><th className="num">Podiums</th>
                  <th className="num">Best</th><th className="num">Points</th>
                </tr>
              </thead>
              <tbody>
                {state.data.map((d) => (
                  <tr key={d.driver}>
                    <td>
                      <Link className="link" to={`/drivers/${season}/${encodeURIComponent(d.driver)}`}>
                        {d.driver}
                      </Link>
                    </td>
                    <td className="muted">{d.team}</td>
                    <td className="num">{d.starts}</td>
                    <td className="num">{d.wins}</td>
                    <td className="num">{d.podiums}</td>
                    <td className="num">{d.best_finish ? `P${d.best_finish}` : '—'}</td>
                    <td className="num">{d.points}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )
      )}
    </div>
  )
}
