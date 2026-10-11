import { useSearchParams } from 'react-router-dom'

import { getNews } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Loading, Panel, Unavailable } from '../components/Panel'
import NewsList from '../components/NewsList'
import { Button } from '@/components/ui/button'

/**
 * Paddock news: the outlets' latest headlines, linked to the originals.
 *
 * This is the one page on the site whose content is not ours, and it says so
 * at the top rather than in a footer. Everything else here is computed from
 * stored data and stands behind its own track record; these are reports we
 * pass on, and the forecast does not read them.
 *
 * Filtering is done in the browser over one fetch. Two outlets and fifty
 * headlines do not justify a request per click, and a filter that waits on
 * the network feels broken.
 */
export default function News() {
  const [params, setParams] = useSearchParams()
  const outlet = params.get('outlet')
  const state = useAsync(() => getNews(50), [])

  if (state.status === 'loading') return <Loading what="Loading the paddock news" />
  if (state.status === 'error')
    return <Unavailable what="Paddock news" reason={state.error.message} />

  const items = state.data?.items || []
  const outlets = [...new Set(items.map((item) => item.outlet))].sort()
  const shown = outlet ? items.filter((item) => item.outlet === outlet) : items

  return (
    <div className="stack">
      <header className="page-head">
        <p className="eyebrow">Paddock news</p>
        <h1>What the paddock is reporting</h1>
        <p className="lede">{state.data.attribution}</p>
      </header>

      {outlets.length > 1 && (
        <div className="news-filter" role="group" aria-label="Filter by outlet">
          <Button
            size="sm"
            variant={outlet ? 'outline' : 'default'}
            aria-pressed={!outlet}
            onClick={() => setParams({})}
          >
            All
          </Button>
          {outlets.map((name) => (
            <Button
              key={name}
              size="sm"
              variant={outlet === name ? 'default' : 'outline'}
              aria-pressed={outlet === name}
              onClick={() => setParams({ outlet: name })}
            >
              {name}
            </Button>
          ))}
        </div>
      )}

      {shown.length > 0 ? (
        <Panel>
          <NewsList items={shown} />
        </Panel>
      ) : (
        <Panel title="No headlines yet">
          <p className="muted">
            The feeds are read every half hour. If this stays empty, the
            outlets could not be reached from this deployment.
          </p>
        </Panel>
      )}
    </div>
  )
}
