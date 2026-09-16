/**
 * Theme preference: light / dark / system.
 *
 * Precedence: user pick (localStorage) → OS ``prefers-color-scheme`` → light.
 * The active token set is selected by ``<html data-theme="light|dark">``;
 * ``system`` resolves to whichever the media query says right now and is
 * re-evaluated live when the OS setting changes.
 *
 * Nothing here talks to React so the toggle stays a leaf component and the
 * initial theme can be applied *before* React mounts (see ``applyThemePreload``
 * called from ``main.tsx``) — otherwise the first paint is always light,
 * which flashes for a moment on dark systems.
 */

import { useCallback, useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "hololab.theme";

function readSaved(): ThemePref {
  const raw = typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "system";
}

function systemPref(): ResolvedTheme {
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolve(pref: ThemePref): ResolvedTheme {
  return pref === "system" ? systemPref() : pref;
}

function apply(resolved: ResolvedTheme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", resolved);
}

/**
 * Blocking sync-apply used once from ``main.tsx`` before React renders.
 * Reads localStorage and paints the html attribute so we never flash
 * light-mode on a dark-mode machine.
 */
export function applyThemePreload(): void {
  apply(resolve(readSaved()));
}

/**
 * React hook: returns the current pref + a setter that persists and applies.
 *
 * Consumers should NOT read ``data-theme`` themselves — either read tokens
 * from CSS (preferred) or use the returned ``resolved`` if you truly need
 * a JS branch (e.g. picking a react-flow prop that isn't in the token system).
 */
export function useTheme() {
  const [pref, setPrefState] = useState<ThemePref>(() => readSaved());
  const [resolved, setResolvedState] = useState<ResolvedTheme>(() => resolve(readSaved()));

  useEffect(() => {
    apply(resolved);
  }, [resolved]);

  // Re-resolve when the OS setting flips *and* the user is on "system".
  // Two separate deps so we correctly reattach when pref changes.
  useEffect(() => {
    if (pref !== "system") return;
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolvedState(mq.matches ? "dark" : "light");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  const setPref = useCallback((next: ThemePref) => {
    setPrefState(next);
    setResolvedState(resolve(next));
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // localStorage may be unavailable in private modes — ignore and keep
      // the in-memory choice for this session.
    }
  }, []);

  return { pref, resolved, setPref } as const;
}
