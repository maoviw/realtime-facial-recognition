import type {
  FaceData,
  HealthStatus,
  RecognitionEvent,
  ReferenceFace,
} from "./types";

// Par défaut, les appels passent par le proxy serveur same-origin
// (`/api/proxy/*`, cf. src/app/api/proxy/[...path]/route.ts) qui injecte la clé
// API côté serveur (R6) : la vraie clé ne touche jamais le navigateur.
// Pour cibler directement le backend (dev/legacy), définir NEXT_PUBLIC_BACKEND_URL.
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "/api/proxy";
// N'est utilisé qu'en mode direct (NEXT_PUBLIC_BACKEND_URL défini). En mode
// proxy, laisser vide : le serveur ajoute la clé. AVERTISSEMENT : toute variable
// NEXT_PUBLIC_* est embarquée dans le bundle client (visible en DevTools).
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

const DEFAULT_TIMEOUT_MS = 15000;

function authHeaders(extra: Record<string, string> = {}): HeadersInit {
  const headers: Record<string, string> = { ...extra };
  if (API_KEY) headers["x-api-key"] = API_KEY;
  return headers;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    const res = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      signal: controller.signal,
    });
    if (!res.ok) {
      let message = res.statusText;
      try {
        const data = await res.json();
        if (data?.detail) message = data.detail;
      } catch {
        /* réponse non-JSON */
      }
      throw new Error(message);
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("Délai d'attente dépassé. Le backend répond-il ?");
    }
    // Échec réseau (backend injoignable, CORS, DNS) : `fetch` rejette avec un
    // TypeError. On le convertit en message clair plutôt que de propager
    // « Failed to fetch » brut à l'UI.
    if (err instanceof TypeError) {
      throw new Error("Backend injoignable. Vérifiez la connexion.");
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  health: () => request<HealthStatus>("/health"),

  analyzeFace: (image: string) =>
    request<{ faces: FaceData[] }>("/analyze-face", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ image }),
    }),

  listReferences: () =>
    request<{ references: ReferenceFace[] }>("/references", {
      headers: authHeaders(),
    }),

  addReference: (name: string, file: File) => {
    const form = new FormData();
    form.append("name", name);
    form.append("file", file);
    return request<ReferenceFace>("/references", {
      method: "POST",
      headers: authHeaders(),
      body: form,
    });
  },

  updateReference: (id: number, name: string) =>
    request<ReferenceFace>(`/references/${id}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name }),
    }),

  deleteReference: (id: number) =>
    request<{ deleted: number }>(`/references/${id}`, {
      method: "DELETE",
      headers: authHeaders(),
    }),

  // Récupère la miniature du visage (auth par header → blob → object URL).
  referenceImageUrl: async (id: number): Promise<string | null> => {
    try {
      const res = await fetch(`${BACKEND_URL}/references/${id}/image`, {
        headers: authHeaders(),
      });
      if (!res.ok) return null;
      return URL.createObjectURL(await res.blob());
    } catch {
      return null;
    }
  },

  history: (limit = 50) =>
    request<{ events: RecognitionEvent[] }>(`/history?limit=${limit}`, {
      headers: authHeaders(),
    }),

  proxyCamera: (url: string, username?: string, password?: string) =>
    request<{ image: string }>("/proxy-camera", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ url, username, password }),
    }),
};
