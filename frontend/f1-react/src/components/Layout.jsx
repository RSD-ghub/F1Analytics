import { useEffect, useState } from 'react'
import { NavLink, Link, useLocation } from 'react-router-dom'
import { Menu, MessageCircle, X } from 'lucide-react'
import { useAuth } from '../context/useAuth'
import { Button } from '@/components/ui/button'

/* Home is the only link left in the masthead; everything else lives in the
   sidebar, which opens from the menu button and closes on navigation. */
const LINKS = [
  { to: '/next-race', label: 'Next race' },
  { to: '/blog', label: 'One Blog' },
  { to: '/news', label: 'Paddock news' },
  { to: '/track-record', label: 'Track record' },
  { to: '/championship', label: 'Championship' },
  { to: '/drivers', label: 'Drivers' },
  { to: '/strategy', label: 'Strategy' },
  { to: '/bernie', label: 'Bernie' },
]

export default function Layout({ children }) {
  const { auth, logout } = useAuth()
  const { pathname } = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuOpen])

  return (
    <div className="app">
      <header className="masthead">
        <button type="button" className="menu-toggle"
                aria-label="Open menu" aria-expanded={menuOpen} aria-controls="sidebar"
                onClick={() => setMenuOpen(true)}>
          <Menu size={20} aria-hidden="true" />
        </button>
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>F1 Forecast</span>
        </Link>
        <nav>
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>
            Home
          </NavLink>
        </nav>
        <div className="account">
          {auth?.token ? (
            <>
              <span className="muted small">{auth.username}</span>
              <Button variant="outline" size="sm" onClick={logout}>Sign out</Button>
            </>
          ) : (
            <Button asChild variant="outline" size="sm"><Link to="/login">Sign in</Link></Button>
          )}
        </div>
      </header>

      <div className={`sidebar-scrim${menuOpen ? ' open' : ''}`} aria-hidden="true"
           onClick={() => setMenuOpen(false)} />
      <aside id="sidebar" className={`sidebar${menuOpen ? ' open' : ''}`}
             aria-label="Site menu" inert={!menuOpen}>
        <div className="sidebar-head">
          <span className="sidebar-title">Menu</span>
          <button type="button" className="menu-toggle" aria-label="Close menu"
                  onClick={() => setMenuOpen(false)}>
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <nav>
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} onClick={() => setMenuOpen(false)}
                     className={({ isActive }) => (isActive ? 'active' : '')}>
              {link.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Bernie is a page of her own; the button is a shortcut to it from
          anywhere else on the site. */}
      {pathname !== '/bernie' && (
        <Link to="/bernie" className="bernie-fab" aria-label="Ask Bernie" title="Ask Bernie">
          <MessageCircle size={24} aria-hidden="true" />
        </Link>
      )}

      <main>
        {children}</main>

      <footer className="colophon">
        <p>
          Forecasts are locked before each race and scored afterwards, whatever
          they turn out to be. Every prediction we have ever published stays on
          the <Link to="/track-record">track record</Link>. <Link to="/data-health">Data health</Link>.
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
