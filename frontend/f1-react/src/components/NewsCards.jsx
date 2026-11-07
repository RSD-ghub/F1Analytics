import { useState } from 'react'
import { ExternalLink } from 'lucide-react'

/**
 * Headlines as a grid of cards, picture on top, the way Formula1.com lays out
 * its latest news.
 *
 * Shared by the news page and the paddock-news entry on a weekend, so a
 * headline looks the same wherever it appears and carries its attribution in
 * both places. The outlet is printed above the headline, not after it: read
 * first, "AUTOSPORT" frames the words below as somebody's report, while the
 * same words with the outlet trailing read as our claim with a footnote.
 *
 * The picture is the one the outlet's feed attached to the item. Formula1.com's
 * feed attaches none, and we do not go to the article page to find one, so
 * those cards get a plain tile naming the outlet instead. A picture that fails
 * to load falls back to the same tile rather than leaving a broken image.
 *
 * Each card is a single link that opens in a new tab with no referrer and no
 * opener. URLs have already been restricted to http(s) by ingestion, and React
 * escapes the text; this component adds no HTML of its own from the feed.
 */

function when(iso, estimated) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const label = date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  return estimated ? `first seen ${label}` : label
}

function Picture({ src, outlet }) {
  const [failed, setFailed] = useState(false)
  if (!src || failed) {
    return (
      <div className="news-card-art news-card-fallback" aria-hidden="true">
        <span>{outlet}</span>
      </div>
    )
  }
  return (
    <div className="news-card-art">
      <img
        src={src}
        alt=""
        loading="lazy"
        decoding="async"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    </div>
  )
}

function Card({ item, showSummary }) {
  return (
    <a className="news-card" href={item.url} target="_blank" rel="noopener noreferrer">
      <Picture src={item.image_url} outlet={item.outlet} />
      <div className="news-card-body">
        <p className="news-meta">
          <span className="news-outlet">{item.outlet}</span>
          {item.published_at && (
            <time dateTime={item.published_at}>
              {when(item.published_at, item.published_estimated)}
            </time>
          )}
        </p>
        <h3 className="news-title">
          {item.title}
          <ExternalLink size={13} aria-label="opens the outlet's site" />
        </h3>
        {showSummary && item.summary && (
          <p className="news-summary">{item.summary}</p>
        )}
      </div>
    </a>
  )
}

export default function NewsCards({ items, showSummary = true, featureFirst = false }) {
  return (
    <ul className={featureFirst ? 'news-grid news-grid-featured' : 'news-grid'}>
      {items.map((item) => (
        <li key={item.id ?? item.url}><Card item={item} showSummary={showSummary} /></li>
      ))}
    </ul>
  )
}

/** The leading stories as large cards in a sideways row. */
export function NewsCarousel({ items }) {
  return (
    <ul className="carousel news-carousel" aria-label="Top stories">
      {items.map((item, i) => (
        <li key={item.id ?? item.url} className="news-carousel-item">
          {i < 3 && <span className="news-rank pit-pill pit-pill-gold">No. {i + 1}</span>}
          <Card item={item} showSummary={false} />
        </li>
      ))}
    </ul>
  )
}

/** Everything else as rows: outlet and time over the headline, picture right. */
export function NewsList({ items }) {
  return (
    <ul className="news-list">
      {items.map((item) => (
        <li key={item.id ?? item.url}>
          <a className="news-row" href={item.url} target="_blank" rel="noopener noreferrer">
            <div className="news-row-text">
              <h3 className="news-title">
                {item.title}
                <ExternalLink size={13} aria-label="opens the outlet's site" />
              </h3>
              <p className="news-meta">
                <span className="news-outlet">{item.outlet}</span>
                {item.published_at && (
                  <time dateTime={item.published_at}>
                    {when(item.published_at, item.published_estimated)}
                  </time>
                )}
              </p>
            </div>
            <div className="news-row-art"><Picture src={item.image_url} outlet={item.outlet} /></div>
          </a>
        </li>
      ))}
    </ul>
  )
}
