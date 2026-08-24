import { createContext, useContext, useState } from 'react'

const SidebarContext = createContext(null)

const STORAGE_KEY = 'f1_sidebar_collapsed'

export function SidebarProvider({ children }) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === 'true'
  )

  function toggle() {
    setCollapsed(prev => {
      const next = !prev
      localStorage.setItem(STORAGE_KEY, String(next))
      return next
    })
  }

  function forceCollapsed(value) {
    localStorage.setItem(STORAGE_KEY, String(value))
    setCollapsed(value)
  }

  return (
    <SidebarContext.Provider value={{ collapsed, toggle, setCollapsed: forceCollapsed }}>
      {children}
    </SidebarContext.Provider>
  )
}

export function useSidebar() {
  return useContext(SidebarContext)
}
