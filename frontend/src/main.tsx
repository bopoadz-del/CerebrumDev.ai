import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// Observability: Sentry activates only when the deployment provides a DSN.
// The dynamic import keeps it out of the bundle when unconfigured.
if (import.meta.env.VITE_SENTRY_DSN) {
  import('@sentry/react')
    .then((Sentry) => {
      Sentry.init({
        dsn: import.meta.env.VITE_SENTRY_DSN,
        environment: import.meta.env.MODE,
        release: import.meta.env.VITE_APP_VERSION as string | undefined,
        sendDefaultPii: false,
        tracesSampleRate: 0.1,
      })
    })
    .catch(() => {
      // Never let observability block the app.
    })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
