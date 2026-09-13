import { open, readdir, realpath } from "node:fs/promises";
import path from "node:path";

const PILOT_ID = /^[a-f0-9]{32}$/;
const MEDIA_FILE = /^(index\.m3u8|init\.mp4|segment-\d{3,6}\.m4s)$/;
const HEADERS = {
  "Cache-Control": "private, no-store",
  "X-Content-Type-Options": "nosniff",
  "Cross-Origin-Resource-Policy": "same-origin",
};

async function readMedia(root: string, parts: string[], maxBytes: number) {
  const target = await realpath(path.join(root, ...parts));
  const relative = path.relative(root, target);
  if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Outside pilot root");
  const file = await open(target, "r");
  try {
    const metadata = await file.stat();
    if (!metadata.isFile() || metadata.size > maxBytes) throw new Error("Invalid media size");
    return { data: await file.readFile(), updatedAt: metadata.mtime.toISOString() };
  } finally {
    await file.close();
  }
}

export async function servePilotMedia(request: Request, parts: string[], configuredRoot?: string) {
  const error = (status: number) => Response.json({ error: "Video indisponible" }, { status, headers: HEADERS });
  if (!configuredRoot) return error(404);
  const url = new URL(request.url);
  const host = request.headers.get("host") ?? url.host;
  if (!/^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(host)) return error(403);
  if (request.headers.get("sec-fetch-site") === "cross-site") return error(403);
  const origin = request.headers.get("origin");
  if (origin && origin !== `${url.protocol}//${host}`) return error(403);
  if (parts.length !== 0 && (parts.length !== 2 || !PILOT_ID.test(parts[0]) || !MEDIA_FILE.test(parts[1]))) {
    return error(404);
  }
  try {
    const root = await realpath(configuredRoot);
    if (parts.length === 0) {
      const directories = (await readdir(root, { withFileTypes: true }))
        .filter((entry) => entry.isDirectory() && PILOT_ID.test(entry.name));
      const pilots = [];
      for (const entry of directories) {
        try {
          const { data, updatedAt } = await readMedia(root, [entry.name, "index.m3u8"], 64 * 1024);
          const playlist = data.toString("utf8");
          if (!playlist.startsWith("#EXTM3U")) continue;
          pilots.push({ id: entry.name, updatedAt, finalized: playlist.split(/\r?\n/).includes("#EXT-X-ENDLIST") });
        } catch {}
      }
      pilots.sort((first, second) => second.updatedAt.localeCompare(first.updatedAt));
      return Response.json({ pilots: pilots.slice(0, 100) }, { headers: HEADERS });
    }
    const isPlaylist = parts[1] === "index.m3u8";
    const { data } = await readMedia(root, parts, isPlaylist ? 64 * 1024 : 8 * 1024 * 1024);
    return new Response(new Uint8Array(data), {
      headers: {
        ...HEADERS,
        "Content-Type": isPlaylist ? "application/vnd.apple.mpegurl" : "video/mp4",
        "Content-Length": String(data.length),
      },
    });
  } catch {
    return error(404);
  }
}