from pathlib import Path

from _media import encode, main, number, probe, video

POSITIONS = {
    f"{v}-{h}": (x, y)
    for v, y in [("top", "{m}"), ("middle", "(H-h)/2"), ("bottom", "H-h-{m}")]
    for h, x in [("left", "{m}"), ("center", "(W-w)/2"), ("right", "W-w-{m}")]
}


def run(i, p, out):
    src = video(i["video"])
    image = Path(i["image"])
    if not image.is_file():
        raise ValueError("image must be one image file")
    ratio = number(p.get("width_ratio", 0.25), "width_ratio", minimum=0.001)
    opacity = number(p.get("opacity", 1), "opacity")
    margin = number(p.get("margin", 20), "margin")
    if ratio > 1 or opacity > 1:
        raise ValueError("width_ratio and opacity must be <= 1")
    if int(margin) != margin:
        raise ValueError("margin must be an integer")
    margin = int(margin)
    stream, _ = probe(src)
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
    filt = (
        f"[1:v:0]scale={box_w}:{available_h}:force_original_aspect_ratio=decrease,"
        f"setsar=1,format=rgba,colorchannelmixer=aa={opacity}[ov];"
        f"[0:v:0][ov]overlay=x={x}:y={y}:format=auto:"
        "eof_action=repeat:repeatlast=1,"
        "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1[v]"
    )
    # A single still frame is repeated by framesync. The primary video alone
    # controls the end and timestamps; no looping image input or shortest cut.
    encode(
        [
            "-i",
            str(src),
            "-i",
            str(image.resolve()),
            "-filter_complex",
            filt,
            "-map",
            "[v]",
            "-vsync",
            "0",
        ],
        out,
    )


if __name__ == "__main__":
    main(run)
