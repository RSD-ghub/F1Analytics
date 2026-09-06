import { getChampionship } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable, Bar } from '../components/Panel'

/**
 * Title probabilities from simulating every remaining race.
 *
 * The framing matters and is stated on the page: these assume the season
 * continues as it looks today. Form is frozen at current values and races are
 * simulated independently, so neither a mid-season upgrade nor a correlated run
 * of failures is anticipated. Presenting the number without that caveat would
 * imply more foresight than the method has.
 */
export default function Championship() {
  const season = new Date().getUTCFullYear()
  const state = useAsync(() => getChampionship(season), [season])

  if (state.status === 'loading') return <Loading what="Simulating the season" />
  if (state.status === 'error')
    return <Unavailable what="The championship forecast" reason={state.error.message} />

  const forecast = state.data

  return (
    <div className="stack">
      <header className="page-head">
        <h1>Championship</h1>
        <p className="muted">
          {forecast.runs.toLocaleString()} simulations of the{' '}
          {forecast.remaining_rounds.length} remaining race
          {forecast.remaining_rounds.length === 1 ? '' : 's'}, after round{' '}
          {forecast.as_of_round}.
        </p>
      </header>

      <p className="notice">
        Assumes the season continues as it looks today. Form is held at current
        values and races are simulated independently, so a mid-season upgrade or
        a run of reliability failures is not anticipated.
      </p>

      <Standings title="Drivers" rows={forecast.drivers} />
      <Standings title="Constructors" rows={forecast.constructors} nameKey="team" />
    </div>
  )
}

function Standings({ title, rows, nameKey = 'driver' }) {
  const contenders = rows.filter((r) => r.p_champion > 0.001)
  const eliminated = rows.length - contenders.length

  return (
    <Panel
      title={title}
      footer={
        eliminated > 0 && (
          <p className="muted small">
            {eliminated} {eliminated === 1 ? 'entry has' : 'entries have'} a title
            chance below 0.1% and are not listed. That is not the same as
            mathematically eliminated.
          </p>
        )
      }
    >
      <table className="grid">
        <thead>
          <tr>
            <th>{nameKey === 'team' ? 'Team' : 'Driver'}</th>
            <th className="num">Points</th>
            <th className="num">Title</th>
            <th className="num">Projected</th>
            <th className="viz" />
          </tr>
        </thead>
        <tbody>
          {contenders.map((row) => (
            <tr key={row[nameKey] ?? row.driver}>
              <td>{row[nameKey] ?? row.driver}</td>
              <td className="num">{row.current_points.toFixed(0)}</td>
              <td className="num strong">{(row.p_champion * 100).toFixed(1)}%</td>
              <td className="num muted">{row.expected_final_points.toFixed(0)}</td>
              <td className="viz"><Bar value={row.p_champion} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
