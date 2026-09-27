import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ChevronDown } from 'lucide-react'

import { getBlogFeed, getBlogIndex } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Loading, Unavailable } from '../components/Panel'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'

/**
 * One Blog: one card a race weekend.
 *
 * It was one card per *entry*, which gave a single grand prix five — practice,
 * qualifying, three forecasts, the result — each carrying the same photograph
 * of the same circuit. That is not a feed, it is a timeline with the weekends
 * taken out of it.
 *
 * A card now shows what a reader wants without opening anything: who won,
 * whether we called it, and how the forecast scored, under two sentences
 * written by the model rather than a paragraph of ours. The entries are
 * underneath for whoever wants them.
 */

function when(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

export default function Blog() {
  const [params, setParams] = useSearchParams()
  const season = params.get('season')
  const feed = useAsync(() => getBlogFeed(10), [])
  const index = useAsync(() => getBlogIndex(season), [season])

  if (feed.status === 'loading') return <Loading what="Loading the record" />
  if (feed.status === 'error')
    return <Unavailable what="The weekend record" reason={feed.error.message} />

  const items = feed.data || []
  const seasons = index.data?.seasons || []
  const upcoming = (index.data?.weekends || []).filter((w) => !w.has_run)

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">One Blog</p>
        <h1>The weekend record</h1>
        <p className="lede">
          Every race weekend as it unfolded, and how the forecast held up.
          Entries are appended, never edited afterwards.
        </p>
      </header>

      <div className="weekends">
        {items.map((item) => <WeekendCard key={item.id} item={item} />)}
      </div>

      {items.length === 0 && (
        <p className="muted">Nothing has been raced yet this season.</p>
      )}

      {upcoming.length > 0 && (
        <section>
          <h2 className="section-head">Still to come</h2>
          <ul className="weekend-list">
            {upcoming.slice(0, 4).map((w) => (
              <li key={w.round}>
                {/* Not links: before a race there is nothing to read. */}
                <div className="weekend-card weekend-card-soon">
                  <span className="weekend-round">R{w.round}</span>
                  <span className="weekend-name">
                    {w.race_name}
                    <span className="weekend-circuit">{w.circuit}</span>
                  </span>
                  <span className="weekend-when">{when(w.race_start_utc)}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {seasons.length > 1 && (
        <div className="season-pick">
          <span className="small muted" id="season-label">Season</span>
          <Select
            value={String(index.data.season)}
            onValueChange={(value) => setParams({ season: value })}
          >
            <SelectTrigger className="w-28" aria-labelledby="season-label">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {seasons.map((s) => (
                <SelectItem key={s} value={String(s)}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  )
}

function WeekendCard({ item }) {
  const [open, setOpen] = useState(false)
  const called = item.called_winner && item.winner
    && item.called_winner === item.winner

  return (
    <Card className="weekend-story">
      {item.image && (
        <div
          className="weekend-photo"
          style={{ backgroundImage: `url("${item.image}")` }}
          role="img"
          aria-label={item.circuit}
        >
          <div className="weekend-photo-label">
            <Badge variant="secondary">R{item.round}</Badge>
            <span>{item.circuit}</span>
          </div>
        </div>
      )}

      <CardContent>
        <p className="small muted weekend-date">
          {item.race_name} · {when(item.race_start_utc)}
        </p>
        <h3 className="weekend-headline">{item.headline}</h3>

        {item.summary && <p className="weekend-summary">{item.summary}</p>}

        {/* The three things a reader came for, without opening anything. */}
        <div className="verdict">
          {item.winner && (
            <div className="verdict-cell">
              <span className="verdict-label">Won by</span>
              <span className="verdict-value">{item.winner}</span>
            </div>
          )}
          {item.called_winner && (
            <div className="verdict-cell">
              <span className="verdict-label">We called</span>
              <span className={`verdict-value ${called ? 'is-right' : 'is-wrong'}`}>
                {item.called_winner}
              </span>
            </div>
          )}
          {item.win_skill != null && (
            <div className="verdict-cell">
              <span className="verdict-label">Winner market</span>
              <span className={`verdict-value ${item.win_skill >= 0 ? 'is-right' : 'is-wrong'}`}>
                {item.win_skill >= 0 ? '+' : ''}{(item.win_skill * 100).toFixed(0)}%
                <span className="verdict-note"> vs guessing</span>
              </span>
            </div>
          )}
        </div>

        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger className="weekend-more">
            <ChevronDown className={open ? 'is-open' : ''} size={15} />
            {open ? 'Hide the weekend' : `Show all ${item.entries.length} entries`}
          </CollapsibleTrigger>
          <CollapsibleContent>
            <Separator className="weekend-rule" />
            <ul className="weekend-entries">
              {item.entries.map((entry) => (
                <li key={entry.id}>
                  <Badge variant="outline">{entry.kind_label}</Badge>
                  <div>
                    <p className="entry-headline">{entry.headline}</p>
                    {entry.facts.length > 0 && (
                      <dl className="entry-facts">
                        {entry.facts.map((fact) => (
                          <div key={fact.label}>
                            <dt>{fact.label}</dt>
                            <dd>{fact.value}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </div>
                </li>
              ))}
            </ul>
            <Link className="link" to={`/blog/${item.season}/${item.round}`}>
              Read the full weekend →
            </Link>
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  )
}
