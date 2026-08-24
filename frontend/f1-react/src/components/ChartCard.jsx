export default function ChartCard({ title, kpi, variant = 'neutral', children, style }) {
  return (
    <div className={`chart-card chart-card--${variant}`} style={style}>
      <p className="chart-card__title">{title}</p>
      {kpi !== undefined && <p className="chart-card__kpi">{kpi}</p>}
      <div className="chart-card__body">{children}</div>
    </div>
  )
}
