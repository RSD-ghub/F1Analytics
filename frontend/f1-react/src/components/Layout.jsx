import { useEffect, useState, useSyncExternalStore } from 'react'
import { NavLink, Link, useLocation } from 'react-router-dom'
import {
  BookOpen, Flag, House, Menu, MessageCircle, Newspaper, Route, Target, Trophy, X,
} from 'lucide-react'
import { useAuth } from '../context/useAuth'
import { Button } from '@/components/ui/button'

/* Every section, in the order a race weekend is read: what's next, what was
   said, what happened, how the record stands. The sidebar lists them all; the
   phone tab bar carries the five a reader reaches for most. */
const LINKS = [
  { to: '/', label: 'Home', icon: House, end: true, tab: true },
  { to: '/next-race', label: 'Next race', icon: Flag, tab: true },
  { to: '/championship', label: 'Championship', short: 'Standings', icon: Trophy, tab: true },
  { to: '/news', label: 'Paddock news', short: 'News', icon: Newspaper, tab: true },
  { to: '/blog', label: 'One Blog', icon: BookOpen },
  { to: '/track-record', label: 'Track record', icon: Target },
  { to: '/strategy', label: 'Strategy', icon: Route },
  { to: '/bernie', label: 'Bernie', icon: MessageCircle, tab: true },
]

/* Wide screens keep the sidebar open as a rail; narrower ones slide it in. */
const WIDE = '(min-width: 1024px)'
function subscribe(onChange) {
  const mq = window.matchMedia(WIDE)
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}
function useWide() {
  return useSyncExternalStore(subscribe, () => window.matchMedia(WIDE).matches, () => true)
}

function Brand() {
  return (
    <Link to="/" className="brand" aria-label="F1 Forecast, home">
      <span className="brand-mark" aria-hidden="true">F</span>
      <span className="brand-word">F1 <em>Forecast</em></span>
    </Link>
  )
}

export default function Layout({ children }) {
  const { auth, logout } = useAuth()
  const { pathname } = useLocation()
  const wide = useWide()
  const [menuOpen, setMenuOpen] = useState(false)
  const drawerOpen = wide || menuOpen

  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuOpen])

  const navClass = ({ isActive }) => (isActive ? 'active' : '')

  return (
    <div className="app">
      <div className={`sidebar-scrim${menuOpen && !wide ? ' open' : ''}`} aria-hidden="true"
           onClick={() => setMenuOpen(false)} />
      <aside id="sidebar" className={`sidebar${drawerOpen ? ' open' : ''}`}
             aria-label="Site menu" inert={!drawerOpen}>
        <div className="sidebar-head">
          <Brand />
          {!wide && (
            <button type="button" className="menu-toggle" aria-label="Close menu"
                    onClick={() => setMenuOpen(false)}>
              <X size={20} aria-hidden="true" />
            </button>
          )}
        </div>
        <nav>
          {LINKS.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} onClick={() => setMenuOpen(false)} className={navClass}>
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="app-main">
        <header className="masthead">
          {!wide && (
            <button type="button" className="menu-toggle"
                    aria-label="Open menu" aria-expanded={menuOpen} aria-controls="sidebar"
                    onClick={() => setMenuOpen(true)}>
              <Menu size={20} aria-hidden="true" />
            </button>
          )}
          {!wide && <Brand />}
          <div className="account">
            {auth?.token ? (
              <>
                <span className="account-name">{auth.username}</span>
                <Button variant="outline" size="sm" onClick={logout}>Sign out</Button>
              </>
            ) : (
              <Button asChild size="sm"><Link to="/login">Sign in</Link></Button>
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

      {/* Bernie is a page of her own; the button is a shortcut to it from
          anywhere else. On a phone she is in the tab bar instead. */}
      {pathname !== '/bernie' && (
        <Link to="/bernie" className="bernie-fab" aria-label="Ask Bernie" title="Ask Bernie">
          <MessageCircle size={24} aria-hidden="true" />
        </Link>
      )}

      <nav className="tabbar" aria-label="Main sections">
        {LINKS.filter((l) => l.tab).map(({ to, label, short, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={navClass}>
            <span className="tabbar-icon"><Icon size={20} aria-hidden="true" /></span>
            <span className="tabbar-label">{short || label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
