import { useEffect, useMemo, useState } from 'react'
import { getBackdrop } from '../api/f1Api'

/**
 * A slow carousel behind the page, showing where they are racing next.
 *
 * It is keyed off the upcoming weekend rather than chosen at random, so the
 * backdrop is about something — and it rolls over on its own once that race
 * is done and the next one becomes upcoming. Nothing schedules that; it
 * follows from asking which race is next.
 *
 * The photographs come from Wikimedia Commons and are filtered server-side to
 * licences that permit reuse. Each one carries the attribution its licence
 * requires and that credit is rendered, which is a condition of using them
 * rather than a courtesy.
 *
 * Where a venue has no freely licensed photograph — several circuits have
 * none — it falls back to circuit outlines drawn from our own geometry.
 *
 * Kept faint throughout. This sits under tables of numbers that people are
 * meant to read and check.
 */

const HOLD_MS = 11000

export default function Backdrop() {
  const [backdrop, setBackdrop] = useState(null)
  const [index, setIndex] = useState(0)

  useEffect(() => {
    let live = true
    getBackdrop()
      .then((data) => { if (live) { setBackdrop(data); setIndex(0) } })
      .catch(() => { /* decoration: a page with no backdrop is a page */ })
    return () => { live = false }
  }, [])

  const still = useMemo(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    [],
  )

  const slides = backdrop?.kind === 'venue'
    ? (backdrop.images || [])
    : (backdrop?.outlines || [])

  useEffect(() => {
    if (slides.length < 2 || still) return undefined
    const timer = setInterval(
      () => setIndex((i) => (i + 1) % slides.length), HOLD_MS,
    )
    return () => clearInterval(timer)
  }, [slides.length, still])

  if (!slides.length) return null
  const venue = backdrop.kind === 'venue'
  const current = slides[index]

  return (
    <div className="backdrop" aria-hidden="true">
      {slides.map((slide, i) => (
        <div
          key={slide.url || slide.slug || i}
          className={`backdrop-slide${i === index ? ' is-on' : ''}`}
        >
          {venue ? (
            <div
              className="backdrop-photo"
              style={{ backgroundImage: `url("${slide.url}")` }}
            />
          ) : (
            <CircuitGlyph outline={slide.outline} />
          )}
        </div>
      ))}

      {/* Rendered, not optional. Every one of these licences requires
          attribution, and the credit is the price of the photograph. */}
      {venue && current && (
        <p className="backdrop-credit">
          {backdrop.circuit}
          {' — '}
          {current.source
            ? <a href={current.source} target="_blank" rel="noreferrer noopener">{current.credit}</a>
            : current.credit}
          {current.licence_url
            ? <> · <a href={current.licence_url} target="_blank" rel="noreferrer noopener">{current.licence}</a></>
            : <> · {current.licence}</>}
          {' · Wikimedia Commons'}
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
