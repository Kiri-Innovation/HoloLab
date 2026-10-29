import tempfile
from pathlib import Path

from _media import encode, main, number, video

POSITIONS = {
    f"{v}-{h}": (x, y)
    for v, y in [("top", "20"), ("middle", "(h-text_h)/2"), ("bottom", "h-text_h-20")]
    for h, x in [("left", "20"), ("center", "(w-text_w)/2"), ("right", "w-text_w-20")]
}


def run(i, p, out):
    src = video(i["video"])
    text = p["text"]
    if not isinstance(text, str) or not text.strip() or "\0" in text:
        raise ValueError("text must be nonempty and contain no NUL")
    size = number(p["font_size"], "font_size", minimum=1)
    if int(size) != size:
        raise ValueError("font_size must be an integer")
    x, y = POSITIONS[p["position"]]
    # Text is a UTF-8 file: quotes, percent signs and colons stay literal.
    with tempfile.TemporaryDirectory() as tmp:
        textfile = Path(tmp) / "title.txt"
        textfile.write_text(text, encoding="utf-8")
        filt = f"drawtext=textfile={textfile}:expansion=none:fontcolor=white:fontsize={int(size)}:x={x}:y={y}:box=1:boxcolor=black@0.55:boxborderw=8,pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1"
        encode(["-i", str(src), "-map", "0:v:0"], out, filt)


if __name__ == "__main__":
    main(run)
