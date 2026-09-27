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

/**
 * Where a layout came from, credited in the legend.
 *
 * OpenStreetMap is ODbL and requires attribution, so this is a licence term
 * rather than a nicety — and it doubles as the honest label for a map that has
 * no telemetry behind it.
 */
function Provenance({ map }) {
  if (map.source_session === 'openstreetmap') {
    return (
      <>
        ©{' '}
        <a href="https://www.openstreetmap.org/copyright"
           target="_blank" rel="noreferrer noopener">
          OpenStreetMap
        </a>{' '}contributors
      </>
    )
  }
  return (
    <>traced from {map.source_season}{' '}
    {map.source_session === 'Q' ? 'qualifying' : 'the race'}</>
  )
}

/** Slow to fast: deep blue, amber, accent red. */
const SCALE = [
  [0.0, [47, 107, 176]],
  [0.5, [199, 145, 20]],
  [1.0, [204, 51, 40]],
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

  const { segments, fastest, slowest, coloured } = useMemo(() => {
    const outline = map?.outline || []
    const speeds = map?.speeds || []
    if (outline.length < 2) {
      return { segments: [], fastest: 0, slowest: 0, coloured: false }
    }

    // A map from OpenStreetMap has a shape and no lap, so no speeds. Drawn as
    // a single accent-coloured ribbon instead of a gradient — the absence of
    // colour is the honest signal that there is no telemetry behind it.
    const measured = speeds.filter((s) => s > 0)
    const coloured = measured.length > 0
    const top = coloured ? Math.max(...measured) : 0
    const bottom = coloured ? Math.min(...measured) : 0
    const range = top - bottom || 1

    // One path per pair of points. More elements than a single path, but a
    // single path can only carry one colour, and the colour is the content.
    const built = []
    for (let i = 1; i < outline.length; i += 1) {
      const speed = speeds[i] || bottom
      built.push({
        d: `M${outline[i - 1][0]},${outline[i - 1][1]}L${outline[i][0]},${outline[i][1]}`,
        // Not the accent. A speed-coloured lap is data and earns the ramp;
        // a bare outline is a shape, and painting it in the brand colour made
        // the largest object on the page the one carrying the least
        // information.
        colour: coloured ? colourFor((speed - bottom) / range) : 'var(--data)',
        speed: coloured ? speed : null,
        index: i,
      })
    }
    return { segments: built, fastest: top, slowest: bottom, coloured }
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
        {/* A casing under the coloured line so the track reads as one ribbon
            rather than a chain of segments where colours meet. It takes the
            page's own ground, so it works on paper as well as it did on the
            dark it was drawn for. */}
        <g stroke="var(--bg)" strokeWidth="26" strokeLinecap="round" fill="none">
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
              fill="var(--dim)"
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
          stroke="var(--text)"
          strokeWidth="4"
        />
      </svg>

      <div className="trackmap-key">
        {coloured ? (
          <>
            <span className="small muted">{slowest} kph</span>
            <span className="trackmap-ramp" aria-hidden="true" />
            <span className="small muted">{fastest} kph</span>
          </>
        ) : (
          <span className="small muted">
            Shape only — no lap telemetry exists for this circuit.
          </span>
        )}
        <span className="small trackmap-readout">
          {coloured && hover
            ? `${hover.speed} kph here`
            : <Provenance map={map} />}
        </span>
      </div>
    </div>
  )
}
