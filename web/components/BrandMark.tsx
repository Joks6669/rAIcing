export default function BrandMark() {
  // Tracé de circuit stylisé avec une voiture sur la trajectoire.
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden>
      <rect x="0.5" y="0.5" width="31" height="31" rx="8" fill="var(--bg-elev-2)" stroke="var(--border-strong)" />
      <path
        d="M8 21.5c-2.2-2.6-1.4-7.6 2.4-9.4 3-1.4 4.6.8 7 .2 2.6-.7 2.9-4 5.6-3.3 2.6.7 3 4.6 1.4 8-1.9 4-5.8 7.2-10.6 7.4-2.6.1-4.5-1-5.8-2.9z"
        fill="none"
        stroke="var(--text)"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      <rect x="20.2" y="18.6" width="5" height="2.6" rx="0.8" transform="rotate(-38 22.7 19.9)" fill="var(--accent)" />
    </svg>
  );
}
