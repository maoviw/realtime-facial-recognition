"use client";

import { Pause, Play, ShieldAlert } from "lucide-react";
import type { IpCameraConfig } from "@/lib/types";

interface Props {
  intervalMs: number;
  onIntervalChange: (ms: number) => void;
  running: boolean;
  onToggleRunning: () => void;
  ipCamera: IpCameraConfig;
  onIpCameraChange: (config: IpCameraConfig) => void;
}

export default function SettingsPanel({
  intervalMs,
  onIntervalChange,
  running,
  onToggleRunning,
  ipCamera,
  onIpCameraChange,
}: Props) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <button
          onClick={onToggleRunning}
          className={`flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white transition-colors duration-200 focus:outline-none focus:ring-2 ${
            running
              ? "bg-rose-600 hover:bg-rose-500 focus:ring-rose-400"
              : "bg-emerald-600 hover:bg-emerald-500 focus:ring-emerald-400"
          }`}
        >
          {running ? (
            <>
              <Pause className="h-4 w-4" aria-hidden="true" /> Mettre en pause
            </>
          ) : (
            <>
              <Play className="h-4 w-4" aria-hidden="true" /> Reprendre l&apos;analyse
            </>
          )}
        </button>
      </div>

      <div className="flex flex-col gap-2">
        <label
          htmlFor="interval"
          className="flex justify-between text-xs font-medium text-slate-400"
        >
          <span>Intervalle de capture</span>
          <span className="font-mono text-slate-200">
            {(intervalMs / 1000).toFixed(1)} s
          </span>
        </label>
        <input
          id="interval"
          type="range"
          min={1000}
          max={10000}
          step={500}
          value={intervalMs}
          onChange={(e) => onIntervalChange(Number(e.target.value))}
          className="w-full accent-amber-500"
        />
        <p className="text-xs text-slate-500">
          Fréquence d&apos;envoi des images au backend. Plus l&apos;intervalle
          est court, plus l&apos;analyse est réactive (mais coûteuse).
        </p>
      </div>

      <div className="flex flex-col gap-3 border-t border-slate-700/50 pt-5">
        <h3 className="text-sm font-semibold text-slate-300">Source Vidéo</h3>
        <label className="flex items-center gap-2 text-sm text-slate-200">
          <input
            type="checkbox"
            checked={ipCamera.enabled}
            onChange={(e) =>
              onIpCameraChange({ ...ipCamera, enabled: e.target.checked })
            }
            className="rounded border-slate-600 bg-slate-800 text-amber-500 focus:ring-amber-500"
          />
          Utiliser une Caméra IP (Réseau)
        </label>

        {ipCamera.enabled && (
          <div className="flex flex-col gap-3 rounded-lg border border-slate-700/50 bg-slate-800/30 p-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-slate-400">URL du Snapshot (ex: http://ip/image/jpeg.cgi)</label>
              <input
                type="text"
                value={ipCamera.url}
                onChange={(e) =>
                  onIpCameraChange({ ...ipCamera, url: e.target.value })
                }
                className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 focus:border-amber-500 focus:outline-none"
                placeholder="http://192.168.1.43/image/jpeg.cgi"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-1">
                <label className="text-xs text-slate-400">Utilisateur</label>
                <input
                  type="text"
                  value={ipCamera.username || ""}
                  onChange={(e) =>
                    onIpCameraChange({ ...ipCamera, username: e.target.value })
                  }
                  className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 focus:border-amber-500 focus:outline-none"
                  placeholder="admin"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs text-slate-400">Mot de passe</label>
                <input
                  type="password"
                  value={ipCamera.password || ""}
                  onChange={(e) =>
                    onIpCameraChange({ ...ipCamera, password: e.target.value })
                  }
                  className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 focus:border-amber-500 focus:outline-none"
                />
              </div>
            </div>
            <p className="text-[10px] text-amber-500/80 flex items-start gap-1 mt-1">
              <ShieldAlert className="w-3 h-3 flex-shrink-0 mt-0.5" />
              L&apos;URL et les identifiants sont envoyés via le serveur local pour contourner la sécurité du navigateur.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
