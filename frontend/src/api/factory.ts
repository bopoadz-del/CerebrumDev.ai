/**
 * CerebrumDev factory console API client.
 * Real backend only: per-account login tokens (cdt_...), SSE chat,
 * product design state, product package export, billing status.
 */

const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') || ''

const TOKEN_KEY = 'cerebrum.factory.token'
const EMAIL_KEY = 'cerebrum.factory.email'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 403 from credential-gated routes when ACCOUNTS_REQUIRE_VERIFIED_EMAIL=1. */
export function isEmailNotVerifiedError(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 403) return false
  return err.message === 'email_not_verified' || err.message.includes('email_not_verified')
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function getEmail(): string | null {
  return localStorage.getItem(EMAIL_KEY)
}
export function setSession(token: string, email: string): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(EMAIL_KEY, email)
}
export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(EMAIL_KEY)
}
