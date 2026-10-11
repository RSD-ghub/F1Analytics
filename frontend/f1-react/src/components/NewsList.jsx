import { ExternalLink } from 'lucide-react'

/**
 * A list of headlines, each naming its outlet and linking to the original.
 *
 * Shared by the news page and the paddock-news entry on a weekend, so a
 * headline looks the same wherever it appears and carries its attribution in
 * both places. The outlet is printed before the headline, not after it: read
 * left to right, "Autosport · Team brings upgrade" is a report, while the same
 * words with the outlet trailing read as our claim with a footnote.
 *
 * Links open in a new tab with no referrer and no opener. The URL has already
 * been restricted to http(s) by ingestion, and React escapes the text; this
 * component adds no HTML of its own from the feed.
 */

function when(iso, estimated) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const label = date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  return estimated ? `first seen ${label}` : label
}

export default function NewsList({ items, showSummary = true }) {
  return (
    <ul className="news-list">
      {items.map((item) => (
        <li key={item.id ?? item.url} className="news-item">
          <p className="news-meta">
            <span className="news-outlet">{item.outlet}</span>
            {item.published_at && (
              <time dateTime={item.published_at}>
                {when(item.published_at, item.published_estimated)}
              </time>
            )}
          </p>
          <a
            className="news-title"
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {item.title}
            <ExternalLink size={13} aria-label="opens the outlet's site" />
          </a>
          {showSummary && item.summary && (
            <p className="news-summary">{item.summary}</p>
          )}
        </li>
      ))}
    </ul>
  )
}
