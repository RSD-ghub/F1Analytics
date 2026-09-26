import { useMemo, useState } from 'react'

/**
 * A circuit drawn from a real lap, coloured by how fast the car was going.
 *
 * The geometry is the racing line a car actually took — position samples from
 * the fastest qualifying lap on record — so the shape, the corner positions
 * and the speeds are the same measurement rather than three sources that can
 * disagree. Nothing here is drawn by hand.
 *
 * The colour is the point of the visual. A lap time tells you a circuit is
 * quick; a line that runs red down one side and blue through a sequence tells
 * you *where*, and that is the thing a viewer wants before a race.
 */

/** Slow to fast: deep blue, amber, accent red. */
const SCALE = [
  [0.0, [59, 110, 165]],
  [0.5, [214, 178, 74]],
  [1.0, [232, 68, 58]],
]

function colourFor(fraction) {
  const t = Math.min(1, Math.max(0, fraction))
  for (let i = 1; i < SCALE.length; i += 1) {
    const [hi, hiRgb] = SCALE[i]
    const [lo, loRgb] = SCALE[i - 1]
    if (t <= hi) {
      const k = (t - lo) / (hi - lo || 1)
      const mix = loRgb.map((c, j) => Math.round(c + (hiRgb[j] - c) * k))
      return `rgb(${mix.join(',')})`
    }
  }
  return `rgb(${SCALE[SCALE.length - 1][1].join(',')})`
}

export default function TrackMap({ map, height = 380 }) {
  const [hover, setHover] = useState(null)

  const { segments, fastest, slowest } = useMemo(() => {
    const outline = map?.outline || []
    const speeds = map?.speeds || []
    if (outline.length < 2) return { segments: [], fastest: 0, slowest: 0 }

    const measured = speeds.filter((s) => s > 0)
    const top = Math.max(...measured)
    const bottom = Math.min(...measured)
    const range = top - bottom || 1

    // One path per pair of points. More elements than a single path, but a
    // single path can only carry one colour, and the colour is the content.
    const built = []
    for (let i = 1; i < outline.length; i += 1) {
      const speed = speeds[i] || bottom
      built.push({
        d: `M${outline[i - 1][0]},${outline[i - 1][1]}L${outline[i][0]},${outline[i][1]}`,
        colour: colourFor((speed - bottom) / range),
        speed,
        index: i,
      })
    }
    return { segments: built, fastest: top, slowest: bottom }
  }, [map])

  const centre = useMemo(() => {
    const outline = map?.outline || []
    if (!outline.length) return { x: 500, y: 500 }
    const sum = outline.reduce((a, p) => [a[0] + p[0], a[1] + p[1]], [0, 0])
    return { x: sum[0] / outline.length, y: sum[1] / outline.length }
  }, [map])

  /*
   * A viewBox cropped to the circuit, not to the square it was normalised in.
   *
   * Tracks are rarely square and are stored in their broadcast orientation,
   * which leaves a diagonal layout like Baku sitting in a box mostly made of
   * empty corners. Fixing the box at 0 0 1000 1000 rendered the track at
   * roughly half the size the panel had room for. Cropping to the drawn extent
   * and letting height follow the aspect ratio gives the space to the track.
   */
  const viewBox = useMemo(() => {
    const outline = map?.outline || []
    if (outline.length < 2) return { box: '0 0 1000 1000', ratio: 1 }
    const pad = 70   // room for the corner numbers, which sit outside the line
    const xs = outline.map((p) => p[0])
    const ys = outline.map((p) => p[1])
    const minX = Math.min(...xs) - pad
    const minY = Math.min(...ys) - pad
    const w = Math.max(...xs) - Math.min(...xs) + pad * 2
    const h = Math.max(...ys) - Math.min(...ys) + pad * 2
    return { box: `${minX} ${minY} ${w} ${h}`, ratio: h / w }
  }, [map])

  if (!map || segments.length === 0) {
    return (
      <p className="muted small">
        No layout is stored for this circuit — the position data it is traced
        from was not available for any visit we hold.
      </p>
    )
  }

  const corners = map.corners || []

  return (
    <div className="trackmap">
      <svg
        viewBox={viewBox.box}
        style={{ width: '100%', maxHeight: height, aspectRatio: 1 / viewBox.ratio }}
        role="img"
        aria-label={`Track layout for ${map.circuit}, coloured by speed`}
        onMouseLeave={() => setHover(null)}
      >
        {/* A dark casing under the coloured line so the track reads as one
            ribbon rather than a chain of segments where colours meet. */}
        <g stroke="#0a0c10" strokeWidth="26" strokeLinecap="round" fill="none">
          {segments.map((s) => <path key={`c${s.index}`} d={s.d} />)}
        </g>
        <g strokeWidth="17" strokeLinecap="round" fill="none">
          {segments.map((s) => (
            <path
              key={s.index}
              d={s.d}
              stroke={s.colour}
              onMouseEnter={() => setHover(s)}
            />
          ))}
        </g>

        {/* Numbers sit outside the line, not on it.
            They were drawn as filled circles over the track, and twenty of
            them on a seventeen-wide ribbon punched twenty holes in it — the
            layout rendered as a dashed line and read as broken geometry
            rather than as labels. Pushing each one out along the ray from the
            circuit's centre keeps it beside its own corner and off the road. */}
        {corners.map((corner) => {
          const dx = corner.x - centre.x
          const dy = corner.y - centre.y
          const away = Math.hypot(dx, dy) || 1
          return (
            <text
              key={`${corner.number}${corner.letter}`}
              x={corner.x + (dx / away) * 40}
              y={corner.y + (dy / away) * 40 + 9}
              textAnchor="middle"
              fontSize="26"
              fill="#6f7688"
            >
              {corner.number}{corner.letter}
            </text>
          )
        })}

        {/* Start/finish, where the lap's own samples begin. */}
        <circle
          cx={map.outline[0][0]}
          cy={map.outline[0][1]}
          r="15"
          fill="none"
          stroke="#e8eaf0"
          strokeWidth="4"
        />
      </svg>

      <div className="trackmap-key">
        <span className="small muted">{slowest} kph</span>
        <span className="trackmap-ramp" aria-hidden="true" />
        <span className="small muted">{fastest} kph</span>
        <span className="small trackmap-readout">
          {hover ? `${hover.speed} kph here` : `traced from ${map.source_season} ${map.source_session === 'Q' ? 'qualifying' : 'the race'}`}
        </span>
      </div>
    </div>
  )
}
