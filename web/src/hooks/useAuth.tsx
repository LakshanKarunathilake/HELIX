import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import * as api from '@/api/client'
import type { FirebaseWebConfig, Role, User } from '@/types'

interface AuthContextValue {
  /** True when the server runs with HELIX_AUTH=1. When false nothing here applies. */
  auth: boolean
  user: User | null
  /** True while the first /info + /auth/me check is in flight. */
  loading: boolean
  /** True when auth is off (today's behaviour) or the signed-in user is an admin. */
  isAdmin: boolean
  /** False until the Google popup machinery is loaded; clicking earlier would lose the user gesture. */
  signInReady: boolean
  /** Loads Firebase ahead of the click (call when the Login page mounts). Safe to call repeatedly. */
  prepareSignIn: () => void
  signInWithGoogle: () => Promise<void>
  logout: () => Promise<void>
  error: string | null
}

type FirebaseAuthModule = typeof import('firebase/auth')

/** Everything the click handler needs, resolved ahead of time. */
interface PreparedGoogleSignIn {
  auth: import('firebase/auth').Auth
  provider: import('firebase/auth').GoogleAuthProvider
  signInWithPopup: FirebaseAuthModule['signInWithPopup']
  signOut: FirebaseAuthModule['signOut']
}

const AuthContext = createContext<AuthContextValue | null>(null)

const NOT_CONFIGURED = 'Sign-in is not configured on this server'
const POPUP_BLOCKED = 'Allow pop-ups for this site and try again.'
const PRELOAD_FAILED = 'Could not load Google sign-in. Reload the page and try again.'
// Firebase codes for a popup the user dismissed: not worth an error message.
const POPUP_DISMISSED = ['auth/popup-closed-by-user', 'auth/cancelled-popup-request']

async function loadGoogleSignIn(config: FirebaseWebConfig): Promise<PreparedGoogleSignIn> {
  const [{ getApp, getApps, initializeApp }, fbAuth] =
    await Promise.all([import('firebase/app'), import('firebase/auth')])
  const app = getApps().length ? getApp() : initializeApp(config)
  return {
    auth: fbAuth.getAuth(app),
    provider: new fbAuth.GoogleAuthProvider(),
    signInWithPopup: fbAuth.signInWithPopup,
    signOut: fbAuth.signOut,
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [auth, setAuth] = useState(false)
  const [firebase, setFirebase] = useState<FirebaseWebConfig | null>(null)
  const [prepared, setPrepared] = useState<PreparedGoogleSignIn | null>(null)
  const preparing = useRef(false)
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
        setFirebase(info.firebase)
        if (info.auth) {
          try { setUser(await api.me()) } catch { setUser(null) }
        }
      })
      // Server unreachable: behave as today (pages show their own connection errors).
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true; api.onUnauthorized(null) }
  }, [])

  const prepareSignIn = useCallback(() => {
    if (!firebase || preparing.current) return
    preparing.current = true
    loadGoogleSignIn(firebase)
      .then(setPrepared)
      .catch(() => { preparing.current = false; setError(PRELOAD_FAILED) })
  }, [firebase])

  // Nothing to load when the server has no Firebase config: the click then explains why.
  const signInReady = !firebase || prepared !== null

  // No `await` may run before signInWithPopup: the popup needs the click's user activation
  // (Safari blocks it after an async gap).
  const signInWithGoogle = useCallback(async () => {
    setError(null)
    if (!firebase) { setError(NOT_CONFIGURED); return }
    if (!prepared) return
    try {
      const { user: fbUser } = await prepared.signInWithPopup(prepared.auth, prepared.provider)
      const signedIn = await api.sessionLogin(await fbUser.getIdToken())
      qc.clear()
      setUser(signedIn)
    } catch (e) {
      const code = (e as { code?: string }).code
      if (code && POPUP_DISMISSED.includes(code)) return
      if (code === 'auth/popup-blocked') { setError(POPUP_BLOCKED); return }
      setError(e instanceof api.ApiError ? e.message : 'Google sign-in failed. Please try again.')
    } finally {
      // HELIX's own session cookie takes over; keep no Firebase state.
      await prepared.signOut(prepared.auth).catch(() => {})
    }
  }, [firebase, prepared, qc])

  const logout = useCallback(async () => {
    try { await api.logout() } catch { /* cookie may already be gone */ }
    // Leave first, without a `from` target, so the next user is not sent to this user's page.
    navigate('/login', { replace: true })
    qc.clear()
    setUser(null)
  }, [navigate, qc])

  const value = useMemo<AuthContextValue>(
    () => ({
      auth, user, loading, error, signInReady, prepareSignIn, signInWithGoogle, logout,
      isAdmin: !auth || user?.role === 'admin',
    }),
    [auth, user, loading, error, signInReady, prepareSignIn, signInWithGoogle, logout],
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
