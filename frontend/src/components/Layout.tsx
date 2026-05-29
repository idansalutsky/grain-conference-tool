import { ReactNode, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

// One tab per moment in the rep's day: Decide → Plan → Capture → Recognise → Act.
const TABS = [
  { to: "/today", label: "Today" },
  { to: "/conferences", label: "Events" },
  { to: "/planning", label: "Planning" },
  { to: "/capture", label: "Capture" },
  { to: "/contacts", label: "Contacts" },
  { to: "/nudges", label: "Nudges" },
  { to: "/discovery", label: "Discovery" },
  { to: "/team", label: "Team" },
  { to: "/settings", label: "Settings" },
];

function Wordmark() {
  return (
    <Link to="/today" className="flex items-center gap-2.5 shrink-0 group">
      <span
        className="grid place-items-center w-7 h-7 rounded-md text-white text-base font-bold"
        style={{ background: "oklch(0.34 0.06 164)" }}
        aria-hidden
      >
        ⌾
      </span>
      <span className="leading-none">
        <span className="masthead block text-[1.05rem] text-ink-900">Grain</span>
        <span className="block text-[0.6rem] uppercase tracking-[0.2em] text-ink-500 -mt-0.5">
          Conference Intel
        </span>
      </span>
    </Link>
  );
}

function navClass({ isActive }: { isActive: boolean }) {
  return (
    "px-3 py-1.5 rounded-md text-sm font-semibold transition-colors " +
    (isActive ? "bg-ink-900 text-white" : "text-ink-500 hover:text-ink-900 hover:bg-ink-100")
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const loc = useLocation();

  return (
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-20 bg-paper/85 backdrop-blur border-b border-ink-200">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-6">
          <Wordmark />
          {/* Desktop nav */}
          <nav className="hidden md:flex items-center gap-0.5 flex-wrap">
            {TABS.map((t) => (
              <NavLink key={t.to} to={t.to} className={navClass}>
                {t.label}
              </NavLink>
            ))}
          </nav>
          {/* Mobile toggle */}
          <button
            onClick={() => setOpen((v) => !v)}
            className="md:hidden ml-auto btn-ghost h-9 w-9 !px-0"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
          >
            <span className="text-lg">{open ? "✕" : "☰"}</span>
          </button>
        </div>
        {/* Mobile nav sheet */}
        {open && (
          <nav className="md:hidden border-t border-ink-200 bg-surface px-3 py-2 grid grid-cols-2 gap-1">
            {TABS.map((t) => (
              <NavLink
                key={t.to}
                to={t.to}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  "px-3 py-2.5 rounded-md text-sm font-semibold " +
                  (isActive ? "bg-ink-900 text-white" : "text-ink-700 hover:bg-ink-100")
                }
              >
                {t.label}
              </NavLink>
            ))}
          </nav>
        )}
      </header>

      <main key={loc.pathname} className="flex-1 max-w-6xl w-full mx-auto px-4 py-6 sm:py-8 rise">
        {children}
      </main>

      <footer className="border-t border-ink-200 mt-8">
        <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between text-xs text-ink-500">
          <span>Grain · Conference Intelligence</span>
          <span className="hidden sm:inline tracking-wide uppercase text-[0.65rem]">
            Decide · Plan · Capture · Recognise · Act
          </span>
        </div>
      </footer>
    </div>
  );
}
