import { Link, useLocation } from 'react-router-dom'
import { Link2, LogOut, Settings } from 'lucide-react'
import { HelixLogo } from './HelixLogo'
import { useAuth } from '@/hooks/useAuth'

export function NavBar() {
  const { pathname } = useLocation()
  const { auth, user, isAdmin, logout } = useAuth()
  const nav = [
    { to: '/', label: 'Sessions', icon: Link2 },
    ...(isAdmin ? [{ to: '/configure', label: 'Configure', icon: Settings }] : []),
  ]
  // With auth on, the links and account block only make sense once signed in.
  const signedOut = auth && !user

  return (
    <header className="sticky top-0 z-40 bg-white border-b border-gray-200 shadow-sm">
      <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
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
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  pathname === to
                    ? 'bg-brand-50 text-brand-700'
                    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                }`}
              >
                <Icon className="w-4 h-4" />
                {label}
              </Link>
            ))}
            {user && (
              <div className="flex items-center gap-2 ml-3 pl-3 border-l border-gray-200">
                <span className="text-sm font-medium text-gray-700">{user.name}</span>
                <span className="text-[10px] font-semibold uppercase tracking-wide text-brand-700 bg-brand-50 border border-brand-200 rounded-full px-2 py-0.5">
                  {user.role}
                </span>
                <button
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors"
                  onClick={logout}
                >
                  <LogOut className="w-4 h-4" />
                  Sign out
                </button>
              </div>
            )}
          </nav>
        )}
      </div>
    </header>
  )
}
