import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import * as api from '@/api/client'
import type { Role, User } from '@/types'

interface AuthContextValue {
  /** True when the server runs with HELIX_AUTH=1. When false nothing here applies. */
  auth: boolean
  user: User | null
  /** True while the first /info + /auth/me check is in flight. */
  loading: boolean
  /** True when auth is off (today's behaviour) or the signed-in user is an admin. */
  isAdmin: boolean
  signInWithGoogle: () => Promise<void>
  logout: () => Promise<void>
  error: string | null
}

const AuthContext = createContext<AuthContextValue | null>(null)

const NOT_CONFIGURED = 'Sign-in is not configured on this server'
// Firebase codes for a popup the user dismissed: not worth an error message.
const POPUP_DISMISSED = ['auth/popup-closed-by-user', 'auth/cancelled-popup-request']

/** Runs the Google popup and returns a Firebase ID token, or null if the server has no Firebase config. */
async function googleIdToken(): Promise<string | null> {
  const { firebase } = await api.getInfo()
  if (!firebase) return null
  const [{ getApp, getApps, initializeApp }, { GoogleAuthProvider, getAuth, signInWithPopup, signOut }] =
    await Promise.all([import('firebase/app'), import('firebase/auth')])
  const app = getApps().length ? getApp() : initializeApp(firebase)
  const auth = getAuth(app)
  try {
    const { user } = await signInWithPopup(auth, new GoogleAuthProvider())
    return await user.getIdToken()
  } finally {
    // HELIX's own session cookie takes over from here; keep no Firebase state.
    await signOut(auth).catch(() => {})
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [auth, setAuth] = useState(false)
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // Any later 401 means the session ended: drop the user and let RequireAuth redirect.
    api.onUnauthorized(() => setUser(null))
    api.getInfo()
      .then(async info => {
        if (cancelled) return
        setAuth(info.auth)
        if (info.auth) {
          try { setUser(await api.me()) } catch { setUser(null) }
        }
      })
      // Server unreachable: behave as today (pages show their own connection errors).
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true; api.onUnauthorized(null) }
  }, [])

  const signInWithGoogle = useCallback(async () => {
    setError(null)
    try {
      const idToken = await googleIdToken()
      if (!idToken) { setError(NOT_CONFIGURED); return }
      const signedIn = await api.sessionLogin(idToken)
      qc.clear()
      setUser(signedIn)
    } catch (e) {
      const code = (e as { code?: string }).code
      if (code && POPUP_DISMISSED.includes(code)) return
      setError(e instanceof api.ApiError ? e.message : 'Google sign-in failed. Please try again.')
    }
  }, [qc])

  const logout = useCallback(async () => {
    try { await api.logout() } catch { /* cookie may already be gone */ }
    qc.clear()
    setUser(null)
  }, [qc])

  const value = useMemo<AuthContextValue>(
    () => ({
      auth, user, loading, error, signInWithGoogle, logout,
      isAdmin: !auth || user?.role === 'admin',
    }),
    [auth, user, loading, error, signInWithGoogle, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export function AuthSpinner() {
  return (
    <div className="flex items-center justify-center py-32 text-gray-400">
      <Loader2 className="w-6 h-6 animate-spin" />
    </div>
  )
}

/** Layout route: signed-in users (or any visitor when auth is off) see the child routes. */
export function RequireAuth() {
  const { auth, user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <AuthSpinner />
  if (auth && !user) return <Navigate to="/login" replace state={{ from: location }} />
  return <Outlet />
}

/** Renders `children` only for the given roles (always, when auth is off). */
export function RequireRole({ roles, fallback = null, children }: {
  roles: Role[]
  fallback?: ReactNode
  children: ReactNode
}) {
  const { auth, user } = useAuth()
  if (auth && !(user && roles.includes(user.role))) return <>{fallback}</>
  return <>{children}</>
}
