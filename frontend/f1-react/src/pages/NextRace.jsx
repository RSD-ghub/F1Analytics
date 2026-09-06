import { Link } from 'react-router-dom'
import { getNextRace } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable, Probability, Bar, Caveats } from '../components/Panel'

/**
 * The upcoming weekend and whatever forecasts are locked for it.
 *
 * The page is arranged around what the model will and will not claim. Before
 * qualifying it publishes podium and points probabilities and explicitly no
 * winner — that refusal is rendered as a headline statement rather than an
 * empty column, because it is a finding about the model's limits and the
 * reader is entitled to it.
 */
export default function NextRace() {
  const state = useAsync(() => getNextRace(), [])

  if (state.status === 'loading') return <Loading what="Loading the next race" />
  if (state.status === 'error')
    return <Unavailable what="The next race" reason={state.error.message} />

  const { weekend, predictions = [], qualifying_freshness: freshness, unavailable } = state.data
  if (!weekend)
    return <Unavailable what="The next race" reason="No upcoming race is on the calendar." />

  const start = weekend.race_start_utc ? new Date(weekend.race_start_utc) : null

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">Round {weekend.round} · {weekend.season}</p>
        <h1>{weekend.race_name}</h1>
        {start && (
          <p className="muted">
            {start.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'short' })}
            {weekend.is_sprint_weekend && ' · sprint weekend'}
          </p>
        )}
      </header>

      {freshness?.is_stale && (
        <div className="notice warn">
          Qualifying has run but its results have not been ingested yet, so no
          grid-aware forecast can be locked.
        </div>
      )}

      {predictions.length === 0 && (
        <Panel title="No forecast locked yet">
          <p className="muted">
            Forecasts lock at two points: roughly three days before the race, and
            again once the grid is set. Both are permanent once published.
          </p>
        </Panel>
      )}

      {predictions.map((prediction) => (
        <Forecast key={prediction.prediction_id} prediction={prediction} />
      ))}

      {unavailable?.panels?.length > 0 && (
        <Unavailable
          what={unavailable.panels.join(' and ')}
          reason="Some panels could not be loaded; the rest of this page is unaffected."
        />
      )}

      <Link className="link" to={`/weekend/${weekend.season}/${weekend.round}`}>
        Read the weekend in full →
      </Link>
    </div>
  )
}

function Forecast({ prediction }) {
  const publishes = new Set(prediction.published_markets || [])
  const rows = [...(prediction.driver_probabilities || [])].sort(
    (a, b) => (b.p_podium ?? 0) - (a.p_podium ?? 0),
  )

  return (
    <Panel
      title={publishes.has('win') ? 'Forecast — grid set' : 'Forecast — before qualifying'}
      subtitle={`Locked ${new Date(prediction.locked_at).toLocaleString()} · model ${prediction.model_version}`}
      footer={<Caveats quality={prediction.data_quality} />}
    >
      {!publishes.has('win') && (
        <p className="notice">
          <strong>No winner call before qualifying.</strong> Measured across two
          held-out seasons, this window's win-market accuracy was no better than
          guessing — so we publish podium and points probabilities and make no
          claim about the winner.
        </p>
      )}

      <table className="grid">
        <thead>
          <tr>
            <th>Driver</th>
            <th className="num">Win</th>
            <th className="num">Podium</th>
            <th className="num">Points</th>
            <th className="viz" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.driver}>
              <td>
                <span className="driver">{row.driver}</span>
                <span className="team">{row.team}</span>
              </td>
              <td className="num"><Probability value={row.p_win} /></td>
              <td className="num"><Probability value={row.p_podium} /></td>
              <td className="num"><Probability value={row.p_points} /></td>
              <td className="viz"><Bar value={row.p_podium ?? 0} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
