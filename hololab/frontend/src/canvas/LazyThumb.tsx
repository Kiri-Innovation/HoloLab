// IntersectionObserver-gated thumbnail image with a fast-scroll skip window.
//
// Rationale (measured, 2026-09-23):
//   The gateway + file server + node runtime all share one asyncio event
//   loop (see ``docs/architecture.md#execution-boundary`` and
//   ``hololab/cli.py:_run_all_in_one_async``). Every ``/_thumb/…`` GET
//   competes with SQLite writer coroutines and node WS traffic. During a
//   fan-out, a full-viewport grid of ~200 thumbnails firing at once
//   pushes ``/api/jobs`` p50 latency from 2.2 ms to 16.7 ms and lengthens
//   the fan-out wall by ~6–12%.
//
//   ``<img loading="lazy">`` alone doesn't fix this: the browser's
//   ``lazy`` heuristic uses a generous ~1000–2000 px "near-viewport"
//   margin and still speculatively fetches many offscreen tiles when
//   the user opens a preview drawer. ``LazyThumb`` gates ``src`` on a
//   stricter ``IntersectionObserver`` with a small ``rootMargin``, so
//   only tiles that are (nearly) *actually visible* trigger a network
//   request.
//
//   Fast-scroll skip: when a tile enters the viewport and leaves within
//   ``debounceMs``, we NEVER request its thumbnail. The user was flying
//   past — the thumb wouldn't have been visible long enough to matter.
//
//   Once loaded, ``src`` stays set even if the tile scrolls away — we
//   don't tear down cached bytes.
//
// The component is a drop-in replacement for
//     <img src={thumbUrl} alt=... loading="lazy" style=... />
// at 3 sites in ``previews.tsx`` (strip renderer, nested-group card
// stack, nested-group detail strip). All other props (``alt``, ``style``,
// ``onError``, etc.) forward to the underlying <img>.

import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ImgHTMLAttributes } from "react";
import { pLimit } from "../net";

/**
 * Global default: how strict the "actually visible" heuristic is.
 *
 * ``50 px`` = "start loading when the tile edge is within 50 px of the
 * viewport". Deliberately much tighter than the browser's built-in
 * ``loading="lazy"`` margin (~1000–2000 px) so a preview drawer opened
 * mid-scroll doesn't fan out 200 requests before the user has decided
 * whether to keep scrolling. Overridable per-instance via the
 * ``rootMargin`` prop.
 */
const DEFAULT_ROOT_MARGIN = "50px";

/**
 * Global default: how long a tile must remain visible before we start
 * the request. 200 ms is well below "noticeable delay" for the user
 * (paint feels immediate at any value < 250 ms) but long enough that a
 * fling-scroll across 100 tiles doesn't fire 100 requests. Overridable
 * via ``debounceMs``.
 */
const DEFAULT_DEBOUNCE_MS = 200;
// Thumbnail bytes are deliberately isolated from canvas-state/facts and from
// metadata summaries.  Waiting for load keeps the slot occupied for the real
// network transfer (rather than merely for setting an img src attribute).
const THUMBNAIL_CONCURRENCY = 2;
const THUMB_RETRY_DELAYS_MS = [1_000, 2_000, 4_000];
const RETRY_ICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cpath fill='%23b8c2d0' d='M25 8V3l-2 2a11 11 0 1 0 3 12h-3a8 8 0 1 1-2-6l-3 3h7V8Z'/%3E%3C/svg%3E";

// Two is intentional: a thumbnail request may invoke ffmpeg at the node,
// and separate from the browser's HTTP/1.1 connection ceiling. Raising this
// would make an already saturated tunnel worse; retries below are jittered by
// their independent visibility timing instead.
const loadThumbnail = pLimit(THUMBNAIL_CONCURRENCY, (src: string) => new Promise<boolean>((resolve) => {
  const image = new Image();
  image.onload = () => resolve(true);
  image.onerror = () => resolve(false);
  image.src = src;
}));

export interface LazyThumbProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> {
  /** The thumbnail URL. Only fetched once the tile becomes visible. */
  src: string;
  /**
   * IntersectionObserver ``rootMargin``. Default is 50 px (tight).
   * Increase for grid views that want a smoother scroll-in with the
   * trade-off of more speculative fetches; decrease to further
   * throttle traffic at the cost of a visible fade-in on scroll.
   */
  rootMargin?: string;
  /**
   * How long the tile must stay in view before its ``src`` is set.
   * Fast-scroll skip: a tile that enters and exits within this window
   * is NEVER requested. Set to ``0`` to disable and load on first
   * intersection.
   */
  debounceMs?: number;
  /**
   * Optional style forwarded to the placeholder wrapper. Keep the
   * ``<img>`` centred/sized against the parent so the surrounding
   * layout doesn't jump on load — the previews.tsx call sites all
   * wrap this in a fixed-size container.
   */
  style?: CSSProperties;
}

/**
 * A thumbnail ``<img>`` whose ``src`` attribute is populated only after
 * the element has been continuously visible for ``debounceMs``.
 *
 * Progressive-enhancement fallback: on runtimes without
 * ``IntersectionObserver`` (very old browsers, or pre-hydration SSR),
 * the image loads immediately — matching the pre-refactor behavior
 * one-for-one. ``loading="lazy"`` stays on the underlying ``<img>`` so
 * the browser's own lazy path still applies as a second line of
 * defence once ``src`` is set.
 */
export function LazyThumb({
  src,
  rootMargin = DEFAULT_ROOT_MARGIN,
  debounceMs = DEFAULT_DEBOUNCE_MS,
  style,
  alt,
  onClick,
  onKeyDown,
  onError,
  title,
  ...imgProps
}: LazyThumbProps) {
  // ``loaded`` is sticky: once we set ``src``, we leave it set even if
  // the tile scrolls out of view. Reloading on every scroll-back would
  // defeat the browser cache and produce visible flicker.
  const [loaded, setLoaded] = useState(false);
  const [visible, setVisible] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    if (loaded || failed) return;
    // No observer available (jsdom, ancient browsers) → load immediately;
    // matches the pre-refactor <img src=…> behavior. Not silent: the
    // ``fallback`` branch is what tests can assert on.
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const el = ref.current;
    if (el === null) return;

    // Debounce state is scoped to this effect so a re-mount doesn't
    // leak a stale timer. ``visibleTimer`` is set when the tile enters
    // the viewport; cleared if it leaves before the timer fires.
    let visibleTimer: ReturnType<typeof setTimeout> | null = null;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            // Start the "must stay visible" timer. Only when it
            // survives ``debounceMs`` do we set ``src``.
            if (visibleTimer !== null) clearTimeout(visibleTimer);
            visibleTimer = setTimeout(() => {
              setVisible(true);
            }, debounceMs);
          } else if (visibleTimer !== null) {
            // Left before the debounce window closed — fast-scroll
            // skip. No request will fire.
            clearTimeout(visibleTimer);
            visibleTimer = null;
            setVisible(false);
          } else {
            // If a queued/preflight attempt has not completed yet, pause its
            // retry chain offscreen.  A later re-entry resumes it from the
            // same bounded attempt counter rather than competing with the
            // visible tiles.
            setVisible(false);
          }
        }
      },
      { rootMargin, threshold: 0 },
    );
    observer.observe(el);

    return () => {
      if (visibleTimer !== null) clearTimeout(visibleTimer);
      observer.disconnect();
    };
  }, [loaded, failed, rootMargin, debounceMs, src]);

  useEffect(() => {
    if (!visible || loaded || failed) return;
    let active = true;
    let retryTimer: number | null = null;
    const retryUrl = attempt === 0 ? src : `${src}${src.includes("?") ? "&" : "?"}retry=${attempt}`;
    void loadThumbnail(retryUrl).then((ok) => {
      if (!active) return;
      if (ok) {
        setLoaded(true);
        return;
      }
      if (attempt >= THUMB_RETRY_DELAYS_MS.length) {
        setFailed(true);
        return;
      }
      retryTimer = window.setTimeout(() => {
        if (active) setAttempt(current => current + 1);
      }, THUMB_RETRY_DELAYS_MS[attempt]);
    });
    return () => {
      active = false;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
    };
  }, [visible, loaded, failed, attempt, src]);

  const retryManually = () => {
    setFailed(false);
    setVisible(true);
    setAttempt(0);
  };

  // The underlying <img> is always rendered so IntersectionObserver has
  // something to observe. Before ``loaded`` we omit ``src`` entirely
  // — HTML browsers treat that as "no request" (an empty ``src=""``
  // still fires a load event for the current URL, which is the opposite
  // of what we want).
  const imgSrc = loaded ? (attempt === 0 ? src : `${src}${src.includes("?") ? "&" : "?"}retry=${attempt}`) : failed ? RETRY_ICON : undefined;

  return (
    // eslint-disable-next-line jsx-a11y/alt-text -- alt is forwarded via ``alt`` prop
    <img
      ref={ref}
      src={imgSrc}
      alt={alt}
      data-hl-thumb-state={failed ? "failed" : loaded ? "loaded" : visible ? "loading" : "idle"}
      data-hl-thumb-retry={failed ? "" : undefined}
      title={failed ? "缩略图加载失败，点击重试" : title}
      role={failed ? "button" : undefined}
      tabIndex={failed ? 0 : undefined}
      onClick={failed ? (event) => { event.stopPropagation(); retryManually(); } : onClick}
      onKeyDown={failed ? (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          event.stopPropagation();
          retryManually();
        }
      } : onKeyDown}
      onError={(event) => {
        // The constrained preloader usually warms the browser cache.  If the
        // rendered image still errors (eviction/race), reuse the same retry
        // path rather than latching a broken tile until it remounts.
        if (!failed) {
          setLoaded(false);
          setVisible(true);
          setAttempt(current => current + 1);
        }
        onError?.(event);
      }}
      // Keep browser-native lazy as a second-line defence: once ``src``
      // is set, the browser can still skip the fetch if the tile is
      // *still* far off-viewport (rare but harmless).
      loading="lazy"
      style={style}
      {...imgProps}
    />
  );
}
