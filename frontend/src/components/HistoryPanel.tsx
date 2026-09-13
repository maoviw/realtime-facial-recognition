"use client";

import { useEffect, useMemo, useState } from "react";
import { History, Search } from "lucide-react";
import type { RecognitionEvent } from "@/lib/types";

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then) / 1000;
  if (diff < 60) return `il y a ${Math.floor(diff)} s`;
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.floor(diff / 3600)} h`;
  return new Date(iso).toLocaleDateString("fr-FR");
}

export default function HistoryPanel({
  events,
  onSearch,
}: {
  events: RecognitionEvent[];
  onSearch?: (query: string) => void;
}) {
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (!onSearch) return;
    const timer = setTimeout(() => onSearch(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [onSearch, query]);

  // Pré-calcule le libellé temporel par événement ; ne se recalcule que si la
  // liste change (et non à chaque re-render du tableau de bord parent).
  const rows = useMemo(
    () => events.map((e) => ({ event: e, ago: timeAgo(e.created_at) })),
    [events],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {onSearch && (
        <label className="relative block">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Rechercher un nom ou un événement"
            aria-label="Rechercher dans l'historique"
            className="w-full rounded-md border border-slate-700 bg-slate-900 py-2 pl-9 pr-3 text-sm text-slate-200 outline-none focus:border-amber-500"
          />
        </label>
      )}
      {events.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 text-sm text-slate-500">
          <History className="h-8 w-8 opacity-50" aria-hidden="true" />
          <p>{query ? "Aucun résultat." : "Aucun événement pour le moment."}</p>
        </div>
      ) : (
        <ul className="flex flex-col gap-2 overflow-y-auto pr-2 custom-scrollbar">
          {rows.map(({ event: e, ago }) => (
        <li
          key={e.id}
          className="flex items-center justify-between rounded-lg border border-slate-700/50 bg-slate-800/40 px-3 py-2 text-xs"
        >
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${
                e.recognized ? "bg-emerald-400" : "bg-rose-500"
              }`}
              aria-hidden="true"
            />
            <span className="font-medium text-slate-200">
              {e.recognized ? e.name || "Reconnu" : "Inconnu"}
            </span>
            <span className="font-mono text-slate-500">
              {(e.confidence * 100).toFixed(0)}%
            </span>
          </div>
          <time className="font-mono text-slate-500" dateTime={e.created_at}>
            {ago}
          </time>
        </li>
          ))}
        </ul>
      )}
    </div>
  );
}
