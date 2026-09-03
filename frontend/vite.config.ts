/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Local Factory Floor talks to the API at same-origin `/v1` when
  // VITE_API_URL is unset. Without this proxy the UI boots and every
  // session/chat/product call 404s on Vite itself.
  server: {
    proxy: {
      '/v1': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/ready': 'http://127.0.0.1:8000',
    },
    headers: {
      'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'DENY',
      'Referrer-Policy': 'no-referrer',
      'Content-Security-Policy':
        "default-src 'self'; frame-ancestors 'none'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss: http://127.0.0.1:8000 http://localhost:8000",
    },
  },
  preview: {
    headers: {
      'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'DENY',
      'Referrer-Policy': 'no-referrer',
      'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
      'Content-Security-Policy':
        "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; connect-src 'self' https://api.cerebrum-dev.com https://*.ingest.sentry.io; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'",
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: false,
    exclude: ['**/node_modules/**', '**/dist/**', '**/e2e/**'],
  },
})
