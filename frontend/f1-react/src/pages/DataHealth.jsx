import { getDataHealth } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable } from '../components/Panel'

/**
 * Is the data behind the forecasts whole and fresh? An operator's page, kept
 * public like the track record: a forecast built on a gap should be visible
 * as one.
 */
export default function DataHealth() {
  const state = useAsync(() => getDataHealth(), [])
  if (state.status === 'loading') return <Loading what="Checking the data" />
  if (state.status === 'error') return <Unavailable what="Data health" reason={state.error.message} />

  const { services, completeness, gaps, gaps_total: gapsTotal, datasets, latest_result: latest,
          next_race: next, unavailable, checked_at: checkedAt } = state.data

  return (
    <div className="stack">
      <header className="page-head">
        <h1>Data health</h1>
        <p className="muted">
          Checked {new Date(checkedAt).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}.
        </p>
      </header>

      {services && (
        <div className="tiles">
          {services.services.map((s) => (
            <div className="tile" key={s.service}>
              <span className={`tile-value ${s.reachable ? 'good' : 'bad'}`}>{s.reachable ? 'Up' : 'Down'}</span>
              <span className="tile-label">{s.service}</span>
            </div>
          ))}
        </div>
      )}

      <Panel title="Completeness" subtitle="Sessions expected against sessions held">
        <table className="datatable compact">
          <thead>
            <tr><th>Depth</th><th>Seasons</th><th className="num">Expected</th><th className="num">Complete</th>
              <th className="num">Partial</th><th className="num">Failed</th><th className="num">Pending</th></tr>
          </thead>
          <tbody>
            {[['Results and qualifying (training)', completeness.training], ['Laps and stops (2018+)', completeness.lap_data]].map(([label, c]) => (
              <tr key={label}>
                <td>{label}</td>
                {c ? (
                  <>
                    <td>{c.from_season}–{c.to_season}</td>
                    <td className="num">{c.expected}</td>
                    <td className={`num ${c.complete === c.expected ? 'good' : ''}`}>{c.complete}</td>
                    <td className="num">{c.partial}</td>
                    <td className={`num ${c.failed ? 'bad' : ''}`}>{c.failed}</td>
                    <td className="num">{c.pending}</td>
                  </>
                ) : <td colSpan={6} className="muted">Unavailable</td>}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">
          Latest result held: {latest ? `${latest.season} round ${latest.round}, ${latest.race_name}` : 'none this season'}.
          {next && ` Next race: ${next.race_name}.`}
        </p>
      </Panel>

      <Panel title="Open gaps" subtitle={gapsTotal > gaps.length ? `First ${gaps.length} of ${gapsTotal}` : `${gapsTotal} open`}>
        {gaps.length === 0 ? <p className="muted">No open gaps.</p> : (
          <table className="datatable compact">
            <thead><tr><th>Session</th><th>State</th><th>Depth</th><th className="num">Attempts</th><th>Reason</th></tr></thead>
            <tbody>
              {gaps.map((g) => (
                <tr key={`${g.session}-${g.depth}`}>
                  <td className="num">{g.session}</td><td>{g.state}</td><td>{g.depth}</td>
                  <td className="num">{g.attempts}</td><td className="muted small">{g.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {datasets && (
        <Panel title="Rows held">
          <div className="tiles">
            {Object.entries(datasets).map(([name, count]) => (
              <div className="tile" key={name}>
                <span className="tile-value">{count.toLocaleString()}</span>
                <span className="tile-label">{name.replaceAll('_', ' ')}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {unavailable.length > 0 && (
        <Unavailable what={unavailable.join(', ')} reason="These checks could not reach their service." />
      )}
    </div>
  )
}
