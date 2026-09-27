import { useEffect, useState } from 'react'

/**
 * The five lights, behind the page.
 *
 * Drawn rather than photographed, and deliberately. A photograph used as a
 * full-page background is cropped to whatever the viewport happens to be and
 * goes soft when it is stretched; this stays crisp at any size, needs no
 * attribution and no external request, and cannot break when somebody
 * reorganises a media library. It is also the one image in Formula One that
 * is already a diagram — five red circles on a gantry — so nothing is lost by
 * drawing it.
 *
 * It runs the real sequence once on load: five lights on, one at a time, a
 * held pause, then out. That pause is the whole drama of a grand prix start,
 * and a backdrop that performs it once and then sits still is a better
 * greeting than one that loops and becomes wallpaper.
 */

const LIGHTS = [0, 1, 2, 3, 4]
const STEP_MS = 620
const HOLD_MS = 1400

/** Whether the viewer has asked for less motion. Read once, at module load. */
const STILL =
  typeof window !== 'undefined' &&
  Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

export default function StartLights() {
  // -1 none lit, 0..4 lighting up, 5 all lit and holding, 6 out — lights out
  // and away we go, which is also the resting state.
  //
  // The reduced-motion case starts at 5 rather than being set there by the
  // effect: no sequence, but not nothing — the gantry sits lit, which is the
  // recognisable image without the movement. Deciding it in the initialiser
  // keeps setState out of the effect body, which cascades a render.
  const [stage, setStage] = useState(STILL ? 5 : -1)

  useEffect(() => {
    if (STILL) return undefined
    const timers = LIGHTS.map((i) =>
      setTimeout(() => setStage(i), STEP_MS * (i + 1)),
    )
    timers.push(setTimeout(() => setStage(5), STEP_MS * 5))
    timers.push(setTimeout(() => setStage(6), STEP_MS * 5 + HOLD_MS))
    return () => timers.forEach(clearTimeout)
  }, [])

  return (
    <div className="lights" aria-hidden="true">
      <svg viewBox="0 0 560 150" preserveAspectRatio="xMidYMid meet">
        {/* The gantry the lights hang from. Without it they read as five
            unexplained dots. */}
        <rect x="8" y="10" width="544" height="9" rx="4" className="lights-beam" />
        {LIGHTS.map((i) => (
          <g key={i} transform={`translate(${64 + i * 108}, 19)`}>
            <rect x="-3" y="0" width="6" height="16" className="lights-beam" />
            <rect x="-40" y="16" width="80" height="86" rx="9" className="lights-box" />
            {/* Two bulbs per column, as the real gantry has. */}
            <circle cx="0" cy="44" r="20" className={`bulb${stage >= i && stage < 6 ? ' is-lit' : ''}`} />
            <circle cx="0" cy="44" r="20" className="bulb-glass" />
          </g>
        ))}
      </svg>
    </div>
  )
}
