import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, User, BookOpen, Flag, TrendingUp, Settings,
  ChevronLeft, ChevronRight, BrainCircuit
} from 'lucide-react'
import { useSidebar } from '../context/SidebarContext'

const NAV_ITEMS = [
  { to: '/dashboard',     icon: LayoutDashboard, label: 'Dashboard'         },
  { to: '/driver',        icon: User,            label: 'Driver Focus'      },
  { to: '/season-review', icon: BookOpen,        label: 'Season in Review'  },
  { to: '/race-rewind',   icon: Flag,            label: 'Race Rewind'       },
  { to: '/race-predict',  icon: TrendingUp,      label: 'Race Predict'      },
  { to: '/bernie',        icon: BrainCircuit,    label: 'Ask Bernie'        },
  { to: '/preferences',   icon: Settings,        label: 'Preferences'       },
]

export default function Sidebar() {
  const { collapsed, toggle } = useSidebar()
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768)

  useEffect(() => {
    function onResize() { setIsMobile(window.innerWidth <= 768) }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  return (
    <>
      {isMobile && !collapsed && (
        <div className="sidebar-overlay" onClick={toggle} />
      )}
      <nav className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`}>
        <button className="sidebar__toggle" onClick={toggle} title={collapsed ? 'Expand' : 'Collapse'}>
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
        <div className="sidebar__nav">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `sidebar__link${isActive ? ' sidebar__link--active' : ''}`
              }
              title={collapsed ? label : undefined}
            >
              <Icon size={18} className="sidebar__icon" />
              <span className="sidebar__label">{label}</span>
            </NavLink>
          ))}
        </div>
      </nav>
    </>
  )
}
