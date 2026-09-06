import { NavLink, Link } from 'react-router-dom'
import { useAuth } from '../context/useAuth'

const LINKS = [
  { to: '/', label: 'Next race', end: true },
  { to: '/track-record', label: 'Track record' },
  { to: '/championship', label: 'Championship' },
  { to: '/bernie', label: 'Bernie' },
]

export default function Layout({ children }) {
  const { auth, logout } = useAuth()

  return (
    <div className="app">
      <header className="masthead">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>F1 Forecast</span>
        </Link>
        <nav>
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end}
                     className={({ isActive }) => (isActive ? 'active' : '')}>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="account">
          {auth?.token ? (
            <>
              <span className="muted small">{auth.username}</span>
              <button className="button subtle" onClick={logout}>Sign out</button>
            </>
          ) : (
            <Link className="button subtle" to="/login">Sign in</Link>
          )}
        </div>
      </header>

      <main>{children}</main>

      <footer className="colophon">
        <p>
          Forecasts are locked before each race and scored afterwards, whatever
          they turn out to be. Every prediction we have ever published stays on
          the <Link to="/track-record">track record</Link>.
        </p>
        <p className="disclaimer">
          An unofficial, non-commercial project. Not associated with, endorsed
          by, or connected to Formula 1, the FIA, or any team. F1, FORMULA ONE
          and related marks belong to Formula One Licensing BV. Timing and
          classification data originate with the FIA and Formula One and are
          used here for analysis only.
        </p>
      </footer>
    </div>
  )
}
