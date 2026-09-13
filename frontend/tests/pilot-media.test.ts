import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { servePilotMedia } from "../src/lib/pilot-media";

const pilotId = "a".repeat(32);
const request = (headers: HeadersInit = {}) => new Request("http://localhost:3000/api/pilot-video", { headers });

test("pilot video is disabled by default and rejects remote origins", async () => {
  assert.equal((await servePilotMedia(request(), [])).status, 404);
  for (const headers of [{ host: "evil.test" }, { origin: "https://evil.test" }, { "sec-fetch-site": "cross-site" }]) {
    assert.equal((await servePilotMedia(request(headers), [], ".")).status, 403);
  }
});

test("serves only named HLS assets and filters the catalog", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pilot-media-"));
  try {
    await mkdir(path.join(root, pilotId));
    await mkdir(path.join(root, "references"));
    await writeFile(path.join(root, pilotId, "index.m3u8"), "#EXTM3U\n#EXT-X-ENDLIST\n");
    await writeFile(path.join(root, pilotId, "init.mp4"), "init");
    await writeFile(path.join(root, pilotId, "segment-000.m4s"), "segment");
    const catalog = await servePilotMedia(request(), [], root);
    const { pilots } = await catalog.json();
    assert.equal(pilots.length, 1);
    assert.equal(pilots[0].id, pilotId);
    assert.equal(pilots[0].finalized, true);
    for (const filename of ["index.m3u8", "init.mp4", "segment-000.m4s"]) {
      const response = await servePilotMedia(request(), [pilotId, filename], root);
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("cache-control"), "private, no-store");
      assert.ok((await response.arrayBuffer()).byteLength > 0);
    }
    for (const parts of [["..", ".env"], [pilotId, "../../.env"], [pilotId, "secret.jpg"], [pilotId, "segment-000.m4s.tmp"], [pilotId]]) {
      assert.equal((await servePilotMedia(request(), parts, root)).status, 404);
    }
    await writeFile(path.join(root, pilotId, "index.m3u8"), "#EXTM3U\n");
    const active = await (await servePilotMedia(request(), [], root)).json();
    assert.equal(active.pilots[0].finalized, false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects symlink escapes and oversized playlists", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "pilot-escape-"));
  const root = path.join(parent, "root");
  const outside = path.join(parent, "outside");
  try {
    await mkdir(root);
    await mkdir(outside);
    await writeFile(path.join(outside, "index.m3u8"), "#EXTM3U\n");
    await symlink(outside, path.join(root, pilotId), process.platform === "win32" ? "junction" : "dir");
    assert.equal((await servePilotMedia(request(), [pilotId, "index.m3u8"], root)).status, 404);
    const otherId = "b".repeat(32);
    await mkdir(path.join(root, otherId));
    await writeFile(path.join(root, otherId, "index.m3u8"), Buffer.alloc(65537));
    assert.equal((await servePilotMedia(request(), [otherId, "index.m3u8"], root)).status, 404);
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});