from _media import encode, main, number, probe, video


def run(i, p, out):
    src = video(i["video"])
    start = number(p["start_seconds"], "start_seconds")
    duration = number(p["duration_seconds"], "duration_seconds", positive=True)
    _, available = probe(src)
    if start >= available:
        raise ValueError("start_seconds must be before the end of the video")
    encode(
        ["-i", str(src), "-ss", str(start), "-t", str(duration), "-map", "0:v:0"],
        out,
        "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1",
    )


if __name__ == "__main__":
    main(run)
