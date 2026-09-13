// Proxy serveur (R6) : le navigateur appelle ce route handler same-origin, qui
// relaie vers le backend en injectant la clé API côté serveur. La vraie clé
// (API_KEY, sans préfixe NEXT_PUBLIC_) ne quitte donc jamais le serveur.
import { NextRequest } from "next/server";

// Origine du backend, résolue côté serveur uniquement.
const BACKEND_ORIGIN =
  process.env.BACKEND_ORIGIN ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "http://localhost:8000";
const SERVER_API_KEY = process.env.API_KEY || "";

// En-têtes hop-by-hop à ne pas recopier vers l'amont/aval.
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  const search = req.nextUrl.search;
  const target = `${BACKEND_ORIGIN.replace(/\/$/, "")}/${path.join("/")}${search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  // Injection serveur de la clé API (jamais exposée au navigateur).
  if (SERVER_API_KEY) headers.set("x-api-key", SERVER_API_KEY);

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const init: RequestInit = {
    method: req.method,
    headers,
    body: hasBody ? await req.arrayBuffer() : undefined,
    redirect: "manual",
  };

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return Response.json(
      { detail: "Backend injoignable." },
      { status: 502 },
    );
  }

  // Recopie le statut, le corps (binaire compris) et les en-têtes utiles.
  const respHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) respHeaders.set(key, value);
  });
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  });
}

type Ctx = { params: Promise<{ path: string[] }> };

async function handler(req: NextRequest, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  return forward(req, path);
}

export {
  handler as GET,
  handler as POST,
  handler as PATCH,
  handler as DELETE,
  handler as OPTIONS,
};
