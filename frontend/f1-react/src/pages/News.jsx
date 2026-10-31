import { useSearchParams } from 'react-router-dom'

import { getNews } from '../api/f1Api'
import { useAsync } from '../hooks/useAsync'
import { Loading, Panel, Unavailable } from '../components/Panel'
import { NewsCarousel, NewsList } from '../components/NewsCards'
import { SectionTitle } from '../components/Pit'

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

      {/* Outlets as round chips, the first letter in the circle and the
          name beneath. No logos: they are the outlets' marks, not ours. */}
      {outlets.length > 1 && (
        <div className="outlet-chips" role="group" aria-label="Filter by outlet">
          <button type="button" className={`outlet-chip${outlet ? '' : ' is-on'}`}
                  aria-pressed={!outlet} onClick={() => setParams({})}>
            <span className="outlet-chip-mark">All</span>
            <span className="outlet-chip-name">Every outlet</span>
          </button>
          {outlets.map((name) => (
            <button key={name} type="button" className={`outlet-chip${outlet === name ? ' is-on' : ''}`}
                    aria-pressed={outlet === name} onClick={() => setParams({ outlet: name })}>
              <span className="outlet-chip-mark">{name.replace(/^The\s+/i, '')[0]}</span>
              <span className="outlet-chip-name">{name}</span>
            </button>
          ))}
        </div>
      )}

      {shown.length > 0 ? (
        <>
          {/* Newest first throughout: "top" here means latest, not most
              read. We hold no readership figures and will not invent them. */}
          <section>
            <SectionTitle>Latest headlines</SectionTitle>
            <NewsCarousel items={shown.slice(0, 6)} />
          </section>
          {shown.length > 6 && (
            <section>
              <SectionTitle>Earlier</SectionTitle>
              <NewsList items={shown.slice(6)} />
            </section>
          )}
        </>
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
