import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Bernie from './pages/Bernie'
import Championship from './pages/Championship'
import Login from './pages/Login'
import NextRace from './pages/NextRace'
import TrackRecord from './pages/TrackRecord'
import Weekend from './pages/Weekend'

/**
 * Routing.
 *
 * Almost everything here is public, which is a product decision rather than an
 * oversight: the forecasts and the record that scores them are the claim, and a
 * claim behind a login cannot be checked. Only Bernie needs an account — his
 * conversations are private and are the only thing that spends model calls.
 */
export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<NextRace />} />
        <Route path="/track-record" element={<TrackRecord />} />
        <Route path="/championship" element={<Championship />} />
        <Route path="/weekend/:season/:round" element={<Weekend />} />
        <Route path="/bernie" element={<Bernie />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Login mode="register" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
