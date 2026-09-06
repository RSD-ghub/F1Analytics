import { getTrackRecord } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable } from '../components/Panel'

/**
 * The public accuracy record — deliberately unauthenticated.
 *
 * Two presentation rules this page exists to honour:
 *
 * A raw Brier score is not a claim a reader can evaluate. Winning is rare, so
 * predicting "nobody wins" for everyone already scores about 0.045 on a
 * twenty-car grid. Skill against that baseline is the number shown large;
 * the Brier score is shown small, next to it.
 *
 * Pending predictions are shown alongside scored ones. A record that displayed
 * only what had been reconciled could be improved by never reconciling the
 * embarrassing races.
 */
export default function TrackRecord() {
  const state = useAsync(() => getTrackRecord(), [])

  if (state.status === 'loading') return <Loading what="Loading the track record" />
  if (state.status === 'error')
    return <Unavailable what="The track record" reason={state.error.message} />

  const { record, unavailable } = state.data
  if (!record)
    return (
      <Unavailable
        what="The track record"
        reason={
          unavailable?.panels?.length
            ? 'The scoring service could not be reached.'
            : 'No predictions have been scored yet.'
        }
      />
    )

  return (
    <div className="stack">
      <header className="page-head">
        <h1>Track record</h1>
        <p className="muted">
          Every forecast we have ever locked, scored against what happened.
          Nothing is removed, and nothing is edited after the fact.
        </p>
      </header>

      <div className="tiles">
        <Tile label="Forecasts scored" value={record.predictions_scored} />
        <Tile
          label="Awaiting reconciliation"
          value={record.predictions_pending}
          note="Counted so the record cannot be improved by leaving bad calls unscored."
        />
        {record.predictions_refused_late > 0 && (
          <Tile
            label="Refused — locked too late"
            value={record.predictions_refused_late}
            note="Locked at or after the race started, so not scored. A call made after the race is not a forecast."
          />
        )}
        <Tile
          label="Made on incomplete data"
          value={record.predictions_incomplete_data}
          note="Published on schedule with the gap recorded, rather than delayed."
        />
      </div>

      {record.model_versions?.length > 1 && (
        <div className="notice">
          This record spans {record.model_versions.length} model versions
          ({record.model_versions.join(', ')}). They are scored separately —
          averaging them would restate history.
        </div>
      )}

      {record.windows.map((window) => (
        <WindowRecord key={window.window} window={window} />
      ))}

      <Calibration curves={record.calibration} />
    </div>
  )
}

function Tile({ label, value, note }) {
  return (
    <div className="tile">
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  )
}

const MARKET_LABEL = { win: 'Winner', podium: 'Podium', points: 'Points finish' }

function WindowRecord({ window }) {
  const title =
    window.window === 'pre_quali' ? 'Before qualifying' : 'With the grid set'

  return (
    <Panel
      title={title}
      subtitle={`${window.predictions_scored} forecast${window.predictions_scored === 1 ? '' : 's'} scored`}
    >
      <div className="markets">
        {Object.entries(window.skill_by_market).map(([market, skill]) => (
          <div className="market" key={market}>
            <span className="market-name">{MARKET_LABEL[market] ?? market}</span>
            <span className={`skill ${skill >= 0 ? 'good' : 'bad'}`}>
              {skill >= 0 ? '+' : ''}{(skill * 100).toFixed(1)}%
            </span>
            <span className="market-note">
              better than guessing
              {window.brier_by_market?.[market] !== undefined &&
                ` · Brier ${window.brier_by_market[market].toFixed(4)}`}
            </span>
          </div>
        ))}
      </div>
      {!('win' in window.skill_by_market) && (
        <p className="muted small">
          This window publishes no winner probability, so there is nothing to
          score in that market.
        </p>
      )}
    </Panel>
  )
}

/**
 * Below this many forecasts, a band cannot say anything about calibration.
 * With one sample the observed rate is necessarily 0% or 100%, so the gap
 * against a 25% forecast is either -75% or +25% — both arithmetically correct
 * and both evidentially empty. Rendering that as a large red number would tell
 * the reader the model is badly miscalibrated when the data supports no such
 * claim, which is precisely the misreading this page exists to prevent.
 */
const MIN_BAND_SAMPLES = 5

function Calibration({ curves }) {
  if (!curves?.length) return null

  return (
    <Panel
      title="Calibration"
      subtitle="Of everything we called 30% likely, did roughly 30% happen?"
    >
      <p className="muted small">
        Accuracy and calibration are different claims. A model can be accurate
        and overconfident, or perfectly calibrated and useless. This is the
        second one: whether the numbers mean what they say.
      </p>
      {curves.map((curve) => (
        <div className="curve" key={`${curve.market}-${curve.window}`}>
          <h3>
            {MARKET_LABEL[curve.market] ?? curve.market}
            <span className="muted"> · {curve.window === 'pre_quali' ? 'before qualifying' : 'grid set'}</span>
            <span className="ece"> ECE {curve.expected_calibration_error.toFixed(3)}</span>
          </h3>
          <table className="grid compact">
            <thead>
              <tr>
                <th>Band</th><th className="num">Forecasts</th>
                <th className="num">We said</th><th className="num">It happened</th>
                <th className="num">Gap</th>
              </tr>
            </thead>
            <tbody>
              {curve.buckets.map((bucket) => {
                const gap = bucket.predicted_mean - bucket.observed_rate
                const thin = bucket.count < MIN_BAND_SAMPLES
                return (
                  <tr key={bucket.lower} className={thin ? 'thin' : ''}>
                    <td>{(bucket.lower * 100).toFixed(0)}–{(bucket.upper * 100).toFixed(0)}%</td>
                    <td className="num">{bucket.count}</td>
                    <td className="num">{(bucket.predicted_mean * 100).toFixed(1)}%</td>
                    <td className="num">
                      {thin ? <span className="muted">—</span>
                            : `${(bucket.observed_rate * 100).toFixed(1)}%`}
                    </td>
                    <td className={`num ${!thin && Math.abs(gap) > 0.1 ? 'bad' : ''}`}>
                      {thin ? (
                        <span className="muted small" title={`Only ${bucket.count} forecast${bucket.count === 1 ? '' : 's'} in this band`}>
                          too few
                        </span>
                      ) : (
                        `${gap >= 0 ? '+' : ''}${(gap * 100).toFixed(1)}%`
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}
      <p className="muted small">
        A positive gap means we were overconfident in that band. Bands with
        fewer than {MIN_BAND_SAMPLES} forecasts show no observed rate: with a
        handful of samples the outcome can only be 0% or 100%, so any gap would
        describe the sample size rather than the model. Bands with no forecasts
        at all are omitted rather than drawn at zero — an absence of evidence is
        not a miscalibration.
      </p>
    </Panel>
  )
}
