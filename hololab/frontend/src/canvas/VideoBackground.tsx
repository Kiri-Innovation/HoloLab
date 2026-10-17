import { useSyncExternalStore } from "react";
import "./video-background.css";

const KEY = "hololab.videoPreviewBackground";
const EVENT = "hololab:video-preview-background";
const MODES = ["black", "transparent", "checkerboard"] as const;
type Mode = typeof MODES[number];
const LABELS: Record<Mode, string> = { black: "黑色", transparent: "纯透明", checkerboard: "棋盘格" };
let fallback: Mode = "black";
function read(): Mode {
  try {
    const saved = localStorage.getItem(KEY);
    return MODES.includes(saved as Mode) ? saved as Mode : fallback;
  } catch { return fallback; }
}
function subscribe(notify: () => void) {
  window.addEventListener(EVENT, notify);
  window.addEventListener("storage", notify);
  return () => {
    window.removeEventListener(EVENT, notify);
    window.removeEventListener("storage", notify);
  };
}
export function useVideoBackground() {
  return useSyncExternalStore(subscribe, read, () => "black" as Mode);
}
export function VideoBackgroundButton({ mode }: { mode: Mode }) {
  const next = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
  const label = `画面外背景：${LABELS[mode]}；点击切换为${LABELS[next]}`;
  return <button
    type="button"
    className="hl-video-background-button nodrag nopan"
    aria-label={label}
    title={label}
    onKeyDown={(event) => event.stopPropagation()}
    onClick={(event) => {
      event.stopPropagation();
      fallback = next;
      try { localStorage.setItem(KEY, next); } catch { /* Keep this session usable. */ }
      window.dispatchEvent(new Event(EVENT));
    }}
  ><span aria-hidden="true" className="hl-video-background-icon" data-mode={mode} /></button>;
}
