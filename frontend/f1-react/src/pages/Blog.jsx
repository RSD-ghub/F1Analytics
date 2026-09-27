import { Link, useSearchParams } from 'react-router-dom'
import { getBlogIndex } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Loading, Unavailable } from '../components/Panel'

/**
 * One Blog's front door.
 *
 * The entries have always existed at /blog/{season}/{round}, which is only
 * reachable by already knowing a season and a round — so the part of the
 * product meant to be a highlight could not be navigated to at all. There was
 * no link to it in the masthead and no index behind one.
 *
 * Ordered as a record, not a calendar: what has happened first, newest at the
 * top, then what is still to come.
 */

function when(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

export default function Blog() {
  const [params, setParams] = useSearchParams()
  const season = params.get('season')
  const state = useAsync(() => getBlogIndex(season), [season])

  if (state.status === 'loading') return <Loading what="Loading the record" />
  if (state.status === 'error')
    return <Unavailable what="The weekend record" reason={state.error.message} />

  const { weekends = [], seasons = [], season: current } = state.data
  const run = weekends.filter((w) => w.has_run)
  const upcoming = weekends.filter((w) => !w.has_run)

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">One Blog</p>
        <h1>The weekend record</h1>
        <p className="lede">
          Every race weekend as it unfolded — what practice showed, what
          qualifying settled, what we forecast before it, and what happened.
          Entries are appended, never edited afterwards.
        </p>
        {seasons.length > 1 && (
          <label className="season-pick">
            <span className="small muted">Season</span>
            <select
              value={current}
              onChange={(e) => setParams({ season: e.target.value })}
            >
              {seasons.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        )}
      </header>

      {run.length > 0 && <WeekendList title="Raced" weekends={run} />}
      {upcoming.length > 0 && <WeekendList title="Still to come" weekends={upcoming} upcoming />}

      {weekends.length === 0 && (
        <p className="muted">No weekends on the calendar for {current}.</p>
      )}
    </div>
  )
}

function WeekendList({ title, weekends, upcoming = false }) {
  return (
    <section>
      <h2 className="section-head">{title}</h2>
      <ul className="weekend-list">
        {weekends.map((w) => (
          <li key={w.round}>
            {/* Upcoming rounds are not links. Before a race there is no
                practice, no qualifying and no result, so following one lands
                on "nothing to report yet" — a card that promises a report and
                opens onto nothing is worse than one that says so. */}
            {upcoming ? (
              <div className="weekend-card weekend-card-soon">
                <Card weekend={w} />
              </div>
            ) : (
              <Link className="weekend-card" to={`/blog/${w.season}/${w.round}`}>
                <Card weekend={w} />
              </Link>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Card({ weekend: w }) {
  return (
    <>
      <span className="weekend-round">R{w.round}</span>
      <span className="weekend-name">
        {w.race_name}
        <span className="weekend-circuit">{w.circuit}{w.country ? ` · ${w.country}` : ''}</span>
      </span>
      <span className="weekend-when">
        {when(w.race_start_utc)}
        {w.has_run && (
          <span className={w.scored ? 'chip chip-on' : 'chip'}>
            {w.scored ? 'scored' : 'not scored'}
          </span>
        )}
      </span>
    </>
  )
}
