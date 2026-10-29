import { MediaResolution } from "./MediaResolution";
import { useEffect, useState } from "react";

/** Read original-media metadata, never the low-resolution playback proxy. */
export function VideoResolution({ src }: { src: string }) {
  const [size, setSize] = useState<{ src: string; width: number; height: number }>();
  useEffect(() => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      if (video.videoWidth > 0 && video.videoHeight > 0)
        setSize({ src, width: video.videoWidth, height: video.videoHeight });
    };
    video.src = src;
    return () => {
      video.onloadedmetadata = null;
      video.removeAttribute("src");
      video.load();
    };
  }, [src]);
  if (!size || size.src !== src) return null;
  return <MediaResolution width={size.width} height={size.height} kind="video" besideBackground />;
}
