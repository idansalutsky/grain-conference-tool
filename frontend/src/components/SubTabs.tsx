import { NavLink } from "react-router-dom";

/** In-page sub-navigation for grouped sections (e.g. People → Contacts / Follow-ups). */
export function SubTabs({ items }: { items: { to: string; label: string }[] }) {
  return (
    <nav className="flex gap-1 mb-5 border-b border-ink-200">
      {items.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          end
          className={({ isActive }) =>
            "px-3 py-2 -mb-px text-sm font-semibold border-b-2 transition-colors " +
            (isActive
              ? "border-brand text-ink-900"
              : "border-transparent text-ink-500 hover:text-ink-900")
          }
        >
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}
