"use client";

import { Pause, Play, ShieldAlert, Trash2 } from "lucide-react";
import type { AlertRule, IpCameraConfig, Zone } from "@/lib/types";
import { useEffect, useRef, useState, type MouseEvent } from "react";

interface Props {
  intervalMs: number;
  onIntervalChange: (ms: number) => void;
  running: boolean;
  onToggleRunning: () => void;
  ipCamera: IpCameraConfig;
  onIpCameraChange: (config: IpCameraConfig) => void;
  zones: Zone[];
  alertRules: AlertRule[];
  onCreateZone: (name: string, polygon: number[][]) => Promise<void>;
  onDeleteZone: (id: number) => Promise<void>;
  onCreateAlertRule: (name: string, zoneId: number | null, eventType: string, cooldown: number) => Promise<void>;
  onDeleteAlertRule: (id: number) => Promise<void>;
}

export default function SettingsPanel({
  intervalMs,
  onIntervalChange,
  running,
  onToggleRunning,
  ipCamera,
  onIpCameraChange,
  zones,
  alertRules,
  onCreateZone,
  onDeleteZone,
  onCreateAlertRule,
  onDeleteAlertRule,
}: Props) {
  // Valeur affichée immédiate ; la remontée au parent (qui recrée la boucle de
  // capture) est débouncée pour éviter de relancer un timer à chaque tick du
  // curseur pendant le glissement.
  const [localMs, setLocalMs] = useState(intervalMs);
  const [prevProp, setPrevProp] = useState(intervalMs);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [zoneName, setZoneName] = useState("");
  const [zonePoints, setZonePoints] = useState<number[][]>([]);
  const [ruleName, setRuleName] = useState("");
  const [ruleZone, setRuleZone] = useState("");
  const [ruleType, setRuleType] = useState("face");
  const [ruleCooldown, setRuleCooldown] = useState(60);

  // Resynchronise si la valeur parente change depuis l'extérieur (ajustement
  // d'état pendant le rendu — pattern React, sans effet).
  if (intervalMs !== prevProp) {
    setPrevProp(intervalMs);
    setLocalMs(intervalMs);
  }

  // Nettoie le timer en attente au démontage.
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const handleSlide = (ms: number) => {
    setLocalMs(ms);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => onIntervalChange(ms), 250);
  };

  const createZone = async () => {
    if (zonePoints.length < 3) return;
    await onCreateZone(zoneName.trim() || "Zone sans nom", zonePoints);
    setZoneName("");
    setZonePoints([]);
  };

  const handleZoneCanvasClick = (event: MouseEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
    const y = Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height));
    setZonePoints((points) => [...points, [Number(x.toFixed(4)), Number(y.toFixed(4))]]);
  };

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
            {(localMs / 1000).toFixed(1)} s
          </span>
        </label>
        <input
          id="interval"
          type="range"
          min={1000}
          max={10000}
          step={500}
          value={localMs}
          onChange={(e) => handleSlide(Number(e.target.value))}
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

      <div className="flex flex-col gap-3 border-t border-slate-700/50 pt-5">
        <h3 className="text-sm font-semibold text-slate-300">Zones et alertes</h3>
        <p className="text-xs text-slate-500">Cliquez dans l&apos;aperçu pour placer les sommets de la zone.</p>
        <input value={zoneName} onChange={(e) => setZoneName(e.target.value)} placeholder="Nom de la zone" className="rounded border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200" />
        <div className="relative aspect-video overflow-hidden rounded border border-slate-700 bg-slate-950">
          <svg viewBox="0 0 1 1" role="application" aria-label="Dessiner une zone" onClick={handleZoneCanvasClick} className="absolute inset-0 h-full w-full cursor-crosshair">
            <defs><pattern id="zone-grid" width="0.1" height="0.1" patternUnits="userSpaceOnUse"><path d="M 0.1 0 L 0 0 0 0.1" fill="none" stroke="rgba(148,163,184,0.2)" strokeWidth="0.004" /></pattern></defs>
            <rect width="1" height="1" fill="url(#zone-grid)" />
            {zonePoints.length >= 2 && <polyline points={zonePoints.map(([x, y]) => `${x},${y}`).join(" ")} fill="rgba(245,158,11,0.2)" stroke="#f59e0b" strokeWidth="0.008" />}
            {zonePoints.map(([x, y], index) => <g key={`${x}-${y}-${index}`}><circle cx={x} cy={y} r="0.025" fill="#f59e0b" /><text x={x} y={y + 0.008} textAnchor="middle" fontSize="0.035" fill="#111827">{index + 1}</text></g>)}
          </svg>
          {zonePoints.length === 0 && <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-slate-500">Cliquez pour commencer</span>}
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => setZonePoints([])} disabled={zonePoints.length === 0} className="flex-1 rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40">Effacer</button>
          <button type="button" onClick={() => void createZone()} disabled={zonePoints.length < 3} className="flex-1 rounded bg-slate-700 px-3 py-2 text-xs text-white hover:bg-slate-600 disabled:opacity-40">Ajouter la zone</button>
        </div>
        <ul className="flex flex-col gap-1 text-xs">
          {zones.map((zone) => <li key={zone.id} className="flex items-center justify-between gap-2 text-slate-300"><span>{zone.name}</span><button type="button" title={`Supprimer ${zone.name}`} aria-label={`Supprimer ${zone.name}`} onClick={() => void onDeleteZone(zone.id)} className="p-1 text-slate-400 hover:text-rose-400"><Trash2 className="h-4 w-4" aria-hidden="true" /></button></li>)}
        </ul>
        <div className="mt-2 grid grid-cols-[1fr_1fr_auto] gap-2">
          <input value={ruleName} onChange={(e) => setRuleName(e.target.value)} placeholder="Règle" className="min-w-0 rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200" />
          <select value={ruleZone} onChange={(e) => setRuleZone(e.target.value)} aria-label="Zone de la règle" className="min-w-0 rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"><option value="">Toutes les zones</option>{zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}</select>
          <input type="number" min="0" max="86400" value={ruleCooldown} onChange={(e) => setRuleCooldown(Number(e.target.value))} aria-label="Cooldown en secondes" className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200" />
        </div>
        <select value={ruleType} onChange={(e) => setRuleType(e.target.value)} aria-label="Type de déclenchement" className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"><option value="face">Tout visage</option><option value="recognized">Visage reconnu</option><option value="unknown">Visage inconnu</option></select>
        <button type="button" onClick={() => void onCreateAlertRule(ruleName.trim() || "Alerte visage", ruleZone ? Number(ruleZone) : null, ruleType, ruleCooldown)} className="rounded bg-amber-600 px-3 py-2 text-xs text-white hover:bg-amber-500">Ajouter la règle</button>
        <ul className="flex flex-col gap-1 text-xs text-slate-400">{alertRules.map((rule) => <li key={rule.id} className="flex items-center justify-between gap-2"><span>{rule.name} · {rule.cooldown_seconds}s</span><button type="button" title={`Supprimer ${rule.name}`} aria-label={`Supprimer ${rule.name}`} onClick={() => void onDeleteAlertRule(rule.id)} className="p-1 hover:text-rose-400"><Trash2 className="h-4 w-4" aria-hidden="true" /></button></li>)}</ul>
      </div>
    </div>
  );
}
