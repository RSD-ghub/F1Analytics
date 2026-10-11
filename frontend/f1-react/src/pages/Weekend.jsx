import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getWeekend, getCircuit } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable, Probability } from '../components/Panel'
import TrackMap from '../components/TrackMap'
import CircuitBrief from '../components/CircuitBrief'
import VenuePhotos from '../components/VenuePhotos'
import NewsList from '../components/NewsList'

/**
 * One Blog — a race weekend as an append-only timeline.
 *
 * Entries arrive in the order the facts became knowable: what practice showed,
 * what qualifying settled, what we forecast, what happened. That ordering is
 * the point. A reader can see exactly what was known at the moment a forecast
 * was committed, which is the same property the leakage tests enforce inside
 * the model.
 *
 * Every entry carries its sources, and they are rendered. An entry whose claims
 * cannot be traced back to stored data is indistinguishable from an invented
 * one, so provenance is shown rather than assumed.
 */
const KIND_LABEL = {
  paddock_news: 'Paddock news',
  practice_report: 'Practice',
  qualifying_report: 'Qualifying',
  forecast: 'Our forecast',
  result: 'Result',
}

export default function Weekend() {
  const { season, round } = useParams()
  /*
   * Two requests, not one, because they cost three orders of magnitude apart.
   *
   * The facts are assembled from stored data and come back in about twenty
   * milliseconds. The narration asks a language model to write prose over
   * every entry and takes thirteen seconds warm, considerably longer cold —
   * and while it ran, this page showed "Loading the weekend…" and nothing
   * else. Ninety-nine per cent of the content was ready almost immediately
   * and was being withheld to wait for the last one per cent.
   *
   * So the facts render at once and the prose arrives underneath them when it
   * is ready. Nothing waits for Bernie, and a narration that fails or times
   * out costs its paragraphs rather than the whole page.
   */
  const state = useAsync(
    () => getWeekend(season, round, { narrate: false }), [season, round],
  )
  // Keyed by the weekend rather than reset inside the effect. Calling
  // setState in an effect body triggers a cascading render, which is what
  // react-hooks/set-state-in-effect flags — and useAsync already solved this
  // the same way: when the key changes, the stale guard discards whatever the
  // previous request returns.
  const [narrated, setNarrated] = useState({ key: null, data: null })
  const key = `${season}/${round}`

  useEffect(() => {
    let current = true
    getWeekend(season, round, { narrate: true })
      .then((data) => { if (current) setNarrated({ key, data }) })
      .catch(() => { /* the facts are already on the page */ })
    return () => { current = false }
  }, [season, round, key])

  const prose = narrated.key === key ? narrated.data : null
  // Fetched separately so a circuit we have no geometry for costs this panel
  // and not the timeline, which is the part of the page that matters.
  const track = useAsync(() => getCircuit(season, round), [season, round])

  if (state.status === 'loading') return <Loading what="Loading the weekend" />
  if (state.status === 'error')
    return <Unavailable what="This weekend" reason={state.error.message} />

  // Prose is merged in by entry id, so an entry the narrator skipped keeps
  // its facts rather than disappearing when the narrated copy lands.
  const written = new Map(
    (prose?.entries || []).filter(Boolean).map((e) => [e.entry_id, e.narrative]),
  )
  const blog = {
    ...state.data,
    entries: (state.data.entries || []).map((entry) =>
      entry && written.get(entry.entry_id)
        ? { ...entry, narrative: written.get(entry.entry_id) }
        : entry,
    ),
  }
  const narrating = prose === null

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">Round {blog.round} · {blog.season}</p>
        <h1>{blog.race_name || `Round ${blog.round}`}</h1>
        {blog.circuit && <p className="muted">{blog.circuit}</p>}
      </header>

      {track.status === 'ready' && track.data && (
        <Panel
          title={track.data.circuit}
          subtitle={[track.data.country, blog.entries.length > 0
            ? 'the track this weekend was run on'
            : 'the track this weekend will be run on'
          ].filter(Boolean).join(' · ')}
        >
          <VenuePhotos images={track.data.imagery} circuit={track.data.circuit} />
          <div className="weekend-hero">
            <TrackMap map={track.data.map} />
            <CircuitBrief circuit={track.data} />
          </div>
        </Panel>
      )}

      {blog.entries.length === 0 && (
        <Panel title="Nothing to report yet">
          <p className="muted">
            Entries appear as the weekend unfolds — practice pace first, then
            qualifying, the locked forecast, and finally the result.
          </p>
        </Panel>
      )}

      <ol className="timeline">
        {blog.entries.map((entry) => (
          <li key={entry.entry_id} className="timeline-item">
            <Entry entry={entry} />
          </li>
        ))}
      </ol>

      {narrating && blog.entries.length > 0 && (
        <p className="muted small narrating">
          <span className="spark" aria-hidden="true" />
          Bernie is writing the commentary. Everything above is already
          computed from the stored data and will not change.
        </p>
      )}

      {!narrating && !blog.narration_available && blog.entries.length > 0 && (
        <p className="muted small">
          Written commentary is switched off on this deployment. Everything
          above is computed directly from the stored data.
        </p>
      )}
    </div>
  )
}

function Entry({ entry }) {
  // Paddock news is the outlets' reporting, not our computation, so it gets
  // its own rendering: every headline with its outlet and a link out, and none
  // of the facts grid or data table that present our own numbers.
  if (entry.kind === 'paddock_news') {
    return (
      <Panel
        title={entry.headline}
        subtitle={KIND_LABEL[entry.kind]}
        footer={
          entry.sources?.length > 0 && (
            <details className="sources">
              <summary>Where these headlines come from</summary>
              <ul>{entry.sources.map((s) => <li key={s}>{s}</li>)}</ul>
            </details>
          )
        }
      >
        {entry.summary && <p className="lede">{entry.summary}</p>}
        <NewsList items={entry.table || []} showSummary={false} />
      </Panel>
    )
  }

  return (
    <Panel
      title={entry.headline}
      subtitle={KIND_LABEL[entry.kind] ?? entry.kind}
      footer={
        entry.sources?.length > 0 && (
          <details className="sources">
            <summary>Where these numbers come from</summary>
            <ul>{entry.sources.map((s) => <li key={s}>{s}</li>)}</ul>
          </details>
        )
      }
    >
      {entry.summary && <p className="lede">{entry.summary}</p>}

      {entry.facts?.length > 0 && (
        <dl className="facts">
          {entry.facts.map((fact) => (
            // Keyed on label + value: a weekend with four grid penalties has
            // four facts sharing the label, and React reconciles duplicate
            // keys by dropping siblings.
            <div className="fact" key={`${fact.label}-${fact.value}`}>
              <dt>{fact.label}</dt>
              <dd>
                <strong>{fact.value}</strong>
                {fact.detail && <span className="fact-detail">{fact.detail}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {entry.table?.length > 0 && <EntryTable rows={entry.table} />}

      {entry.narrative && (
        <blockquote className="narrative">
          {entry.narrative.split('\n\n').map((para, i) => <p key={i}>{para}</p>)}
        </blockquote>
      )}
    </Panel>
  )
}

/**
 * How each column the server can send is named and rendered.
 *
 * The table used to print the raw field name and the raw value, so a forecast
 * arrived as "p win  0.298". A reader has no way to read that as "a 30% chance
 * of winning", and a probability written as a decimal invites being read as a
 * score out of one. Probabilities are therefore percentages here, and the
 * labels say what the number is about rather than what the field is called.
 *
 * ``points`` and ``p_points`` are different quantities — championship points
 * scored, and the chance of finishing in the points — so they must not share a
 * heading.
 *
 * Anything not listed falls back to the old generic rendering. A column the
 * server adds later should look plain, not vanish.
 */
const COLUMNS = {
  position: { label: 'Pos', numeric: true },
  driver: { label: 'Driver' },
  team: { label: 'Team' },
  starts: { label: 'Starts', numeric: true },
  points: { label: 'Points', numeric: true },
  // Zero is the pole-sitter, not a missing gap. "+0.000" reads as a dead heat
  // with themselves.
  gap_to_pole: {
    label: 'Gap to pole',
    numeric: true,
    render: (v) =>
      typeof v !== 'number' ? (v ?? '—') : v === 0 ? 'pole' : `+${v.toFixed(3)}`,
  },
  // Probability renders a null as "not published" rather than a dash: the
  // backend distinguishes "we make no claim" from "we claim zero", and the
  // pre-qualifying window publishes no winner at all.
  p_win: { label: 'Win', numeric: true, render: (v) => <Probability value={v} /> },
  p_podium: { label: 'Podium', numeric: true, render: (v) => <Probability value={v} /> },
  p_points: { label: 'Points finish', numeric: true, render: (v) => <Probability value={v} /> },
}

function EntryTable({ rows }) {
  const columns = Object.keys(rows[0])
  const generic = (value) =>
    typeof value === 'number'
      ? Number.isInteger(value) ? value : value.toFixed(3)
      : value ?? '—'

  const spec = (column) => COLUMNS[column] ?? {
    label: column.replace(/_/g, ' '),
    numeric: typeof rows[0][column] === 'number',
  }

  return (
    <table className="datatable compact">
      <thead>
        <tr>
          {columns.map((c) => {
            const { label, numeric } = spec(c)
            return <th key={c} className={numeric ? 'num' : ''}>{label}</th>
          })}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {columns.map((c) => {
              const { numeric, render } = spec(c)
              return (
                <td key={c} className={numeric ? 'num' : ''}>
                  {render ? render(row[c]) : generic(row[c])}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
