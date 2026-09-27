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
  // Keyed on the circuit rather than reset by an effect. Resetting inside one
  // cascades a render, and the key does the same job: a frame index left over
  // from another circuit is simply not used.
  const [frame, setFrame] = useState({ circuit: null, index: 0 })
  const index = frame.circuit === circuit ? frame.index : 0

  const still = useMemo(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    [],
  )

  useEffect(() => {
    if (!images || images.length < 2 || still) return undefined
    const timer = setInterval(() => {
      setFrame((f) => ({
        circuit,
        index: (f.circuit === circuit ? f.index + 1 : 1) % images.length,
      }))
    }, HOLD_MS)
    return () => clearInterval(timer)
  }, [images, still, circuit])

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
              onClick={() => setFrame({ circuit, index: i })}
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
