import { Link, useLocation } from 'react-router-dom'
import { Link2, Settings, PenLine } from 'lucide-react'

export function NavBar() {
  const { pathname } = useLocation()
  const nav = [
    { to: '/', label: 'Sessions', icon: Link2 },
    { to: '/configure', label: 'Configure', icon: Settings },
  ]

  return (
    <header className="sticky top-0 z-40 bg-white border-b border-gray-200 shadow-sm">
      <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-2 font-bold text-brand-600 text-lg tracking-tight">
          <PenLine className="w-5 h-5" />
          ELTool
        </Link>
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
        </nav>
      </div>
    </header>
  )
}
