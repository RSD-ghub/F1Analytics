import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { initials, splitName, teamColour } from '../lib/teams'
import { useHeadshot } from '../lib/headshots'

/**
 * The shared furniture of the pit-wall look: avatars, team bars, the podium,
 * ranked rows, pills, the segmented toggle and section titles. Pages compose
 * these; none of them fetch anything.
 */

/** Initials in a circle ringed in the team colour. We hold no rights to
    driver photographs, and initials say who it is without pretending. */
export function Avatar({ name, team, size = 40 }) {
  const found = useHeadshot(name)
  // OpenF1 links the one-column rendition; big avatars ask for the two-column one.
  const photo = found && size > 48 ? found.replace('/1col/', '/2col/') : found
  const [broken, setBroken] = useState(null)
  const show = photo && broken !== photo
  return (
    <span className={`avatar${show ? ' has-photo' : ''}`}
          style={{ '--team': teamColour(team), width: size, height: size, fontSize: size * 0.36 }}
          aria-hidden="true">
      {show
        ? <img src={photo} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer"
               onError={() => setBroken(photo)} />
        : initials(name)}
    </span>
  )
}

/** Small first name over a large surname, the way a timing screen sets it. */
export function DriverName({ name, team }) {
  const { first, last } = splitName(name)
  return (
    <span className="driver-name">
      {first && <span className="driver-first">{first}</span>}
      <span className="driver-last">{last}</span>
      {team && <span className="driver-team">{team}</span>}
    </span>
  )
}

export function Pill({ children, tone = 'gold', className = '' }) {
  return <span className={`pit-pill pit-pill-${tone} ${className}`}>{children}</span>
}

/** A section title with an optional "View all" link on the right. */
export function SectionTitle({ children, to, action = 'View all' }) {
  return (
    <div className="section-title">
      <h2>{children}</h2>
      {to && (
        <Link to={to} className="section-title-link">
          {action} <ChevronRight size={15} aria-hidden="true" />
        </Link>
      )}
    </div>
  )
}

/** Two or three options, one active, the active one in red. */
export function Segmented({ options, value, onChange, label }) {
  return (
    <div className="segmented" role="tablist" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" aria-selected={value === o.value}
                className={value === o.value ? 'is-on' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/**
 * The top three on steps, second on the left and third on the right.
 * `entries` is [{ name, team, value, note? }] in finishing order; `value` is
 * what the pill under the avatar says (points, a probability).
 */
export function Podium({ entries, caption }) {
  const [p1, p2, p3] = entries
  const order = [[p2, 2], [p1, 1], [p3, 3]].filter(([e]) => e)
  return (
    <figure className="podium-stage">
      <ol className="podium-steps">
        {order.map(([e, pos]) => (
          <li key={pos} className={`podium-step step-${pos}`} style={{ '--team': teamColour(e.team) }}>
            <div className="podium-who">
              <Avatar name={e.name} team={e.team} size={pos === 1 ? 84 : 66} />
              {e.value != null && <span className="podium-value num">{e.value}</span>}
              <DriverName name={e.name} />
            </div>
            <div className="podium-block"><span>{pos}</span></div>
          </li>
        ))}
      </ol>
      {caption && <figcaption className="small muted">{caption}</figcaption>}
    </figure>
  )
}

/**
 * Position, team bar, avatar, name, and whatever goes on the right. Used for
 * standings, forecasts and results alike, so the three read as one family.
 */
export function RankRow({ pos, name, team, children, dim = false }) {
  return (
    <li className={`rank-row${dim ? ' is-dim' : ''}`} style={{ '--team': teamColour(team) }}>
      <span className="rank-pos num">{pos != null ? String(pos).padStart(2, '0') : '–'}</span>
      <span className="rank-bar" aria-hidden="true" />
      <Avatar name={name} team={team} size={34} />
      <DriverName name={name} team={team} />
      <span className="rank-right">{children}</span>
    </li>
  )
}

export function RankList({ children, label }) {
  return <ol className="rank-list" aria-label={label}>{children}</ol>
}

/** A horizontally scrolling row of cards; the next one peeks in from the edge. */
export function Carousel({ children, label }) {
  return <div className="carousel" role="list" aria-label={label}>{children}</div>
}
