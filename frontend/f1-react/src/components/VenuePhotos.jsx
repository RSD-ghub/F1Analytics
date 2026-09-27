import { useEffect, useMemo, useState } from 'react'

/**
 * Photographs of the circuit, inside the circuit card.
 *
 * These were behind the whole page at six per cent opacity, where they were
 * barely visible and could not be made more so: text sits directly on the
 * page ground, and a photograph bright enough to enjoy is a photograph body
 * text fails its contrast check against. Inside a card there is nothing to
 * read through them, so they can be shown properly.
 *
 * From Wikimedia Commons, filtered server-side to licences that permit reuse.
 * The credit tracks the visible frame because that is the licence term, not a
 * courtesy.
 */

const HOLD_MS = 6000

export default function VenuePhotos({ images, circuit }) {
  const [index, setIndex] = useState(0)

  const still = useMemo(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    [],
  )

  useEffect(() => { setIndex(0) }, [circuit])

  useEffect(() => {
    if (!images || images.length < 2 || still) return undefined
    const timer = setInterval(
      () => setIndex((i) => (i + 1) % images.length), HOLD_MS,
    )
    return () => clearInterval(timer)
  }, [images, still])

  if (!images?.length) return null
  const current = images[index]

  return (
    <figure className="venue">
      {images.map((image, i) => (
        <div
          key={image.url}
          className={`venue-frame${i === index ? ' is-on' : ''}`}
          style={{ backgroundImage: `url("${image.url}")` }}
          role="img"
          aria-label={`${circuit}: ${image.title}`}
        />
      ))}

      {images.length > 1 && (
        <div className="venue-dots">
          {images.map((image, i) => (
            <button
              key={image.url}
              type="button"
              className={`venue-dot${i === index ? ' is-on' : ''}`}
              aria-label={`Photograph ${i + 1} of ${images.length}`}
              onClick={() => setIndex(i)}
            />
          ))}
        </div>
      )}

      <figcaption className="venue-credit">
        {current.source
          ? <a href={current.source} target="_blank" rel="noreferrer noopener">{current.credit}</a>
          : current.credit}
        {current.licence_url
          ? <> · <a href={current.licence_url} target="_blank" rel="noreferrer noopener">{current.licence}</a></>
          : <> · {current.licence}</>}
        {' · Wikimedia Commons'}
      </figcaption>
    </figure>
  )
}
