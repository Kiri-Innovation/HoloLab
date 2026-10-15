from fractions import Fraction

from _media import encode, entries, main, probe, video


def run(i, p, out):
    clips = [video(entry) for entry in entries(i["videos"])]
    if not clips:
        raise ValueError("videos must contain at least one clip")
    signatures = []
    for clip in clips:
        s, _ = probe(clip)
        signatures.append(
            (
                s["width"],
                s["height"],
                Fraction(s["avg_frame_rate"]),
                s.get("sample_aspect_ratio", "1:1"),
            )
        )
    if len(set(signatures)) != 1:
        raise ValueError(
            "All clips must have the same width, height, frame rate and pixel aspect ratio; normalize them first"
        )
    args = []
    filters = []
    for index, clip in enumerate(clips):
        args += ["-i", str(clip)]
        filters.append(f"[{index}:v:0]setpts=PTS-STARTPTS[v{index}]")
    filters.append(
        "".join(f"[v{i}]" for i in range(len(clips)))
        + f"concat=n={len(clips)}:v=1:a=0,pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1[out]"
    )
    encode([*args, "-filter_complex", ";".join(filters), "-map", "[out]"], out)


if __name__ == "__main__":
    main(run)
