import { Link } from 'react-router-dom'
import { getNextRace, getLastRace } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable, Probability, Caveats } from '../components/Panel'
import TrackMap from '../components/TrackMap'
import CircuitBrief from '../components/CircuitBrief'
import VenuePhotos from '../components/VenuePhotos'
import { OddsBar, HundredRaces, InPlainWords } from '../components/Odds'
import RaceHero from '../components/RaceHero'
import { Podium, RankList, RankRow } from '../components/Pit'
import LastRace from '../components/LastRace'
import RaceSimulation from '../components/RaceSimulation'

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
  // Fetched separately so a scoring outage costs this panel and not the
  // forecast the page exists for.
  const last = useAsync(() => getLastRace(), [])

  if (state.status === 'loading') return <Loading what="Loading the next race" />
  if (state.status === 'error')
    return <Unavailable what="The next race" reason={state.error.message} />

  const {
    weekend, predictions = [], sprint_predictions: sprintPredictions = [],
    qualifying_freshness: freshness, circuit, simulation, unavailable,
  } = state.data
  if (!weekend)
    return <Unavailable what="The next race" reason="No upcoming race is on the calendar." />

  return (
    <div className="stack">
      <RaceHero
        headingLevel={1}
        season={weekend.season} round={weekend.round} raceName={weekend.race_name}
        circuit={circuit?.circuit} country={circuit?.country}
        start={weekend.race_start_utc} sprint={weekend.is_sprint_weekend}
        outline={circuit?.map?.outline}
      />

      {circuit && (
        <Panel
          title={circuit.circuit}
          subtitle={[circuit.country, weekend.race_name].filter(Boolean).join(' · ')}
        >
          <VenuePhotos images={circuit.imagery} circuit={circuit.circuit} />
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

      {/* The sprint runs first, so its forecast leads while it is the next
          thing to happen. It is a separate race with its own grid and its own
          result, and is never folded into the Grand Prix forecasts below. */}
      {sprintPredictions.map((prediction) => (
        <Forecast key={prediction.prediction_id} prediction={prediction} sprint />
      ))}

      {/* The neural model's ten thousand races on the confirmed grid. Leads
          the Grand Prix once it exists: it is the most informed view of the
          race there is. Before the grid is confirmed it says what it is
          waiting for. */}
      <RaceSimulation simulation={simulation} />

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

      {last.status === 'ready' && last.data && <LastRace race={last.data} />}

      {unavailable?.panels?.length > 0 && (
        <Unavailable
          what={unavailable.panels.join(' and ')}
          reason="Some panels could not be loaded; the rest of this page is unaffected."
        />
      )}

      {/* Only once the weekend has something on it. Before a race there is no
          practice, no qualifying and no result to read, so this was the only
          call to action on the front page and it led to "nothing to report
          yet" for about five days in every seven. The last-race panel carries
          its own link to the weekend it describes, so there is no gap to
          fill here. */}
      {predictions.length > 0 && (
        <Link className="link" to={`/blog/${weekend.season}/${weekend.round}`}>
          Read the weekend in full →
        </Link>
      )}
    </div>
  )
}

function Forecast({ prediction, sprint = false }) {
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
  const race = sprint ? 'sprint' : 'race'
  const pointsPlaces = prediction.points_positions ?? 10
  const title = sprint
    ? 'Sprint forecast — sprint grid set'
    : callsWinner ? 'Forecast — grid set' : 'Forecast — before qualifying'

  return (
    <Panel
      title={title}
      subtitle={`Locked ${new Date(prediction.locked_at).toLocaleString()} · model ${prediction.model_version}`}
      footer={<Caveats quality={prediction.data_quality} />}
    >
      <div className="forecast-head">
        <div>
          <InPlainWords
            leader={leader?.driver}
            win={callsWinner ? leader?.p_win : null}
            podium={leader?.p_podium}
            race={race}
          />
          <p className="small muted">
            Each bar below is one driver&rsquo;s chances, nested: the pale band
            is a points finish, the middle band a podium, the bright tip a win.
            Every win is a podium and every podium scores, which is why they
            sit inside one another rather than side by side.
            {sprint && ` In a sprint only the top ${pointsPlaces} score, so the points band is narrower than on Sunday.`}
          </p>
        </div>
        {leader && (
          <HundredRaces
            probability={callsWinner ? leader.p_win : leader.p_podium}
            unit={sprint ? 'sprints' : 'races'}
            label={callsWinner
              ? `${sprint ? 'Sprints' : 'Races'} out of 100 that ${leader.driver} wins`
              : `Races out of 100 that ${leader.driver} finishes on the podium`}
          />
        )}
      </div>

      {/* The three most likely to stand on the podium, on the podium. Ranked
          by the market the forecast leads with, so the steps, the sentence
          above and the list below all agree on who is first. */}
      <Podium
        entries={[...rows]
          .sort((a, b) => ((callsWinner ? b.p_win : b.p_podium) ?? 0) - ((callsWinner ? a.p_win : a.p_podium) ?? 0))
          .slice(0, 3)
          .map((r) => ({
            name: r.driver, team: r.team,
            value: `${Math.round(((callsWinner ? r.p_win : r.p_podium) ?? 0) * 100)}% ${callsWinner ? 'win' : 'podium'}`,
          }))}
      />

      <div className="forecast-list">
        <div className="forecast-list-head" aria-hidden="true">
          <span>Driver</span>
          <span className="viz">Chances</span>
          <span className="num">Win</span>
          <span className="num">Podium</span>
          <span className="num">Points</span>
        </div>
        <RankList label={`${race} forecast, most likely podium first`}>
          {rows.map((row, i) => (
            <RankRow key={row.driver} pos={i + 1} name={row.driver} team={row.team}>
              <span className="viz">
                <OddsBar win={row.p_win} podium={row.p_podium} points={row.p_points} />
              </span>
              {/* The numbers stay. The bar is for reading the shape of the
                  field at a glance; anyone checking a specific claim against
                  the scored record still needs the figure. */}
              <span className="num"><Probability value={row.p_win} /></span>
              <span className="num"><Probability value={row.p_podium} /></span>
              <span className="num"><Probability value={row.p_points} /></span>
            </RankRow>
          ))}
        </RankList>
      </div>
    </Panel>
  )
}
