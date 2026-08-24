import { useNavigate } from 'react-router-dom'
import Sidebar from './Sidebar'
import { useAuth } from '../context/AuthContext'

export default function Layout({ children }) {
  const navigate        = useNavigate()
  const { auth, logout } = useAuth()

  function signOut() {
    logout()
    navigate('/')
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header__brand">
          <span className="app-header__logo">F1</span>
          <span className="app-header__title">Formula 1 Analytics Dashboard</span>
        </div>
        {auth && (
          <div className="app-header__user">
            <span>{auth.username}</span>
            <button
              className="btn btn--secondary"
              onClick={signOut}
              style={{ padding: '5px 10px' }}
            >
              Sign Out
            </button>
          </div>
        )}
      </header>

      <div className="app-body">
        <Sidebar />
        <div className="app-content">
          <main className="app-main">{children}</main>
          <footer className="app-footer">
            <a href="https://www.formula1.com" target="_blank" rel="noreferrer">Formula 1</a>
            <a href="https://www.statsf1.com"  target="_blank" rel="noreferrer">Stats F1</a>
            <a href="https://ergast.com/mrd"   target="_blank" rel="noreferrer">Ergast API</a>
          </footer>
        </div>
      </div>
    </div>
  )
}
