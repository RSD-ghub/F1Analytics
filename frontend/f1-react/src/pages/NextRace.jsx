import { Link } from 'react-router-dom'
import { getNextRace } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable, Probability, Bar, Caveats } from '../components/Panel'
import TrackMap from '../components/TrackMap'
import CircuitBrief from '../components/CircuitBrief'
import { OddsBar, HundredRaces, InPlainWords } from '../components/Odds'

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

  const {
    weekend, predictions = [], qualifying_freshness: freshness, circuit, unavailable,
  } = state.data
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

      {circuit && (
        <Panel
          title={circuit.circuit}
          subtitle={[circuit.country, weekend.race_name].filter(Boolean).join(' · ')}
        >
          <div className="weekend-hero">
            <TrackMap map={circuit.map} />
            <CircuitBrief circuit={circuit} />
          </div>
        </Panel>
      )}

      {freshness?.is_stale && (
        <div className="notice warn">
          Qualifying has run but its results have not been ingested yet, so no
          grid-aware forecast can be locked.
        </div>
      )}

      {predictions.length === 0 && (
        <Panel title="No forecast locked yet">
          <p className="muted">
            Forecasts lock at three points: roughly three days out, again after
            qualifying, and once more on the confirmed grid forty-five minutes
            before the start. All three are permanent once published.
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
  const callsWinner = publishes.has('win')
  const rows = [...(prediction.driver_probabilities || [])].sort(
    (a, b) => (b.p_podium ?? 0) - (a.p_podium ?? 0),
  )
  // Ranked by the market being led with, so the sentence and the frequency
  // grid describe the same driver the table puts first.
  const leader = callsWinner
    ? [...rows].sort((a, b) => (b.p_win ?? 0) - (a.p_win ?? 0))[0]
    : rows[0]

  return (
    <Panel
      title={callsWinner ? 'Forecast — grid set' : 'Forecast — before qualifying'}
      subtitle={`Locked ${new Date(prediction.locked_at).toLocaleString()} · model ${prediction.model_version}`}
      footer={<Caveats quality={prediction.data_quality} />}
    >
      <div className="forecast-head">
        <div>
          <InPlainWords
            leader={leader?.driver}
            win={callsWinner ? leader?.p_win : null}
            podium={leader?.p_podium}
          />
          <p className="small muted">
            Each bar below is one driver&rsquo;s chances, nested: the pale band
            is a points finish, the middle band a podium, the bright tip a win.
            Every win is a podium and every podium scores, which is why they
            sit inside one another rather than side by side.
          </p>
        </div>
        {leader && (
          <HundredRaces
            probability={callsWinner ? leader.p_win : leader.p_podium}
            label={callsWinner
              ? `Races out of 100 that ${leader.driver} wins`
              : `Races out of 100 that ${leader.driver} finishes on the podium`}
          />
        )}
      </div>

      <table className="grid">
        <thead>
          <tr>
            <th>Driver</th>
            <th className="viz">Chances</th>
            <th className="num">Win</th>
            <th className="num">Podium</th>
            <th className="num">Points</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.driver}>
              <td>
                <span className="driver">{row.driver}</span>
                <span className="team">{row.team}</span>
              </td>
              <td className="viz">
                <OddsBar win={row.p_win} podium={row.p_podium} points={row.p_points} />
              </td>
              {/* The numbers stay. The bar is for reading the shape of the
                  field at a glance; anyone checking a specific claim against
                  the scored record still needs the figure. */}
              <td className="num"><Probability value={row.p_win} /></td>
              <td className="num"><Probability value={row.p_podium} /></td>
              <td className="num"><Probability value={row.p_points} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
