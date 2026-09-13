"use client";

import React, { useState } from "react";
import Webcam from "react-webcam";
import { Camera } from "lucide-react";
import type { FaceData } from "@/lib/types";

interface Props {
  webcamRef: React.RefObject<Webcam | null>;
  faces: FaceData[];
  cameraSize: { width: number; height: number };
  onVideoLoad: (e: React.SyntheticEvent<HTMLVideoElement | HTMLImageElement>) => void;
  isAnalyzing: boolean;
  useIpCamera: boolean;
  ipCameraImage: string | null;
}

export default function CameraView({
  webcamRef,
  faces,
  cameraSize,
  onVideoLoad,
  isAnalyzing,
  useIpCamera,
  ipCameraImage,
}: Props) {
  const [webcamState, setWebcamState] = useState<"waiting" | "ready" | "error">("waiting");
  // Le conteneur adopte exactement le ratio de la caméra : aucun rognage,
  // donc les coordonnées Azure se mappent en pourcentage exact (zéro décalage).
  const ratio =
    cameraSize.width > 0 && cameraSize.height > 0
      ? cameraSize.width / cameraSize.height
      : 16 / 9;

  return (
    <section className="flex flex-col gap-4" aria-label="Flux caméra">
      <div
        className="relative w-full overflow-hidden rounded-2xl border border-[var(--color-border)] bg-black shadow-2xl flex items-center justify-center"
        style={{ aspectRatio: ratio }}
      >
        {useIpCamera ? (
          ipCameraImage ? (
            <img
              src={ipCameraImage}
              alt="Flux Caméra IP"
              className="h-full w-full object-fill"
              onLoad={onVideoLoad}
            />
          ) : (
            <div className="flex flex-col items-center justify-center text-slate-500 gap-2">
              <Camera className="w-8 h-8 animate-pulse" />
              <span className="text-sm">Connexion à la caméra IP...</span>
            </div>
          )
        ) : (
          <Webcam
            ref={webcamRef}
            audio={false}
            screenshotFormat="image/jpeg"
            screenshotQuality={0.7}
            forceScreenshotSourceSize
            mirrored
            videoConstraints={{ facingMode: "user", width: 1280, height: 720 }}
            onUserMedia={() => setWebcamState("ready")}
            onUserMediaError={() => setWebcamState("error")}
            onLoadedData={onVideoLoad}
            className="h-full w-full object-fill"
            aria-label="Flux vidéo en direct de la webcam"
          />
        )}

        {!useIpCamera && webcamState === "error" && (
          <p role="alert" className="absolute inset-x-4 top-1/3 text-center text-sm text-rose-300">
            Accès à la webcam refusé ou caméra indisponible.
          </p>
        )}

        {cameraSize.width > 0 &&
          faces.map((face) => {
            const r = face.faceRectangle;
            const left = (r.left / cameraSize.width) * 100;
            const top = (r.top / cameraSize.height) * 100;
            const width = (r.width / cameraSize.width) * 100;
            const height = (r.height / cameraSize.height) * 100;
            const border = face.recognized
              ? "border-emerald-400"
              : "border-rose-500";
            const shadow = face.recognized
              ? "shadow-[0_0_15px_rgba(16,185,129,0.5)]"
              : "shadow-[0_0_15px_rgba(239,68,68,0.5)]";
            // Clé stable basée sur la position du visage plutôt que l'index :
            // évite que React réassocie les overlays au mauvais visage quand
            // l'ordre du tableau change d'une frame à l'autre.
            const key = `${r.left}-${r.top}-${r.width}-${r.height}`;
            return (
              <div
                key={key}
                className={`pointer-events-none absolute rounded-lg border-2 transition-all duration-300 ${border} ${shadow}`}
                style={{
                  left: `${left}%`,
                  top: `${top}%`,
                  width: `${width}%`,
                  height: `${height}%`,
                }}
              >
                <div className="absolute left-1/2 top-[-35px] flex -translate-x-1/2 items-center gap-2 whitespace-nowrap rounded-full border border-[var(--color-border)] bg-slate-900/90 px-3 py-1 text-sm font-semibold shadow-lg backdrop-blur-sm">
                  <span
                    className={
                      face.recognized ? "text-emerald-400" : "text-rose-400"
                    }
                  >
                    {face.recognized ? face.name || "Reconnu" : "Inconnu"}
                  </span>
                  <span className="font-mono text-xs text-slate-400">
                    {(face.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            );
          })}

        <div className="absolute bottom-4 left-4 flex items-center gap-2 rounded-lg border border-white/10 bg-black/50 px-3 py-1.5 text-xs font-medium backdrop-blur-md">
          <Camera className="h-4 w-4 text-slate-300" aria-hidden="true" />
          <span>{isAnalyzing ? "Analyse…" : useIpCamera ? (ipCameraImage ? "Image IP reçue" : "En attente") : webcamState === "ready" ? "Flux actif" : webcamState === "error" ? "Caméra indisponible" : "Connexion caméra…"}</span>
        </div>
      </div>
    </section>
  );
}
