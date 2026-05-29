import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Semantic search box in the header.
 * Embedding-based — finds matches by MEANING, not keyword.
 * "treasury lead at a travel platform" → finds CFOs at booking, even
 * without those exact words in the record.
 */
export function SearchBar() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [debounced, setDebounced] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);

  // Debounce input — wait 350ms after the rep stops typing before firing.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  const { data, isFetching } = useQuery({
    queryKey: ["semantic-search", debounced],
    queryFn: () =>
      api.get<any>(`/api/search?q=${encodeURIComponent(debounced)}&limit_per_kind=3`),
    enabled: debounced.length >= 3,
  });

  // Close on outside click
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function jump(url: string) {
    navigate(url);
    setOpen(false);
    setQ("");
  }

  const results = data?.results || {};
  const hasAny =
    (results.conference?.length || 0) +
    (results.person?.length || 0) +
    (results.contact?.length || 0) > 0;

  return (
    <div className="relative" ref={wrapRef}>
      <input
        type="search"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        placeholder="Search by meaning…"
        className="input w-64 text-sm"
      />
      {open && debounced.length >= 3 && (
        <div className="absolute top-full mt-1 left-0 w-[28rem] max-h-[28rem] overflow-y-auto bg-white border border-ink-200 rounded-md shadow-lg z-20">
          {isFetching && (
            <div className="px-3 py-2 text-xs text-ink-500">Searching by meaning…</div>
          )}
          {!isFetching && !hasAny && (
            <div className="px-3 py-2 text-xs text-ink-500">No matches.</div>
          )}
          {results.conference?.length > 0 && (
            <div>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-ink-500 bg-ink-50">
                Conferences
              </div>
              {results.conference.map((c: any) => (
                <button
                  key={c.id}
                  onClick={() => jump(`/conferences/${c.id}`)}
                  className="block w-full text-left px-3 py-2 hover:bg-ink-50 text-sm border-b border-ink-100 last:border-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="badge bg-ink-100 text-ink-700">{c.tier || "?"}</span>
                    <span className="font-medium truncate">{c.name}</span>
                    <span className="text-[10px] text-ink-400 ml-auto font-mono">
                      {(c.score * 1).toFixed(0)}
                    </span>
                  </div>
                  <div className="text-xs text-ink-500">
                    {c.start_date} · {c.city} · {c.vertical}
                  </div>
                </button>
              ))}
            </div>
          )}
          {results.person?.length > 0 && (
            <div>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-ink-500 bg-ink-50">
                People
              </div>
              {results.person.map((p: any) => (
                <button
                  key={p.id}
                  onClick={() =>
                    p.conference_id
                      ? jump(`/conferences/${p.conference_id}`)
                      : jump(`/people`)
                  }
                  className="block w-full text-left px-3 py-2 hover:bg-ink-50 text-sm border-b border-ink-100 last:border-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="badge bg-emerald-50 text-emerald-700 text-[9px]">
                      {p.persona || "—"}
                    </span>
                    <span className="font-medium truncate">{p.full_name}</span>
                  </div>
                  <div className="text-xs text-ink-500">
                    {p.title || "?"} @ {p.company_name || "?"}
                  </div>
                </button>
              ))}
            </div>
          )}
          {results.contact?.length > 0 && (
            <div>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-ink-500 bg-ink-50">
                Contacts (cross-conference history)
              </div>
              {results.contact.map((ct: any) => (
                <button
                  key={ct.id}
                  onClick={() => jump(`/contacts/${ct.id}`)}
                  className="block w-full text-left px-3 py-2 hover:bg-ink-50 text-sm border-b border-ink-100 last:border-0"
                >
                  <div className="flex items-center gap-2">
                    {ct.arc_verdict && (
                      <span className="badge bg-blue-50 text-blue-700 text-[9px]">
                        {ct.arc_verdict}
                      </span>
                    )}
                    <span className="font-medium truncate">{ct.primary_name}</span>
                  </div>
                  <div className="text-xs text-ink-500">
                    {ct.primary_title || "?"} @ {ct.primary_company || "?"}
                  </div>
                </button>
              ))}
            </div>
          )}
          <div className="px-3 py-1.5 text-[9px] text-ink-400 bg-ink-50 border-t border-ink-100">
            🔮 semantic — matches by meaning, not keyword
          </div>
        </div>
      )}
    </div>
  );
}
