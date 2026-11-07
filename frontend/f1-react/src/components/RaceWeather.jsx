import { CloudRain, Sun, CloudSun } from 'lucide-react'
import { Panel } from './Panel'

/**
 * The race-window rain forecast, hour by hour.
 *
 * Says plainly that the forecast model does not use it. A reader seeing "70%
 * chance of rain" next to the win probabilities would otherwise assume those
 * probabilities already priced the rain in, and they do not.
 *
 * Renders nothing until the race is inside the weather forecast's range:
 * absence means "not forecast yet", and drawing an empty panel would read as
 * "dry".
 */
export default function RaceWeather({ weather }) {
  if (!weather?.hours?.length) return null
  const peak = weather.rain_chance_max_pct ?? 0
  const Icon = peak >= 50 ? CloudRain : peak >= 20 ? CloudSun : Sun

  return (
    <Panel
      title="Race-day weather"
      subtitle={`${weather.source} forecast at the circuit for the race window. Times are in your time zone.`}
    >
      <p className="weather-summary">
        <Icon aria-hidden="true" />
        <span>{weather.summary}</span>
      </p>
      <ol className="weather-hours">
        {weather.hours.map((hour) => (
          <li key={hour.time_utc}>
            <span className="weather-time">
              {new Date(hour.time_utc).toLocaleTimeString(undefined, { timeStyle: 'short' })}
            </span>
            <span
              className="weather-bar"
              style={{ '--chance': `${hour.rain_chance_pct ?? 0}%` }}
              aria-label={`${hour.rain_chance_pct ?? 0}% chance of rain`}
            />
            <span className="weather-num">{hour.rain_chance_pct ?? '–'}%</span>
            <span className="weather-num muted">
              {hour.temperature_c != null ? `${Math.round(hour.temperature_c)}°C` : ''}
            </span>
          </li>
        ))}
      </ol>
      <p className="muted small">{weather.note}</p>
    </Panel>
  )
}
