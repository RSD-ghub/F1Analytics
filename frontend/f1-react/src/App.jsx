import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import Layout from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'
import Bernie from './pages/Bernie'
import Strategy from './pages/Strategy'
import Championship from './pages/Championship'
import Login from './pages/Login'
import News from './pages/News'
import NextRace from './pages/NextRace'
import TrackRecord from './pages/TrackRecord'
import Weekend from './pages/Weekend'
import Blog from './pages/Blog'
import Home from './pages/Home'
import Drivers from './pages/Drivers'
import Driver from './pages/Driver'
import RaceReview from './pages/RaceReview'

import DataHealth from './pages/DataHealth'

/**
 * Routing.
 *
 * Almost everything here is public, which is a product decision rather than an
 * oversight: the forecasts and the record that scores them are the claim, and a
 * claim behind a login cannot be checked. Only Bernie needs an account — her
 * conversations are private and are the only thing that spends model calls.
 */
export default function App() {
  const { pathname } = useLocation()
  return (
    <Layout>
      {/* Keyed on the path, so a crash on one page clears on navigating away. */}
      <ErrorBoundary key={pathname}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/next-race" element={<NextRace />} />
        <Route path="/track-record" element={<TrackRecord />} />
        <Route path="/championship" element={<Championship />} />
        <Route path="/blog" element={<Blog />} />
        <Route path="/blog/:season/:round" element={<Weekend />} />
        {/* The entries moved under /blog when One Blog got a front door.
            Old links stay working. */}
        <Route path="/weekend/:season/:round" element={<WeekendRedirect />} />
        <Route path="/drivers" element={<Drivers />} />
        <Route path="/drivers/:season/:driver" element={<Driver />} />
        <Route path="/race-review/:season/:round" element={<RaceReview />} />

        <Route path="/data-health" element={<DataHealth />} />
        <Route path="/news" element={<News />} />
        <Route path="/strategy" element={<Strategy />} />
        <Route path="/bernie" element={<Bernie />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Login mode="register" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </ErrorBoundary>
    </Layout>
  )
}


/** Old /weekend/... links, kept working after One Blog got its own path. */
function WeekendRedirect() {
  const { season, round } = useParams()
  return <Navigate to={`/blog/${season}/${round}`} replace />
}
