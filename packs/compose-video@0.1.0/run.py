import json
import math
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from _media import encode, entries, main, probe, video

DECIMAL = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: expected a number, not boolean/string")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError(f"{label}: expected a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{label}: expected a finite number")
    return result


def attributes(path, ids, label, vector=False):
    root = Path(path)
    if not root.is_dir():
        raise ValueError(f"{label}: expected an array directory")
    files = entries(root)
    if len(files) != len(ids):
        raise ValueError(f"{label} has {len(files)} layers; videos has {len(ids)} layers")
    values = []
    for index, item in enumerate(files):
        context = f"{label}[{index}] ({item.name})"
        if not item.is_file():
            raise ValueError(f"{context}: expected one file")
        raw = item.read_text().strip()
        if vector:
            try:
                value = json.loads(raw)
            except ValueError as exc:
                raise ValueError(f"{context}: invalid vec3 JSON: {exc}") from None
            if not isinstance(value, list) or len(value) != 3:
                raise ValueError(f"{context}: expected JSON [x_ratio, y_ratio, scale]")
            value = [finite(v, context) for v in value]
            if value[2] <= 0:
                raise ValueError(f"{context}: scale must be > 0")
        else:
            if not DECIMAL.fullmatch(raw) or not math.isfinite(float(raw)):
                raise ValueError(f"{context}: expected one finite decimal number")
            value = Fraction(raw)
            if value < 0:
                raise ValueError(f"{context}: offset must be >= 0")
        values.append(value)
    return values


def info(path):
    stream, _ = probe(path)
    sar = stream.get("sample_aspect_ratio", "1:1")
    sar = Fraction(sar.replace(":", "/")) if sar not in ("N/A", "0:1") else Fraction(1)
    width, height = float(stream["width"] * sar), stream["height"]
    for side in stream.get("side_data_list", []):
        if abs(float(side.get("rotation", 0))) % 180 == 90:
            width, height = height, width
            break
    rate = None
    for key in ["avg_frame_rate", "r_frame_rate"]:
        try:
            candidate = Fraction(stream.get(key, "0/1"))
            if candidate > 0:
                rate = candidate
                break
        except (ValueError, ZeroDivisionError):
            pass
    try:
        duration = Fraction(str(stream.get("duration", "N/A")))
    except (ValueError, ZeroDivisionError):
        # Containers such as MKV often omit stream duration. Do not use
        # format duration: an unrelated audio track may run longer.
        packets = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_packets",
                    "-show_entries",
                    "packet=pts_time,duration_time",
                    "-of",
                    "json",
                    str(path),
                ],
                text=True,
            )
        )["packets"]
        stamped = sorted(
            (Fraction(q["pts_time"]), q.get("duration_time"))
            for q in packets
            if q.get("pts_time") not in (None, "N/A")
        )
        if not stamped:
            raise ValueError(f"{path}: cannot determine natural video duration") from None
        fallback = 1 / rate if rate else Fraction(0)
        duration = (
            max(t + (Fraction(d) if d not in (None, "N/A") else fallback) for t, d in stamped)
            - stamped[0][0]
        )
    if duration <= 0:
        raise ValueError(f"{path}: video duration must be positive")
    return width, height, duration, rate


def run(i, p, out):
    for port in ("videos", "transforms", "offsets"):
        if port not in i:
            raise ValueError(f"required input {port!r} is not provided")
    root = Path(i["videos"])
    if not root.is_dir():
        raise ValueError("videos must be an array directory")
    layers = entries(root)
    if not layers:
        raise ValueError("videos must contain at least one layer")
    ids = [layer.name for layer in layers]
    transforms = attributes(i["transforms"], ids, "transforms", True)
    offsets = attributes(i["offsets"], ids, "offsets")
    clips = [video(layer) for layer in layers]
    metadata = [info(clip) for clip in clips]
    width, height = [max(2, math.ceil(v / 2) * 2) for v in metadata[0][:2]]
    rate = Fraction(str(finite(p["fps"], "fps"))) if p.get("fps") is not None else metadata[0][3]
    if rate is None or not 0 < rate <= 240:
        raise ValueError("fps must be > 0 and <= 240; specify fps if the base rate is unavailable")
    ends = [offset + data[2] for offset, data in zip(offsets, metadata, strict=True)]
    length = max(ends) * rate
    frames = -(-length.numerator // length.denominator)
    args = ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={rate}"]
    filters = []
    for index, (clip, data, start, end) in enumerate(
        zip(clips, metadata, offsets, ends, strict=True)
    ):
        args += ["-i", str(clip)]
        iw, ih, duration, _ = data
        factor = min(width / iw, height / ih)
        transform = transforms[index]
        scale = transform[2]
        sizes = [iw * factor * scale, ih * factor * scale]
        if not all(math.isfinite(v) for v in sizes):
            raise ValueError(f"transforms[{ids[index]}]: scale exceeds finite pixel range")
        sw, sh = [max(1, round(v)) for v in sizes]
        x, y = transform[0] * width, transform[1] * height
        if not all(math.isfinite(v) for v in (x, y)):
            raise ValueError(f"transforms[{ids[index]}]: position exceeds finite pixel range")
        # Keep source timestamps: pre-resampling a short layer can discard all
        # its frames before its offset is applied. Framesync samples it on the
        # canvas clock. Hold its last frame only within the natural-duration
        # window; enable prevents any frozen tail after that window ends.
        # AVTB permits offsets finer than the source frame interval.
        filters.append(
            f"[{index + 1}:v:0]setpts=PTS-STARTPTS,trim=duration={float(duration)},"
            f"scale={sw}:{sh},setsar=1,format=rgba,"
            f"settb=AVTB,setpts=PTS+{float(start)}/TB[l{index}]"
        )
        previous = "0:v" if index == 0 else f"c{index - 1}"
        filters.append(
            f"[{previous}][l{index}]overlay=x={x}:y={y}:format=auto:"
            f"eof_action=repeat:repeatlast=1:enable='gte(t,{float(start)})*lt(t,{float(end)})'[c{index}]"
        )
    encode(
        [
            *args,
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[c{len(clips) - 1}]",
            "-frames:v",
            str(frames),
            "-r",
            str(rate),
        ],
        out,
    )


if __name__ == "__main__":
    main(run)
