import { AlertCircle } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

/**
 * Shared primitives.
 *
 * `Panel` is the container every page is built from, so putting it on
 * shadcn's Card moves all of them at once rather than rewriting six pages by
 * hand. The props stay exactly as they were — a migration that changes the
 * call sites as well as the implementation cannot be verified one step at a
 * time.
 *
 * `Unavailable` exists because core-api can answer partially: a panel whose
 * downstream service is down must say so. Rendering an empty card instead
 * leaves the reader unable to tell "no data yet" from "broken", which is the
 * one distinction an outage most needs to communicate.
 */

export function Panel({ title, subtitle, children, footer }) {
  return (
    <Card className="panel">
      {title && (
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          {subtitle && <CardDescription>{subtitle}</CardDescription>}
        </CardHeader>
      )}
      <CardContent>{children}</CardContent>
      {footer && <CardFooter className="panel-foot">{footer}</CardFooter>}
    </Card>
  )
}

export function Unavailable({ what, reason }) {
  return (
    <Alert variant="destructive" role="status">
      <AlertCircle />
      <AlertTitle>{what} is unavailable.</AlertTitle>
      <AlertDescription>
        {reason || 'The service behind this panel could not be reached.'}
      </AlertDescription>
    </Alert>
  )
}

/**
 * A skeleton rather than a word.
 *
 * "Loading the weekend…" told a reader nothing about how much was coming or
 * how long it would take. Blocks in the shape of the page at least say that
 * something is arriving and roughly what.
 */
export function Loading({ what = 'Loading' }) {
  return (
    <div className="stack" role="status" aria-label={`${what}…`}>
      <div className="page-head">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-9 w-80 mt-3" />
        <Skeleton className="h-4 w-full max-w-lg mt-3" />
      </div>
      <Card className="panel">
        <CardHeader><Skeleton className="h-5 w-48" /></CardHeader>
        <CardContent>
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6 mt-2" />
          <Skeleton className="h-4 w-2/3 mt-2" />
        </CardContent>
      </Card>
    </div>
  )
}

/** A probability, or an explicit statement that no claim was made. */
export function Probability({ value, absentLabel = 'not published' }) {
  if (value === null || value === undefined) {
    // Never render a missing probability as 0% or a dash. The backend
    // distinguishes "we make no claim" from "we claim zero", and collapsing
    // them here would undo that at the last step.
    return <span className="prob absent" title="No claim was made for this market">{absentLabel}</span>
  }
  return <span className="prob">{(value * 100).toFixed(0)}%</span>
}

export function Bar({ value, max = 1 }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="bar" aria-hidden="true">
      <div className="bar-fill" style={{ width: `${pct}%` }} />
    </div>
  )
}

/** Data-quality caveats travel with a forecast; they are not decoration. */
export function Caveats({ quality }) {
  if (!quality) return null
  const notes = []
  if (quality.grid_is_provisional)
    notes.push('Grid is qualifying classification — penalties not yet applied.')
  else if (quality.grid_source === 'official_provisional')
    notes.push('Grid is the FIA provisional grid; a later decision could still change it.')
  if (quality.complete === false && quality.notes) notes.push(quality.notes)
  if (!notes.length) return null
  return (
    <ul className="caveats">
      {notes.map((n) => <li key={n}>{n}</li>)}
    </ul>
  )
}
