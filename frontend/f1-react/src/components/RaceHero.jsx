import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Pill } from './Pit'

/**
 * The next weekend as a hero card: an abstract night-track backdrop with the
 * circuit's own outline drawn large across it, gold tags on top, and a frosted
 * strip along the bottom carrying when, how long until, and where.
 *
 * No illustration or photograph is needed. The outline is the circuit's real
 * shape from the track-map data; without it the card still stands on its
 * gradient.
 */
function outlinePath(outline) {
  if (!outline || outline.length < 2) return null
  const xs = outline.map((p) => p[0])
  const ys = outline.map((p) => p[1])
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  const w = Math.max(...xs) - minX || 1
  const h = Math.max(...ys) - minY || 1
  const d = outline.map((p, i) => `${i ? 'L' : 'M'}${(p[0] - minX).toFixed(1)},${(p[1] - minY).toFixed(1)}`).join('') + 'Z'
  return { d, box: `-40 -40 ${w + 80} ${h + 80}` }
}

function useCountdown(iso) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60000)
    return () => clearInterval(id)
  }, [])
  if (!iso) return null
  const ms = new Date(iso) - now
  if (ms <= 0) return null
  const mins = Math.floor(ms / 60000)
  return { days: Math.floor(mins / 1440), hours: Math.floor((mins % 1440) / 60), mins: mins % 60 }
}

export default function RaceHero({
  season, round, raceName, circuit, country, start, outline, sprint, to, tag = 'Next race',
  headingLevel = 2,
}) {
  const Heading = `h${headingLevel}`
  const shape = useMemo(() => outlinePath(outline), [outline])
  const left = useCountdown(start)
  const date = start ? new Date(start) : null

  const body = (
    <>
      <div className="hero-art" aria-hidden="true">
        {shape && (
          <svg viewBox={shape.box} preserveAspectRatio="xMidYMid meet">
            <path d={shape.d} className="hero-track-glow" />
            <path d={shape.d} className="hero-track" />
          </svg>
        )}
      </div>
      <div className="hero-tags">
        <Pill>{tag}</Pill>
        <span className="hero-tags-right">
          {sprint && <Pill tone="dark">Sprint weekend</Pill>}
          {round != null && <Pill tone="dark">Round {round}{season ? ` · ${season}` : ''}</Pill>}
        </span>
      </div>
      <div className="hero-glass">
        <div className="hero-title">
          <Heading>{raceName}</Heading>
          <p>{[circuit, country].filter(Boolean).join(' · ')}</p>
        </div>
        <dl className="hero-facts">
          {date && (
            <div>
              <dd className="num">{date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</dd>
              <dt>{date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })} lights out</dt>
            </div>
          )}
          {left && (
            <div>
              <dd className="num">{left.days}<small>d</small> {String(left.hours).padStart(2, '0')}<small>h</small> {String(left.mins).padStart(2, '0')}<small>m</small></dd>
              <dt>to go</dt>
            </div>
          )}
          {shape && (
            <div className="hero-mini" aria-hidden="true">
              <svg viewBox={shape.box}><path d={shape.d} /></svg>
            </div>
          )}
        </dl>
      </div>
    </>
  )

  return to
    ? <Link to={to} className="race-hero" aria-label={`${raceName}, open the forecast`}>{body}</Link>
    : <section className="race-hero">{body}</section>
}
