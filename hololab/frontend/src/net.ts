// Network resilience layer.
//
// The gateway restart scenario: for a few seconds the browser's fetch calls
// throw ``TypeError: Failed to fetch`` and previews / cards latch a permanent
// error state whose ``useEffect`` deps never change again. Manual refresh
// was the only way out. This module gives the rest of the app two building
// blocks so that stops being true:
//
//   1. ``resilientFetch`` — retries transient failures (network TypeError,
//      5xx, 408/429) with exponential backoff. Non-retriable outcomes
//      (4xx business errors, user abort, non-idempotent POST by default)
//      pass through unchanged so ``ApiError`` semantics are preserved.
//
//   2. ``netBus`` + ``useReconnectTick`` — the WS layer marks connect /
//      disconnect; a *true* reconnect (previously connected → dropped →
//      re-established) bumps a monotonic tick that preview / hydration
//      effects list in their deps so they re-run when the network comes
//      back. Bookend that with per-card ``useRetryTick`` for the "还是
//      失败？点这里重试" branch the UI convention asks for.

import { useEffect, useState, useCallback } from "react";

// ---------------------------------------------------------------------------
// resilientFetch
// ---------------------------------------------------------------------------

export interface ResilientFetchOptions {
  // How many attempts total (including the first). Default 6 — with the
  // default backoff schedule that's ~30 s of wall time before giving up,
  // which fits a "restart the gateway" recovery window comfortably.
  retries?: number;
  // First retry delay in ms. Successive retries double this until
  // ``maxDelayMs`` is hit. Each delay carries ±25% jitter.
  baseDelayMs?: number;
  maxDelayMs?: number;
  // Cancels the whole retry loop; the caller's ``AbortError`` short-circuits
  // as it would with plain fetch — never retried.
  signal?: AbortSignal;
  // When true (default) retries 5xx / 408 / 429. Off for callers who want
  // network-only retries.
  retryOnServerError?: boolean;
  // When true (default) retry non-idempotent methods (POST/PATCH/PUT/DELETE)
  // on transient failures. Default false — a POST that reached the server
  // but lost its response would double-write on retry. GET/HEAD are always
  // retried because they're safe.
  retryNonIdempotent?: boolean;
}

const DEFAULT_RETRIES = 6;
const DEFAULT_BASE_DELAY_MS = 500;
const DEFAULT_MAX_DELAY_MS = 15000;
const RETRIABLE_STATUS = new Set([408, 429, 500, 502, 503, 504]);
const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function methodOf(init: RequestInit | undefined): string {
  const m = init?.method ?? "GET";
  return m.toUpperCase();
}

// Chosen so the caller can distinguish "our retry loop gave up" from a
// business error thrown by ``json<T>``. Kept minimal — status is populated
// only when the last attempt returned a Response.
export class NetworkError extends Error {
  status: number | null;
  attempts: number;
  constructor(message: string, opts: { status: number | null; attempts: number; cause?: unknown }) {
    super(message);
    this.name = "NetworkError";
    this.status = opts.status;
    this.attempts = opts.attempts;
    if (opts.cause !== undefined) {
      (this as { cause?: unknown }).cause = opts.cause;
    }
  }
}

export function computeBackoffMs(
  attempt: number,
  baseDelayMs: number,
  maxDelayMs: number,
  rand: () => number = Math.random,
): number {
  // attempt starts at 1 for the delay *before* the second attempt.
  const raw = Math.min(maxDelayMs, baseDelayMs * Math.pow(2, attempt - 1));
  // ±25% jitter so N tabs hitting the same gateway restart don't all
  // retry in lockstep.
  const jitter = raw * 0.25 * (rand() * 2 - 1);
  return Math.max(0, Math.round(raw + jitter));
}

function isRetriableResponse(status: number, retryOnServerError: boolean): boolean {
  if (!retryOnServerError) return false;
  return RETRIABLE_STATUS.has(status);
}

// Race the retry sleep against the caller's AbortSignal — a mid-backoff
// cancel needs to short-circuit, not wait out the timer.
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const t = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

// Detect a network-layer failure: browsers throw ``TypeError`` for DNS
// failure, connection refused, TLS error, and "server closed before
// response". We treat DOMException("NetworkError") the same way even
// though it's rarer.
function isNetworkThrow(err: unknown): boolean {
  if (err instanceof TypeError) return true;
  if (err instanceof DOMException && err.name === "NetworkError") return true;
  // Node's undici throws Error with cause set — used only in tests but
  // handy enough to keep in the classifier.
  if (err instanceof Error && /fetch failed|network|ECONN|EAI_/i.test(err.message)) {
    return true;
  }
  return false;
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

export async function resilientFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
  opts?: ResilientFetchOptions,
): Promise<Response> {
  const retries = opts?.retries ?? DEFAULT_RETRIES;
  const baseDelayMs = opts?.baseDelayMs ?? DEFAULT_BASE_DELAY_MS;
  const maxDelayMs = opts?.maxDelayMs ?? DEFAULT_MAX_DELAY_MS;
  const retryOnServerError = opts?.retryOnServerError ?? true;
  const signal = opts?.signal ?? init?.signal ?? undefined;

  const method = methodOf(init);
  const methodIsSafe = IDEMPOTENT_METHODS.has(method);
  const allowMethodRetry = methodIsSafe || (opts?.retryNonIdempotent ?? false);

  let lastErr: unknown = null;
  let lastStatus: number | null = null;

  for (let attempt = 0; attempt < retries; attempt++) {
    if (signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    try {
      const res = await fetch(input, { ...init, signal });
      if (res.ok) return res;
      lastStatus = res.status;
      if (!allowMethodRetry || !isRetriableResponse(res.status, retryOnServerError)) {
        // 4xx business error, or an unretriable method on a 5xx — return
        // the Response so ``json<T>`` can turn it into ``ApiError``.
        return res;
      }
      // Drain body to free the connection before backing off.
      try {
        await res.arrayBuffer();
      } catch {
        /* ignore */
      }
    } catch (err) {
      if (isAbort(err)) throw err;
      if (!allowMethodRetry || !isNetworkThrow(err)) throw err;
      lastErr = err;
    }
    if (attempt >= retries - 1) break;
    const delay = computeBackoffMs(attempt + 1, baseDelayMs, maxDelayMs);
    await sleep(delay, signal);
  }

  throw new NetworkError(
    lastStatus !== null
      ? `HTTP ${lastStatus} after ${retries} attempts`
      : `network unreachable after ${retries} attempts`,
    { status: lastStatus, attempts: retries, cause: lastErr ?? undefined },
  );
}

// ---------------------------------------------------------------------------
// netBus — connection lifecycle events, consumed by hydration effects
// ---------------------------------------------------------------------------

type NetEventKind = "reconnect";
type Listener = () => void;

class NetBus {
  private listeners: Map<NetEventKind, Set<Listener>> = new Map();
  // Tracks whether we've ever seen a successful connection. Prevents the
  // first onopen from being treated as a "reconnect".
  private hasBeenConnected = false;
  private isConnected = false;

  on(kind: NetEventKind, fn: Listener): () => void {
    const set = this.listeners.get(kind) ?? new Set();
    set.add(fn);
    this.listeners.set(kind, set);
    return () => set.delete(fn);
  }

  markConnected(): void {
    const wasDisconnectedAfterConnect = this.hasBeenConnected && !this.isConnected;
    this.isConnected = true;
    this.hasBeenConnected = true;
    if (wasDisconnectedAfterConnect) {
      this.emit("reconnect");
    }
  }

  markDisconnected(): void {
    this.isConnected = false;
  }

  // Test-only reset — resets the "first connect is not a reconnect" latch.
  _resetForTests(): void {
    this.listeners.clear();
    this.hasBeenConnected = false;
    this.isConnected = false;
  }

  private emit(kind: NetEventKind): void {
    const set = this.listeners.get(kind);
    if (!set) return;
    for (const fn of set) {
      try {
        fn();
      } catch (e) {
        console.warn("netBus listener threw", e);
      }
    }
  }
}

export const netBus = new NetBus();

// ---------------------------------------------------------------------------
// pLimit — bounded-concurrency wrapper
// ---------------------------------------------------------------------------
//
// A ``Promise.all(list.map(fetchOne))`` over hundreds of items fires every
// request at once, which Chrome refuses with ``net::ERR_INSUFFICIENT_
// RESOURCES`` once its per-renderer pending-request quota is exhausted.
// ``resilientFetch`` then retries each of those failures 6x with
// exponential backoff — a request storm that multiplies rather than
// heals. Measured on the arrayed workflow's cold-start hydration: ~1000
// unique ``getHandle`` targets fanned out to ~4000 network requests, of
// which >60% failed on the client with the socket-exhaustion error and
// the tail-latency success took ~17 s.
//
// ``pLimit`` caps the number of concurrent invocations so we stay below
// Chrome's threshold. The wrapped function still returns a Promise —
// callers don't need to know about the queue.
export function pLimit<Args extends unknown[], R>(
  concurrency: number,
  fn: (...args: Args) => Promise<R>,
): (...args: Args) => Promise<R> {
  let inFlight = 0;
  const queue: Array<() => void> = [];

  const drain = () => {
    while (inFlight < concurrency && queue.length > 0) {
      const run = queue.shift();
      run?.();
    }
  };

  return (...args: Args) =>
    new Promise<R>((resolve, reject) => {
      const run = () => {
        inFlight += 1;
        fn(...args).then(
          (v) => {
            inFlight -= 1;
            drain();
            resolve(v);
          },
          (e) => {
            inFlight -= 1;
            drain();
            reject(e);
          },
        );
      };
      queue.push(run);
      drain();
    });
}

// ---------------------------------------------------------------------------
// React hooks
// ---------------------------------------------------------------------------

// Monotonically increments each time the WS layer reports a true reconnect
// (previously connected → dropped → back). Wire into ``useEffect`` deps of
// any hydration that should re-run when the network is restored.
export function useReconnectTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => netBus.on("reconnect", () => setTick((t) => t + 1)), []);
  return tick;
}

// Per-card retry — pair with ``<RetryLink onRetry={bump}/>`` in the error
// branch. The tick is a stable dep for the effect that fetches the data.
export function useRetryTick(): { tick: number; bump: () => void } {
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((t) => t + 1), []);
  return { tick, bump };
}
