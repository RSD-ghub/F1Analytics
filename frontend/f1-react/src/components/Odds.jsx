/**
 * Probabilities rendered as things people already understand.
 *
 * A table of "31% / 74% / 96%" is precise and communicates almost nothing: a
 * reader has to know that 31% is a *high* win probability in a twenty-car
 * field, that the three numbers are nested rather than independent, and that
 * 31% still means the favourite loses most of the time. None of that is on the
 * page. These components put it there.
 */

/**
 * One driver's chances as a single nested bar.
 *
 * Win, podium and points are not three separate numbers — they are one
 * outcome seen at three depths, and every win is a podium and every podium
 * scores. Three columns of percentages hide that completely; drawn as nested
 * segments the structure is the picture. A bar that is nearly full and barely
 * tipped tells you "certain to score, will not win" without reading anything.
 */
export function OddsBar({ win, podium, points }) {
  const pct = (v) => Math.max(0, Math.min(100, (v ?? 0) * 100))
  const label = [
    win != null && `${Math.round(pct(win))}% win`,
    podium != null && `${Math.round(pct(podium))}% podium`,
    points != null && `${Math.round(pct(points))}% points`,
  ].filter(Boolean).join(', ')

  return (
    <div className="odds" role="img" aria-label={label || 'no forecast published'}>
      <div className="odds-track">
        {points != null && (
          <div className="odds-seg odds-points" style={{ width: `${pct(points)}%` }} />
        )}
        {podium != null && (
          <div className="odds-seg odds-podium" style={{ width: `${pct(podium)}%` }} />
        )}
        {/* Drawn last and on top: the win chance is the smallest of the three
            and would otherwise be painted over by the wider segments. */}
        {win != null && (
          <div className="odds-seg odds-win" style={{ width: `${pct(win)}%` }} />
        )}
      </div>
    </div>
  )
}

/**
 * A hundred races, and how many of them this driver wins.
 *
 * The plainest honest device for a single probability, and the one that stops
 * a favourite reading as a certainty. Thirty-one filled squares out of a
 * hundred is unambiguous in a way "31%" is not — you can see the empty ones.
 */
export function HundredRaces({ probability, label }) {
  const wins = Math.round((probability ?? 0) * 100)
  return (
    <div className="hundred">
      <div className="hundred-grid" role="img"
           aria-label={`${wins} of every 100 races`}>
        {Array.from({ length: 100 }, (_, i) => (
          <span key={i} className={i < wins ? 'pip pip-on' : 'pip'} />
        ))}
      </div>
      <p className="hundred-note small muted">{label}</p>
    </div>
  )
}

/**
 * What the forecast actually says, in a sentence.
 *
 * Written from the numbers rather than stored, so it cannot drift from them.
 * The phrasing deliberately leads with the losing case: the most common way to
 * misread a favourite is to treat the highest probability as the outcome, and
 * naming how often they are beaten is the correction.
 */
export function InPlainWords({ leader, win, podium }) {
  if (!leader) return null
  if (win == null) {
    return (
      <p className="lede">
        No winner is called before qualifying — measured across two held-out
        seasons, this window&rsquo;s win-market accuracy was no better than
        guessing. On the podium market, <strong>{leader}</strong> is the most
        likely of the field, at {Math.round((podium ?? 0) * 100)}%.
      </p>
    )
  }
  const losses = 100 - Math.round(win * 100)
  return (
    <p className="lede">
      <strong>{leader}</strong> is the favourite at{' '}
      {Math.round(win * 100)}% — which also means the model expects the race to
      go to someone else in {losses} of every 100 run from this grid.
    </p>
  )
}
