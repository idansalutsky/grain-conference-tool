import { ReactNode, useState } from "react";
import { Link, useLocation } from "react-router-dom";

// Six grouped destinations along the rep's day. `match` = path prefixes that
// keep the tab lit (e.g. Events stays active on the Find-new sub-page).
const TABS = [
  { to: "/today", label: "Dashboard", match: ["/today"] },
  { to: "/brain", label: "Brain", match: ["/brain"] },
  { to: "/conferences", label: "Events", match: ["/conferences", "/discovery"] },
  { to: "/planning", label: "Calendar", match: ["/planning"] },
  { to: "/capture", label: "Capture", match: ["/capture"] },
  { to: "/contacts", label: "People", match: ["/contacts", "/nudges", "/companies"] },
  { to: "/team", label: "Admin", match: ["/team", "/settings"] },
];

function isGroupActive(match: string[], path: string): boolean {
  return match.some((m) => path === m || path.startsWith(m + "/"));
}

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

function navClass(active: boolean) {
  return (
    "px-3 py-1.5 rounded-md text-sm font-semibold transition-colors " +
    (active ? "bg-ink-900 text-white" : "text-ink-500 hover:text-ink-900 hover:bg-ink-100")
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
          <nav className="hidden md:flex items-center gap-0.5">
            {TABS.map((t) => (
              <Link key={t.to} to={t.to} className={navClass(isGroupActive(t.match, loc.pathname))}>
                {t.label}
              </Link>
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
            {TABS.map((t) => {
              const active = isGroupActive(t.match, loc.pathname);
              return (
                <Link
                  key={t.to}
                  to={t.to}
                  onClick={() => setOpen(false)}
                  className={
                    "px-3 py-2.5 rounded-md text-sm font-semibold " +
                    (active ? "bg-ink-900 text-white" : "text-ink-700 hover:bg-ink-100")
                  }
                >
                  {t.label}
                </Link>
              );
            })}
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
