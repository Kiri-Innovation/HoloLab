import "./video-background.css";

/** Shared image/video badge; callers supply intrinsic, loaded-media dimensions. */
export function MediaResolution({ width, height, kind, besideBackground = false }: {
  width: number; height: number; kind: "image" | "video"; besideBackground?: boolean;
}) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  return <span className={`hl-media-resolution${besideBackground ? " hl-video-resolution" : ""}`}
    aria-label={kind === "image" ? "图片尺寸" : "视频尺寸"}>{width} × {height}</span>;
}
