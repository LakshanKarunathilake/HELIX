// HELIX brand mark — a DNA double-helix set inside a light network graph,
// with the cyan→blue→magenta gradient. Rebuilt as inline SVG so it stays crisp
// at every size (header, favicon, print). `network` toggles the node halo.
export function HelixLogo({ className = 'w-6 h-6', network = true }: { className?: string; network?: boolean }) {
  return (
    <svg viewBox="0 0 64 64" className={className} fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="helixGrad" x1="32" y1="6" x2="32" y2="58" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#22d3ee" />
          <stop offset="0.35" stopColor="#3b82f6" />
          <stop offset="0.7" stopColor="#a855f7" />
          <stop offset="1" stopColor="#ec4899" />
        </linearGradient>
      </defs>

      {/* network halo */}
      {network && (
        <g stroke="url(#helixGrad)" strokeWidth="1" opacity="0.35">
          <path d="M9 22 L15 31 M15 31 L7 40 M7 40 L16 46" strokeLinecap="round" />
          <path d="M55 24 L49 32 M49 32 L57 41 M57 41 L48 46" strokeLinecap="round" />
          <g fill="url(#helixGrad)" stroke="none">
            <circle cx="9" cy="22" r="2.2" /><circle cx="15" cy="31" r="1.8" />
            <circle cx="7" cy="40" r="2.2" /><circle cx="16" cy="46" r="1.6" />
            <circle cx="55" cy="24" r="2.2" /><circle cx="49" cy="32" r="1.8" />
            <circle cx="57" cy="41" r="2.2" /><circle cx="48" cy="46" r="1.6" />
          </g>
        </g>
      )}

      {/* base-pair rungs */}
      <g stroke="url(#helixGrad)" strokeWidth="2.6" strokeLinecap="round" opacity="0.9">
        <line x1="22" y1="14" x2="42" y2="14" />
        <line x1="22" y1="26" x2="42" y2="26" />
        <line x1="22" y1="38" x2="42" y2="38" />
        <line x1="22" y1="50" x2="42" y2="50" />
      </g>

      {/* two strands */}
      <g stroke="url(#helixGrad)" strokeWidth="3.4" strokeLinecap="round" fill="none">
        <path d="M32 8 C32 11 43 11 43 14 C43 20 21 20 21 26 C21 32 43 32 43 38 C43 44 21 44 21 50 C21 53 32 53 32 56" />
        <path d="M32 8 C32 11 21 11 21 14 C21 20 43 20 43 26 C43 32 21 32 21 38 C21 44 43 44 43 50 C43 53 32 53 32 56" />
      </g>
    </svg>
  )
}
