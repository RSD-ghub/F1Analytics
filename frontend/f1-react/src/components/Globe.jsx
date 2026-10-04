import { useEffect, useMemo, useRef, useState } from 'react'
import { geoOrthographic, geoPath } from 'd3-geo'
import { feature } from 'topojson-client'

/**
 * The season, on a globe you can turn.
 *
 * Drawn with d3-geo's orthographic projection into an SVG rather than with
 * WebGL. A three.js globe is the obvious choice and costs six hundred
 * kilobytes to render twenty-three dots on a sphere; this is about forty, has
 * no GPU dependency, and every mark on it is a DOM node that can be focused,
 * tabbed to and read by a screen reader — which matters, because the dots are
 * the navigation.
 *
 * Rotation is a pointer drag mapped to longitude and latitude. Latitude is
 * clamped: past the poles the projection keeps working and the viewer loses
 * all sense of which way is up.
 */

const LAND_URL = 'https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json'
const TILT_LIMIT = 68

export default function Globe({ stops, size = 440, onSelect, selected }) {
  const [land, setLand] = useState(null)
  const [rotation, setRotation] = useState([-10, -18])
  const dragging = useRef(null)
  const svgRef = useRef(null)

  useEffect(() => {
    let live = true
    fetch(LAND_URL)
      .then((r) => r.json())
      .then((topology) => {
        if (live) setLand(feature(topology, topology.objects.land))
      })
      .catch(() => { /* the globe degrades to its graticule and the markers */ })
    return () => { live = false }
  }, [])

  const projection = useMemo(
    () => geoOrthographic()
      .translate([size / 2, size / 2])
      .scale(size / 2 - 2)
      .rotate(rotation),
    [rotation, size],
  )
  const path = useMemo(() => geoPath(projection), [projection])

  // A point on the far side of the sphere still projects to a coordinate, so
  // without a visibility test every marker renders and half of them sit behind
  // the planet. Anything more than ninety degrees from the point facing the
  // viewer is on the back.
  const visible = useMemo(() => {
    const centre = [-rotation[0], -rotation[1]]
    return (stops || [])
      .map((stop) => {
        const point = projection([stop.lon, stop.lat])
        return {
          stop,
          x: point?.[0],
          y: point?.[1],
          front: distanceDegrees(centre, [stop.lon, stop.lat]) < 89,
        }
      })
      .filter((marker) => marker.front && Number.isFinite(marker.x))
  }, [stops, projection, rotation])

  /*
   * The pointer is captured only once a drag has actually begun.
   *
   * Capturing on pointerdown is the obvious way to keep a drag alive outside
   * the element, and it swallowed every click on a marker: with the pointer
   * captured by the globe, the press and release no longer resolved to a
   * click on the dot underneath. The markers are the navigation, so that was
   * the whole feature.
   *
   * Four pixels of slop before it counts as a drag, which is enough to absorb
   * the shake in a click without making the globe feel stuck.
   */
  function onPointerDown(event) {
    dragging.current = {
      x: event.clientX, y: event.clientY, start: rotation,
      moved: false, id: event.pointerId,
    }
  }

  function onPointerMove(event) {
    const drag = dragging.current
    if (!drag) return
    const dx = event.clientX - drag.x
    const dy = event.clientY - drag.y
    if (!drag.moved) {
      if (Math.hypot(dx, dy) < 4) return
      drag.moved = true
      event.currentTarget.setPointerCapture(drag.id)
    }
    // Degrees per pixel scales with the globe, so the same gesture turns it
    // the same amount at any size.
    const perPixel = 180 / (size / 2) / 2.2
    const lon = drag.start[0] + dx * perPixel
    const lat = drag.start[1] - dy * perPixel
    setRotation([lon, Math.max(-TILT_LIMIT, Math.min(TILT_LIMIT, lat))])
  }

  function onPointerUp(event) {
    const drag = dragging.current
    if (drag?.moved) event.currentTarget.releasePointerCapture?.(drag.id)
    dragging.current = null
  }

  /** True when the pointer has moved far enough that this is a drag, not a pick. */
  function wasDragged() {
    return Boolean(dragging.current?.moved)
  }

  /** Turn the globe so a race faces the viewer. Used by the list and by keys. */
  function faceTowards(stop) {
    setRotation([-stop.lon, Math.max(-TILT_LIMIT, Math.min(TILT_LIMIT, -stop.lat))])
  }

  return (
    <svg
      ref={svgRef}
      className="globe"
      viewBox={`0 0 ${size} ${size}`}
      width={size}
      height={size}
      role="group"
      aria-label="The season's races on a globe. Drag to rotate."
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <defs>
        <radialGradient id="globe-sea" cx="38%" cy="32%">
          <stop offset="0%" stopColor="#2b4a74" />
          <stop offset="100%" stopColor="#101d2e" />
        </radialGradient>
      </defs>

      <circle cx={size / 2} cy={size / 2} r={size / 2 - 2} fill="url(#globe-sea)" />
      {land && <path className="globe-land" d={path(land)} />}
      <circle
        cx={size / 2} cy={size / 2} r={size / 2 - 2}
        fill="none" className="globe-rim"
      />

      {visible.map(({ stop, x, y }) => {
        const key = `${stop.season}-${stop.round}`
        const isSelected = selected
          && selected.season === stop.season && selected.round === stop.round
        return (
          <g
            key={key}
            className={`stop stop-${stop.status}${isSelected ? ' is-selected' : ''}`}
            transform={`translate(${x}, ${y})`}
            tabIndex={0}
            role="button"
            aria-label={`${stop.race_name}, round ${stop.round}`}
            onClick={(e) => {
              e.stopPropagation()
              if (!wasDragged()) onSelect?.(stop)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                faceTowards(stop)
                onSelect?.(stop)
              }
            }}
          >
            {stop.status === 'next' && <circle className="stop-pulse" r="11" />}
            <circle className="stop-dot" r={isSelected ? 6.5 : 4.5} />
          </g>
        )
      })}
    </svg>
  )
}

/** Great-circle separation in degrees, for deciding what faces the viewer. */
function distanceDegrees([lon1, lat1], [lon2, lat2]) {
  const toRad = Math.PI / 180
  const dLon = (lon2 - lon1) * toRad
  const a = Math.sin(lat1 * toRad) * Math.sin(lat2 * toRad)
  const b = Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.cos(dLon)
  return Math.acos(Math.max(-1, Math.min(1, a + b))) / toRad
}
