import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("api request error handling (R9)", () => {
  it("converts a network TypeError into a clear message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    await expect(api.health()).rejects.toThrow("Backend injoignable. Vérifiez la connexion.");
  });

  it("extracts `detail` from a non-OK JSON error response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: "Clé API invalide ou manquante." }), {
            status: 401,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
    await expect(api.listReferences()).rejects.toThrow("Clé API invalide ou manquante.");
  });

  it("resolves with parsed JSON on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ references: [{ id: 1, name: "Alice" }] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
    await expect(api.listReferences()).resolves.toEqual({
      references: [{ id: 1, name: "Alice" }],
    });
  });
});
