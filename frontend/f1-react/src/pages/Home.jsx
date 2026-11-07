import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Link } from 'react-router-dom'
import { getGlobe, getNextRace } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Loading, Unavailable } from '../components/Panel'
import Globe from '../components/Globe'
import RaceHero from '../components/RaceHero'
import { Carousel, Pill, SectionTitle } from '../components/Pit'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'

/**
 * The season as a place.
 *
 * A calendar is a list of names and dates, and Formula One is not a list — it
 * is twenty-three places a year, and where they are is most of what makes a
 * season feel like one. Turning the globe to find a race is a better index
 * than scrolling a table, and it answers "where is this actually happening"
 * without a reader having to know that Sepang is in Malaysia.
 *
 * Selecting a race routes on the status the server computed, so this page and
 * the rest of the product cannot disagree about which weekend is next:
 *
 *   raced     → the weekend's own entry in One Blog
 *   next      → the Next race page
 *   scheduled → held here, with its name and when it runs
 */

function when(iso, withTime = false) {
  if (!iso) return ''
  const date = new Date(iso)
  return date.toLocaleDateString(undefined, {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  })
}

function daysUntil(iso) {
  if (!iso) return null
  const days = Math.ceil((new Date(iso) - Date.now()) / 86400000)
  return days > 0 ? days : null
}

export default function Home() {
  const navigate = useNavigate()
  const state = useAsync(() => getGlobe(), [])
  // Only for the circuit's outline on the hero card. If it fails the card
  // stands on its own, so its failure is not shown.
  const nextRace = useAsync(() => getNextRace(), [])
  const [selected, setSelected] = useState(null)

  if (state.status === 'loading') return <Loading what="Placing the season" />
  if (state.status === 'error')
    return <Unavailable what="The season map" reason={state.error.message} />

  const stops = state.data || []
  const raced = stops.filter((s) => s.status === 'raced').length
  const next = stops.find((s) => s.status === 'next')
  const upcoming = stops.filter((s) => s.status !== 'raced').slice(next ? 1 : 0, 9)
  const outline = nextRace.status === 'ready' ? nextRace.data?.circuit?.map?.outline : null

  function choose(stop) {
    setSelected(stop)
    if (stop.status === 'raced') navigate(`/blog/${stop.season}/${stop.round}`)
    else if (stop.status === 'next') navigate('/next-race')
  }

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">The {next?.season || ''} season</p>
        {/* Not a hardcoded count. The globe leaves off a circuit it cannot
            place rather than dropping it in the Atlantic, so a number written
            into the copy would be wrong in a way nobody would think to check. */}
        <h1>Where the season goes</h1>
        <p className="lede">
          {raced} of these {stops.length} rounds have been run and written up;
          the rest are still to come.
        </p>
      </header>

      {next && (
        <RaceHero
          season={next.season} round={next.round} raceName={next.race_name}
          circuit={next.circuit} country={next.country} start={next.race_start_utc}
          outline={outline} to="/next-race"
        />
      )}

      {upcoming.length > 0 && (
        <section>
          <SectionTitle>Upcoming rounds</SectionTitle>
          <Carousel label="Upcoming rounds">
            {upcoming.map((stop) => (
              <button key={stop.round} type="button" role="listitem" className="round-card"
                      onClick={() => { setSelected(stop); document.getElementById('atlas')?.scrollIntoView({ behavior: 'smooth' }) }}>
                <Pill tone="dark">Round {stop.round}</Pill>
                <span className="round-country">{stop.country}</span>
                <span className="round-name">{stop.race_name}</span>
                <span className="round-date">{shortDate(stop.race_start_utc)}</span>
              </button>
            ))}
          </Carousel>
        </section>
      )}

      <section id="atlas">
        <SectionTitle to="/blog" action="All weekends">The season on a globe</SectionTitle>
        <div className="atlas">
          <div className="atlas-globe">
            <Globe stops={stops} selected={selected} onSelect={choose} />
            <ul className="atlas-key">
              <li><span className="key-dot key-raced" /> Raced</li>
              <li><span className="key-dot key-next" /> Next</li>
              <li><span className="key-dot key-scheduled" /> To come</li>
            </ul>
          </div>

          <div className="atlas-detail">
            {selected ? (
              <Chosen stop={selected} />
            ) : next ? (
              <Card>
                <CardContent>
                  <Badge>Next</Badge>
                  <h2 className="atlas-name">{next.race_name}</h2>
                  <p className="muted">{next.circuit}{next.country ? ` · ${next.country}` : ''}</p>
                  <p className="atlas-when">{when(next.race_start_utc, true)}</p>
                  <p className="small muted">
                    Turn the globe and pick any marker. A race already run
                    opens its report; <Link to="/next-race">this one opens the forecast</Link>.
                  </p>
                </CardContent>
              </Card>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  )
}

function shortDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/**
 * A race still to come. The other two statuses navigate away, so this is the
 * only one that has anything to render here — which is the whole reason the
 * page keeps a detail panel at all.
 */
function Chosen({ stop }) {
  const days = daysUntil(stop.race_start_utc)
  return (
    <Card>
      <CardContent>
        <Badge variant={stop.status === 'raced' ? 'secondary' : 'outline'}>
          Round {stop.round}
        </Badge>
        <h2 className="atlas-name">{stop.race_name}</h2>
        <p className="muted">{stop.circuit}{stop.country ? ` · ${stop.country}` : ''}</p>
        <p className="atlas-when">{when(stop.race_start_utc, true)}</p>
        {days != null && (
          <p className="atlas-countdown">
            <strong>{days}</strong> day{days === 1 ? '' : 's'} away
          </p>
        )}
        <p className="small muted">
          Nothing has happened here yet this season — no practice, no
          qualifying, no forecast. It appears in One Blog once it has.
        </p>
      </CardContent>
    </Card>
  )
}
