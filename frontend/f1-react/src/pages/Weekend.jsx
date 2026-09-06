import { useParams } from 'react-router-dom'
import { getWeekend } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable } from '../components/Panel'

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
  practice_report: 'Practice',
  qualifying_report: 'Qualifying',
  forecast: 'Our forecast',
  result: 'Result',
}

export default function Weekend() {
  const { season, round } = useParams()
  const state = useAsync(() => getWeekend(season, round), [season, round])

  if (state.status === 'loading') return <Loading what="Loading the weekend" />
  if (state.status === 'error')
    return <Unavailable what="This weekend" reason={state.error.message} />

  const blog = state.data

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">Round {blog.round} · {blog.season}</p>
        <h1>{blog.race_name || `Round ${blog.round}`}</h1>
        {blog.circuit && <p className="muted">{blog.circuit}</p>}
      </header>

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

      {!blog.narration_available && blog.entries.length > 0 && (
        <p className="muted small">
          Written commentary is switched off on this deployment. Everything
          above is computed directly from the stored data.
        </p>
      )}
    </div>
  )
}

function Entry({ entry }) {
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
            <div className="fact" key={fact.label}>
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

function EntryTable({ rows }) {
  const columns = Object.keys(rows[0])
  const format = (value) =>
    typeof value === 'number'
      ? Number.isInteger(value) ? value : value.toFixed(3)
      : value ?? '—'

  return (
    <table className="grid compact">
      <thead>
        <tr>{columns.map((c) => <th key={c} className={typeof rows[0][c] === 'number' ? 'num' : ''}>{c.replace(/_/g, ' ')}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {columns.map((c) => (
              <td key={c} className={typeof row[c] === 'number' ? 'num' : ''}>{format(row[c])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
