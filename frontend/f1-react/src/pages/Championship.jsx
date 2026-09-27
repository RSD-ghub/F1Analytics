import { getChampionship } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable, Bar } from '../components/Panel'
import { HundredRaces } from '../components/Odds'

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

      <Lead forecast={forecast} />

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

/**
 * What twenty thousand simulated seasons actually said.
 *
 * The page buried it. Antonelli takes the title in 99.9% of them with eight
 * races still to run, and that figure sat in a table cell at thirteen pixels
 * between two columns nobody came for. A championship that is effectively
 * decided is the story, and a page about it should say so before it starts
 * tabulating.
 */
function Lead({ forecast }) {
  const leader = [...(forecast.drivers || [])]
    .sort((a, b) => b.p_champion - a.p_champion)[0]
  if (!leader) return null

  const pct = leader.p_champion * 100
  const remaining = forecast.remaining_rounds.length
  const settled = pct >= 95

  return (
    <Panel
      title="The title, as the simulations see it"
      subtitle={`${forecast.runs.toLocaleString()} seasons played out from where this one stands`}
    >
      <div className="forecast-head">
        <div>
          <p className="lede">
            <strong>{leader.driver}</strong> takes the championship in{' '}
            {pct >= 99.95 ? 'almost every' : `${pct.toFixed(1)}% of`} simulated
            season{pct >= 99.95 ? '' : 's'}, with {remaining} race
            {remaining === 1 ? '' : 's'} still to run.
          </p>
          <p className="small muted">
            {settled
              ? 'That is not the same as mathematically decided — the points are still there to be taken. It means that across twenty thousand plausible remainders of this season, almost none of them ended another way.'
              : 'Still open. The square below is one simulated season each; the filled ones are the seasons this driver wins.'}
          </p>
        </div>
        <HundredRaces
          probability={leader.p_champion}
          unit="simulated seasons"
          label={`Simulated seasons ${leader.driver} wins`}
        />
      </div>
    </Panel>
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
            <th className="num">Title chance</th>
            {/* The bar shows the title chance, so it sits beside it. It used
                to be the last column, two places away from the number it
                draws and flush against projected points, which is what a
                reader took it to mean. */}
            <th className="viz" />
            <th className="num">Projected</th>
          </tr>
        </thead>
        <tbody>
          {contenders.map((row) => (
            <tr key={row[nameKey] ?? row.driver}>
              <td>{row[nameKey] ?? row.driver}</td>
              <td className="num">{row.current_points.toFixed(0)}</td>
              <td className="num strong">{(row.p_champion * 100).toFixed(1)}%</td>
              <td className="viz"><Bar value={row.p_champion} /></td>
              <td className="num muted">{row.expected_final_points.toFixed(0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
