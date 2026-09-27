import { useEffect, useMemo, useState } from 'react'
import { getBackdrop } from '../api/f1Api'

/**
 * A slow carousel behind the page.
 *
 * Two sources, in order of preference.
 *
 * **Photographs**, if any are present. Drop files into `public/backgrounds/`
 * and list them in `public/backgrounds/manifest.json` as
 * `[{ "src": "car.jpg", "credit": "…", "href": "…" }]`. The credit is
 * rendered — which is the point of requiring the manifest rather than
 * globbing a directory. Race photography is licensed aggressively, so
 * anything put here needs to be yours or openly licensed, and the page has to
 * say whose it is.
 *
 * **Circuit outlines** otherwise, which is what ships. They are drawn from
 * the same telemetry and OpenStreetMap geometry as the track maps, so they
 * cost nothing, belong to the project, and are recognisably about this sport
 * rather than being stock texture.
 *
 * Kept deliberately faint. This sits under tables of numbers that people are
 * meant to read and check, and a backdrop that competes with them would undo
 * the point of the product.
 */

const HOLD_MS = 11000

export default function Backdrop() {
  const [slides, setSlides] = useState([])
  const [index, setIndex] = useState(0)

  useEffect(() => {
    let live = true
    async function load() {
      // Photographs first, if someone has supplied any.
      try {
        const res = await fetch('/backgrounds/manifest.json')
        if (res.ok) {
          const listed = await res.json()
          if (live && Array.isArray(listed) && listed.length) {
            setSlides(listed.map((item) => ({ kind: 'photo', ...item })))
            return
          }
        }
      } catch {
        /* no manifest is the normal case */
      }
      try {
        const outlines = await getBackdrop(10)
        if (live) {
          setSlides(outlines.map((o) => ({ kind: 'circuit', ...o })))
        }
      } catch {
        /* decoration: a page with no backdrop is a page */
      }
    }
    load()
    return () => { live = false }
  }, [])

  const still = useMemo(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    [],
  )

  useEffect(() => {
    if (slides.length < 2 || still) return undefined
    const timer = setInterval(
      () => setIndex((i) => (i + 1) % slides.length), HOLD_MS,
    )
    return () => clearInterval(timer)
  }, [slides.length, still])

  if (!slides.length) return null
  const current = slides[index]

  return (
    <div className="backdrop" aria-hidden="true">
      {slides.map((slide, i) => (
        <div
          key={slide.src || slide.slug || i}
          className={`backdrop-slide${i === index ? ' is-on' : ''}`}
        >
          {slide.kind === 'photo' ? (
            <div
              className="backdrop-photo"
              style={{ backgroundImage: `url(/backgrounds/${slide.src})` }}
            />
          ) : (
            <CircuitGlyph outline={slide.outline} />
          )}
        </div>
      ))}

      {current?.credit && (
        <p className="backdrop-credit">
          {current.href
            ? <a href={current.href} target="_blank" rel="noreferrer noopener">{current.credit}</a>
            : current.credit}
        </p>
      )}
    </div>
  )
}

/** One circuit, drawn large and faint. */
function CircuitGlyph({ outline }) {
  if (!outline || outline.length < 2) return null
  const d = outline.map((p, i) => `${i ? 'L' : 'M'}${p[0]},${p[1]}`).join('')
  return (
    <svg viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid meet">
      <path d={d} fill="none" strokeWidth="9" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
