/**
 * Reliability diagram: what we said (x) against what happened (y), one dot
 * per probability band. On the diagonal is honest; below it is overconfident
 * (we said more than happened); above it is underconfident.
 *
 * Bands with fewer than `minSamples` forecasts are left off, for the reason the
 * table explains: with a handful of samples the observed rate can only be 0% or
 * 100%. Dot area grows with the number of forecasts in the band.
 */
const SIZE = 180
const PAD = 28
const PLOT = SIZE - PAD - 8

function toX(p) { return PAD + p * PLOT }
function toY(p) { return 8 + (1 - p) * PLOT }

export default function ReliabilityChart({ buckets, minSamples, label }) {
  const shown = buckets.filter((b) => b.count >= minSamples)
  if (!shown.length) return null
  const most = Math.max(...shown.map((b) => b.count))

  return (
    <svg
      className="reliability"
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      width={SIZE}
      height={SIZE}
      role="img"
      aria-label={`${label}: predicted against observed rate per band`}
    >
      <rect x={PAD} y={8} width={PLOT} height={PLOT} className="reliability-frame" />
      {[0.25, 0.5, 0.75].map((t) => (
        <g key={t} className="reliability-grid">
          <line x1={toX(t)} y1={8} x2={toX(t)} y2={8 + PLOT} />
          <line x1={PAD} y1={toY(t)} x2={PAD + PLOT} y2={toY(t)} />
        </g>
      ))}
      <line x1={toX(0)} y1={toY(0)} x2={toX(1)} y2={toY(1)} className="reliability-diagonal" />
      {shown.map((b) => {
        const gap = b.predicted_mean - b.observed_rate
        return (
          <circle
            key={b.lower}
            cx={toX(b.predicted_mean)}
            cy={toY(b.observed_rate)}
            r={3 + 5 * Math.sqrt(b.count / most)}
            className={`reliability-dot ${Math.abs(gap) > 0.1 ? 'off' : ''}`}
          >
            <title>
              {`We said ${(b.predicted_mean * 100).toFixed(0)}%, it happened ${(b.observed_rate * 100).toFixed(0)}% (${b.count} forecasts)`}
            </title>
          </circle>
        )
      })}
      <text x={PAD} y={SIZE - 6} className="reliability-axis">0%</text>
      <text x={PAD + PLOT} y={SIZE - 6} textAnchor="end" className="reliability-axis">we said 100%</text>
      <text x={PAD - 4} y={12} textAnchor="end" className="reliability-axis">100%</text>
      <text x={PAD - 4} y={8 + PLOT} textAnchor="end" className="reliability-axis">0%</text>
    </svg>
  )
}
