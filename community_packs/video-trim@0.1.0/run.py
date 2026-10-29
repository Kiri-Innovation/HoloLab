from _media import encode, main, number, probe, video


def run(i, p, out):
    src = video(i["video"])
    start = number(p.get("start_seconds", 0), "start_seconds")
    if p.get("end_seconds") is not None:
        end = number(p["end_seconds"], "end_seconds")
        if end <= start:
            raise ValueError("end_seconds must be greater than start_seconds")
        duration = end - start
    else:
        duration = number(p.get("duration_seconds", 5), "duration_seconds", positive=True)
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
