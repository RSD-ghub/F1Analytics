/**
 * The pre-race brief: what this track is, and what it has historically done.
 *
 * Every number traces to our own corpus and is shown with the sample it rests
 * on. Nine visits is nine visits, and the panel says so rather than phrasing a
 * small sample as a law of the circuit — the same standard the forecasts are
 * held to, applied to the prose around them.
 */

function Stat({ label, value, note }) {
  return (
    <div className="brief-stat">
      <span className="brief-value">{value}</span>
      <span className="brief-label">{label}</span>
      {note && <span className="brief-note">{note}</span>}
    </div>
  )
}

const ARCHETYPE_BLURB = {
  power: 'Long full-throttle runs. Rewards a low-drag car and punishes drag.',
  technical: 'Corner after corner with little straight. Downforce and precision.',
  balanced: 'No single demand dominates; the compromise is the challenge.',
}

function lapTime(seconds) {
  if (!seconds) return '—'
  const m = Math.floor(seconds / 60)
  const s = (seconds - m * 60).toFixed(3).padStart(6, '0')
  return `${m}:${s}`
}

export default function CircuitBrief({ circuit }) {
  if (!circuit) return null
  const stats = circuit.stats || {}
  const map = circuit.map
  const official = circuit.official
  const character = circuit.character || {}
  const archetype = character.archetype && character.archetype !== 'unknown'
    ? character.archetype
    : null

  const visits = stats.races_in_corpus || 0

  return (
    <div className="brief">
      {archetype && (
        <p className="brief-character">
          <span className={`tag tag-${archetype}`}>{archetype}</span>
          {ARCHETYPE_BLURB[archetype]}
        </p>
      )}

      <div className="brief-stats">
        {/* The official length, when we have it, over the one traced from a
            lap: the racing line cuts every apex and comes out short — Baku
            measures 5.937km off the telemetry against a stated 6.003km. */}
        {(official?.length_km || map?.lap_distance_m > 0) && (
          <Stat
            label="Lap"
            value={`${(official?.length_km ?? map.lap_distance_m / 1000).toFixed(3)} km`}
            note={[
              map?.corners?.length ? `${map.corners.length} corners` : null,
              official?.scheduled_laps ? `${official.scheduled_laps} laps` : null,
            ].filter(Boolean).join(' · ')}
          />
        )}
        {/* The official record beats ours wherever it exists. Ours is the
            fastest lap in the races we hold, which have lap timing only from
            2018 — for Sepang, returning after last racing in 2017, that is no
            record at all. */}
        {(official?.lap_record_time || stats.fastest_lap) && (
          <Stat
            label="Lap record"
            value={official?.lap_record_time ?? lapTime(stats.fastest_lap.seconds)}
            note={official?.lap_record_time
              ? `${official.lap_record_driver}, ${official.lap_record_season}`
              : `${stats.fastest_lap.driver}, ${stats.fastest_lap.season}`}
          />
        )}
        {stats.top_speed_kph > 0 && (
          <Stat
            label="Top speed seen"
            value={`${stats.top_speed_kph} kph`}
            note={`${stats.median_speed_trap_kph} kph typical`}
          />
        )}
        {stats.pole_starts > 0 && (
          <Stat
            label="Pole converted"
            value={`${stats.pole_wins} of ${stats.pole_starts}`}
            note={visits < 4 ? 'too few visits to call' : 'wins from pole'}
          />
        )}
        {stats.mean_places_changed != null && (
          <Stat
            label="Places changed"
            value={stats.mean_places_changed.toFixed(1)}
            note="average car, grid to flag"
          />
        )}
        {stats.finish_rate != null && (
          <Stat
            label="Cars classified"
            value={`${Math.round(stats.finish_rate * 100)}%`}
            note={`${stats.starts_counted} starts`}
          />
        )}
      </div>

      <p className="brief-source small muted">
        {official?.official_name && official.official_name !== circuit.circuit && (
          <>{official.official_name}. </>
        )}
        {official?.first_season && (
          <>First grand prix {official.first_season}. </>
        )}
        {visits > 0
          ? `From ${visits} race${visits === 1 ? '' : 's'} in our corpus, ${stats.first_season}–${stats.last_season}.`
          : 'No previous race at this circuit in our corpus.'}
        {stats.most_wins &&
          ` ${stats.most_wins.driver} has won here ${stats.most_wins.wins} times.`}
        {official && (
          <>
            {' '}Official figures from{' '}
            <a href={official.url} target="_blank" rel="noreferrer noopener">
              formula1.com
            </a>.
          </>
        )}
      </p>
    </div>
  )
}
