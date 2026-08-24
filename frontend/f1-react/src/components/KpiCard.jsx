export default function KpiCard({ label, value }) {
  return (
    <div className="kpi-card">
      <span className="kpi-card__label">{label}</span>
      <span className="kpi-card__value">{value ?? '—'}</span>
    </div>
  )
}
