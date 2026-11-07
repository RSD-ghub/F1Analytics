import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Pill } from './Pit'

/**
 * The next weekend as a hero card: an abstract night-track backdrop with the
 * circuit's own outline drawn large across it as a neon line, red tags on
 * top, and a frosted strip along the bottom carrying the name, when, and a
 * countdown in tiles.
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
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  if (!iso) return null
  const ms = new Date(iso) - now
  if (ms <= 0) return null
  const secs = Math.floor(ms / 1000)
  return {
    days: Math.floor(secs / 86400), hours: Math.floor((secs % 86400) / 3600),
    mins: Math.floor((secs % 3600) / 60), secs: secs % 60,
  }
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
            <path d={shape.d} className="hero-track-line" />
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
        {date && (
          <p className="hero-when-line">
            {date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}
            {' · '}
            {date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })} lights out, your time
          </p>
        )}
        {left && (
          <ol className="count-tiles" aria-label={`${left.days} days, ${left.hours} hours and ${left.mins} minutes to go`}>
            {[[left.days, 'Days'], [left.hours, 'Hours'], [left.mins, 'Min'], [left.secs, 'Sec']].map(([v, l]) => (
              <li key={l} aria-hidden="true">
                <span className="num">{String(v).padStart(2, '0')}</span>
                <small>{l}</small>
              </li>
            ))}
          </ol>
        )}
      </div>
    </>
  )

  return to
    ? <Link to={to} className="race-hero" aria-label={`${raceName}, open the forecast`}>{body}</Link>
    : <section className="race-hero">{body}</section>
}
