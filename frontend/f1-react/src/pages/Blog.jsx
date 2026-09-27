import { Link, useSearchParams } from 'react-router-dom'
import { getBlogFeed, getBlogIndex } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Loading, Unavailable } from '../components/Panel'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

/**
 * One Blog, as a feed.
 *
 * It was a table of contents: eleven rounds listed by name, any of which
 * might open onto nothing. A reader arriving at a blog wants to see what
 * there is to read, so the page now leads with what happened — pole,
 * forecasts, results — newest first, each card carrying a photograph of the
 * venue and the two or three figures behind the headline.
 *
 * Built on shadcn's Card and Badge. The entries themselves come from the same
 * builders the weekend page uses, so a card and the page it opens onto cannot
 * disagree about what happened.
 */

const KIND_TONE = {
  Result: 'default',
  Qualifying: 'secondary',
  Forecast: 'outline',
  Practice: 'outline',
}

function when(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

export default function Blog() {
  const [params, setParams] = useSearchParams()
  const season = params.get('season')
  const feed = useAsync(() => getBlogFeed(24), [])
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
          Every race weekend as it unfolded — what practice showed, what
          qualifying settled, what we forecast before it, and what happened.
          Entries are appended, never edited afterwards.
        </p>
      </header>

      <div className="feed">
        {items.map((item) => (
          <Link key={item.id} to={`/blog/${item.season}/${item.round}`} className="feed-link">
            <Card className="feed-card">
              {item.image && (
                <div
                  className="feed-image"
                  style={{ backgroundImage: `url("${item.image}")` }}
                  role="img"
                  aria-label={item.circuit}
                />
              )}
              <CardHeader>
                <div className="feed-meta">
                  <Badge variant={KIND_TONE[item.kind_label] || 'outline'}>
                    {item.kind_label}
                  </Badge>
                  <span className="small muted">
                    {item.race_name} · R{item.round}
                    {item.occurred_at ? ` · ${when(item.occurred_at)}` : ''}
                  </span>
                </div>
                <CardTitle className="feed-headline">{item.headline}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="small muted feed-summary">{item.summary}</p>
                {item.facts.length > 0 && (
                  <>
                    <Separator className="feed-rule" />
                    <dl className="feed-facts">
                      {item.facts.map((fact) => (
                        <div key={fact.label}>
                          <dt>{fact.label}</dt>
                          <dd>{fact.value}</dd>
                        </div>
                      ))}
                    </dl>
                  </>
                )}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      {items.length === 0 && (
        <p className="muted">Nothing has been raced yet this season.</p>
      )}

      {upcoming.length > 0 && (
        <section className="soon">
          <h2 className="section-head">Still to come</h2>
          <ul className="weekend-list">
            {upcoming.map((w) => (
              <li key={w.round}>
                {/* Not links. Before a race there is no practice, no
                    qualifying and no result to read. */}
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
          {/* shadcn's Select rather than a bare <select>: it is keyboard
              navigable, announces itself, and looks like the rest of the
              product on every platform, which a native control does not. */}
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
