import { Panel, Probability, Caveats } from './Panel'
import { OddsBar, InPlainWords } from './Odds'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'

/**
 * The race run ten thousand times on the confirmed grid.
 *
 * A locked forecast gives each driver three numbers. Every simulated run is a
 * whole race, so the runs together say much more — how often a car retires,
 * and whether its result is a narrow peak or spread across half the field.
 * The strip in each row is that spread: one cell per finishing place, darker
 * where more of the simulated races ended.
 *
 * The network is shown next to the linear model it is measured against, and
 * the panel says which of the two places the permanent final-grid forecast.
 * A reader comparing this panel with the locked forecast should never have to
 * guess why they differ.
 */
export default function RaceSimulation({ simulation }) {
  if (!simulation) return null
  const { status } = simulation

  if (status === 'awaiting_confirmed_grid') {
    return (
      <Panel title="Race simulation">
        <p className="muted">
          The race is simulated ten thousand times once the
          FIA confirms the starting grid, about an hour before the start.
          Qualifying order is not the grid: a driver with a penalty would start
          from the wrong place in every simulated race.
        </p>
      </Panel>
    )
  }
  if (status !== 'ready') return null

  const drivers = simulation.drivers || []
  const leader = drivers[0]
  const runs = simulation.runs.toLocaleString()
  const metrics = simulation.model_metrics || {}
  // No baseline means the simulation ran on the Plackett-Luce model itself
  // (no network trained yet): there is no second opinion to show.
  const compared = Boolean(simulation.baseline_model_version)
  // Lap by lap (strategy, safety cars, passing) or sampled straight from the
  // strengths — the second when the lap simulator has not earned its place.
  const lapByLap = (simulation.engine || '').startsWith('lap-sim')

  return (
    <Panel
      title={`${runs} simulated races on the confirmed grid`}
      subtitle={compared
        ? `Neural model ${simulation.model_version} · compared with ${simulation.baseline_model_version}`
        : `Model ${simulation.model_version}`}
      footer={<Caveats quality={simulation.data_quality} />}
    >
      <div className="forecast-head">
        <div>
          <InPlainWords leader={leader?.driver} win={leader?.p_win} podium={leader?.p_podium} />
          {compared && <p className="small muted">
            {simulation.model_promoted
              ? 'This network beat the linear model on two held-out seasons, so it also places the locked final-grid forecast.'
              : 'Shown alongside the locked forecast, not instead of it: the network has not yet beaten the linear model on held-out seasons, so the permanent record stays with the linear model.'}
            {metrics.verdict && ` (${metrics.verdict}.)`}
          </p>}
          <p className="small muted">
            {lapByLap
              ? 'Each race is run lap by lap: pit strategies used at this circuit, its safety-car and VSC rates, tyre wear, and passing that takes a real pace advantage.'
              : 'Each race is a finishing order sampled from the model’s strengths, with retirements.'}
          </p>
        </div>
      </div>

      <Table className="datatable">
        <TableHeader>
          <TableRow>
            <TableHead>Driver</TableHead>
            <TableHead className="num">Grid</TableHead>
            <TableHead className="viz">Where the {runs} races ended</TableHead>
            <TableHead className="num">Win</TableHead>
            <TableHead className="num">Podium</TableHead>
            <TableHead className="num">Points</TableHead>
            <TableHead className="num">DNF</TableHead>
            {compared && <TableHead className="num">Linear model win</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {drivers.map((row) => (
            <TableRow key={row.driver}>
              <TableCell>
                <span className="driver">{row.driver}</span>
                <span className="team">{row.team}</span>
              </TableCell>
              <TableCell className="num">{row.grid_position || '–'}</TableCell>
              <TableCell className="viz">
                <PositionStrip probabilities={row.position_probabilities} dnf={row.p_dnf} />
                <OddsBar win={row.p_win} podium={row.p_podium} points={row.p_points} />
              </TableCell>
              <TableCell className="num"><Probability value={row.p_win} /></TableCell>
              <TableCell className="num"><Probability value={row.p_podium} /></TableCell>
              <TableCell className="num"><Probability value={row.p_points} /></TableCell>
              <TableCell className="num"><Probability value={row.p_dnf} /></TableCell>
              {compared && (
                <TableCell className="num muted">
                  <Probability value={row.baseline?.p_win} absentLabel="–" />
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  )
}

/**
 * One driver's finishing places across every simulated race.
 *
 * Shaded relative to the driver's own most likely outcome, so a midfielder's
 * spread is as legible as a favourite's peak; the exact share is in each
 * cell's title and in the summary label.
 */
function PositionStrip({ probabilities = [], dnf = 0 }) {
  if (!probabilities.length) return null
  const peak = Math.max(dnf, ...probabilities, 1e-9)
  const best = probabilities.indexOf(Math.max(...probabilities))
  const label = `Most often P${best + 1} (${Math.round(probabilities[best] * 100)}%), retires in ${Math.round(dnf * 100)}%`
  return (
    <div className="sim-strip" role="img" aria-label={label}>
      {probabilities.map((p, i) => (
        <span
          key={i}
          className="sim-cell"
          title={`P${i + 1}: ${(p * 100).toFixed(1)}%`}
          style={{ '--share': p / peak }}
        />
      ))}
      <span
        className="sim-cell sim-dnf"
        title={`DNF: ${(dnf * 100).toFixed(1)}%`}
        style={{ '--share': dnf / peak }}
      />
    </div>
  )
}
