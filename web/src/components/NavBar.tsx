import { Link, useLocation } from 'react-router-dom'
import { Link2, LogOut, Settings, Users } from 'lucide-react'
import { HelixLogo } from './HelixLogo'
import { useAuth } from '@/hooks/useAuth'

export function NavBar() {
  const { pathname } = useLocation()
  const { auth, user, loading, isAdmin, logout } = useAuth()
  const nav = [
    { to: '/', label: 'Sessions', icon: Link2 },
    ...(isAdmin ? [{ to: '/configure', label: 'Configure', icon: Settings }] : []),
    // Accounts only exist when auth is on; `isAdmin` alone is also true with auth off.
    ...(auth && isAdmin ? [{ to: '/admin/users', label: 'Users', icon: Users }] : []),
  ]
  // Links and account block wait for the auth check and, with auth on, for a signed-in user.
  const signedOut = loading || (auth && !user)
  // Signed in, the account block makes the bar too wide for a phone, so below `sm` it
  // is tighter and the links show icons only (their aria-labels keep them named).
  const sidePadding = user ? 'px-3 sm:px-6' : 'px-6'
  const itemPadding = user ? 'px-2 py-2 sm:px-3 sm:py-1.5' : 'px-3 py-1.5'
  const itemText = user ? 'hidden sm:inline' : undefined

  return (
    <header className="sticky top-0 z-40 bg-white border-b border-gray-200 shadow-sm">
      <div className={`max-w-7xl mx-auto ${sidePadding} h-14 flex items-center justify-between`}>
        <Link to="/" className="flex items-center gap-2 font-extrabold text-lg tracking-tight">
          <HelixLogo className="w-7 h-7" />
          <span className="bg-gradient-to-r from-cyan-500 via-blue-600 to-fuchsia-500 bg-clip-text text-transparent">HELIX</span>
        </Link>
        {!signedOut && (
          <nav className="flex items-center gap-1">
            {nav.map(({ to, label, icon: Icon }) => (
              <Link
                key={to}
                to={to}
                aria-label={label}
                className={`flex items-center gap-1.5 ${itemPadding} rounded-lg text-sm font-medium transition-colors ${
                  pathname === to
                    ? 'bg-brand-50 text-brand-700'
                    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className={itemText}>{label}</span>
              </Link>
            ))}
            {user && (
              <div className="flex items-center gap-1 ml-1 pl-2 sm:gap-2 sm:ml-3 sm:pl-3 border-l border-gray-200">
                <span className="hidden sm:inline text-sm font-medium text-gray-700">{user.name}</span>
                <span className="text-[10px] font-semibold uppercase tracking-wide text-brand-700 bg-brand-50 border border-brand-200 rounded-full px-2 py-0.5">
                  {user.role}
                </span>
                <button
                  className="flex items-center gap-1.5 px-2 py-2 sm:px-3 sm:py-1.5 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors"
                  onClick={logout}
                  aria-label="Sign out"
                >
                  <LogOut className="w-4 h-4" />
                  <span className="hidden sm:inline">Sign out</span>
                </button>
              </div>
            )}
          </nav>
        )}
      </div>
    </header>
  )
}
