import { getChampionship } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Panel, Loading, Unavailable, Bar } from '../components/Panel'
import { HundredRaces } from '../components/Odds'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'

/**
 * Title probabilities from simulating every remaining race.
 *
 * The framing matters and is stated on the page: these assume the season
 * continues as it looks today. Form is frozen at current values and races are
 * simulated independently, so neither a mid-season upgrade nor a correlated run
 * of failures is anticipated. Presenting the number without that caveat would
 * imply more foresight than the method has.
 */
export default function Championship() {
  const season = new Date().getUTCFullYear()
  const state = useAsync(() => getChampionship(season), [season])

  if (state.status === 'loading') return <Loading what="Simulating the season" />
  if (state.status === 'error')
    return <Unavailable what="The championship forecast" reason={state.error.message} />

  const forecast = state.data

  return (
    <div className="stack">
      <header className="page-head">
        <h1>Championship</h1>
        <p className="muted">
          {forecast.runs.toLocaleString()} simulations of the{' '}
          {forecast.remaining_rounds.length} remaining race
          {forecast.remaining_rounds.length === 1 ? '' : 's'}, after round{' '}
          {forecast.as_of_round}.
        </p>
      </header>

      <Lead forecast={forecast} />

      <p className="notice">
        Assumes the season continues as it looks today. Form is held at current
        values and races are simulated independently, so a mid-season upgrade or
        a run of reliability failures is not anticipated.
      </p>

      <Standings title="Drivers" rows={forecast.drivers} />
      <Standings title="Constructors" rows={forecast.constructors} nameKey="team" />
    </div>
  )
}

/**
 * What twenty thousand simulated seasons actually said.
 *
 * The page buried it. Antonelli takes the title in 99.9% of them with eight
 * races still to run, and that figure sat in a table cell at thirteen pixels
 * between two columns nobody came for. A championship that is effectively
 * decided is the story, and a page about it should say so before it starts
 * tabulating.
 */
function Lead({ forecast }) {
  const leader = [...(forecast.drivers || [])]
    .sort((a, b) => b.p_champion - a.p_champion)[0]
  if (!leader) return null

  const pct = leader.p_champion * 100
  const remaining = forecast.remaining_rounds.length
  const settled = pct >= 95

  return (
    <Panel
      title="The title, as the simulations see it"
      subtitle={`${forecast.runs.toLocaleString()} seasons played out from where this one stands`}
    >
      <div className="forecast-head">
        <div>
          <p className="lede">
            <strong>{leader.driver}</strong> takes the championship in{' '}
            {pct >= 99.95 ? 'almost every' : `${pct.toFixed(1)}% of`} simulated
            season{pct >= 99.95 ? '' : 's'}, with {remaining} race
            {remaining === 1 ? '' : 's'} still to run.
          </p>
          <p className="small muted">
            {settled
              ? 'That is not the same as mathematically decided — the points are still there to be taken. It means that across twenty thousand plausible remainders of this season, almost none of them ended another way.'
              : 'Still open. The square below is one simulated season each; the filled ones are the seasons this driver wins.'}
          </p>
        </div>
        <HundredRaces
          probability={leader.p_champion}
          unit="simulated seasons"
          label={`Simulated seasons ${leader.driver} wins`}
        />
      </div>
    </Panel>
  )
}

/** Below this, a title chance is reported as "<0.1%" rather than a figure. */
const LONG_SHOT = 0.001

function Standings({ title, rows, nameKey = 'driver' }) {
  // Everyone is listed.
  //
  // Rows under 0.1% used to be dropped with a footnote. That was tolerable
  // while a dozen drivers were still in it, and became absurd once one driver
  // reached ~100%: the standings rendered a single row, and a reader looking
  // for second place found a sentence explaining that twenty-one entries
  // existed somewhere else. Championship standings are the point of the page.
  //
  // So the long shots stay, dimmed, with their chance shown as a bound rather
  // than a rounded zero — "<0.1%" is a claim about precision, "0.0%" reads as
  // mathematically out, and they are not the same thing.
  const outOfIt = rows.filter((r) => r.p_champion <= LONG_SHOT).length

  return (
    <Panel
      title={title}
      footer={
        outOfIt > 0 && (
          <p className="muted small">
            {outOfIt} {outOfIt === 1 ? 'entry is' : 'entries are'} shown at
            under 0.1%. That is not the same as mathematically eliminated —
            the points are still there to be taken.
          </p>
        )
      }
    >
      <Table className="datatable">
        <TableHeader>
          <TableRow>
            <TableHead>{nameKey === 'team' ? 'Team' : 'Driver'}</TableHead>
            <TableHead className="num">Points</TableHead>
            <TableHead className="num">Title chance</TableHead>
            {/* The bar shows the title chance, so it sits beside it. It used
                to be the last column, two places away from the number it
                draws and flush against projected points, which is what a
                reader took it to mean. */}
            <TableHead className="viz" />
            <TableHead className="num">Projected</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow
              key={row[nameKey] ?? row.driver}
              className={row.p_champion <= LONG_SHOT ? 'is-long-shot' : ''}
            >
              <TableCell>{row[nameKey] ?? row.driver}</TableCell>
              <TableCell className="num">{row.current_points.toFixed(0)}</TableCell>
              <TableCell className="num strong">
                {row.p_champion <= LONG_SHOT
                  ? <span className="muted">&lt;0.1%</span>
                  : `${(row.p_champion * 100).toFixed(1)}%`}
              </TableCell>
              <TableCell className="viz"><Bar value={row.p_champion} /></TableCell>
              <TableCell className="num muted">{row.expected_final_points.toFixed(0)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  )
}
