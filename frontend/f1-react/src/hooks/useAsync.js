import { useEffect, useState } from 'react'

/**
 * Run an async loader and expose {status, data, error}.
 *
 * Four pages were repeating the same load/error/ready dance, and each one reset
 * to "loading" by calling setState synchronously at the top of its effect —
 * which triggers a cascading render and is what
 * `react-hooks/set-state-in-effect` flags. Here the reset happens through the
 * dependency key instead: when the key changes the effect re-runs and the
 * stale-guard discards whatever the previous request returns.
 *
 * The guard matters beyond the lint rule. Without it, navigating from one race
 * weekend to another while the first request is in flight lets the slower,
 * older response land last and overwrite the newer one — the page then shows
 * data for a race the user has already navigated away from.
 */
export function useAsync(loader, deps = []) {
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let current = true
    loader()
      .then((data) => { if (current) setState({ status: 'ready', data }) })
      .catch((error) => { if (current) setState({ status: 'error', error }) })
    return () => { current = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return state
}
