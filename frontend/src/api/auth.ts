/**
 * Client-side auth state for CerebrumDev.ai.
 *
 * A user login token (cdt_…) lives in localStorage. When present it wins over
 * the shared VITE_API_KEY so every request carries the caller's identity.
 */

const TOKEN_KEY = 'cerebrumdev:login-token';
const EMAIL_KEY = 'cerebrumdev:account-email';
const ACCOUNT_KEY = 'cerebrumdev:account-id';

export function getLoginToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function getAccountEmail(): string {
  return localStorage.getItem(EMAIL_KEY) || '';
}

export function saveAuth(token: string, email: string, accountId: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(EMAIL_KEY, email);
  localStorage.setItem(ACCOUNT_KEY, accountId);
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
  localStorage.removeItem(ACCOUNT_KEY);
}
