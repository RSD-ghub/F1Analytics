import { useState } from 'react'
import { useSidebar } from '../context/SidebarContext'
import Layout from '../components/Layout'

const PREFS_KEY = 'f1_preferences'

function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY)) ?? {}
  } catch { return {} }
}

export default function Preferences() {
  const { setCollapsed } = useSidebar()
  const [prefs, setPrefs] = useState(loadPrefs)
  const [saved, setSaved] = useState(false)

  function set(key, value) {
    setPrefs(p => ({ ...p, [key]: value }))
    setSaved(false)
  }

  function handleSave() {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
    if (prefs.sidebarDefaultCollapsed !== undefined) {
      setCollapsed(!!prefs.sidebarDefaultCollapsed)
    }
    setSaved(true)
    setTimeout(() => setSaved(false), 2500)
  }

  return (
    <Layout>
      <div className="page-preferences">
        <h1>Preferences</h1>
        <div className="preferences-card">

          <div className="preferences-section">
            <p className="preferences-section__title">Sidebar</p>
            <label className="preferences-row">
              <input
                type="checkbox"
                checked={!!prefs.sidebarDefaultCollapsed}
                onChange={e => set('sidebarDefaultCollapsed', e.target.checked)}
              />
              Start collapsed by default
            </label>
          </div>

          <div className="preferences-section">
            <p className="preferences-section__title">Data</p>
            <div className="preferences-row">
              <label htmlFor="defaultSeason">Default season</label>
              <input
                id="defaultSeason"
                className="input-text"
                type="number"
                min="2010"
                max={new Date().getFullYear()}
                value={prefs.defaultSeason ?? ''}
                onChange={e => set('defaultSeason', e.target.value)}
                placeholder="e.g. 2024"
                style={{ width: 100 }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button className="btn btn--primary" onClick={handleSave}>
              Save Preferences
            </button>
            {saved && (
              <span style={{ color: 'var(--teal)', fontSize: '0.85rem', fontWeight: 600 }}>
                ✓ Saved
              </span>
            )}
          </div>
        </div>
      </div>
    </Layout>
  )
}
