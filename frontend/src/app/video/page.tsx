"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, RefreshCw, Video } from "lucide-react";
import HlsPlayer from "@/components/HlsPlayer";

type Pilot = { id: string; updatedAt: string; finalized: boolean };

export default function VideoPage() {
  const [pilots, setPilots] = useState<Pilot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const selected = pilots.find((pilot) => pilot.id === selectedId) ?? pilots[0];

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const response = await fetch("/api/pilot-video", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(response.status === 404 ? "Lecture locale desactivee ou dossier indisponible." : "Acces video indisponible.");
        const data = await response.json();
        if (controller.signal.aborted) return;
        setPilots(data.pilots);
        setError(null);
      } catch (failure) {
        if (controller.signal.aborted) return;
        setError(failure instanceof Error ? failure.message : "Chargement impossible.");
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(load, 5000);
        }
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh]);

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 sm:py-10">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--color-border)] pb-5">
        <div className="flex min-w-0 items-center gap-3">
          <Video className="h-6 w-6 shrink-0 text-amber-400" aria-hidden="true" />
          <div>
            <p className="text-sm text-[var(--color-muted)]">Cognitive Face Live</p>
            <h1 className="text-2xl font-semibold">Video locale</h1>
          </div>
        </div>
        <Link href="/" prefetch={false} className="flex min-h-11 items-center gap-2 text-sm text-[var(--color-muted)] hover:text-white">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Reconnaissance
        </Link>
      </header>
      <div className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0">
          {selected ? (
            <HlsPlayer key={selected.id} src={`/api/pilot-video/${selected.id}/index.m3u8`} />
          ) : (
            <div className="flex aspect-video items-center justify-center bg-black px-5 text-center text-[var(--color-muted)]" role="status">
              {loading ? "Chargement..." : error ? "Video indisponible" : "Aucune capture HLS"}
            </div>
          )}
        </div>
        <aside aria-label="Captures locales" className="min-w-0">
          <div className="mb-3 flex items-center justify-between gap-2 border-b border-[var(--color-border)] pb-2">
            <h2 className="text-base font-semibold">Captures HLS</h2>
            <button
              type="button" title="Actualiser les captures" aria-label="Actualiser les captures"
              disabled={loading}
              className="flex h-11 w-11 items-center justify-center rounded-md hover:bg-[var(--color-surface-2)] focus-visible:outline-2 focus-visible:outline-amber-400 disabled:opacity-50"
              onClick={() => { setLoading(true); setRefresh(refresh + 1); }}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            </button>
          </div>
          {error && <p role="alert" className="mb-3 break-words text-sm text-rose-400">{error}</p>}
          <ul className="flex flex-col gap-2">
            {pilots.map((pilot) => (
              <li key={pilot.id}>
                <button
                  type="button" aria-pressed={selected?.id === pilot.id}
                  onClick={() => setSelectedId(pilot.id)}
                  className={`flex w-full flex-col gap-1 rounded-md border p-3 text-left text-sm focus-visible:outline-2 focus-visible:outline-amber-400 ${selected?.id === pilot.id ? "border-amber-400 bg-[var(--color-surface-2)]" : "border-[var(--color-border)] hover:bg-[var(--color-surface)]"}`}
                >
                  <span>{new Date(pilot.updatedAt).toLocaleString("fr-FR")}</span>
                  <span className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
                    <span className="font-mono">{pilot.id.slice(0, 8)}</span>
                    <span>{pilot.finalized ? "Finalise" : "Non finalise"}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </main>
  );
}