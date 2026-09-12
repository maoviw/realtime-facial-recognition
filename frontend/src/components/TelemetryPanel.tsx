"use client";

import { RefreshCw } from "lucide-react";
import type { FaceData } from "@/lib/types";

export default function TelemetryPanel({ faces }: { faces: FaceData[] }) {
  if (faces.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 text-sm text-slate-500">
        <RefreshCw
          className="h-8 w-8 animate-spin-slow opacity-50"
          aria-hidden="true"
        />
        <p>En attente d&apos;un visage…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 overflow-y-auto pr-2 custom-scrollbar">
      {faces.map((face, index) => {
        return (
          <div
            key={`tel-${index}`}
            className="flex flex-col gap-4 rounded-xl border border-slate-700/50 bg-slate-800/50 p-4"
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-slate-200">
                {face.name || `Visage #${index + 1}`}
              </span>
              <span
                className={`rounded px-2 py-1 text-xs font-medium ${
                  face.recognized
                    ? "bg-emerald-500/20 text-emerald-400"
                    : "bg-rose-500/20 text-rose-400"
                }`}
              >
                {face.recognized ? "✓ Authentifié" : "✗ Non reconnu"}
              </span>
            </div>

            <div className="flex flex-col gap-2 text-xs text-slate-400">
              <div className="flex justify-between">
                <span>Action système</span>
                <span className="font-mono text-amber-400">
                  {face.system_action}
                </span>
              </div>
              <div className="flex justify-between">
                <span>Confiance</span>
                <span className="font-mono text-slate-200">
                  {(face.confidence * 100).toFixed(0)}%
                </span>
              </div>
            </div>

            {/* Attributs Azure */}
            {(face.glasses && face.glasses !== "NoGlasses") ||
            (face.mask && face.mask.type !== "noMask") ? (
              <div className="mt-1 flex flex-col gap-2 border-t border-slate-700/50 pt-3 text-xs text-slate-400">
                {face.glasses && face.glasses !== "NoGlasses" && (
                  <div className="flex justify-between">
                    <span>Lunettes</span>
                    <span className="font-medium text-indigo-400">
                      {face.glasses}
                    </span>
                  </div>
                )}
                {face.mask && face.mask.type !== "noMask" && (
                  <div className="flex justify-between">
                    <span>Masque</span>
                    <span className="font-medium text-emerald-400">
                      {face.mask.type}
                      {face.mask.nose_and_mouth_covered ? " (Nez/Bouche)" : ""}
                    </span>
                  </div>
                )}
              </div>
            ) : null}

            {/* Qualité de l'image (Azure) */}
            {face.quality && (
              <div className="mt-1 flex flex-col gap-2 border-t border-slate-700/50 pt-3 text-xs">
                <div className="flex justify-between items-center">
                  <span className="text-slate-400">Qualité de capture</span>
                  <span className={`font-medium ${face.quality.is_sufficient_quality ? "text-emerald-400" : "text-amber-500"}`}>
                    {face.quality.is_sufficient_quality ? "Optimale" : "Insuffisante"}
                  </span>
                </div>
                
                {face.quality.issues.length > 0 && (
                  <ul className="list-disc pl-4 text-amber-500/80">
                    {face.quality.issues.map((issue, idx) => (
                      <li key={idx}>{issue}</li>
                    ))}
                  </ul>
                )}
                
                <div className="flex flex-col gap-1 mt-1 text-[10px] text-slate-500">
                  <div className="flex justify-between">
                    <span>Flou</span>
                    <span>{face.quality.metrics.blur?.level}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Exposition</span>
                    <span>{face.quality.metrics.exposure?.level}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Démographie et Émotions (DeepFace) */}
            {face.demographics && (
              <div className="mt-1 flex flex-col gap-2 border-t border-slate-700/50 pt-3 text-xs">
                <span className="text-slate-400">Analyse DeepFace</span>
                <div className="flex flex-col gap-1">
                  <div className="flex justify-between">
                    <span>Âge estimé</span>
                    <span className="font-medium text-cyan-400">{face.demographics.age} ans</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Genre</span>
                    <span className="font-medium text-pink-400">{face.demographics.gender}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Émotion dominante</span>
                    <span className="font-medium text-amber-400 capitalize">{face.demographics.emotion}</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
