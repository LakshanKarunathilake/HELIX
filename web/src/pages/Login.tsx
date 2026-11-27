import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import type { Location } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { HelixLogo } from '@/components/HelixLogo'
import { AuthSpinner, useAuth } from '@/hooks/useAuth'

export function Login() {
  const { auth, user, loading, error, signInReady, prepareSignIn, signInWithGoogle } = useAuth()
  const location = useLocation()
  const [busy, setBusy] = useState(false)

  // Load Firebase now so the click can open the popup without awaiting anything first.
  useEffect(() => {
    if (!loading && auth) prepareSignIn()
  }, [loading, auth, prepareSignIn])

  if (loading) return <AuthSpinner />
  if (!auth) return <Navigate to="/" replace />
  if (user) {
    const from = (location.state as { from?: Location } | null)?.from
    return <Navigate to={from ? `${from.pathname}${from.search}` : '/'} replace />
  }

  const signIn = async () => {
    setBusy(true)
    try { await signInWithGoogle() } finally { setBusy(false) }
  }

  return (
    <div className="max-w-sm mx-auto px-6 py-24">
      <div className="card p-8 flex flex-col items-center gap-5 text-center">
        <HelixLogo className="w-14 h-14" />
        <div>
          <h1 className="text-2xl font-bold w-fit mx-auto bg-gradient-to-r from-cyan-500 via-blue-600 to-fuchsia-500 bg-clip-text text-transparent">
            HELIX
          </h1>
          <p className="text-sm text-gray-500 mt-1">Sign in to continue</p>
        </div>
        <button className="btn-primary w-full justify-center" onClick={signIn} disabled={busy || !signInReady}>
          {(busy || !signInReady) && <Loader2 className="w-4 h-4 animate-spin" />}
          {signInReady ? 'Sign in with Google' : 'Preparing sign-in…'}
        </button>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      </div>
    </div>
  )
}
