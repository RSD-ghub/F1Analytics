import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { getGlobe } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Loading, Unavailable } from '../components/Panel'
import Globe from '../components/Globe'
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
  const [selected, setSelected] = useState(null)

  if (state.status === 'loading') return <Loading what="Placing the season" />
  if (state.status === 'error')
    return <Unavailable what="The season map" reason={state.error.message} />

  const stops = state.data || []
  const raced = stops.filter((s) => s.status === 'raced').length
  const next = stops.find((s) => s.status === 'next')

  function choose(stop) {
    setSelected(stop)
    if (stop.status === 'raced') navigate(`/blog/${stop.season}/${stop.round}`)
    else if (stop.status === 'next') navigate('/next-race')
  }

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">The season</p>
        {/* Not a hardcoded count. The calendar is twenty-three rounds and the
            globe shows twenty-two, because a circuit we cannot place is left
            off rather than dropped in the Atlantic — so a number written into
            the headline would be wrong, and wrong in a way nobody would think
            to check. */}
        <h1>Where the season goes</h1>
        <p className="lede">
          Turn the globe and pick a race. {raced} of these {stops.length} have
          been run and written up; the rest are still to come.
        </p>
      </header>

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
                  Pick any marker to see that weekend. A race already run opens
                  its report; this one opens the forecast.
                </p>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  )
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
