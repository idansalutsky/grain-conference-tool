import { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

// One tab per moment in the rep's day: Decide → Plan → Capture → Recognise →
// Act. (Company drill-down lives behind a contact/target, not as its own tab.)
const TABS = [
  { to: "/today", label: "Today" },
  { to: "/conferences", label: "Conferences" },
  { to: "/planning", label: "Planning" },
  { to: "/capture", label: "Capture" },
  { to: "/contacts", label: "Contacts" },
  { to: "/nudges", label: "Nudges" },
  { to: "/discovery", label: "Discovery" },
  { to: "/settings", label: "Settings" },
];

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-ink-200 bg-white sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-6">
          <Link to="/today" className="font-bold text-lg text-ink-900">
            <span className="text-brand">Grain</span> Conference Intel
          </Link>
          <nav className="flex gap-1 text-sm">
            {TABS.map((t) => (
              <NavLink
                key={t.to}
                to={t.to}
                className={({ isActive }) =>
                  "px-3 py-1.5 rounded-md font-medium " +
                  (isActive
                    ? "bg-ink-100 text-ink-900"
                    : "text-ink-500 hover:text-ink-900 hover:bg-ink-50")
                }
              >
                {t.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-6">
        {children}
      </main>
      <footer className="border-t border-ink-200 bg-white text-center text-xs text-ink-500 py-3">
        Grain Conference Intelligence — built for the brief
      </footer>
    </div>
  );
}
