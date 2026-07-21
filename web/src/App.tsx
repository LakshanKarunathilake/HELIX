import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { NavBar } from '@/components/NavBar'
import { Home } from '@/pages/Home'
import { Configure } from '@/pages/Configure'
import { Annotate } from '@/pages/Annotate'

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col bg-gray-50">
        <NavBar />
        <main className="flex-1">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/configure" element={<Configure />} />
            <Route path="/configure/:sessionName" element={<Configure />} />
            <Route path="/annotate/:sessionName" element={<Annotate />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
