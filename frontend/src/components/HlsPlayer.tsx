"use client";

import { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import { RotateCcw } from "lucide-react";

export default function HlsPlayer({ src }: { src: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [status, setStatus] = useState("Chargement...");
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let hls: Hls | undefined;
    let cancelled = false;
    const fail = () => {
      if (cancelled) return;
      setError(true);
      setStatus("Lecture indisponible");
    };
    if (Hls.isSupported()) {
      hls = new Hls({ maxBufferLength: 12, backBufferLength: 30 });
      hls.on(Hls.Events.MEDIA_ATTACHED, () => {
        if (!cancelled) hls?.loadSource(src);
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          fail();
          hls?.destroy();
        }
      });
      hls.attachMedia(video);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src;
    } else {
      queueMicrotask(fail);
    }
    return () => {
      cancelled = true;
      hls?.destroy();
      video.pause();
      video.removeAttribute("src");
      video.load();
    };
  }, [src, attempt]);

  return (
    <section aria-label="Lecteur HLS" className="min-w-0">
      <video
        ref={videoRef}
        controls
        autoPlay
        muted
        playsInline
        aria-label="Capture video locale"
        className="aspect-video w-full bg-black object-contain"
        onPlaying={() => setStatus("Lecture")}
        onPause={() => setStatus("En pause")}
        onEnded={() => setStatus("Termine")}
        onWaiting={() => setStatus("Mise en tampon...")}
        onError={() => { setError(true); setStatus("Lecture indisponible"); }}
      />
      <div className="flex min-h-14 items-center justify-between gap-3 border-b border-[var(--color-border)] py-2">
        <span role="status" className={error ? "text-rose-400" : "text-[var(--color-muted)]"}>
          {error ? "Lecture indisponible" : status}
        </span>
        {error && (
          <button
            type="button"
            title="Reessayer la lecture"
            aria-label="Reessayer la lecture"
            className="flex h-11 w-11 items-center justify-center rounded-md border border-[var(--color-border)] hover:bg-[var(--color-surface-2)] focus-visible:outline-2 focus-visible:outline-amber-400"
            onClick={() => { setError(false); setStatus("Chargement..."); setAttempt(attempt + 1); }}
          >
            <RotateCcw className="h-5 w-5" aria-hidden="true" />
          </button>
        )}
      </div>
    </section>
  );
}