import { Link } from 'react-router-dom'
import { Panel } from './Panel'

/**
 * The race that just happened, and whether we were right about it.
 *
 * The front page spends most of the week between races. Without this it showed
 * a circuit and "no forecast locked yet" for five days out of seven — while
 * the thing the product actually claims, that it publishes first and is marked
 * afterwards, had just been demonstrated and appeared nowhere.
 *
 * It leads with the result rather than the score. A reader who has not opened
 * the track record does not yet know what a skill score is, and the honest
 * order is what happened, then what we had said, then how that rated.
 */

const WINDOW_LABEL = {
  final_grid: 'on the confirmed grid, 45 minutes before the start',
  post_quali: 'after qualifying, on the provisional grid',
  pre_quali: 'three days out, before qualifying',
}

function verdict(called, winner, probability) {
  if (!called) return null
  const pct = Math.round((probability ?? 0) * 100)
  if (called === winner) {
    return (
      <>We made <strong>{called}</strong> the favourite at {pct}%, and the race
      went that way. At those odds it would not have, two times in three.</>
    )
  }
  return (
    <>Our favourite was <strong>{called}</strong> at {pct}%. It went to{' '}
    <strong>{winner}</strong> instead.</>
  )
}

export default function LastRace({ race }) {
  if (!race) return null
  const win = (race.markets || []).find((m) => m.market === 'win')
  const called = race.called_winner

  return (
    <Panel
      title={`Last time out — ${race.race_name}`}
      subtitle={`Forecast locked ${WINDOW_LABEL[race.window] || race.window}, and scored afterwards`}
    >
      <ol className="podium">
        {(race.podium || []).map((row) => (
          <li key={row.position} className={`podium-${row.position}`}>
            <span className="podium-pos">P{row.position}</span>
            <span className="podium-driver">{row.driver}</span>
            <span className="podium-team">{row.team}</span>
          </li>
        ))}
      </ol>

      <p className="lede">{verdict(called, race.winner, race.called_winner_probability)}</p>

      {win && (
        <p className="small muted">
          {/* Named as a comparison rather than a bare number. "+46%" means
              nothing on its own; what it measures is how much better the
              forecast did than assuming every car equally likely. */}
          On the winner market that forecast scored{' '}
          <strong>{(win.skill_vs_baseline * 100).toFixed(0)}% better</strong>{' '}
          than treating all {win.drivers_scored} cars as equally likely.{' '}
          <Link className="link" to={`/blog/${race.season}/${race.round}`}>
            See the full weekend →
          </Link>
        </p>
      )}
    </Panel>
  )
}
