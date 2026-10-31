import { useEffect } from 'react'

const SITE = 'F1 Forecast'

/**
 * Set the tab title for a page. Every page used to share one title, so five
 * open tabs and the browser history all read the same.
 */
export function usePageTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} · ${SITE}` : `${SITE} — published before the race, scored after`
  }, [title])
}
