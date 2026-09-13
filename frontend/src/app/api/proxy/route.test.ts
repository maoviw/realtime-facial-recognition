import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Le handler lit les variables d'env au moment de l'import : on les pose avant.
beforeEach(() => {
  vi.resetModules();
  process.env.BACKEND_ORIGIN = "http://backend.test";
  process.env.API_KEY = "server-secret";
});

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.BACKEND_ORIGIN;
  delete process.env.API_KEY;
});

describe("proxy serveur (R6)", () => {
  it("relaie vers le backend en injectant x-api-key côté serveur", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("./[...path]/route");
    const { NextRequest } = await import("next/server");

    const req = new NextRequest("http://localhost:3000/api/proxy/health?x=1");
    const res = await GET(req, { params: Promise.resolve({ path: ["health"] }) });

    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://backend.test/health?x=1");
    const headers = new Headers(init.headers);
    expect(headers.get("x-api-key")).toBe("server-secret");
  });

  it("renvoie 502 si le backend est injoignable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("fail"))),
    );
    const { GET } = await import("./[...path]/route");
    const { NextRequest } = await import("next/server");

    const req = new NextRequest("http://localhost:3000/api/proxy/health");
    const res = await GET(req, { params: Promise.resolve({ path: ["health"] }) });
    expect(res.status).toBe(502);
  });
});
