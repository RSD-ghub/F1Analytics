import { useEffect, useState } from 'react'

/**
 * Run an async loader and expose {status, data, error, updatedAt}.
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
 *
 * ``refreshMs`` re-runs the loader on an interval while the tab is visible,
 * keeping the current data on screen in the meantime. A failed refresh keeps
 * the last good data rather than replacing the page with an error: on race
 * day a dropped request should cost one update, not the page. It may be a
 * function of the loaded data, so a page can refresh only when it matters
 * (Next Race during a race weekend) and return 0 otherwise.
 */
export function useAsync(loader, deps = [], { refreshMs = 0 } = {}) {
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let current = true
    loader()
      .then((data) => {
        if (current) setState({ status: 'ready', data, updatedAt: new Date() })
      })
      .catch((error) => { if (current) setState({ status: 'error', error }) })
    return () => { current = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  const interval =
    typeof refreshMs === 'function'
      ? (state.status === 'ready' ? refreshMs(state.data) : 0)
      : refreshMs

  useEffect(() => {
    if (!interval) return undefined
    let current = true
    const tick = () => {
      if (document.visibilityState === 'hidden') return
      loader()
        .then((data) => {
          if (current) setState({ status: 'ready', data, updatedAt: new Date() })
        })
        .catch(() => {})
    }
    const timer = setInterval(tick, interval)
    return () => { current = false; clearInterval(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval, ...deps])

  return state
}
