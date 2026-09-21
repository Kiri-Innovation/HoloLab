// Unit tests for the resilience layer. Verifies the three claims that
// matter for the "network glitch → previews stuck" bug:
//
//   * transient failures (network TypeError, retriable 5xx) DO retry
//     on idempotent methods, and the eventual success resolves normally;
//   * 4xx business errors + user abort DO NOT retry (the caller's
//     ``ApiError`` / cancel semantics must survive intact);
//   * backoff is bounded and jittered so N tabs don't fire in lockstep.
//
// No DOM / React deps — pure fetch stubbing.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  NetworkError,
  computeBackoffMs,
  netBus,
  pLimit,
  resilientFetch,
} from "./net";

// Speed up the backoff so N-attempt tests don't wait real wall time.
const FAST_OPTS = { baseDelayMs: 1, maxDelayMs: 4 };

beforeEach(() => {
  netBus._resetForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function okResponse(): Response {
  return new Response("ok", { status: 200 });
}

function errResponse(status: number): Response {
  return new Response(`fail`, { status });
}

describe("resilientFetch — retry classification", () => {
  it("returns the success Response immediately on 2xx", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse());
    const r = await resilientFetch("/api/x", undefined, FAST_OPTS);
    expect(r.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retries a TypeError (network throw) and returns success", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(okResponse());
    const r = await resilientFetch("/api/x", undefined, FAST_OPTS);
    expect(r.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("retries retriable 5xx (503) until success", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(errResponse(503))
      .mockResolvedValueOnce(errResponse(502))
      .mockResolvedValueOnce(okResponse());
    const r = await resilientFetch("/api/x", undefined, FAST_OPTS);
    expect(r.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("does NOT retry 4xx business errors — returns Response as-is", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(errResponse(404));
    const r = await resilientFetch("/api/handles/nope", undefined, FAST_OPTS);
    expect(r.status).toBe(404);
    // Exactly one attempt — the caller's ``json<T>`` will convert to ApiError.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does NOT retry POST by default (non-idempotent)", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(
      resilientFetch("/api/write", { method: "POST" }, FAST_OPTS),
    ).rejects.toBeInstanceOf(TypeError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retries POST when retryNonIdempotent is opted in", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(okResponse());
    const r = await resilientFetch(
      "/api/write",
      { method: "POST" },
      { ...FAST_OPTS, retryNonIdempotent: true },
    );
    expect(r.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("gives up after ``retries`` attempts and throws NetworkError", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(
      resilientFetch("/api/x", undefined, { ...FAST_OPTS, retries: 3 }),
    ).rejects.toBeInstanceOf(NetworkError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("propagates an AbortSignal cancellation without retrying", async () => {
    const ctl = new AbortController();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() =>
        Promise.reject(new DOMException("Aborted", "AbortError")),
      );
    ctl.abort();
    await expect(
      resilientFetch("/api/x", { signal: ctl.signal }, FAST_OPTS),
    ).rejects.toMatchObject({ name: "AbortError" });
    // First attempt guard fires before fetch even runs.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aborts a mid-backoff wait", async () => {
    const ctl = new AbortController();
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
      new TypeError("Failed to fetch"),
    );
    const p = resilientFetch(
      "/api/x",
      { signal: ctl.signal },
      { baseDelayMs: 500, maxDelayMs: 2000 },
    );
    // Give the retry loop a chance to enter its sleep before cancelling.
    await new Promise((r) => setTimeout(r, 5));
    ctl.abort();
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("computeBackoffMs", () => {
  it("doubles until capped at maxDelayMs", () => {
    const noJitter = () => 0.5; // rand() such that 2*rand-1 == 0
    expect(computeBackoffMs(1, 500, 15000, noJitter)).toBe(500);
    expect(computeBackoffMs(2, 500, 15000, noJitter)).toBe(1000);
    expect(computeBackoffMs(3, 500, 15000, noJitter)).toBe(2000);
    expect(computeBackoffMs(6, 500, 15000, noJitter)).toBe(15000);
    expect(computeBackoffMs(10, 500, 15000, noJitter)).toBe(15000);
  });

  it("stays inside ±25% of the ideal even with adversarial rand", () => {
    for (const r of [0, 1, 0.001, 0.999]) {
      const delay = computeBackoffMs(3, 500, 15000, () => r);
      // ideal = 2000, ±25% → [1500, 2500]
      expect(delay).toBeGreaterThanOrEqual(1500);
      expect(delay).toBeLessThanOrEqual(2500);
    }
  });
});

describe("netBus reconnect semantics", () => {
  it("first connect does NOT emit reconnect", () => {
    const spy = vi.fn();
    netBus.on("reconnect", spy);
    netBus.markConnected(); // initial page-load handshake
    expect(spy).not.toHaveBeenCalled();
  });

  it("connect → disconnect → connect emits reconnect exactly once", () => {
    const spy = vi.fn();
    netBus.on("reconnect", spy);
    netBus.markConnected();
    netBus.markDisconnected();
    netBus.markConnected();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("repeated connects without a disconnect do not re-emit", () => {
    const spy = vi.fn();
    netBus.on("reconnect", spy);
    netBus.markConnected();
    netBus.markConnected();
    netBus.markConnected();
    expect(spy).not.toHaveBeenCalled();
  });

  it("unsubscribe stops later emissions", () => {
    const spy = vi.fn();
    const off = netBus.on("reconnect", spy);
    netBus.markConnected();
    off();
    netBus.markDisconnected();
    netBus.markConnected();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("pLimit — bounded concurrency", () => {
  it("never runs more than ``concurrency`` invocations at once", async () => {
    let inFlight = 0;
    let peak = 0;
    const wrapped = pLimit(3, async (i: number) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      // A microtask yield is enough for pLimit's Promise machinery to
      // dispatch further queued items; we don't need real timers.
      await Promise.resolve();
      await Promise.resolve();
      inFlight -= 1;
      return i;
    });

    const results = await Promise.all(
      Array.from({ length: 50 }, (_, i) => wrapped(i)),
    );
    expect(results).toHaveLength(50);
    expect(results[0]).toBe(0);
    expect(results[49]).toBe(49);
    expect(peak).toBeLessThanOrEqual(3);
  });

  it("propagates rejections without stalling later invocations", async () => {
    const wrapped = pLimit(2, async (i: number) => {
      if (i === 1) throw new Error("boom");
      return i;
    });

    const settled = await Promise.allSettled([
      wrapped(0),
      wrapped(1),
      wrapped(2),
      wrapped(3),
    ]);
    expect(settled[0].status).toBe("fulfilled");
    expect(settled[1].status).toBe("rejected");
    expect(settled[2].status).toBe("fulfilled");
    expect(settled[3].status).toBe("fulfilled");
  });
});
