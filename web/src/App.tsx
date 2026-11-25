import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { NavBar } from '@/components/NavBar'
import { Home } from '@/pages/Home'
import { Configure } from '@/pages/Configure'
import { Annotate } from '@/pages/Annotate'
import { Login } from '@/pages/Login'
import { AuthProvider, RequireAuth, RequireRole } from '@/hooks/useAuth'

const adminOnly = (page: JSX.Element) => (
  <RequireRole roles={['admin']} fallback={<Navigate to="/" replace />}>{page}</RequireRole>
)

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <div className="min-h-screen flex flex-col bg-gray-50">
          <NavBar />
          <main className="flex-1">
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route element={<RequireAuth />}>
                <Route path="/" element={<Home />} />
                <Route path="/configure" element={adminOnly(<Configure />)} />
                <Route path="/configure/:sessionName" element={adminOnly(<Configure />)} />
                <Route path="/annotate/:sessionName" element={<Annotate />} />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </AuthProvider>
    </BrowserRouter>
  )
}
