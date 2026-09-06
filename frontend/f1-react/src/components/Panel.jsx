/**
 * Shared primitives.
 *
 * `Unavailable` exists because core-api can answer partially: a panel whose
 * downstream service is down must say so. Rendering an empty card instead
 * leaves the reader unable to tell "no data yet" from "broken", which is the
 * one distinction an outage most needs to communicate.
 */

export function Panel({ title, subtitle, children, footer }) {
  return (
    <section className="panel">
      {title && (
        <header className="panel-head">
          <h2>{title}</h2>
          {subtitle && <p className="panel-sub">{subtitle}</p>}
        </header>
      )}
      <div className="panel-body">{children}</div>
      {footer && <footer className="panel-foot">{footer}</footer>}
    </section>
  )
}

export function Unavailable({ what, reason }) {
  return (
    <div className="unavailable" role="status">
      <strong>{what} is unavailable.</strong>{' '}
      {reason || 'The service behind this panel could not be reached.'}
    </div>
  )
}

export function Loading({ what = 'Loading' }) {
  return <div className="loading" role="status">{what}…</div>
}

/** A probability, or an explicit statement that no claim was made. */
export function Probability({ value, absentLabel = 'not published' }) {
  if (value === null || value === undefined) {
    // Never render a missing probability as 0% or a dash. The backend
    // distinguishes "we make no claim" from "we claim zero", and collapsing
    // them here would undo that at the last step.
    return <span className="prob absent" title="No claim was made for this market">{absentLabel}</span>
  }
  return <span className="prob">{(value * 100).toFixed(0)}%</span>
}

export function Bar({ value, max = 1 }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="bar" aria-hidden="true">
      <div className="bar-fill" style={{ width: `${pct}%` }} />
    </div>
  )
}

/** Data-quality caveats travel with a forecast; they are not decoration. */
export function Caveats({ quality }) {
  if (!quality) return null
  const notes = []
  if (quality.grid_is_provisional)
    notes.push('Grid is qualifying classification — penalties not yet applied.')
  else if (quality.grid_source === 'official_provisional')
    notes.push('Grid is the FIA provisional grid; a later decision could still change it.')
  if (quality.complete === false && quality.notes) notes.push(quality.notes)
  if (!notes.length) return null
  return (
    <ul className="caveats">
      {notes.map((n) => <li key={n}>{n}</li>)}
    </ul>
  )
}
