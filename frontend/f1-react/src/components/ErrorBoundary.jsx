import { Component } from 'react'
import { AlertCircle } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'

/**
 * Contains a rendering crash to the page it happened on.
 *
 * Without one, a single malformed field from an API (a null where a list was
 * expected) unmounts the whole app and leaves a blank white screen with the
 * navigation gone, so the reader cannot even move to a page that works. The
 * boundary is keyed on the route in App, so navigating away clears it.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Page crashed', error, info?.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <Alert variant="destructive" role="alert">
        <AlertCircle />
        <AlertTitle>This page hit a problem.</AlertTitle>
        <AlertDescription>
          <p>
            Something in the data could not be displayed. The rest of the site
            still works: use the menu to carry on, or reload to try again.
          </p>
          <button type="button" className="link" onClick={() => window.location.reload()}>
            Reload the page
          </button>
        </AlertDescription>
      </Alert>
    )
  }
}
