from pathlib import Path

from _media import encode, number, probe, video

POSITIONS = {
    f"{v}-{h}": (x, y)
    for v, y in [("top", "{m}"), ("middle", "(H-h)/2"), ("bottom", "H-h-{m}")]
    for h, x in [("left", "{m}"), ("center", "(W-w)/2"), ("right", "W-w-{m}")]
}


def run(i, p, out, *, video_layer=False):
    src = video(i["video"])
    layer = video(i["overlay"]) if video_layer else Path(i["image"])
    if not layer.is_file():
        raise ValueError("image must be one image file")
    ratio = number(p.get("width_ratio", 0.25), "width_ratio", minimum=0.001)
    opacity = number(p.get("opacity", 1), "opacity")
    margin = number(p.get("margin", 20), "margin")
    if ratio > 1 or opacity > 1:
        raise ValueError("width_ratio and opacity must be <= 1")
    if int(margin) != margin:
        raise ValueError("margin must be an integer")
    margin = int(margin)
    stream, available = probe(src)
    start, end = window(p, available)
    width, height = stream["width"], stream["height"]
    # ffmpeg autorotates display-matrix videos before filtering.
    for side in stream.get("side_data_list", []):
        if abs(float(side.get("rotation", 0))) % 180 == 90:
            width, height = height, width
            break
    available_w, available_h = width - 2 * margin, height - 2 * margin
    if min(available_w, available_h) < 1:
        raise ValueError("margin leaves no room for the image")
    box_w = max(1, min(int(width * ratio), available_w))
    x, y = POSITIONS[p.get("position", "top-right")]
    x, y = x.format(m=margin), y.format(m=margin)
    for name, axis, size in [("offset_x_ratio", "x", "W-w"), ("offset_y_ratio", "y", "H-h")]:
        if p.get(name) is not None:
            value = number(p[name], name)
            if value > 1:
                raise ValueError(f"{name} must be <= 1")
            frame = "W" if axis == "x" else "H"
            expression = f"min(max({value}*{frame},{margin}),{size}-{margin})"
            if axis == "x":
                x = expression
            else:
                y = expression
    timing = f"setpts=PTS-STARTPTS+{start}/TB," if video_layer else ""
    eof = "eof_action=pass:repeatlast=0" if video_layer else "eof_action=repeat:repeatlast=1"
    # between is inclusive; lt makes the end exclusive, matching video-trim.
    enable = f"between(t,{start},{end})*lt(t,{end})"
    filt = (
        f"[1:v:0]{timing}scale={box_w}:{available_h}:force_original_aspect_ratio=decrease,"
        f"setsar=1,format=rgba,colorchannelmixer=aa={opacity}[ov];"
        f"[0:v:0][ov]overlay=x='{x}':y='{y}':format=auto:"
        f"{eof}:enable='{enable}',"
        "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1[v]"
    )
    # The primary video controls output timestamps and end, even after layer EOF.
    encode(
        [
            "-i",
            str(src),
            "-i",
            str(layer.resolve()),
            "-filter_complex",
            filt,
            "-map",
            "[v]",
            "-vsync",
            "0",
        ],
        out,
    )


def window(params, available):
    """Trim precedence, but omission keeps legacy overlays visible to video EOF."""
    start = number(params.get("start_seconds", 0), "start_seconds")
    if params.get("end_seconds") is not None:
        end = number(params["end_seconds"], "end_seconds")
        if end <= start:
            raise ValueError("end_seconds must be greater than start_seconds")
    elif params.get("duration_seconds") is not None:
        end = start + number(params["duration_seconds"], "duration_seconds", positive=True)
        number(end, "window end")
    else:
        end = available
    if start >= available:
        raise ValueError("start_seconds must be before the end of the video")
    return start, end
